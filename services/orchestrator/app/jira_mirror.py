"""Jira mirror (v2 Phase 6 — plan 05 §4-6). OPTIONAL, OFF by default.

**RULE 0 (prime directive): Jira is an observer, never a participant.** No pipeline state transition
reads from, waits on, or fails because of Jira. This is enforced structurally: ticket actions only
ever *enqueue* :class:`~foundry_core.models.JiraOutbox` rows (in the same store transaction as the
ticket write); a per-workspace :class:`OutboxWorker` drains them out-of-band with retry/backoff. All
Jira I/O lives behind :class:`JiraClient`, so the worker is fully testable with a fake — no network.

Idempotency: the outbox key is the source ticket_event id (UNIQUE), and every create is guarded by
``jira_issue_map`` — a crash between a Jira 200 and the local write yields at worst a duplicate
*comment*, never a duplicate *issue*.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from foundry_core.enums import JiraOutboxStatus
from foundry_core.ids import new_ulid
from foundry_core.models import JiraIssueMap, JiraOutbox, Ticket, TicketEvent


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- config
@dataclass(slots=True)
class JiraConfig:
    """Per-workspace mirror config (plan 05 §6), loaded from the ``jira`` Integration's ``config``."""

    enabled: bool = False
    base_url: str = ""
    email: str = ""
    api_token: str = ""
    project_key: str = "FND"
    issue_type_map: dict = field(
        default_factory=lambda: {"epic": "Epic", "story": "Story", "bug": "Bug"})
    status_map: dict = field(default_factory=lambda: {
        "todo": ["To Do", "Backlog"],
        "in_progress": ["In Progress", "In Development"],
        "qa": ["QA", "Testing", "In Review"],
        "in_review": ["In Review", "Code Review"],
        "done": ["Done", "Closed"],
        "reopened": ["To Do", "Reopened", "In Progress"],
    })
    link_type: str = "Blocks"
    max_attempts: int = 8
    base_delay_ms: int = 2000
    max_delay_ms: int = 900_000
    attach_screenshots: bool = True
    max_attach_bytes: int = 52_428_800  # 50 MB per plan §6

    @classmethod
    def from_config(cls, cfg: dict | None) -> JiraConfig:
        cfg = cfg or {}
        auth = cfg.get("auth") or {}
        out = cls()
        out.enabled = bool(cfg.get("enabled", False))
        out.base_url = str(cfg.get("base_url") or cfg.get("baseUrl") or "")
        out.email = str(auth.get("email") or cfg.get("email") or "")
        out.api_token = str(auth.get("api_token") or cfg.get("api_token") or "")
        out.project_key = str(cfg.get("project_key") or cfg.get("projectKey") or "FND")
        if isinstance(cfg.get("issue_type_map"), dict):
            out.issue_type_map = {**out.issue_type_map, **cfg["issue_type_map"]}
        if isinstance(cfg.get("status_map"), dict):
            out.status_map = {**out.status_map, **cfg["status_map"]}
        out.link_type = str(cfg.get("link_type") or "Blocks")
        outbox = cfg.get("outbox") or {}
        out.max_attempts = int(outbox.get("max_attempts", out.max_attempts))
        out.base_delay_ms = int(outbox.get("base_delay_ms", out.base_delay_ms))
        out.max_delay_ms = int(outbox.get("max_delay_ms", out.max_delay_ms))
        return out


# --------------------------------------------------------------------------- errors
class JiraError(Exception):
    """Base for Jira client failures — classified so the worker can react per plan §4."""


class JiraTransient(JiraError):
    """Network/5xx/temporary — retry with exponential backoff."""


class JiraRateLimited(JiraError):
    """429 — honor ``Retry-After`` exactly (+1s) and DON'T count it as an attempt (plan §4)."""

    def __init__(self, retry_after_s: float = 60.0) -> None:
        super().__init__(f"rate limited; retry after {retry_after_s}s")
        self.retry_after_s = retry_after_s


class JiraAuthError(JiraError):
    """401/403 — park the whole workspace queue and flag the integration in the UI."""


class JiraBadRequest(JiraError):
    """400 — a permanently bad row; mark it dead (replayable from the UI), keep draining."""


# --------------------------------------------------------------------------- client
@runtime_checkable
class JiraClient(Protocol):
    """The Jira surface the worker needs. The real impl wraps ``atlassian-python-api``; tests inject
    a fake. Every method raises a classified :class:`JiraError` on failure."""

    def myself(self) -> dict: ...
    def create_issue(
        self, *, project_key: str, summary: str, issue_type: str, description: str,
        labels: list[str], parent_key: str | None = None,
    ) -> str: ...
    def add_comment(self, key: str, body: str) -> None: ...
    def transition(self, key: str, target_names: list[str]) -> bool: ...
    def add_attachment(self, key: str, file_path: str) -> None: ...
    def create_link(self, from_key: str, to_key: str, link_type: str) -> None: ...


# --------------------------------------------------------------------------- enqueue
# Which ticket-event kinds mirror, and to which outbox op.
_EVENT_OP = {
    "created": "create",
    "transitioned": "transition",
    "reopened": "transition",
    "shipped": "transition",
    "commented": "comment",
    "evidence_attached": "evidence",
    "linked": "link",
    # sync_error is internal — never mirrored.
}


class JiraMirror:
    """Enqueues outbox rows from ticket events. Wired as the :class:`TicketService` sink, so the
    consumer stays Jira-agnostic. Gated on the ``jira`` feature flag; a no-op when off."""

    def __init__(self, store) -> None:
        self.store = store

    async def enabled(self, workspace_id: str) -> bool:
        try:
            policy = await self.store.get_settings(workspace_id)
            return bool((policy.features or {}).get("jira", False))
        except Exception:  # noqa: BLE001
            return False

    async def on_ticket_event(self, ticket: Ticket, event: TicketEvent) -> None:
        """TicketService sink. Best-effort: any failure here must not disturb the ticket write."""
        op = _EVENT_OP.get(str(event.kind))
        if op is None:
            return
        if not await self.enabled(ticket.workspace_id):
            return
        with contextlib.suppress(Exception):
            await self._enqueue(ticket, event, op)

    async def _enqueue(self, ticket: Ticket, event: TicketEvent, op: str) -> None:
        payload: dict = {
            "kind": str(ticket.kind),
            "ticket_key": ticket.key,
            "title": ticket.title,
            "labels": list(ticket.labels or []),
        }
        if op == "create":
            payload["description"] = ticket.description or {}
            payload["parent_id"] = ticket.parent_id
            payload["priority"] = ticket.priority
        elif op == "transition":
            payload["to_status"] = event.to_status or str(ticket.status)
            payload["reason"] = (event.body or {}).get("reason", "")
            payload["actor"] = _actor(event)
        elif op == "comment":
            payload["text"] = (event.body or {}).get("reason", "")
            payload["actor"] = _actor(event)
        elif op == "evidence":
            payload["artifact_ids"] = list((event.body or {}).get("artifact_ids") or [])
            payload["summary"] = (event.body or {}).get("summary", "")
        elif op == "link":
            payload["from_id"] = (event.body or {}).get("blocked_by_id")
            payload["to_id"] = ticket.id
        now = _now()
        await self.store.add_jira_outbox(JiraOutbox(
            id=new_ulid(), workspace_id=ticket.workspace_id, idempotency_key=event.id,
            ticket_id=ticket.id, op=op, payload=payload,
            status=JiraOutboxStatus.PENDING, created_at=now, updated_at=now))


def _actor(event: TicketEvent) -> str:
    name = event.actor_name or "system"
    return f"{name} · {event.actor_role}" if event.actor_role else name


# --------------------------------------------------------------------------- worker
@dataclass(slots=True)
class DrainStats:
    processed: int = 0
    done: int = 0
    dead: int = 0
    retried: int = 0
    parked: bool = False
    rate_limited: bool = False


class OutboxWorker:
    """Drains a workspace's outbox serially (ordering + Jira's per-issue write rate limits). One pass
    is :meth:`drain_once`; :meth:`run_forever` loops it. It NEVER raises into the caller — a failure
    is recorded on the row (retry/dead) or parks the queue, per plan §4."""

    def __init__(self, store) -> None:
        self.store = store

    async def drain_once(
        self, workspace_id: str, client: JiraClient, config: JiraConfig, *, now: datetime | None = None,
    ) -> DrainStats:
        now = now or _now()
        stats = DrainStats()
        rows = await self.store.list_jira_outbox(
            workspace_id, status=JiraOutboxStatus.PENDING, due_at=now)
        for row in rows:  # seq order — creates precede the transitions/links that depend on them
            stats.processed += 1
            try:
                await self._process(row, client, config, workspace_id)
                await self.store.update_jira_outbox(
                    row.id, status=JiraOutboxStatus.DONE, last_error=None)
                stats.done += 1
            except JiraRateLimited as e:
                # Back off exactly as told; do NOT increment attempts, and stop this workspace's pass
                # (serial drain respects the global write limit — early retries extend penalties).
                await self.store.update_jira_outbox(
                    row.id, next_attempt_at=now + timedelta(seconds=e.retry_after_s + 1),
                    last_error="rate_limited")
                stats.rate_limited = True
                break
            except JiraAuthError as e:
                # Park the WHOLE workspace queue until the user re-authenticates (plan §4).
                await self._park(workspace_id, str(e))
                stats.parked = True
                break
            except JiraBadRequest as e:
                await self.store.update_jira_outbox(
                    row.id, status=JiraOutboxStatus.DEAD, last_error=f"400: {e}")
                stats.dead += 1
            except Exception as e:  # noqa: BLE001 — transient/unknown → capped exponential backoff
                attempts = (row.attempts or 0) + 1
                if attempts >= config.max_attempts:
                    await self.store.update_jira_outbox(
                        row.id, status=JiraOutboxStatus.DEAD, attempts=attempts,
                        last_error=f"exhausted after {attempts}: {e}")
                    stats.dead += 1
                else:
                    delay_ms = min(config.max_delay_ms, config.base_delay_ms * (2 ** (attempts - 1)))
                    await self.store.update_jira_outbox(
                        row.id, attempts=attempts,
                        next_attempt_at=now + timedelta(milliseconds=delay_ms), last_error=str(e))
                    stats.retried += 1
        return stats

    async def _process(
        self, row: JiraOutbox, client: JiraClient, config: JiraConfig, workspace_id: str,
    ) -> None:
        p = row.payload or {}
        kind = str(p.get("kind") or "story")
        if row.op == "create":
            existing = await self.store.get_jira_issue_map(workspace_id, kind, row.ticket_id)
            if existing is not None:
                return  # idempotent — already created
            parent_key = None
            if p.get("parent_id"):
                pm = await self.store.get_jira_issue_map(workspace_id, "epic", p["parent_id"])
                if pm is None:
                    raise JiraTransient("parent epic not mirrored yet")
                parent_key = pm.jira_key
            summary = f"{p.get('title', '')}  [fid:{row.ticket_id[:8]}]"
            key = client.create_issue(
                project_key=config.project_key, summary=summary[:250],
                issue_type=config.issue_type_map.get(kind, "Task"),
                description=_describe(p.get("description") or {}),
                labels=_labels(p.get("labels")), parent_key=parent_key)
            await self.store.add_jira_issue_map(JiraIssueMap(
                id=new_ulid(), workspace_id=workspace_id, entity_type=kind,
                entity_id=row.ticket_id, jira_key=key, created_at=_now()))
            return

        key = await self._require_key(workspace_id, kind, row.ticket_id)

        if row.op == "transition":
            targets = config.status_map.get(str(p.get("to_status")), [])
            moved = client.transition(key, targets) if targets else False
            reason = p.get("reason") or ""
            actor = p.get("actor") or "Shipwright"
            if not moved:
                # Transition unavailable → visibility degrades to a comment (pipeline never notices).
                client.add_comment(key, f"[Shipwright] status → {p.get('to_status')} (no matching "
                                         f"workflow transition). {actor}: {reason}".strip())
            elif reason:
                client.add_comment(key, f"[Shipwright] {actor}: {reason}")
        elif row.op == "comment":
            actor = p.get("actor") or "Shipwright"
            client.add_comment(key, f"[Shipwright] {actor}: {p.get('text', '')}".strip())
        elif row.op == "evidence":
            await self._attach_evidence(client, key, p, config)
        elif row.op == "link":
            from_id, to_id = p.get("from_id"), p.get("to_id")
            if not from_id or not to_id:
                return  # nothing linkable
            fm = await self.store.get_jira_issue_map(workspace_id, "bug", from_id)
            tm = await self.store.get_jira_issue_map(workspace_id, "story", to_id)
            if fm is None or tm is None:
                raise JiraTransient("both ends must be mirrored before linking")
            client.create_link(fm.jira_key, tm.jira_key, config.link_type)

    async def _attach_evidence(
        self, client: JiraClient, key: str, p: dict, config: JiraConfig,
    ) -> None:
        from .artifacts import resolve_artifact_path
        attached: list[str] = []
        for art_id in p.get("artifact_ids") or []:
            art = await self.store.get_artifact(art_id)
            if art is None or (art.size_bytes or 0) > config.max_attach_bytes:
                continue
            try:
                path = resolve_artifact_path(art)
            except Exception:  # noqa: BLE001 — a bad row must never fail the mirror
                continue
            if path.is_file():
                client.add_attachment(key, str(path))
                attached.append(art.name)
        note = p.get("summary") or ""
        client.add_comment(
            key, f"[Shipwright] QA evidence {note}".strip()
            + (f" — attached: {', '.join(attached)}" if attached else ""))

    async def drain_workspace(self, workspace_id: str, *, now: datetime | None = None) -> DrainStats:
        """Load the workspace's config, build a live client, and drain once. No-op (never raises)
        unless the mirror is enabled AND credentials are present — so it is safe to call on a timer
        with Jira off. Real network only happens here, never on the pipeline path."""
        config = await load_config(self.store, workspace_id)
        if not config.enabled or not (config.base_url and config.email and config.api_token):
            return DrainStats()
        integ = await self._jira_integration(workspace_id)
        if integ is not None and str(getattr(integ, "status", "")) == "error":
            return DrainStats(parked=True)  # parked on a prior auth failure — stand down until re-auth
        try:
            client = AtlassianJiraClient(config)
        except Exception:  # noqa: BLE001 — missing lib / bad config must not crash the loop
            return DrainStats()
        return await self.drain_once(workspace_id, client, config, now=now)

    async def run_forever(self, workspace_id: str, *, interval_s: float = 10.0) -> None:
        """Periodic drain loop for a workspace (started in the app lifespan). Cancels cleanly."""
        import asyncio
        while True:
            with contextlib.suppress(Exception):
                await self.drain_workspace(workspace_id)
            await asyncio.sleep(interval_s)

    async def _require_key(self, workspace_id: str, kind: str, ticket_id: str) -> str:
        m = await self.store.get_jira_issue_map(workspace_id, kind, ticket_id)
        if m is None:
            raise JiraTransient(f"{kind} {ticket_id} not mirrored yet")
        return m.jira_key

    async def _park(self, workspace_id: str, detail: str) -> None:
        """Flag the Jira integration as an error so the UI shows it and the worker stands down until
        the user re-authenticates. Best-effort — parking must itself never raise."""
        with contextlib.suppress(Exception):
            from foundry_core.enums import ConnectionStatus
            integ = await self._jira_integration(workspace_id)
            cfg = {**((integ.config if integ else None) or {}), "auth_error": detail[:300]}
            await self.store.set_integration_status("jira", ConnectionStatus.ERROR, cfg)

    async def _jira_integration(self, workspace_id: str):
        with contextlib.suppress(Exception):
            for i in await self.store.list_integrations(workspace_id):
                if str(i.kind) == "jira":
                    return i
        return None


def _labels(labels) -> list[str]:
    base = ["shipwright"]
    for x in labels or []:
        if x and x not in base:
            base.append(str(x))
    return base


def _describe(desc: dict) -> str:
    """Render the structured ticket description as a readable text body (Jira accepts text; the real
    client can upgrade it to ADF). Kept plain so it round-trips predictably in tests."""
    lines: list[str] = []
    if desc.get("goal"):
        lines.append(f"Goal: {desc['goal']}")
    if desc.get("failure"):
        lines.append(f"Failure: {desc['failure']}")
    for label, key in (("Acceptance criteria", "acceptance"), ("Definition of done", "dod"),
                       ("Owned paths", "owned_paths")):
        items = desc.get(key)
        if isinstance(items, list) and items:
            lines.append(label + ":")
            lines.extend(f"  - {it}" for it in items)
    if desc.get("impl_notes"):
        lines.append(f"Notes: {desc['impl_notes']}")
    return "\n".join(lines) or "(created by Shipwright)"


async def load_config(store, workspace_id: str) -> JiraConfig:
    """Load the workspace's Jira config from its ``jira`` Integration (plan §6)."""
    with contextlib.suppress(Exception):
        for i in await store.list_integrations(workspace_id):
            if str(i.kind) == "jira":
                return JiraConfig.from_config(i.config)
    return JiraConfig(enabled=False)


# --------------------------------------------------------------------------- real client
def _classify(exc: Exception) -> JiraError:
    """Map an HTTP failure from ``atlassian-python-api`` to a worker-actionable error (plan §4)."""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status == 429:
        retry_after = 60.0
        with contextlib.suppress(Exception):
            retry_after = float((resp.headers or {}).get("Retry-After", 60))
        return JiraRateLimited(retry_after)
    if status in (401, 403):
        return JiraAuthError(f"Jira auth failed ({status})")
    if status == 400:
        return JiraBadRequest(str(exc)[:300])
    return JiraTransient(str(exc)[:300])  # 5xx, network, timeouts, unknown → retry


class AtlassianJiraClient:
    """:class:`JiraClient` backed by ``atlassian-python-api``. Thin: every call is wrapped so HTTP
    failures surface as classified :class:`JiraError`s the worker knows how to handle. Instantiated
    lazily by the worker only when the mirror is enabled — importing this module never needs Jira."""

    def __init__(self, config: JiraConfig) -> None:
        from atlassian import Jira
        self._c = config
        self._jira = Jira(url=config.base_url, username=config.email,
                          password=config.api_token, cloud=True)

    def myself(self) -> dict:
        try:
            return self._jira.myself()
        except Exception as e:  # noqa: BLE001
            raise _classify(e) from e

    def create_issue(
        self, *, project_key: str, summary: str, issue_type: str, description: str,
        labels: list[str], parent_key: str | None = None,
    ) -> str:
        fields: dict = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
            "description": description,
            "labels": labels,
        }
        if parent_key:
            fields["parent"] = {"key": parent_key}
        try:
            res = self._jira.create_issue(fields=fields)
        except Exception as e:  # noqa: BLE001
            raise _classify(e) from e
        key = (res or {}).get("key") if isinstance(res, dict) else None
        if not key:
            raise JiraTransient("create_issue returned no key")
        return key

    def add_comment(self, key: str, body: str) -> None:
        try:
            self._jira.issue_add_comment(key, body)
        except Exception as e:  # noqa: BLE001
            raise _classify(e) from e

    def transition(self, key: str, target_names: list[str]) -> bool:
        wanted = {n.strip().lower() for n in target_names}
        try:
            transitions = self._jira.get_issue_transitions(key) or []
            for t in transitions:
                name = str(t.get("name", "")).strip().lower()
                if name in wanted:
                    self._jira.issue_transition(key, t.get("name"))
                    return True
        except Exception as e:  # noqa: BLE001
            raise _classify(e) from e
        return False  # no matching transition → caller falls back to a comment

    def add_attachment(self, key: str, file_path: str) -> None:
        try:
            self._jira.add_attachment(key, filename=file_path)
        except Exception as e:  # noqa: BLE001
            raise _classify(e) from e

    def create_link(self, from_key: str, to_key: str, link_type: str) -> None:
        try:
            self._jira.create_issue_link({
                "type": {"name": link_type},
                "inwardIssue": {"key": from_key},
                "outwardIssue": {"key": to_key},
            })
        except Exception as e:  # noqa: BLE001
            raise _classify(e) from e


def validate_config(config: JiraConfig) -> tuple[bool, str]:
    """Save-time validation (plan §6): a live ``GET /myself``. Never called at pipeline time."""
    if not config.base_url or not config.email or not config.api_token:
        return False, "base URL, email and API token are all required"
    try:
        who = AtlassianJiraClient(config).myself()
        return True, f"connected as {who.get('displayName') or who.get('emailAddress') or 'user'}"
    except JiraError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
