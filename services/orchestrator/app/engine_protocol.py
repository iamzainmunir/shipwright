"""The engine facade — the ONLY surface the API layer may call (plan 01 §5, seam 1).

Both engines (the legacy hand-rolled loop and the v2 LangGraph engine) implement this
Protocol. The API routes depend on it structurally, never on a concrete class, so the
engine is swappable via ``SHIPWRIGHT_ENGINE=legacy|graph`` with zero API/web changes.

Everything else an engine does (store rows, events, gates) flows through the store and
event bus — the UI renders those, not engine internals. Signatures mirror the legacy
``RunEngine`` exactly (that is the contract the API already calls).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from foundry_core.enums import ApprovalDecision
from foundry_core.models import Blocker, Mission, Run


@runtime_checkable
class EngineProtocol(Protocol):
    """The 6-method contract the API layer uses (verified against api/v1 call sites)."""

    async def start_run(self, mission: Mission, *, start_phase: str = "intake") -> Run: ...

    async def cancel_run(self, mission: Mission, *, actor: str | None = None) -> int:
        """Force-stop the mission's active run(s); park the mission STOPPED. Returns count."""
        ...

    async def retry_run(self, mission: Mission, *, from_start: bool = False) -> Run:
        """Resume from the checkpoint (or the beginning), preserving context."""
        ...

    async def request_change(
        self, mission: Mission, request: str, *, actor: str | None = None
    ) -> Run:
        """Reopen a shipped mission with a change request folded into the brief."""
        ...

    async def resolve_blocker(
        self, blocker_id: str, decision: ApprovalDecision, *, actor: str | None,
        note: str | None, repo: str | None = None, branch: str | None = None,
        force: bool = False,
    ) -> Blocker:
        """Resolve an approval gate. Repo/branch/force are USER decisions (invariant 4)."""
        ...

    async def submit_clarification(
        self, blocker_id: str, answers: list[dict], *, actor: str | None = None
    ) -> Blocker:
        """Answer a clarify gate's questions; the run resumes."""
        ...
