"""In-process event bus for live streaming (SSE/WebSocket).

Phase 1 uses an asyncio fan-out bus. When Redis is configured this is where a
``XADD ws:{workspace_id}:events`` publish is added (Canon §7/§13.8) — the API and the
store keep the same shape, so that upgrade is drop-in.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from foundry_core.models import Event


class EventBus:
    """Fan-out asyncio bus. Subscribers get every event; filtering is the caller's job."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    async def publish(self, event: Event) -> None:
        for q in list(self._subscribers):
            # Never let a slow/broken subscriber block the engine.
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - unbounded queues below
                pass

    async def subscribe(self) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)
