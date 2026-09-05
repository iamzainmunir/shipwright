"""Re-export the deterministic test/eval provider so tests import it from one place.

The canonical implementation lives with the eval harness (``evals.harness.scripted_provider``) —
it is testing infrastructure, never part of the shipped product (the app uses
``app.providers.NoModelProvider`` when no real model is connected).
"""

from __future__ import annotations

from evals.harness.scripted_provider import ScriptedProvider

__all__ = ["ScriptedProvider"]
