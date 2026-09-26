"""Semantic ranking for workspace memory (Phase 9).

Shared by both stores so in-memory and Postgres rank identically. Uses the offline lexical
embedder by default; when pgvector lands, the ranking moves into SQL (ANN) but this stays the
reference implementation and the fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime

from foundry_core.embedding import cosine, get_embedder
from foundry_core.models import Memory


def memory_text(memory: Memory) -> str:
    """The text a memory is embedded from — its title and body."""
    return f"{memory.title}\n{memory.body}"


def embedding_for(memory: Memory) -> list[float]:
    """A memory's stored embedding, or one computed on the fly if it was never embedded."""
    return memory.embedding or get_embedder().embed(memory_text(memory))


def compute_embedding(memory: Memory) -> list[float]:
    """Fresh embedding for a memory (called on create/update so it's stored, not recomputed)."""
    return get_embedder().embed(memory_text(memory))


def rank_memories(memories: list[Memory], query: str, k: int) -> list[Memory]:
    """Top-k memories by cosine similarity to the query.

    When the (offline lexical) embedder finds NO overlap — common across different missions, which
    silently returned nothing before (audit A8) — fall back to the most **recent** memories so prior
    context (esp. lessons) still surfaces, rather than an empty recall."""
    if not query.strip() or not memories:
        return []
    q = get_embedder().embed(query)
    scored = [(cosine(q, embedding_for(m)), m) for m in memories]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    positive = [m for score, m in scored[:k] if score > 0]
    if positive:
        return positive
    # zero-overlap fallback → recency (updated_at desc; unstamped last, stable input order otherwise)
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(memories, key=lambda m: m.updated_at or floor, reverse=True)[:k]
