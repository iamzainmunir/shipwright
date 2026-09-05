"""Memory retrieval quality (Phase 9) — the right memory ranks for a natural-language query.

A recall@1 / recall@3 gate over a small labeled corpus. Runs offline against the deterministic
lexical embedder, so it's reproducible in CI. A change that regresses retrieval flips it red.
"""

from __future__ import annotations

import pytest
from app.seed import DEMO_ORG, DEMO_WS
from app.store import InMemoryStore
from foundry_core.enums import MemoryType
from foundry_core.ids import new_ulid
from foundry_core.models import Memory

# (title, body) — the corpus the store is seeded with for this test.
CORPUS = [
    ("Tenant scoping is non-negotiable", "Every by-id read or write must pin the caller's workspace."),
    ("CSV export uses RFC-4180", "Monthly cost exports are RFC-4180 CSV with a header row and CRLF."),
    ("QA runs in headless Chrome", "Acceptance stories execute in a headless Chrome via Playwright."),
    ("Prefer squash merges", "Pull requests are squash-merged so main keeps one commit per mission."),
    ("Secrets live in Vault", "API keys and tokens are read from Vault, never committed to the repo."),
]

# (query, expected-title) — a natural-language question and the memory that should answer it.
QUERIES = [
    ("how do we stop one workspace from reading another workspace's rows", "Tenant scoping is non-negotiable"),
    ("what format is the monthly cost export file", "CSV export uses RFC-4180"),
    ("which browser do the acceptance tests run in", "QA runs in headless Chrome"),
    ("how are pull requests merged into main", "Prefer squash merges"),
    ("where are api keys and tokens stored", "Secrets live in Vault"),
]


async def _seeded_store() -> InMemoryStore:
    store = InMemoryStore()
    store.memories.clear()  # drop the default seed so the corpus is exactly ours
    for title, body in CORPUS:
        await store.add_memory(
            Memory(id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
                   type=MemoryType.PROJECT, title=title, body=body)
        )
    return store


@pytest.mark.parametrize("query,expected", QUERIES)
async def test_recall_at_1(query: str, expected: str) -> None:
    store = await _seeded_store()
    hits = await store.search_memories(query, k=3)
    assert hits, f"no memory retrieved for {query!r}"
    assert hits[0].title == expected, f"{query!r} → {hits[0].title!r}, expected {expected!r}"


async def test_recall_at_3_is_total() -> None:
    store = await _seeded_store()
    for query, expected in QUERIES:
        titles = [m.title for m in await store.search_memories(query, k=3)]
        assert expected in titles, f"{expected!r} not in top-3 for {query!r}: {titles}"
