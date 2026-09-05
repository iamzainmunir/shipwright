"""Per-provider 'reset usage' — a connection's config.usageResetAt drops runs that finished before
it from the usage counts (cost + tokens), non-destructively (the runs themselves stay)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.api.v1.runs import _parse_reset, model_connection_usage
from app.state import set_store
from app.store import DEMO_ORG, DEMO_WS, InMemoryStore
from foundry_core.models import (
    ConnectionStatus,
    ModelConnection,
    ModelKind,
    ModelProvider,
    Run,
    RunStatus,
)


def test_parse_reset_handles_z_and_offset_and_junk() -> None:
    assert _parse_reset("2026-09-06T10:00:00Z").tzinfo is not None
    assert _parse_reset("2026-09-06T10:00:00+00:00") is not None
    assert _parse_reset("") is None
    assert _parse_reset(None) is None
    assert _parse_reset("not-a-date") is None


def _run(rid: str, provider: str, finished: datetime, *, cents: int, tin: int, tout: int) -> Run:
    return Run(
        id=rid, mission_id="m1", workspace_id=DEMO_WS, status=RunStatus.SUCCEEDED,
        started_at=finished - timedelta(minutes=5), finished_at=finished,
        cost_cents=cents, tokens_in=tin, tokens_out=tout, provider=provider, model=f"{provider}-x",
    )


@pytest.mark.asyncio
async def test_reset_excludes_older_runs_from_cost_and_tokens() -> None:
    store = InMemoryStore()
    set_store(store)
    now = datetime.now(UTC)
    reset_at = now - timedelta(hours=1)

    # anthropic connection with a reset stamped 1h ago; groq connection with no reset.
    await store.add_model_connection(ModelConnection(
        id="c-anthropic", org_id=DEMO_ORG, workspace_id=DEMO_WS,
        provider=ModelProvider.ANTHROPIC, kind=ModelKind.CLOUD,
        status=ConnectionStatus.CONNECTED, config={"usageResetAt": reset_at.isoformat()},
    ))
    await store.add_model_connection(ModelConnection(
        id="c-groq", org_id=DEMO_ORG, workspace_id=DEMO_WS,
        provider=ModelProvider.GROQ, kind=ModelKind.CLOUD, status=ConnectionStatus.CONNECTED,
    ))

    # anthropic: one run BEFORE the reset (dropped) and one AFTER (kept). groq: one run (kept, no reset).
    await store.add_run(_run("r-old", "anthropic", now - timedelta(hours=2), cents=500, tin=1000, tout=200))
    await store.add_run(_run("r-new", "anthropic", now - timedelta(minutes=10), cents=300, tin=800, tout=150))
    await store.add_run(_run("r-groq", "groq", now - timedelta(minutes=30), cents=99, tin=400, tout=100))

    usage = await model_connection_usage()
    anth = usage["providers"]["anthropic"]
    assert anth["runs"] == 1                       # only the post-reset run counts
    assert anth["costCents"] == 300 and anth["tokensIn"] == 800 and anth["tokensOut"] == 150
    # groq (no reset) is untouched
    assert usage["providers"]["groq"]["costCents"] == 99
    # totals exclude the reset-dropped run too, so the top-line spend also resets
    assert usage["totals"]["costCents"] == 399 and usage["totals"]["runs"] == 2
