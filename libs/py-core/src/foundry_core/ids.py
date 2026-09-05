"""Identifier helpers — ULIDs and user-facing mission keys (Canon §8).

- Internal IDs are ULIDs (26-char Crockford base32, lexicographically sortable).
- Mission keys are ``FND-<n>`` with a per-workspace counter (the counter lives in the DB /
  Redis; this module only formats the key).
"""

from __future__ import annotations

from ulid import ULID

__all__ = ["new_ulid", "mission_key"]


def new_ulid() -> str:
    """Return a fresh ULID as a 26-char Crockford base32 string.

    ULIDs are time-ordered, so string sort == creation-time sort.
    """
    return str(ULID())


def mission_key(n: int) -> str:
    """Format a per-workspace mission sequence number ``n`` as ``FND-<n>`` (Canon §8).

    ``n`` is the 1-based value from the workspace's mission-key counter.
    """
    if n < 1:
        raise ValueError(f"mission sequence number must be >= 1, got {n!r}")
    return f"FND-{n}"
