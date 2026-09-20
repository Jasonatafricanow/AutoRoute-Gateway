"""Gemini adapter (Phase 2) — standalone, NOT an OpenAI-compatible provider.

Protocol conversion != capability equivalence (§12): this adapter declares
exactly the capabilities it actually implements, capability-by-capability.
Current implementation: text chat (non-stream + stream). vision/tools stay
undelared until the Gate B2 smoke test proves them.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from ..domain.capability import Capability, CapabilitySet
from ..domain.model import ConcreteModel
from ..domain.state import HealthState, QuotaInfo
from .base import AdapterError, ChatPayload, ProviderAdapter, UpstreamHttpError


class GeminiAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, base_url: str, models: list[ConcreteModel], timeout: float = 120.0):
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self._models = {m.model_id: m for m in models}
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def list_models(self) -> list[ConcreteModel]:
        return list(self._models.values())

    def capability_manifest(self, model_id: str) -> CapabilitySet:
        # Explicit declaration (§12): only what this adapter really implements.
        return CapabilitySet.all_of(Capability.TEXT, Capability.STREAM, Capability.USAGE)

    # --- payload conversion: OpenAI chat payload -> Gemini generateContent ---
    @staticmethod
    def _to_gemini(payload: ChatPayload, model_id: str) -> dict:
        contents: list[dict] = []
        for msg in payload.get("messages", []):
            role = "model" if msg.get("role") == "assistant" else "user"
            content = msg.get("content")
            if isinstance(content, str):
                parts: list[dict] = [{"text": content}]
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, str):
                        parts.append({"text": block})
                    elif block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                    elif block.get("type") == "image_url":
                        raise AdapterError("gemini vision_input is not enabled before capability conformance (POC pending)")
                    else:
                        parts.append({"text": json.dumps(block, ensure_ascii=False)})
            else:
                parts = [{"text": ""}]
            contents.append({"role": role, "parts": parts})
        body: dict[str, Any] = {"contents": contents}
        if payload.get("max_tokens") is not None:
            body["generationConfig"] = {**body.get("generationConfig", {}), "maxOutputTokens": payload["max_tokens"]}
        if payload.get("temperature") is not None:
            body["generationConfig"] = {**body.get("generationConfig", {}), "temperature": payload["temperature"]}
        return body

    @staticmethod
    def _to_openai(response: dict, model_id: str) -> dict:
        candidates = response.get("candidates") or []
        text = ""
        for c in candidates:
            for part in c.get("content", {}).get("parts", []):
                if "text" in part:
                    text += part["text"]
        usage = response.get("usageMetadata") or {}
        return {
            "id": response.get("responseId", "gemini"),
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
        }

    def _headers(self, credential_secret: str) -> dict[str, str]:
        return {"x-goog-api-key": credential_secret}

    async def chat_completions(self, payload: ChatPayload, credential_secret: str, model_id: str) -> dict:
        url = f"{self.base_url}/models/{model_id}:generateContent"
        body = self._to_gemini(payload, model_id)
        try:
            resp = await self._http().post(url, headers=self._headers(credential_secret), json=body)
        except httpx.HTTPError as exc:
            raise AdapterError(f"{self.provider_id} transport failure: {exc}") from exc
        if resp.status_code >= 400:
            raise UpstreamHttpError(resp.status_code, resp.text, dict(resp.headers))
        return self._to_openai(resp.json(), model_id)

    async def chat_completions_stream(self, payload: ChatPayload, credential_secret: str, model_id: str) -> AsyncIterator[dict]:
        url = f"{self.base_url}/models/{model_id}:streamGenerateContent?alt=sse"
        body = self._to_gemini(payload, model_id)
        try:
            async with self._http().stream("POST", url, headers=self._headers(credential_secret), json=body) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise UpstreamHttpError(resp.status_code, text, dict(resp.headers))
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        try:
                            yield {"data": self._to_openai(json.loads(data), model_id)}
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError as exc:
            raise AdapterError(f"{self.provider_id} stream transport failure: {exc}") from exc

    async def probe(self, credential_secret: str) -> HealthState:
        try:
            resp = await self._http().get(
                f"{self.base_url}/models", headers=self._headers(credential_secret)
            )
            return HealthState.HEALTHY if resp.status_code == 200 else HealthState.DOWN
        except httpx.HTTPError:
            return HealthState.DOWN

    async def quota_info(self, credential_secret: str) -> QuotaInfo | None:
        return None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
