"""Process singletons: store, event bus, provider, run engine.

The store is a mutable singleton so the app lifespan can swap in the Postgres store at startup
(``set_store``) before any request; the engine is rebuilt lazily against whatever store is set.
The API/engine depend on the store *interface*, never its construction.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from .engine import RunEngine
from .events import EventBus
from .providers import get_provider
from .store import InMemoryStore


class Store(Protocol):
    """The async store interface both InMemoryStore and PostgresStore satisfy."""

    async def next_mission_key(self) -> str: ...


_store: object | None = None
_engine: RunEngine | None = None


def get_store() -> InMemoryStore:  # return type is the structural interface at runtime
    global _store
    if _store is None:
        _store = InMemoryStore()
    return _store  # type: ignore[return-value]


def set_store(store: object) -> None:
    """Install a concrete store (e.g. PostgresStore) and reset the engine to use it."""
    global _store, _engine
    _store = store
    _engine = None


@lru_cache(maxsize=1)
def get_bus() -> EventBus:
    return EventBus()


@lru_cache(maxsize=1)
def get_preview_manager():
    """Singleton that runs built apps on localhost ports for the 'Run app' feature."""
    from .preview import PreviewManager

    return PreviewManager()


def get_engine() -> RunEngine:
    """The active engine, selected by ``FOUNDRY_ENGINE`` (plan 02 §6 strangler migration).

    'legacy' → the hand-rolled RunEngine (default, battle-tested);
    'graph'  → the LangGraph engine (v2). Both satisfy :class:`app.engine_protocol.EngineProtocol`;
    the return annotation stays ``RunEngine`` for the legacy default's typing — call sites only
    use the 6 facade methods.
    """
    global _engine
    if _engine is None:
        from .config import get_settings

        if get_settings().engine.lower() == "graph":
            from .graph_engine import GraphEngine

            _engine = GraphEngine(get_store(), get_bus(), get_provider())  # type: ignore[assignment]
        else:
            _engine = RunEngine(get_store(), get_bus(), get_provider())
    return _engine  # type: ignore[return-value]
