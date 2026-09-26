"""A15 — a test suite that actually RAN and is red must block ship; 'no tests run' stays advisory."""
from __future__ import annotations

from types import SimpleNamespace

from app.engine import _assess_build


def _result(**kw):
    base = dict(files=["src/app.py"], steps=3, files_written=2, summary="",
                tests_ran=False, tests_passed=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_red_suite_blocks():
    healthy, why = _assess_build(_result(tests_ran=True, tests_passed=False))
    assert healthy is False
    assert "failing" in why.lower()


def test_untested_stays_advisory():
    # tests never ran (tests_passed False but tests_ran False) → NOT blocked by this gate
    healthy, _ = _assess_build(_result(tests_ran=False, tests_passed=False))
    assert healthy is True


def test_green_suite_passes():
    healthy, _ = _assess_build(_result(tests_ran=True, tests_passed=True))
    assert healthy is True


def test_missing_signal_defaults_safe():
    # a result object without the tests_ran attribute (e.g. reconstructed facts) is not blocked
    healthy, _ = _assess_build(SimpleNamespace(files=["a.py"], steps=1, files_written=1, summary=""))
    assert healthy is True
