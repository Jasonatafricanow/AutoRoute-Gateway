"""Test doubles: scriptable fake adapters and config builders."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import AsyncIterator

from gateway.config.schema import SecretResolver, gateway_config_from_dict
from gateway.domain.capability import CapabilitySet
from gateway.domain.model import ConcreteModel
from gateway.domain.state import HealthState, QuotaInfo
from gateway.providers.base import ProviderAdapter, UpstreamHttpError


def workdir(name: str) -> Path:
    """Fresh test scratch dir. Avoids pytest tmp_path: the file sandbox denies
    subdir creation under pytest-managed temp dirs."""
    p = Path(os.getcwd()) / ".tmp" / f"tests-{name}-{uuid.uuid4().hex[:8]}"
    p.mkdir(parents=True, exist_ok=True)
    return p


class FakeAdapter(ProviderAdapter):
    """Scriptable adapter: each call pops the next scripted outcome.

    Outcome shapes:
    - dict                     -> chat_completions returns it
    - UpstreamHttpError/Exception -> raised
    - FakeStream               -> chat_completions_stream returns it
    """

    def __init__(self, provider_id: str, models: list[ConcreteModel] | None = None, script: list | None = None):
        self.provider_id = provider_id
        self._models = {m.model_id: m for m in (models or [])}
        self.script = list(script or [])
        self.calls: list[tuple[str, str]] = []  # (method, model)
        self.secrets_seen: list[str] = []

    def list_models(self) -> list[ConcreteModel]:
        return list(self._models.values())

    def capability_manifest(self, model_id: str) -> CapabilitySet:
        m = self._models.get(model_id)
        return m.capabilities if m else CapabilitySet()

    def _pop(self):
        if not self.script:
            raise AssertionError("FakeAdapter script exhausted")
        return self.script.pop(0)

    async def chat_completions(self, payload, credential_secret: str, model_id: str) -> dict:
        self.calls.append(("chat", model_id))
        self.secrets_seen.append(credential_secret)
        outcome = self._pop()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def chat_completions_stream(self, payload, credential_secret: str, model_id: str) -> AsyncIterator[dict]:
        self.calls.append(("stream", model_id))
        self.secrets_seen.append(credential_secret)
        outcome = self._pop()
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, FakeStream):
            async for event in outcome.events():
                yield event
            return
        async for event in outcome:
            yield event

    async def probe(self, credential_secret: str) -> HealthState:
        return HealthState.HEALTHY

    async def quota_info(self, credential_secret: str) -> QuotaInfo | None:
        return None


class FakeStream:
    """Scripted SSE stream: yields events, then optionally fails."""

    def __init__(self, chunks: list[dict] | None = None, fail_after: Exception | None = None):
        self.chunks = chunks or []
        self.fail_after = fail_after

    async def events(self) -> AsyncIterator[dict]:
        for chunk in self.chunks:
            yield {"data": chunk}
        if self.fail_after is not None:
            raise self.fail_after


def chunk(content: str = "", role: str | None = None, finish: str | None = None, model: str = "test") -> dict:
    delta: dict = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    return {"id": "x", "object": "chat.completion.chunk", "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def completion_body(content: str, model: str = "test") -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def make_test_config() -> dict:
    """A three-provider multi-key config matching the baseline acceptance cases."""
    return gateway_config_from_dict(
        {
            "routes": {
                "gateway-fast": {
                    "providers": [
                        {"provider": "amd", "model": "DS-Flash"},
                        {"provider": "modelscope", "model": "DS-Flash"},
                        {"provider": "opencode", "model": "DS-Flash"},
                    ]
                },
                "gateway-deep": {"providers": [{"provider": "opencode", "model": "Pro"}]},
            },
            "providers": {
                "amd": {
                    "type": "openai_compatible",
                    "base_url": "https://amd.test/v1",
                    "models": [{"id": "DS-Flash", "default": True, "capabilities": ["text", "stream", "usage", "multi_turn"]}],
                    "keys": [{"id": "amd-01", "env": "AMD_API_KEY", "priority": 100}],
                },
                "modelscope": {
                    "type": "openai_compatible",
                    "base_url": "https://ms.test/v1",
                    "models": [{"id": "DS-Flash", "default": True, "capabilities": ["text", "stream", "usage", "multi_turn"]}],
                    "keys": [{"id": "ms-01", "env": "MODELSCOPE_API_KEY", "priority": 100}],
                },
                "opencode": {
                    "type": "openai_compatible",
                    "base_url": "https://ocg.test/v1",
                    "models": [
                        {"id": "DS-Flash", "default": True, "capabilities": ["text", "stream", "usage", "multi_turn"]},
                        {"id": "Pro", "capabilities": ["text", "stream", "tools", "structured_output", "usage", "multi_turn"]},
                    ],
                    "keys": [
                        {"id": "ocg-01", "env": "OPENCODE_KEY_1", "priority": 100},
                        {"id": "ocg-02", "env": "OPENCODE_KEY_2", "priority": 50, "reserve": True},
                    ],
                },
            },
            "server": {"bind": "127.0.0.1", "port": 8700, "database_path": ":memory:"},
        }
    )


def make_test_resolver() -> SecretResolver:
    return SecretResolver(
        {
            "AMD_API_KEY": "k-amd",
            "MODELSCOPE_API_KEY": "k-ms",
            "OPENCODE_KEY_1": "k-ocg1",
            "OPENCODE_KEY_2": "k-ocg2",
        }
    )
