"""Compounding-learning loop — turn the org's richest signals into durable, embedded memories the
next mission recalls.

Two signals were previously discarded (2026-09-26 audit A1/A11):
  * **why work was sent back** (`_rework_reason`, consumed once then dropped), and
  * **how a run ended** (only a successful ship wrote memory; failures/halts taught nothing).

:func:`remember_lesson` persists a rework cause as a ``LESSON`` memory the moment it happens;
:func:`reflect` distils a durable lesson from a terminal outcome. Both are **best-effort and
failure-isolated** (Observer rule): a reflection error NEVER fails a run.
"""
from __future__ import annotations

import re

import structlog
from foundry_core.enums import MemoryType
from foundry_core.ids import new_ulid
from foundry_core.models import Memory

from .memory_search import compute_embedding

log = structlog.get_logger(__name__)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _type_value(t: object) -> str:
    return str(getattr(t, "value", t))


async def remember_lesson(store, mission, phase_key: str, cause: str) -> None:
    """Persist a rework signal (QA/review/CTO sending work back) as an embedded LESSON memory, so a
    future similar mission recalls it instead of re-hitting the same failure. Deduped on the cause so
    a repeated identical rework doesn't spam rows."""
    cause = (cause or "").strip()
    if not cause or mission is None:
        return
    title = f"Lesson [{phase_key}]: {cause}"[:120]
    body = (
        f"On {mission.key} ({mission.title}), the {phase_key} phase sent work back: {cause}\n"
        f"Apply this earlier next time to avoid the same rework."
    )
    key = _norm(cause)[:80]
    try:
        for m in await store.list_memories(mission.workspace_id):
            if _type_value(m.type) == "lesson" and key and key in _norm(m.body):
                return  # already learned this lesson
        mem = Memory(
            id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
            type=MemoryType.LESSON, title=title, body=body,
        )
        mem = mem.model_copy(update={"embedding": compute_embedding(mem)})
        await store.add_memory(mem)
    except Exception as exc:  # noqa: BLE001 — learning is best-effort, never fail a run
        log.warning("reflection.lesson_failed", error=str(exc))


async def reflect(store, provider, mission, outcome: str, context: str) -> None:
    """On a terminal outcome (failed/halted), distil ONE durable, generalizable lesson and store it as
    a FAILURE memory. LLM-assisted; skips silently when there is no provider or nothing useful."""
    if provider is None or mission is None:
        return
    try:
        result = await provider.complete(
            system="You are a staff engineer writing a one-line, generalizable post-mortem lesson. Be terse.",
            prompt=(
                f"A software task '{mission.title}' ended: {outcome}.\n"
                f"Context:\n{(context or '(none)')[:1000]}\n\n"
                "Write ONE durable lesson starting 'Next time,' that would help a FUTURE similar task "
                "(not specific to this ticket). If nothing useful, reply exactly: NONE"
            ),
            purpose="reflect", max_tokens=120,
        )
        lesson = (result.text or "").strip()
        if not lesson or lesson.upper().startswith("NONE"):
            return
        title = f"Lesson from {mission.key} ({outcome}): {mission.title}"[:120]
        mem = Memory(
            id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
            type=MemoryType.FAILURE, title=title, body=lesson[:600],
        )
        mem = mem.model_copy(update={"embedding": compute_embedding(mem)})
        await store.add_memory(mem)
    except Exception as exc:  # noqa: BLE001 — reflection is best-effort, never fail a run
        log.warning("reflection.reflect_failed", error=str(exc))
