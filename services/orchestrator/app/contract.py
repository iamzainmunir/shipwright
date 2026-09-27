"""Spec Contract (P1) — the single, versioned source of truth every agent grades against.

The spec/PM agent emits two machine blocks at spec time: the existing ``foundry-criteria`` (UI items)
and a new ``foundry-api`` block (API/response items). :func:`parse_contract` folds both into one
:class:`Contract`; :func:`contract_slice` hands each builder only the items it owns; QA/review get the
whole thing. Every parser is FAIL-SOFT — malformed input yields fewer/zero items, never an error.

A structured feedback ledger (:func:`record_feedback`) replaces prose-only "make it better": every QA
finding / review comment references a contract item id with expected-vs-actual, so rework is grounded.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from foundry_core.ids import new_ulid
from foundry_core.models import Contract, ContractFeedback, ContractItem

from .specdoc import _json_candidates, extract_criteria

# Appended to the spec prompt so the PM also documents the API contract (in addition to CRITERIA_ASK).
API_CONTRACT_ASK = (
    "\n\nIf the app exposes an HTTP API, ALSO end the spec with a fenced ```json foundry-api code block "
    "containing a JSON array; one object per endpoint: "
    '{"id": "API1", "criterion": "<what it does>", "method": "GET|POST|PUT|PATCH|DELETE", '
    '"path": "/api/<route>", "request": {"example": {...}}, '
    '"response": {"status": 200, "contentType": "application/json", "requiredKeys": ["<top-level keys>"]}, '
    '"errors": [{"when": "malformed body", "status": 400}], "severity": "blocking"|"non_blocking"}. '
    "Only include endpoints the app really serves; QA will issue real requests and assert these."
)

_API_FENCE = "foundry-api"


def _norm_severity(v: object) -> str:
    return ("non_blocking" if str(v or "").strip().lower()
            in ("non_blocking", "non-blocking", "minor", "nice-to-have") else "blocking")


def _ui_items_from_criteria(spec_text: str) -> list[ContractItem]:
    """UI contract items — reuse the existing, battle-tested criteria parser."""
    out: list[ContractItem] = []
    for c in extract_criteria(spec_text):
        out.append(ContractItem(
            id=c.id, kind="ui", criterion=c.criterion, severity=c.severity,
            route=c.route, expect_text=list(c.expect_text), expect_selector=list(c.expect_selector),
        ))
    return out


def extract_api_items(spec_text: str) -> list[ContractItem]:
    """Pull API contract items from a ```json foundry-api block (or a bare array as a fallback). Only
    a candidate whose objects carry a ``path`` is treated as the API block, so it never collides with
    the UI ``foundry-criteria`` array. [] on any failure (fail-soft)."""
    text = spec_text or ""
    for cand in _json_candidates(text):
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            continue
        items: list[ContractItem] = []
        for i, it in enumerate(data):
            if not isinstance(it, dict) or not str(it.get("path", "")).strip():
                continue  # an API item is identified by a path (UI items have none)
            items.append(ContractItem(
                id=str(it.get("id") or f"API{i + 1}"),
                kind="api", criterion=str(it.get("criterion", "")).strip(),
                severity=_norm_severity(it.get("severity")),
                method=str(it.get("method") or "GET").upper().strip(),
                path=str(it["path"]).strip(),
                request=it.get("request") if isinstance(it.get("request"), dict) else {},
                response=it.get("response") if isinstance(it.get("response"), dict) else {},
                errors=[e for e in (it.get("errors") or []) if isinstance(e, dict)],
            ))
        if items:
            return items
    return []


def parse_contract(spec_text: str, *, mission_id: str, workspace_id: str, org_id: str | None = None,
                   version: int = 1, contract_id: str | None = None) -> Contract:
    """Parse a spec reply into a Contract (UI + API items). Fail-soft: an empty spec → an empty
    contract, never an error."""
    items = _ui_items_from_criteria(spec_text) + extract_api_items(spec_text)
    now = datetime.now(UTC)
    return Contract(
        id=contract_id or new_ulid(), org_id=org_id, workspace_id=workspace_id, mission_id=mission_id,
        version=version, items=items, created_at=now, updated_at=now,
    )


def _subtask_attr(subtask: object, name: str, default=None):
    return getattr(subtask, name, None) if not isinstance(subtask, dict) else subtask.get(name, default)


def contract_slice(contract: Contract, subtask: object) -> Contract:
    """Return a sub-contract of just the items a subtask owns, so a builder gets EXACT endpoints/UI to
    build (replacing vague `produces`). Matching is best-effort: a frontend subtask gets UI items, a
    backend subtask gets API items, and an item whose path/route/text matches the subtask's owned files
    or title tokens is included. When nothing can be localized, return ALL items (safe — over-inform
    rather than starve a builder)."""
    role = str(_subtask_attr(subtask, "role", "") or "").lower()
    files = [str(f).lower() for f in (_subtask_attr(subtask, "files", []) or [])]
    title = str(_subtask_attr(subtask, "title", "") or "").lower()
    blob = " ".join([title, *files])

    picked: list[ContractItem] = []
    for it in contract.items:
        take = False
        if role == "frontend" and it.kind == "ui":
            take = True
        elif role == "backend" and it.kind == "api":
            take = True
        # token/path match regardless of role (e.g. a subtask that owns web/tasks.js gets /api/tasks)
        needle = (it.path or it.route or "").strip("/").lower()
        if needle and needle in blob:
            take = True
        if take:
            picked.append(it)

    items = picked if picked else list(contract.items)
    return contract.model_copy(update={"items": items})


def render_contract_md(contract: Contract | None) -> str:
    """Render the contract as a readable, prompt-injectable block. Empty contract → ''."""
    if contract is None or not contract.items:
        return ""
    ui = [i for i in contract.items if i.kind == "ui"]
    api = [i for i in contract.items if i.kind == "api"]
    lines = [f"## Spec Contract (v{contract.version}) — build & verify against THIS"]
    if ui:
        lines.append("UI:")
        for i in ui:
            bits = [f"[{i.severity}] {i.id}: {i.criterion}"]
            if i.route:
                bits.append(f"@ {i.route}")
            if i.expect_text:
                bits.append("shows " + ", ".join(f'“{t}”' for t in i.expect_text))
            lines.append("- " + " ".join(bits))
    if api:
        lines.append("API:")
        for i in api:
            resp = i.response or {}
            keys = resp.get("requiredKeys") or []
            tail = f" → {resp.get('status', 200)}" + (f" keys={keys}" if keys else "")
            lines.append(f"- [{i.severity}] {i.id}: {i.method} {i.path}{tail}  ({i.criterion})")
    return "\n".join(lines) + "\n\n"


def api_items_as_dicts(contract: Contract | None) -> list[dict]:
    """The API items as plain dicts for the api-checks harness rung."""
    if contract is None:
        return []
    return [i.model_dump(by_alias=False) for i in contract.items if i.kind == "api"]


def record_feedback(*, contract: Contract, item_id: str, expected: str, actual: str,
                    phase: str, run_id: str | None = None, severity: str = "blocking",
                    feedback: str = "") -> ContractFeedback:
    """Build a structured feedback-ledger entry against a contract item (caller persists it)."""
    return ContractFeedback(
        id=new_ulid(), org_id=contract.org_id, workspace_id=contract.workspace_id,
        contract_id=contract.id, item_id=item_id, run_id=run_id, phase=phase,
        expected=expected, actual=actual, severity=_norm_severity(severity), feedback=feedback,
        created_at=datetime.now(UTC),
    )
