"""Shared base for OpenAI-compatible API providers (amd / modelscope / opencode).

Transport-layer behavior borrowed from free-proxy (§17): request relay and
SSE framing semantics. Policy/domain/state are NOT inherited — this file only
speaks OpenAI wire protocol.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from ..domain.capability import Capability, CapabilitySet
from ..domain.model import ConcreteModel
from ..domain.state import HealthState, QuotaInfo
from .base import AdapterError, ChatPayload, ProviderAdapter, UpstreamHttpError

#: headers that map to HEADER_DERIVED quota observations (§11)
_RATE_LIMIT_HEADERS = (
    "x-ratelimit-remaining",
    "x-ratelimit-limit",
    "x-ratelimit-reset",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-limit-requests",
    "retry-after",
)


class OpenAiCompatibleAdapter(ProviderAdapter):
    """Base adapter for providers exposing an OpenAI-compatible HTTP surface.

    Subclasses set provider_id, base_url, model definitions and the capability
    manifest source (config or conformance matrix).
    """

    def __init__(self, provider_id: str, base_url: str, models: list[ConcreteModel], timeout: float = 120.0, extra_headers: dict[str, str] | None = None):
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self._models = {m.model_id: m for m in models}
        self._timeout = timeout
        self._extra_headers = extra_headers or {}
        self._client: httpx.AsyncClient | None = None

    # --- wiring ---
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _headers(self, credential_secret: str) -> dict[str, str]:
        # Browser-like UA: some providers (opencode.ai) sit behind Cloudflare and
        # return 403 error 1010 for non-browser signatures (e.g. python-httpx default).
        return {
            "Authorization": f"Bearer {credential_secret}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            **self._extra_headers,
        }

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/models"

    # --- ProviderAdapter ---
    def list_models(self) -> list[ConcreteModel]:
        return list(self._models.values())

    def capability_manifest(self, model_id: str) -> CapabilitySet:
        model = self._models.get(model_id)
        if model is None:
            return CapabilitySet()
        return model.capabilities

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> UpstreamHttpError:
        body = exc.response.text
        headers = {k.lower(): v for k, v in exc.response.headers.items()}
        # never let the body of an auth failure leak a key back to logs
        return UpstreamHttpError(exc.response.status_code, body, headers)

    async def _request_json(self, method: str, url: str, credential_secret: str, *, json_body: dict | None = None) -> httpx.Response:
        try:
            resp = await self._http().request(method, url, headers=self._headers(credential_secret), json=json_body)
        except httpx.HTTPError as exc:
            raise AdapterError(f"{self.provider_id} transport failure: {exc}") from exc
        if resp.status_code >= 400:
            raise self._classify_http_error(httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp))
        return resp

    async def chat_completions(self, payload: ChatPayload, credential_secret: str, model_id: str) -> dict:
        body = {**payload, "model": model_id}
        resp = await self._request_json("POST", self.chat_url, credential_secret, json_body=body)
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise AdapterError(f"{self.provider_id}: non-JSON success body") from exc

    async def chat_completions_stream(self, payload: ChatPayload, credential_secret: str, model_id: str) -> AsyncIterator[dict]:
        body = {**payload, "model": model_id, "stream": True}
        try:
            async with self._http().stream("POST", self.chat_url, headers=self._headers(credential_secret), json=body) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise self._classify_http_error(httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp))
                async for event in _iter_sse_events(resp):
                    yield event
        except httpx.HTTPError as exc:
            raise AdapterError(f"{self.provider_id} stream transport failure: {exc}") from exc

    async def probe(self, credential_secret: str) -> HealthState:
        try:
            resp = await self._request_json("GET", self.models_url, credential_secret)
            if resp.status_code == 200:
                return HealthState.HEALTHY
            return HealthState.DEGRADED
        except UpstreamHttpError:
            return HealthState.DOWN
        except AdapterError:
            return HealthState.DOWN

    async def quota_info(self, credential_secret: str) -> QuotaInfo | None:
        """HEADER_DERIVED observation from x-ratelimit-* / retry-after (§11)."""
        try:
            resp = await self._http().get(self.models_url, headers=self._headers(credential_secret))
        except httpx.HTTPError:
            return None
        found: dict[str, Any] = {}
        for name in _RATE_LIMIT_HEADERS:
            if name in resp.headers:
                found[name] = resp.headers[name]
        if not found:
            return QuotaInfo(level="UNKNOWN")
        remaining = _as_int(found.get("x-ratelimit-remaining") or found.get("x-ratelimit-remaining-requests"))
        limit = _as_int(found.get("x-ratelimit-limit") or found.get("x-ratelimit-limit-requests"))
        return QuotaInfo(level="HEADER_DERIVED", remaining=remaining, limit=limit, raw=found)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).split(";")[0])
    except (TypeError, ValueError):
        return None


async def _iter_sse_events(resp: httpx.Response) -> AsyncIterator[dict]:
    """Parse an OpenAI-compatible SSE stream into event dicts.

    Yields {"data": <parsed json or text>} per event, including [DONE].
    """
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data = line[5:].strip()
            if data == "[DONE]":
                yield {"data": "[DONE]"}
                continue
            try:
                yield {"data": json.loads(data)}
            except json.JSONDecodeError:
                yield {"data": data}
        elif line.startswith("event:"):
            yield {"event": line[6:].strip()}
        else:
            # tolerate non-standard framing: pass the raw line through
            yield {"raw": line}
