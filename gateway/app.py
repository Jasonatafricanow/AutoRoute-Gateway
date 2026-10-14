"""App assembly: FastAPI app + auth/ACL middleware (frozen §24.22).

Non-loopback deployment MUST configure ACL / gateway auth; unbounded public
exposure without auth is forbidden — the gateway holds every upstream key.
"""

from __future__ import annotations

import hmac
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
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:  # unix socket / unknown peer: deny rather than 500
        return False
    for entry in allow_ips:
        if "/" in entry:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        elif ipaddress.ip_address(entry) == addr:
            return True
    return False


def _host_is_loopback(host_header: str) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):
        name = host[1:].partition("]")[0]
    else:
        name = host.rpartition(":")[0] if host.count(":") == 1 else host
    return name == "localhost" or _is_loopback(name)


def _browser_forged(request: Request) -> bool:
    """Without a gateway token, loopback is the only guard — and a browser on
    this machine is on loopback. A web page can DNS-rebind its own hostname to
    127.0.0.1 (keeping that hostname in Host) or send cross-origin requests
    (always carrying Origin); local SDK/CLI clients do neither."""
    if request.headers.get("origin") is not None:
        return True
    return not _host_is_loopback(request.headers.get("host", ""))


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
            expected = f"Bearer {config.server.auth_token}"
            if not hmac.compare_digest(auth.encode("utf-8"), expected.encode("utf-8")):
                return JSONResponse(status_code=401, content={"error": {"message": "gateway auth required"}})
        elif _browser_forged(request):
            return JSONResponse(status_code=403, content={"error": {"message": "cross-origin or non-loopback Host refused; configure a gateway auth token"}})
        if not _allowed(client_ip, config.server.allow_ips):
            return JSONResponse(status_code=403, content={"error": {"message": "network ACL denied"}})
        return await call_next(request)

    app.include_router(openai_api.router, prefix="/v1")
    app.include_router(status_api.router)
    return app
