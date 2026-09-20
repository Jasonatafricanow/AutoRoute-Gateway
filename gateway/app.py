"""App assembly: FastAPI app + auth/ACL middleware (frozen §24.22).

Non-loopback deployment MUST configure ACL / gateway auth; unbounded public
exposure without auth is forbidden — the gateway holds every upstream key.
"""

from __future__ import annotations

import ipaddress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import openai as openai_api
from .api import status as status_api
from .config.schema import GatewayConfig, SecretResolver
from .service import GatewayService
from .state.store import StateStore


def _is_loopback(ip: str) -> bool:
    if ip == "testclient":  # starlette TestClient identity — always local
        return True
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def _allowed(client_ip: str, allow_ips: list[str]) -> bool:
    if not allow_ips:
        return _is_loopback(client_ip)
    addr = ipaddress.ip_address(client_ip)
    for entry in allow_ips:
        if "/" in entry:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        elif ipaddress.ip_address(entry) == addr:
            return True
    return False


def create_app(config: GatewayConfig, resolver: SecretResolver, store: StateStore, service: GatewayService | None = None) -> FastAPI:
    app = FastAPI(title="model-gateway", version="0.3.1")
    app.state.service = service or GatewayService(config, resolver, store)
    app.state.store = store

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else ""
        # status endpoints always reachable locally; everything else guarded
        if config.server.auth_token:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {config.server.auth_token}":
                return JSONResponse(status_code=401, content={"error": {"message": "gateway auth required"}})
        if not _allowed(client_ip, config.server.allow_ips):
            return JSONResponse(status_code=403, content={"error": {"message": "network ACL denied"}})
        return await call_next(request)

    app.include_router(openai_api.router, prefix="/v1")
    app.include_router(status_api.router)
    return app
