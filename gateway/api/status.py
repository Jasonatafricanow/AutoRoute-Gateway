"""Status / observability endpoints (§15).

GET /health    liveness
GET /ready     readiness (state store reachable)
GET /providers three-state overview per provider/key
GET /routes    route policies
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    service = request.app.state.service
    try:
        service.store.snapshot()
        return {"status": "ready"}
    except Exception:  # noqa: BLE001
        return {"status": "not_ready"}


@router.get("/providers")
async def providers(request: Request):
    service = request.app.state.service
    return service.providers_overview()


@router.get("/routes")
async def routes(request: Request):
    service = request.app.state.service
    return service.routes_overview()
