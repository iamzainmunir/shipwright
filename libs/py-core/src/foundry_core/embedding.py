"""Text embeddings for memory retrieval (Canon §13.10).

Shipwright's "works offline by default, real backend is a drop-in" rule applies here too:

  * :class:`LexicalEmbedder` is a deterministic, dependency-free embedder — it hashes tokens into
    a fixed-dimension bag-of-words vector and L2-normalizes, so cosine similarity reflects keyword
    overlap. Good enough to retrieve the right memory offline, and fully reproducible.
  * A real semantic embedder (OpenAI / Voyage / a local model) implements the same
    :class:`Embedder` protocol and swaps in with no call-site changes; then pgvector replaces the
    Python cosine scan for scale (that step is environment-blocked here).

Vectors are plain ``list[float]`` so they serialize straight into the ``memories.embedding`` JSON
column (and later a pgvector column).
"""

from __future__ import annotations

import math
import re
from typing import Protocol, runtime_checkable

_TOKEN = re.compile(r"[a-z0-9]+")

# Common words carry no retrieval signal and cause spurious ties — drop them.
_STOP = frozenset(
    "a an and are as at be by do does for from how in into is it its of on or our so that the "
    "them then there these this to us we what when where which who why with you your".split()
)


def _stem(token: str) -> str:
    """Crude suffix stripper so word forms match (reading→read, exports→export, rows→row)."""
    if token.endswith("'s"):
        token = token[:-2]
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


@runtime_checkable
class Embedder(Protocol):
    """Turns text into a unit-length vector. Implementations must be deterministic per input."""

    @property
    def dim(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


class LexicalEmbedder:
    """Deterministic hashing bag-of-words embedder (offline default).

    Tokens are lowercased word/number runs, hashed into ``dim`` buckets, counted, and the vector
    is L2-normalized so a dot product equals cosine similarity.
    """

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for raw in _TOKEN.findall(text.lower()):
            if raw in _STOP:
                continue
            token = _stem(raw)
            if len(token) <= 1 or token in _STOP:
                continue
            # Fixed FNV hash → a bucket (Python's built-in hash is process-seeded).
            vec[_stable_hash(token) % self._dim] += 1.0
        return _normalize(vec)


def _stable_hash(token: str) -> int:
    """FNV-1a 32-bit — stable across processes (unlike the salted built-in ``hash``)."""
    h = 0x811C9DC5
    for ch in token.encode("utf-8"):
        h = ((h ^ ch) * 0x01000193) & 0xFFFFFFFF
    return h


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors (0.0 for a length mismatch or a zero vector)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


_default: Embedder | None = None


def get_embedder() -> Embedder:
    """Process-wide embedder singleton (the offline lexical one by default)."""
    global _default
    if _default is None:
        _default = LexicalEmbedder()
    return _default
