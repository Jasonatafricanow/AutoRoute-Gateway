"""chat_stream records a success route_event when the stream completes cleanly."""

from __future__ import annotations

import pytest

from gateway.config.loader import load_gateway_config
from gateway.config.schema import _route
from gateway.service import GatewayService
from gateway.state import InMemoryStateStore


class FakeAdapter:
    """Streams two chunks then ends cleanly with [DONE]."""

    async def chat_completions_stream(self, payload, secret, model):
        yield {"data": {"choices": [{"delta": {"content": "hi"}}]}}
        yield {"data": {"choices": [{"delta": {"content": "!"}}]}}
        yield {"data": "[DONE]"}

    async def aclose(self):
        pass


class FakeSecretResolver:
    def resolve(self, key):  # pragma: no cover - never called by stream path before adapter
        return "fake"


@pytest.mark.asyncio
async def test_chat_stream_records_success(monkeypatch):
    cfg, resolver = load_gateway_config("gateway.yaml")
    cfg.routes["gateway-fast"] = _route(
        "gateway-fast", {"providers": [{"provider": "amd", "model": "DeepSeek-V4-Flash"}]}
    )
    store = InMemoryStateStore()
    svc = GatewayService(cfg, resolver, store=store)
    monkeypatch.setattr(svc, "adapter_for", lambda c: FakeAdapter())
    monkeypatch.setattr(svc, "_secret", lambda c: "fake")

    events = [e async for e in svc.chat_stream("gateway-fast", {"messages": [{"role": "user", "content": "hi"}]})]
    assert len(events) == 3

    successes = [e for e in store.events if e.get("action") == "success"]
    assert len(successes) == 1, f"expected one success event, got {store.events}"
    assert successes[0]["provider_id"] == "amd"


@pytest.mark.asyncio
async def test_chat_stream_records_partial_on_mid_stream_error(monkeypatch):
    cfg, resolver = load_gateway_config("gateway.yaml")
    cfg.routes["gateway-fast"] = _route(
        "gateway-fast", {"providers": [{"provider": "amd", "model": "DeepSeek-V4-Flash"}]}
    )
    store = InMemoryStateStore()
    svc = GatewayService(cfg, resolver, store=store)

    class BrokenAdapter(FakeAdapter):
        async def chat_completions_stream(self, payload, secret, model):
            yield {"data": {"choices": [{"delta": {"content": "hi"}}]}}
            raise RuntimeError("mid-stream boom")

    monkeypatch.setattr(svc, "adapter_for", lambda c: BrokenAdapter())
    monkeypatch.setattr(svc, "_secret", lambda c: "fake")

    with pytest.raises(Exception):
        async for _ in svc.chat_stream("gateway-fast", {"messages": [{"role": "user", "content": "hi"}]}):
            pass

    actions = [e.get("action") for e in store.events]
    assert "partial_stream_failure" in actions, actions

@pytest.mark.asyncio
async def test_role_only_prefix_failure_falls_back_before_commit(monkeypatch):
    cfg, resolver = load_gateway_config("gateway.yaml")
    cfg.routes["gateway-fast"] = _route(
        "gateway-fast",
        {
            "providers": [
                {"provider": "amd", "model": "DeepSeek-V4-Flash"},
                {
                    "provider": "modelscope",
                    "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
                },
            ]
        },
    )
    store = InMemoryStateStore()
    svc = GatewayService(cfg, resolver, store=store)

    class RoleThenFail:
        async def chat_completions_stream(self, payload, secret, model):
            yield {"data": {"choices": [{"delta": {"role": "assistant"}}]}}
            raise RuntimeError("disconnect before content")

    class FallbackAdapter:
        async def chat_completions_stream(self, payload, secret, model):
            yield {"data": {"choices": [{"delta": {"content": "fallback"}}]}}
            yield {"data": "[DONE]"}

    monkeypatch.setattr(
        svc,
        "adapter_for",
        lambda candidate: (
            RoleThenFail()
            if candidate.provider_id == "amd"
            else FallbackAdapter()
        ),
    )
    monkeypatch.setattr(svc, "_secret", lambda candidate: "fake")

    events = [
        event
        async for event in svc.chat_stream(
            "gateway-fast",
            {"messages": [{"role": "user", "content": "hi"}]},
        )
    ]

    assert events == [
        {"data": {"choices": [{"delta": {"content": "fallback"}}]}},
        {"data": "[DONE]"},
    ]
    assert any(
        event.get("provider_id") == "amd"
        and event.get("action") == "next_candidate"
        for event in store.events
    )
    assert any(
        event.get("provider_id") == "modelscope"
        and event.get("action") == "success"
        for event in store.events
    )
