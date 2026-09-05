"""Liveness / readiness probes (unversioned, used by k8s + docker-compose healthchecks)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])

SERVICE = "orchestrator"


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    """Process is up. Never touches downstreams."""
    return {"status": "ok", "service": SERVICE}


@router.get("/readyz", summary="Readiness probe")
async def readyz() -> dict[str, object]:
    """Ready to serve. Phase-0 stub: dependency checks (Postgres/Redis/Temporal) are wired
    in a later phase, so nothing is probed hard yet — this always reports ready."""
    return {"status": "ready", "service": SERVICE, "checks": {}}
