"""Postgres-backed store (Phase 2 persistence + Phase 2b RLS tenant isolation).

Same async interface as :class:`app.store.InMemoryStore`. Phase 2b adds **row-level security**:
every tenant table has an RLS policy keyed on ``current_setting('app.workspace_id')``, and each
session sets that GUC (``SET LOCAL``) — so the database itself enforces that a workspace can only
see its own rows, even on an unfiltered query (Canon §13.2, the non-negotiable tenant guard).
FORCE ROW LEVEL SECURITY makes it bind even for the table owner; the app connects as a
**non-superuser** role so RLS is not bypassed.

The schema (tables + the RLS policies + the mission-key sequence) is owned by the
Alembic migrations in ``migrations/`` — never ``create_all``. ``setup`` brings the schema to
head (local/dev convenience) and seeds fixtures; in staging/prod the deploy pipeline runs
``alembic upgrade head`` and ``setup`` only seeds. Full doc-02 schema (pgvector + enum types)
remains future work.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from typing import Any, TypeVar

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
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .db_models import (
    AgentRow,
    ArtifactRow,
    AutonomyPolicyRow,
    BlockerRow,
    CustomRoleRow,
    EventRow,
    IntegrationRow,
    JiraIssueMapRow,
    JiraOutboxRow,
    MemoryRow,
    MissionRow,
    ModelConnectionRow,
    RunRow,
    SkillRow,
    StepRow,
    TeamRow,
    TicketEventRow,
    TicketLinkRow,
    TicketRow,
)
from .memory_search import compute_embedding, rank_memories
from .seed import (
    DEMO_WS,
    default_settings,
    seed_agents,
    seed_integrations,
    seed_memories,
    seed_missions,
    seed_model_connections,
    seed_skills,
)

_T = TypeVar("_T")
_KEY_SEQ = "mission_key_seq"  # created by the initial migration; read by next_mission_key()


def _now() -> datetime:
    return datetime.now(UTC)


def _col(value: Any) -> Any:
    """Normalize a value for a string column (StrEnum → its value)."""
    return value.value if isinstance(value, Enum) else value


class PostgresStore:
    """Async SQLAlchemy store with RLS. Call :meth:`setup` once at startup before serving."""

    def __init__(self, dsn: str, *, workspace: str = DEMO_WS, auto_migrate: bool = True,
                 seed: bool = True) -> None:
        self._dsn = dsn
        self._auto_migrate = auto_migrate  # local/dev: bring schema to head at startup
        self._seed = seed  # seed the baseline demo fixtures into empty tables (FOUNDRY_SEED=0 to skip)
        self.engine = create_async_engine(dsn, pool_pre_ping=True)
        self._maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.workspace = workspace  # the tenant this instance operates as (demo: single workspace)

    @asynccontextmanager
    async def _sess(self):
        """A session with the tenant GUC set, so RLS policies allow this workspace's rows."""
        async with self._maker() as s:
            await s.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": self.workspace}
            )
            yield s

    async def setup(self) -> None:
        """Ensure the schema is at head (local/dev only), then seed fixtures.

        In staging/prod ``auto_migrate`` is off — the deploy runs ``alembic upgrade head`` — so
        this only seeds. Schema (tables + RLS + sequence) lives entirely in the migrations.
        """
        if self._auto_migrate:
            from .db_migrate import upgrade_to_head

            await upgrade_to_head(self._dsn)
        if self._seed:
            await self._seed_if_empty()

    async def _seed_if_empty(self) -> None:
        """Seed each collection independently (idempotent) so new tables get fixtures on an
        already-populated database, not just a brand-new one."""
        async def _empty(row_cls: type) -> bool:
            return not await s.scalar(select(func.count()).select_from(row_cls))

        async with self._sess() as s:
            # Each collection is gated on its OWN table, so a table that gained fixtures in a
            # later code version is seeded on the next startup — not skipped because `missions`
            # already has rows.
            if await _empty(AgentRow):
                for a in seed_agents():
                    s.add(AgentRow(**a.model_dump()))
            if await _empty(MissionRow):
                for m in seed_missions():
                    s.add(MissionRow(**m.model_dump()))
            if await _empty(ModelConnectionRow):
                for c in seed_model_connections():
                    s.add(ModelConnectionRow(**c.model_dump()))
            if await _empty(SkillRow):
                for sk in seed_skills():
                    s.add(SkillRow(**sk.model_dump()))
            if await _empty(MemoryRow):
                for mem in seed_memories():
                    s.add(MemoryRow(**mem.model_dump()))
            if await _empty(IntegrationRow):
                for integ in seed_integrations():
                    s.add(IntegrationRow(**integ.model_dump()))
            if await _empty(AutonomyPolicyRow):
                s.add(AutonomyPolicyRow(**default_settings().model_dump()))
            await s.commit()

    async def aclose(self) -> None:
        await self.engine.dispose()

    # ---- ids / keys -------------------------------------------------------------
    async def next_mission_key(self) -> str:
        from .config import get_settings

        async with self._sess() as s:
            n = await s.scalar(select(func.nextval(_KEY_SEQ)))
            return f"{get_settings().mission_key_prefix}-{n}"

    # ---- missions ---------------------------------------------------------------
    async def list_missions(self, workspace_id: str = DEMO_WS) -> list[Mission]:
        async with self._sess() as s:
            rows = (await s.scalars(select(MissionRow).order_by(MissionRow.created_at))).all()
            return [Mission.model_validate(r) for r in rows]

    async def get_mission(self, key_or_id: str) -> Mission | None:
        async with self._sess() as s:
            row = await s.scalar(
                select(MissionRow).where(or_(MissionRow.id == key_or_id, MissionRow.key == key_or_id))
            )
            return Mission.model_validate(row) if row else None

    async def add_mission(self, mission: Mission) -> Mission:
        async with self._sess() as s:
            s.add(MissionRow(**mission.model_dump()))
            await s.commit()
        return mission

    async def update_mission(self, mission_id: str, **changes: object) -> Mission:
        return await self._update(MissionRow, mission_id, Mission, {**changes, "updated_at": _now()})

    async def delete_mission(self, mission_id: str) -> None:
        """Delete a mission and cascade runs, steps, events, blockers, and tickets (RLS scopes rows)."""
        async with self._sess() as s:
            run_ids = (await s.scalars(
                select(RunRow.id).where(RunRow.mission_id == mission_id)
            )).all()
            if run_ids:
                await s.execute(sa_delete(StepRow).where(StepRow.run_id.in_(run_ids)))
            # Tickets and their entire trail (events, links, Jira mirror) belong to the mission.
            ticket_ids = (await s.scalars(
                select(TicketRow.id).where(TicketRow.mission_id == mission_id)
            )).all()
            if ticket_ids:
                await s.execute(
                    sa_delete(TicketEventRow).where(TicketEventRow.ticket_id.in_(ticket_ids))
                )
                await s.execute(sa_delete(TicketLinkRow).where(
                    or_(
                        TicketLinkRow.from_ticket.in_(ticket_ids),
                        TicketLinkRow.to_ticket.in_(ticket_ids),
                    )
                ))
                await s.execute(
                    sa_delete(JiraOutboxRow).where(JiraOutboxRow.ticket_id.in_(ticket_ids))
                )
                await s.execute(sa_delete(JiraIssueMapRow).where(
                    or_(
                        and_(
                            JiraIssueMapRow.entity_type == "ticket",
                            JiraIssueMapRow.entity_id.in_(ticket_ids),
                        ),
                        and_(
                            JiraIssueMapRow.entity_type == "mission",
                            JiraIssueMapRow.entity_id == mission_id,
                        ),
                    )
                ))
            await s.execute(sa_delete(TicketRow).where(TicketRow.mission_id == mission_id))
            await s.execute(sa_delete(EventRow).where(EventRow.mission_id == mission_id))
            await s.execute(sa_delete(BlockerRow).where(BlockerRow.mission_id == mission_id))
            await s.execute(sa_delete(RunRow).where(RunRow.mission_id == mission_id))
            await s.execute(sa_delete(MissionRow).where(MissionRow.id == mission_id))
            await s.commit()

    # ---- runs / steps -----------------------------------------------------------
    async def add_run(self, run: Run) -> Run:
        async with self._sess() as s:
            s.add(RunRow(**run.model_dump()))
            await s.commit()
        return run

    async def get_run(self, run_id: str) -> Run | None:
        async with self._sess() as s:
            row = await s.get(RunRow, run_id)
            return Run.model_validate(row) if row else None

    async def list_runs(self, workspace_id: str = DEMO_WS) -> list[Run]:
        async with self._sess() as s:
            rows = (await s.scalars(select(RunRow))).all()
            return [Run.model_validate(r) for r in rows]

    async def update_run(self, run_id: str, **changes: object) -> Run:
        return await self._update(RunRow, run_id, Run, changes)

    async def add_step(self, step: Step) -> Step:
        async with self._sess() as s:
            s.add(StepRow(**step.model_dump()))
            await s.commit()
        return step

    async def update_step(self, step_id: str, **changes: object) -> Step:
        return await self._update(StepRow, step_id, Step, changes)

    async def list_steps(self, run_id: str) -> list[Step]:
        async with self._sess() as s:
            rows = (await s.scalars(
                select(StepRow).where(StepRow.run_id == run_id).order_by(StepRow.seq)
            )).all()
            return [Step.model_validate(r) for r in rows]

    # ---- artifacts (v2: QA evidence + deliverable files) --------------------------
    async def add_artifact(self, artifact: Artifact) -> Artifact:
        async with self._sess() as s:
            s.add(ArtifactRow(**artifact.model_dump()))
            await s.commit()
        return artifact

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        async with self._sess() as s:
            row = await s.get(ArtifactRow, artifact_id)
            return Artifact.model_validate(row) if row else None

    async def list_artifacts(
        self, *, run_id: str | None = None, mission_id: str | None = None
    ) -> list[Artifact]:
        async with self._sess() as s:
            q = select(ArtifactRow)
            if run_id is not None:
                q = q.where(ArtifactRow.run_id == run_id)
            if mission_id is not None:
                q = q.where(ArtifactRow.mission_id == mission_id)
            rows = (await s.scalars(q.order_by(ArtifactRow.created_at, ArtifactRow.name))).all()
            return [Artifact.model_validate(r) for r in rows]

    # ---- tickets ----------------------------------------------------------------
    async def next_ticket_key(self) -> str:
        from .config import get_settings
        async with self._sess() as s:
            n = await s.scalar(select(func.nextval("ticket_key_seq")))
            return f"{get_settings().ticket_key_prefix}-{n}"

    async def add_ticket(self, ticket: Ticket) -> Ticket:
        async with self._sess() as s:
            s.add(TicketRow(**ticket.model_dump()))
            await s.commit()
        return ticket

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        async with self._sess() as s:
            row = await s.get(TicketRow, ticket_id)
            return Ticket.model_validate(row) if row else None

    async def get_ticket_by_key(self, key: str, workspace_id: str = DEMO_WS) -> Ticket | None:
        async with self._sess() as s:
            row = await s.scalar(select(TicketRow).where(TicketRow.key == key))
            return Ticket.model_validate(row) if row else None

    async def find_ticket(
        self, *, mission_id: str, kind: str | None = None, subtask_id: str | None = None,
        run_id: str | None = None,
    ) -> Ticket | None:
        q = select(TicketRow).where(TicketRow.mission_id == mission_id)
        if kind is not None:
            q = q.where(TicketRow.kind == kind)
        if subtask_id is not None:
            q = q.where(TicketRow.subtask_id == subtask_id)
        if run_id is not None:
            q = q.where(TicketRow.run_id == run_id)
        async with self._sess() as s:
            row = await s.scalar(q.order_by(TicketRow.seq).limit(1))
            return Ticket.model_validate(row) if row else None

    async def list_tickets(
        self, *, mission_id: str | None = None, workspace_id: str = DEMO_WS,
    ) -> list[Ticket]:
        q = select(TicketRow)
        if mission_id is not None:
            q = q.where(TicketRow.mission_id == mission_id)
        async with self._sess() as s:
            rows = (await s.scalars(q.order_by(TicketRow.seq))).all()
            return [Ticket.model_validate(r) for r in rows]

    async def update_ticket(self, ticket_id: str, **changes: object) -> Ticket:
        changes.setdefault("updated_at", datetime.now(UTC))
        async with self._sess() as s:
            row = await s.get(TicketRow, ticket_id)
            for k, v in changes.items():
                setattr(row, k, v.value if isinstance(v, Enum) else v)
            await s.commit()
            return Ticket.model_validate(row)

    async def add_ticket_event(self, event: TicketEvent) -> TicketEvent:
        async with self._sess() as s:
            s.add(TicketEventRow(**event.model_dump()))
            await s.commit()
        return event

    async def list_ticket_events(self, ticket_id: str) -> list[TicketEvent]:
        async with self._sess() as s:
            rows = (await s.scalars(
                select(TicketEventRow).where(TicketEventRow.ticket_id == ticket_id)
                .order_by(TicketEventRow.seq)
            )).all()
            return [TicketEvent.model_validate(r) for r in rows]

    async def add_ticket_link(self, link: TicketLink) -> TicketLink:
        async with self._sess() as s:
            s.add(TicketLinkRow(**link.model_dump()))
            await s.commit()
        return link

    async def list_ticket_links(self, ticket_id: str) -> list[TicketLink]:
        async with self._sess() as s:
            rows = (await s.scalars(
                select(TicketLinkRow).where(
                    or_(TicketLinkRow.from_ticket == ticket_id, TicketLinkRow.to_ticket == ticket_id)
                )
            )).all()
            return [TicketLink.model_validate(r) for r in rows]

    # ---- jira mirror (issue map + transactional outbox) -------------------------
    async def add_jira_issue_map(self, m: JiraIssueMap) -> JiraIssueMap:
        existing = await self.get_jira_issue_map(m.workspace_id, m.entity_type, m.entity_id)
        if existing is not None:
            return existing
        async with self._sess() as s:
            s.add(JiraIssueMapRow(**m.model_dump()))
            await s.commit()
        return m

    async def get_jira_issue_map(
        self, workspace_id: str, entity_type: str, entity_id: str
    ) -> JiraIssueMap | None:
        async with self._sess() as s:
            row = await s.scalar(
                select(JiraIssueMapRow).where(
                    JiraIssueMapRow.entity_type == entity_type,
                    JiraIssueMapRow.entity_id == entity_id,
                )
            )
            return JiraIssueMap.model_validate(row) if row else None

    async def add_jira_outbox(self, row: JiraOutbox) -> JiraOutbox:
        async with self._sess() as s:
            existing = await s.scalar(
                select(JiraOutboxRow).where(JiraOutboxRow.idempotency_key == row.idempotency_key)
            )
            if existing is not None:
                return JiraOutbox.model_validate(existing)
            s.add(JiraOutboxRow(**row.model_dump()))
            await s.commit()
        return row

    async def list_jira_outbox(
        self, workspace_id: str, *, status: str | None = None, due_at: datetime | None = None,
    ) -> list[JiraOutbox]:
        q = select(JiraOutboxRow)
        if status is not None:
            q = q.where(JiraOutboxRow.status == status)
        if due_at is not None:
            q = q.where(
                or_(JiraOutboxRow.next_attempt_at.is_(None), JiraOutboxRow.next_attempt_at <= due_at)
            )
        async with self._sess() as s:
            rows = (await s.scalars(q.order_by(JiraOutboxRow.seq))).all()
            return [JiraOutbox.model_validate(r) for r in rows]

    async def update_jira_outbox(self, row_id: str, **changes: object) -> JiraOutbox:
        changes.setdefault("updated_at", datetime.now(UTC))
        async with self._sess() as s:
            row = await s.get(JiraOutboxRow, row_id)
            for k, v in changes.items():
                setattr(row, k, v.value if isinstance(v, Enum) else v)
            await s.commit()
            return JiraOutbox.model_validate(row)

    # ---- events -----------------------------------------------------------------
    async def add_event(self, event: Event) -> Event:
        async with self._sess() as s:
            s.add(EventRow(**event.model_dump()))
            await s.commit()
        return event

    async def list_events(
        self, *, run_id: str | None = None, mission_id: str | None = None, limit: int = 200
    ) -> list[Event]:
        stmt = select(EventRow)
        if run_id is not None:
            stmt = stmt.where(EventRow.run_id == run_id)
        if mission_id is not None:
            stmt = stmt.where(EventRow.mission_id == mission_id)
        stmt = stmt.order_by(EventRow.seq.desc()).limit(limit)
        async with self._sess() as s:
            rows = list((await s.scalars(stmt)).all())
        rows.reverse()
        return [Event.model_validate(r) for r in rows]

    # ---- blockers ---------------------------------------------------------------
    async def add_blocker(self, blocker: Blocker) -> Blocker:
        async with self._sess() as s:
            s.add(BlockerRow(**blocker.model_dump()))
            await s.commit()
        return blocker

    async def get_blocker(self, blocker_id: str) -> Blocker | None:
        async with self._sess() as s:
            row = await s.get(BlockerRow, blocker_id)
            return Blocker.model_validate(row) if row else None

    async def list_blockers(
        self, workspace_id: str = DEMO_WS, *, unresolved_only: bool = True
    ) -> list[Blocker]:
        stmt = select(BlockerRow)
        if unresolved_only:
            stmt = stmt.where(BlockerRow.resolved_at.is_(None))
        async with self._sess() as s:
            rows = (await s.scalars(stmt)).all()
            return [Blocker.model_validate(r) for r in rows]

    async def resolve_blocker_record(self, blocker_id: str, *, resolved_by: str | None) -> Blocker:
        return await self._update(
            BlockerRow, blocker_id, Blocker, {"resolved_at": _now(), "resolved_by": resolved_by}
        )

    # ---- reference collections --------------------------------------------------
    async def list_agents(self, workspace_id: str = DEMO_WS) -> list[Agent]:
        async with self._sess() as s:
            rows = (await s.scalars(select(AgentRow))).all()
            return [Agent.model_validate(r) for r in rows]

    async def update_agent(self, agent_id: str, **changes: object) -> Agent:
        return await self._update(AgentRow, agent_id, Agent, changes)

    async def add_agent(self, agent: Agent) -> Agent:
        async with self._sess() as s:
            s.add(AgentRow(**agent.model_dump()))
            await s.commit()
        return agent

    async def delete_agent(self, agent_id: str) -> None:
        async with self._sess() as s:
            row = await s.get(AgentRow, agent_id)
            if row is not None:
                await s.delete(row)
                await s.commit()

    # ---- teams ------------------------------------------------------------------
    async def list_teams(self, workspace_id: str = DEMO_WS) -> list[Team]:
        async with self._sess() as s:
            rows = (await s.scalars(select(TeamRow))).all()
            return [Team.model_validate(r) for r in rows]

    async def get_team(self, team_id: str) -> Team | None:
        async with self._sess() as s:
            row = await s.get(TeamRow, team_id)
            return Team.model_validate(row) if row else None

    async def add_team(self, team: Team) -> Team:
        async with self._sess() as s:
            s.add(TeamRow(**team.model_dump()))
            await s.commit()
        return team

    async def update_team(self, team_id: str, **changes: object) -> Team:
        return await self._update(TeamRow, team_id, Team, changes)

    async def delete_team(self, team_id: str) -> None:
        async with self._sess() as s:
            row = await s.get(TeamRow, team_id)
            if row is not None:
                await s.delete(row)
                await s.commit()

    async def list_model_connections(self, workspace_id: str = DEMO_WS) -> list[ModelConnection]:
        async with self._sess() as s:
            rows = (await s.scalars(select(ModelConnectionRow))).all()
            return [ModelConnection.model_validate(r) for r in rows]

    async def add_model_connection(self, conn: ModelConnection) -> ModelConnection:
        async with self._sess() as s:
            s.add(ModelConnectionRow(**conn.model_dump()))
            await s.commit()
        return conn

    async def update_model_connection(self, conn_id: str, **changes: object) -> ModelConnection:
        async with self._sess() as s:
            row = await s.get(ModelConnectionRow, conn_id)
            if row is None:
                raise KeyError(conn_id)
            for key, value in changes.items():
                setattr(row, key, _col(value))
            if changes.get("is_primary"):  # only one primary per workspace
                others = (await s.scalars(
                    select(ModelConnectionRow).where(ModelConnectionRow.id != conn_id)
                )).all()
                for other in others:
                    other.is_primary = False
            await s.commit()
            return ModelConnection.model_validate(row)

    async def list_skills(self, workspace_id: str = DEMO_WS) -> list[Skill]:
        async with self._sess() as s:
            rows = (await s.scalars(select(SkillRow))).all()
            return [Skill.model_validate(r) for r in rows]

    async def add_skill(self, skill: Skill) -> Skill:
        async with self._sess() as s:
            s.add(SkillRow(**skill.model_dump()))
            await s.commit()
        return skill

    async def update_skill(self, skill_id: str, **changes: object) -> Skill:
        return await self._update(SkillRow, skill_id, Skill, changes)

    async def list_memories(self, workspace_id: str = DEMO_WS) -> list[Memory]:
        async with self._sess() as s:
            rows = (await s.scalars(select(MemoryRow))).all()
            return [Memory.model_validate(r) for r in rows]

    async def search_memories(
        self, query: str, *, k: int = 5, workspace_id: str = DEMO_WS
    ) -> list[Memory]:
        # No pgvector here → rank in Python over the (small) workspace set.
        return rank_memories(await self.list_memories(workspace_id), query, k)

    async def add_memory(self, memory: Memory) -> Memory:
        stored = memory.model_copy(update={
            "embedding": compute_embedding(memory), "updated_at": _now(),
        })
        async with self._sess() as s:
            s.add(MemoryRow(**stored.model_dump()))
            await s.commit()
        return stored

    async def update_memory(self, memory_id: str, **changes: object) -> Memory:
        async with self._sess() as s:
            row = await s.get(MemoryRow, memory_id)
            if row is None:
                raise KeyError(memory_id)
            for key, value in changes.items():
                setattr(row, key, _col(value))
            row.updated_at = _now()
            row.embedding = compute_embedding(Memory.model_validate(row))
            await s.commit()
            return Memory.model_validate(row)

    async def delete_memory(self, memory_id: str) -> None:
        async with self._sess() as s:
            row = await s.get(MemoryRow, memory_id)
            if row is not None:
                await s.delete(row)
                await s.commit()

    async def delete_model_connection(self, conn_id: str) -> None:
        async with self._sess() as s:
            row = await s.get(ModelConnectionRow, conn_id)
            if row is not None:
                await s.delete(row)
                await s.commit()

    async def list_custom_roles(self, workspace_id: str = DEMO_WS) -> list[CustomRole]:
        async with self._sess() as s:
            rows = (await s.scalars(select(CustomRoleRow).order_by(CustomRoleRow.created_at))).all()
            return [CustomRole.model_validate(r) for r in rows]

    async def get_custom_role(self, workspace_id: str, key: str) -> CustomRole | None:
        async with self._sess() as s:
            row = await s.scalar(select(CustomRoleRow).where(CustomRoleRow.key == key))
            return CustomRole.model_validate(row) if row else None

    async def add_custom_role(self, role: CustomRole) -> CustomRole:
        async with self._sess() as s:
            s.add(CustomRoleRow(**role.model_dump()))
            await s.commit()
        return role

    async def delete_custom_role(self, workspace_id: str, key: str) -> bool:
        async with self._sess() as s:
            row = await s.scalar(select(CustomRoleRow).where(CustomRoleRow.key == key))
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    async def list_integrations(self, workspace_id: str = DEMO_WS) -> list[Integration]:
        async with self._sess() as s:
            rows = (await s.scalars(select(IntegrationRow))).all()
            return [Integration.model_validate(r) for r in rows]

    async def add_integration(self, integ: Integration) -> Integration:
        async with self._sess() as s:
            s.add(IntegrationRow(**integ.model_dump()))
            await s.commit()
        return integ

    async def set_integration_status(
        self, kind: str, status: str, config: dict | None = None
    ) -> Integration | None:
        async with self._sess() as s:
            row = await s.scalar(select(IntegrationRow).where(IntegrationRow.kind == kind))
            if row is None:
                return None
            row.status = status
            if config is not None:
                row.config = config
            await s.commit()
            return Integration.model_validate(row)

    async def get_settings(self, workspace_id: str = DEMO_WS) -> AutonomyPolicy:
        async with self._sess() as s:
            row = await s.scalar(select(AutonomyPolicyRow).limit(1))
            if row is None:  # create-on-read so callers always get a policy
                row = AutonomyPolicyRow(**default_settings().model_dump())
                s.add(row)
                await s.commit()
            return AutonomyPolicy.model_validate(row)

    async def update_settings(self, workspace_id: str = DEMO_WS, **changes: object) -> AutonomyPolicy:
        async with self._sess() as s:
            row = await s.scalar(select(AutonomyPolicyRow).limit(1))
            if row is None:
                raise KeyError("autonomy_policy")
            for key, value in changes.items():
                setattr(row, key, _col(value))
            await s.commit()
            return AutonomyPolicy.model_validate(row)

    # ---- shared update helper ---------------------------------------------------
    async def _update(self, row_cls: type, pk: str, model_cls: type[_T], changes: dict) -> _T:
        async with self._sess() as s:
            row = await s.get(row_cls, pk)
            if row is None:
                raise KeyError(pk)
            for key, value in changes.items():
                setattr(row, key, _col(value))
            await s.commit()
            return model_cls.model_validate(row)  # type: ignore[attr-defined]
