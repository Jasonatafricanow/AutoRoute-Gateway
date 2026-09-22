"""Service-level integration: a cli executor candidate flows through chat()."""

from __future__ import annotations

from dataclasses import replace

import pytest

from gateway.config.loader import load_gateway_config
from gateway.config.schema import _route
from gateway.state import InMemoryStateStore
from gateway.service import GatewayService


@pytest.mark.asyncio
async def test_chat_uses_executor_for_cli_candidate(monkeypatch, tmp_path):
    cfg, resolver = load_gateway_config("gateway.yaml")
    cfg = replace(
        cfg,
        codex_enabled=True,
        providers={
            **cfg.providers,
            "codex": replace(cfg.providers["codex"], enabled=True),
        },
    )
    # codex-only route → forces the cli candidate path
    cfg.routes["gateway-fast"] = _route("gateway-fast", {"providers": [{"provider": "codex", "model": "codex-gpt"}]})

    monkeypatch.setenv("DSH_USER_STATE_DB", "ignored")
    svc = GatewayService(cfg, resolver, store=InMemoryStateStore())

    calls = {}

    class FakeExecutor:
        async def execute(self, task):
            calls["task"] = task
            return {
                "ok": True,
                "text": "cli-answer",
                "usage": {"input_tokens": 7, "output_tokens": 3},
                "latency_ms": 555,
            }

    monkeypatch.setattr(svc, "executor_for", lambda candidate: FakeExecutor())

    body = await svc.chat("gateway-fast", {"messages": [{"role": "user", "content": "hi"}]})
    assert body["choices"][0]["message"]["content"] == "cli-answer"
    assert body["x_executor"] == "codex-cli"
    assert body["usage"]["total_tokens"] == 10
    assert calls["task"]["messages"][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_chat_executor_failure_becomes_adapter_error(monkeypatch):
    cfg, resolver = load_gateway_config("gateway.yaml")
    cfg = replace(
        cfg,
        codex_enabled=True,
        providers={
            **cfg.providers,
            "codex": replace(cfg.providers["codex"], enabled=True),
        },
    )
    cfg.routes["gateway-fast"] = _route("gateway-fast", {"providers": [{"provider": "codex", "model": "codex-gpt"}]})
    svc = GatewayService(cfg, resolver, store=InMemoryStateStore())

    class FailingExecutor:
        async def execute(self, task):
            return {"ok": False, "error_class": "upstream", "message": "codex exploded"}

    monkeypatch.setattr(svc, "executor_for", lambda candidate: FailingExecutor())

    from gateway.routing import NoEligibleCandidateError

    with pytest.raises(Exception) as ei:
        await svc.chat("gateway-fast", {"messages": [{"role": "user", "content": "hi"}]})
    # adapter error surfaces up through fallback chain (no more candidates → NoEligibleCandidateError)
    assert isinstance(ei.value, NoEligibleCandidateError) or "adapter" in str(ei.value).lower()