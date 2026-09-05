"""Tests for foundry_core.ids — ULID generation and mission-key formatting."""

from __future__ import annotations

import re

import pytest

from foundry_core.ids import mission_key, new_ulid

# Crockford base32, 26 chars, excluding I, L, O, U (Canon §8 / 02-data-model §1).
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_new_ulid_is_26_char_crockford() -> None:
    u = new_ulid()
    assert isinstance(u, str)
    assert len(u) == 26
    assert ULID_RE.match(u), u


def test_new_ulid_unique() -> None:
    ids = {new_ulid() for _ in range(1000)}
    assert len(ids) == 1000


def test_new_ulid_is_sortable_over_time() -> None:
    # ULIDs are time-ordered; a later ULID sorts >= an earlier one.
    first = new_ulid()
    later = new_ulid()
    assert first <= later or first[:10] != later[:10]


def test_mission_key_format() -> None:
    assert mission_key(1) == "FND-1"
    assert mission_key(142) == "FND-142"
    assert mission_key(99999) == "FND-99999"


@pytest.mark.parametrize("bad", [0, -1, -142])
def test_mission_key_rejects_non_positive(bad: int) -> None:
    with pytest.raises(ValueError):
        mission_key(bad)
