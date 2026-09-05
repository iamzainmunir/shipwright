"""In-memory repository (default store).

Holds the domain aggregates in process so the vertical slice runs with no database. Every
method mirrors the shape the Postgres store (:mod:`app.pgstore`) exposes, so the two are
interchangeable behind the engine/API. Tenant scoping (RLS / ``app.workspace_id``) belongs to
the Postgres store; here we filter by ``workspace_id`` in Python.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from foundry_core.models import (
    Agent,
    Artifact,
    AutonomyPolicy,
    Blocker,
    CustomRole,
    Event,
    Integration,
    JiraIssueMap,
    JiraOutbox,
    Memory,
    Mission,
    ModelConnection,
    Run,
    Skill,
    Step,
    Team,
    Ticket,
    TicketEvent,
    TicketLink,
)

from .memory_search import compute_embedding, rank_memories
from .seed import (
    DEMO_ORG,
    DEMO_WS,
    default_settings,
    seed_agents,
    seed_integrations,
    seed_memories,
    seed_missions,
    seed_model_connections,
    seed_skills,
)

__all__ = ["InMemoryStore", "DEMO_WS", "DEMO_ORG"]


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryStore:
    """Process-local aggregates. Async methods so the interface matches the DB store."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.missions: dict[str, Mission] = {}
        self.runs: dict[str, Run] = {}
        self.steps: dict[str, Step] = {}
        self.events: list[Event] = []
        self.blockers: dict[str, Blocker] = {}
        self.agents: dict[str, Agent] = {}
        self.teams: dict[str, Team] = {}
        self.artifacts: dict[str, Artifact] = {}
        self.tickets: dict[str, Ticket] = {}
        self.ticket_events: list[TicketEvent] = []
        self.ticket_links: list[TicketLink] = []
        self._ticket_seq = 100
        self.jira_issue_map: dict[str, JiraIssueMap] = {}
        self.jira_outbox: list[JiraOutbox] = []
        self.model_connections: dict[str, ModelConnection] = {}
        self.skills: dict[str, Skill] = {}
        self.memories: dict[str, Memory] = {}
        self.integrations: dict[str, Integration] = {}
        self.custom_roles: dict[str, CustomRole] = {}
        self.settings: AutonomyPolicy = default_settings()
        self._key_seq = 150
        self._seed()

    # ---- ids / keys -------------------------------------------------------------
    async def next_mission_key(self) -> str:
        from .config import get_settings

        async with self._lock:
            self._key_seq += 1
            return f"{get_settings().mission_key_prefix}-{self._key_seq}"

    # ---- missions ---------------------------------------------------------------
    async def list_missions(self, workspace_id: str = DEMO_WS) -> list[Mission]:
        return [m for m in self.missions.values() if m.workspace_id == workspace_id]

    async def get_mission(self, key_or_id: str) -> Mission | None:
        if key_or_id in self.missions:
            return self.missions[key_or_id]
        return next((m for m in self.missions.values() if m.key == key_or_id), None)

    async def add_mission(self, mission: Mission) -> Mission:
        self.missions[mission.id] = mission
        return mission

    async def update_mission(self, mission_id: str, **changes: object) -> Mission:
        m = self.missions[mission_id]
        updated = m.model_copy(update={**changes, "updated_at": _now()})
        self.missions[mission_id] = updated
        return updated

    async def delete_mission(self, mission_id: str) -> None:
        """Delete a mission and cascade its runs, steps, events, blockers, and tickets."""
        self.missions.pop(mission_id, None)
        run_ids = {r.id for r in self.runs.values() if r.mission_id == mission_id}
        for rid in run_ids:
            self.runs.pop(rid, None)
        self.steps = {sid: s for sid, s in self.steps.items() if s.run_id not in run_ids}
        self.events = [e for e in self.events if e.mission_id != mission_id]
        self.blockers = {bid: b for bid, b in self.blockers.items() if b.mission_id != mission_id}
        # Tickets and their trail belong to the mission — cascade them too.
        ticket_ids = {t.id for t in self.tickets.values() if t.mission_id == mission_id}
        for tid in ticket_ids:
            self.tickets.pop(tid, None)
        self.ticket_events = [e for e in self.ticket_events if e.ticket_id not in ticket_ids]
        self.ticket_links = [
            link_
            for link_ in self.ticket_links
            if link_.from_ticket not in ticket_ids and link_.to_ticket not in ticket_ids
        ]

    # ---- runs / steps -----------------------------------------------------------
    async def add_run(self, run: Run) -> Run:
        self.runs[run.id] = run
        return run

    async def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    async def list_runs(self, workspace_id: str = DEMO_WS) -> list[Run]:
        return [r for r in self.runs.values() if r.workspace_id == workspace_id]

    async def update_run(self, run_id: str, **changes: object) -> Run:
        r = self.runs[run_id]
        updated = r.model_copy(update=changes)
        self.runs[run_id] = updated
        return updated

    async def add_step(self, step: Step) -> Step:
        self.steps[step.id] = step
        return step

    async def update_step(self, step_id: str, **changes: object) -> Step:
        s = self.steps[step_id]
        updated = s.model_copy(update=changes)
        self.steps[step_id] = updated
        return updated

    async def list_steps(self, run_id: str) -> list[Step]:
        return [s for s in self.steps.values() if s.run_id == run_id]

    # ---- artifacts (v2: QA evidence + deliverable files) --------------------------
    async def add_artifact(self, artifact: Artifact) -> Artifact:
        self.artifacts[artifact.id] = artifact
        return artifact

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    async def list_artifacts(
        self, *, run_id: str | None = None, mission_id: str | None = None
    ) -> list[Artifact]:
        out = list(self.artifacts.values())
        if run_id is not None:
            out = [a for a in out if a.run_id == run_id]
        if mission_id is not None:
            out = [a for a in out if a.mission_id == mission_id]
        return sorted(out, key=lambda a: (str(a.created_at or ""), a.name))

    # ---- tickets ----------------------------------------------------------------
    async def next_ticket_key(self) -> str:
        from .config import get_settings
        self._ticket_seq += 1
        return f"{get_settings().ticket_key_prefix}-{self._ticket_seq}"

    async def add_ticket(self, ticket: Ticket) -> Ticket:
        self.tickets[ticket.id] = ticket
        return ticket

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        return self.tickets.get(ticket_id)

    async def get_ticket_by_key(self, key: str, workspace_id: str = DEMO_WS) -> Ticket | None:
        for t in self.tickets.values():
            if t.key == key and (t.workspace_id or DEMO_WS) == workspace_id:
                return t
        return None

    async def find_ticket(
        self, *, mission_id: str, kind: str | None = None, subtask_id: str | None = None,
        run_id: str | None = None,
    ) -> Ticket | None:
        """First ticket for a mission matching the given discriminators — the consumer's idempotency
        lookup (one Epic per mission, one Story per subtask, one Bug per QA run)."""
        for t in self.tickets.values():
            if t.mission_id != mission_id:
                continue
            if kind is not None and str(t.kind) != kind:
                continue
            if subtask_id is not None and t.subtask_id != subtask_id:
                continue
            if run_id is not None and t.run_id != run_id:
                continue
            return t
        return None

    async def list_tickets(
        self, *, mission_id: str | None = None, workspace_id: str = DEMO_WS,
    ) -> list[Ticket]:
        out = [t for t in self.tickets.values() if (t.workspace_id or DEMO_WS) == workspace_id]
        if mission_id is not None:
            out = [t for t in out if t.mission_id == mission_id]
        return sorted(out, key=lambda t: str(t.created_at or ""))

    async def update_ticket(self, ticket_id: str, **changes: object) -> Ticket:
        t = self.tickets[ticket_id]
        changes.setdefault("updated_at", _now())
        self.tickets[ticket_id] = t.model_copy(update=changes)
        return self.tickets[ticket_id]

    async def add_ticket_event(self, event: TicketEvent) -> TicketEvent:
        self.ticket_events.append(event)
        return event

    async def list_ticket_events(self, ticket_id: str) -> list[TicketEvent]:
        out = [e for e in self.ticket_events if e.ticket_id == ticket_id]
        return sorted(out, key=lambda e: str(e.created_at or ""))

    async def add_ticket_link(self, link: TicketLink) -> TicketLink:
        self.ticket_links.append(link)
        return link

    async def list_ticket_links(self, ticket_id: str) -> list[TicketLink]:
        return [ln for ln in self.ticket_links
                if ticket_id in (ln.from_ticket, ln.to_ticket)]

    # ---- jira mirror (issue map + transactional outbox) -------------------------
    async def add_jira_issue_map(self, m: JiraIssueMap) -> JiraIssueMap:
        # Idempotent on (workspace, entity_type, entity_id) — a re-create returns the existing map.
        existing = await self.get_jira_issue_map(m.workspace_id, m.entity_type, m.entity_id)
        if existing is not None:
            return existing
        self.jira_issue_map[m.id] = m
        return m

    async def get_jira_issue_map(
        self, workspace_id: str, entity_type: str, entity_id: str
    ) -> JiraIssueMap | None:
        for m in self.jira_issue_map.values():
            if (m.workspace_id, m.entity_type, m.entity_id) == (workspace_id, entity_type, entity_id):
                return m
        return None

    async def add_jira_outbox(self, row: JiraOutbox) -> JiraOutbox:
        # Idempotent on (workspace, idempotency_key) — a replayed event never enqueues twice.
        for existing in self.jira_outbox:
            if (existing.workspace_id, existing.idempotency_key) == (row.workspace_id, row.idempotency_key):
                return existing
        self.jira_outbox.append(row)
        return row

    async def list_jira_outbox(
        self, workspace_id: str, *, status: str | None = None, due_at: datetime | None = None,
    ) -> list[JiraOutbox]:
        out = [r for r in self.jira_outbox if (r.workspace_id or DEMO_WS) == workspace_id]
        if status is not None:
            out = [r for r in out if str(r.status) == status]
        if due_at is not None:
            out = [r for r in out if r.next_attempt_at is None or r.next_attempt_at <= due_at]
        return out  # append order == seq order

    async def update_jira_outbox(self, row_id: str, **changes: object) -> JiraOutbox:
        for i, r in enumerate(self.jira_outbox):
            if r.id == row_id:
                changes.setdefault("updated_at", _now())
                self.jira_outbox[i] = r.model_copy(update=changes)
                return self.jira_outbox[i]
        raise KeyError(row_id)

    # ---- events -----------------------------------------------------------------
    async def add_event(self, event: Event) -> Event:
        self.events.append(event)
        return event

    async def list_events(
        self, *, run_id: str | None = None, mission_id: str | None = None, limit: int = 200
    ) -> list[Event]:
        out = self.events
        if run_id is not None:
            out = [e for e in out if e.run_id == run_id]
        if mission_id is not None:
            out = [e for e in out if e.mission_id == mission_id]
        return out[-limit:]

    # ---- blockers ---------------------------------------------------------------
    async def add_blocker(self, blocker: Blocker) -> Blocker:
        self.blockers[blocker.id] = blocker
        return blocker

    async def get_blocker(self, blocker_id: str) -> Blocker | None:
        return self.blockers.get(blocker_id)

    async def list_blockers(
        self, workspace_id: str = DEMO_WS, *, unresolved_only: bool = True
    ) -> list[Blocker]:
        out = [b for b in self.blockers.values() if (b.workspace_id or DEMO_WS) == workspace_id]
        if unresolved_only:
            out = [b for b in out if b.resolved_at is None]
        return out

    async def resolve_blocker_record(self, blocker_id: str, *, resolved_by: str | None) -> Blocker:
        b = self.blockers[blocker_id]
        updated = b.model_copy(update={"resolved_at": _now(), "resolved_by": resolved_by})
        self.blockers[blocker_id] = updated
        return updated

    # ---- reference collections --------------------------------------------------
    async def list_agents(self, workspace_id: str = DEMO_WS) -> list[Agent]:
        return [a for a in self.agents.values() if a.workspace_id == workspace_id]

    async def update_agent(self, agent_id: str, **changes: object) -> Agent:
        agent = self.agents[agent_id]
        updated = agent.model_copy(update=changes)
        self.agents[agent_id] = updated
        return updated

    async def add_agent(self, agent: Agent) -> Agent:
        self.agents[agent.id] = agent
        return agent

    async def delete_agent(self, agent_id: str) -> None:
        self.agents.pop(agent_id, None)

    # ---- teams ------------------------------------------------------------------
    async def list_teams(self, workspace_id: str = DEMO_WS) -> list[Team]:
        return [t for t in self.teams.values() if t.workspace_id == workspace_id]

    async def get_team(self, team_id: str) -> Team | None:
        return self.teams.get(team_id)

    async def add_team(self, team: Team) -> Team:
        self.teams[team.id] = team
        return team

    async def update_team(self, team_id: str, **changes: object) -> Team:
        team = self.teams[team_id]
        updated = team.model_copy(update=changes)
        self.teams[team_id] = updated
        return updated

    async def delete_team(self, team_id: str) -> None:
        self.teams.pop(team_id, None)

    async def list_model_connections(self, workspace_id: str = DEMO_WS) -> list[ModelConnection]:
        return [c for c in self.model_connections.values() if c.workspace_id == workspace_id]

    async def add_model_connection(self, conn: ModelConnection) -> ModelConnection:
        self.model_connections[conn.id] = conn
        return conn

    async def update_model_connection(self, conn_id: str, **changes: object) -> ModelConnection:
        conn = self.model_connections[conn_id]
        updated = conn.model_copy(update=changes)
        self.model_connections[conn_id] = updated
        if changes.get("is_primary"):  # only one primary per workspace
            for cid, other in self.model_connections.items():
                if cid != conn_id and other.workspace_id == updated.workspace_id and other.is_primary:
                    self.model_connections[cid] = other.model_copy(update={"is_primary": False})
        return updated

    async def delete_model_connection(self, conn_id: str) -> None:
        self.model_connections.pop(conn_id, None)

    async def list_skills(self, workspace_id: str = DEMO_WS) -> list[Skill]:
        return [s for s in self.skills.values() if s.workspace_id == workspace_id]

    async def add_skill(self, skill: Skill) -> Skill:
        self.skills[skill.id] = skill
        return skill

    async def update_skill(self, skill_id: str, **changes: object) -> Skill:
        current = self.skills[skill_id]
        updated = current.model_copy(update=changes)
        self.skills[skill_id] = updated
        return updated

    async def list_memories(self, workspace_id: str = DEMO_WS) -> list[Memory]:
        return [m for m in self.memories.values() if m.workspace_id == workspace_id]

    async def search_memories(
        self, query: str, *, k: int = 5, workspace_id: str = DEMO_WS
    ) -> list[Memory]:
        return rank_memories(await self.list_memories(workspace_id), query, k)

    async def add_memory(self, memory: Memory) -> Memory:
        stored = memory.model_copy(update={
            "embedding": compute_embedding(memory), "updated_at": _now(),
        })
        self.memories[stored.id] = stored
        return stored

    async def update_memory(self, memory_id: str, **changes: object) -> Memory:
        current = self.memories[memory_id]
        updated = current.model_copy(update={**changes, "updated_at": _now()})
        updated = updated.model_copy(update={"embedding": compute_embedding(updated)})
        self.memories[memory_id] = updated
        return updated

    async def delete_memory(self, memory_id: str) -> None:
        self.memories.pop(memory_id, None)

    # ---- custom roles -----------------------------------------------------------
    async def list_custom_roles(self, workspace_id: str = DEMO_WS) -> list[CustomRole]:
        return [r for r in self.custom_roles.values() if (r.workspace_id or DEMO_WS) == workspace_id]

    async def get_custom_role(self, workspace_id: str, key: str) -> CustomRole | None:
        for r in self.custom_roles.values():
            if (r.workspace_id or DEMO_WS) == workspace_id and r.key == key:
                return r
        return None

    async def add_custom_role(self, role: CustomRole) -> CustomRole:
        self.custom_roles[role.id] = role
        return role

    async def delete_custom_role(self, workspace_id: str, key: str) -> bool:
        for rid, r in list(self.custom_roles.items()):
            if (r.workspace_id or DEMO_WS) == workspace_id and r.key == key:
                del self.custom_roles[rid]
                return True
        return False

    # ---- integrations & settings ------------------------------------------------
    async def list_integrations(self, workspace_id: str = DEMO_WS) -> list[Integration]:
        return [i for i in self.integrations.values() if i.workspace_id == workspace_id]

    async def add_integration(self, integ: Integration) -> Integration:
        self.integrations[integ.id] = integ
        return integ

    async def set_integration_status(
        self, kind: str, status: str, config: dict | None = None
    ) -> Integration | None:
        for i in self.integrations.values():
            if i.kind == kind:
                changes: dict = {"status": status}
                if config is not None:
                    changes["config"] = config
                updated = i.model_copy(update=changes)
                self.integrations[i.id] = updated
                return updated
        return None

    async def get_settings(self, workspace_id: str = DEMO_WS) -> AutonomyPolicy:
        return self.settings

    async def update_settings(self, workspace_id: str = DEMO_WS, **changes: object) -> AutonomyPolicy:
        self.settings = self.settings.model_copy(update=changes)
        return self.settings

    # ---- seed -------------------------------------------------------------------
    def _seed(self) -> None:
        for a in seed_agents():
            self.agents[a.id] = a
        for m in seed_missions():
            self.missions[m.id] = m
        for c in seed_model_connections():
            self.model_connections[c.id] = c
        for s in seed_skills():
            self.skills[s.id] = s
        for mem in seed_memories():
            self.memories[mem.id] = mem
        for integ in seed_integrations():
            self.integrations[integ.id] = integ
