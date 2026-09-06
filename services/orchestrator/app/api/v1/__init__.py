"""The versioned public API (Canon §8: `/api/v1/<plural-resource>`, camelCase JSON)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import fs, missions, projects, runs, tickets

router = APIRouter(prefix="/api/v1")
router.include_router(missions.router)
router.include_router(projects.router)
router.include_router(fs.router)
router.include_router(runs.router)
router.include_router(tickets.router)

__all__ = ["router"]
