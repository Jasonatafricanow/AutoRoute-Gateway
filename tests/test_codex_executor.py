"""Codex Executor tests (§18): prompt flattening, JSONL parsing, execute().

Pure functions are tested directly; subprocess paths are mocked (no real
codex invocation in CI). Real invocation was validated in POC (6/6,
2026-08-16) — see docs/CODEX_EXECUTOR_RESEARCH.md.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from gateway.domain.capability import Capability
from gateway.executors import CodexExecutor, chat_response_from_executor, parse_jsonl, prompt_from_task
from gateway.providers.base import AdapterError


def test_prompt_from_task_string_content():
    task = {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}, {"role": "user", "content": "again"}]}
    assert prompt_from_task(task) == "hello\nhi\nagain"


def test_prompt_from_task_parts_content():
    task = {"messages": [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "x.png"}}]}]}
    assert prompt_from_task(task) == "look\n[image]"


def test_prompt_from_task_empty():
    assert prompt_from_task({}) == ""
    assert prompt_from_task({"messages": []}) == ""


def test_parse_jsonl_success():
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}),
        ]
    )
    text, usage, error = parse_jsonl(raw)
    assert text == "done"
    assert usage == {"input_tokens": 10, "output_tokens": 2}
    assert error is None


def test_parse_jsonl_takes_last_agent_message():
    raw = "\n".join(
        [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "final"}}),
        ]
    )
    text, _, _ = parse_jsonl(raw)
    assert text == "final"


def test_parse_jsonl_error_event():
    raw = "\n".join(
        [
            json.dumps({"type": "error", "error": "boom"}),
        ]
    )
    text, _, error = parse_jsonl(raw)
    assert text is None
    assert error == "boom"


def test_parse_jsonl_turn_failed():
    raw = "\n".join(
        [
            json.dumps({"type": "turn.failed", "item": {"text": "could not complete"}}),
        ]
    )
    text, _, error = parse_jsonl(raw)
    assert error == "could not complete"


def test_parse_jsonl_garbage_lines():
    text, usage, error = parse_jsonl("not json\n\n{\"type\":\"turn.completed\",\"usage\":null}\n")
    assert (text, usage, error) == (None, None, None)


def test_capability_manifest_disabled():
    assert CodexExecutor(enabled=False).capability_manifest() == set()


def test_capability_manifest_enabled():
    caps = CodexExecutor(enabled=True).capability_manifest()
    assert Capability.TEXT in caps
    assert Capability.STRUCTURED_OUTPUT in caps
    assert Capability.MULTI_TURN in caps
    assert Capability.STREAM not in caps  # CLI 非真流式


def test_execute_disabled_raises():
    async def run():
        with pytest_raises(AdapterError):
            await CodexExecutor(enabled=False).execute({})

    asyncio.run(run())


def pytest_raises(exc):
    return _Raises(exc)


class _Raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, self.exc)


def test_execute_success():
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "exec-ok"}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 5}},
    ]
    raw = "\n".join(json.dumps(e) for e in events) + "\n"

    async def run():
        with patch("gateway.executors.codex.asyncio.create_subprocess_exec", new=AsyncMock()) as m:
            proc = AsyncMock()
            proc.communicate.return_value = (raw.encode(), b"")
            proc.returncode = 0
            m.return_value = proc
            result = await CodexExecutor(enabled=True).execute({"messages": [{"role": "user", "content": "go"}]})
        return result

    result = asyncio.run(run())
    assert result["ok"] is True
    assert result["text"] == "exec-ok"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 5}


def test_execute_rejected_still_ok_text(monkeypatch):
    # POC 发现：sandbox 拒绝也会 rc=0 + 正常 agent_message —— 按事件流处理
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "I can't do that: read-only sandbox"}},
        {"type": "turn.completed", "usage": None},
    ]
    raw = "\n".join(json.dumps(e) for e in events) + "\n"

    async def run():
        with patch("gateway.executors.codex.asyncio.create_subprocess_exec", new=AsyncMock()) as m:
            proc = AsyncMock()
            proc.communicate.return_value = (raw.encode(), b"")
            proc.returncode = 0
            m.return_value = proc
            return await CodexExecutor(enabled=True).execute({"messages": [{"role": "user", "content": "write file"}]})

    result = asyncio.run(run())
    assert result["ok"] is True
    assert "can't" in result["text"]


def test_execute_failure_no_text():
    events = [{"type": "turn.failed", "item": {"text": "boom"}}]
    raw = "\n".join(json.dumps(e) for e in events) + "\n"

    async def run():
        with patch("gateway.executors.codex.asyncio.create_subprocess_exec", new=AsyncMock()) as m:
            proc = AsyncMock()
            proc.communicate.return_value = (raw.encode(), b"")
            proc.returncode = 1
            m.return_value = proc
            return await CodexExecutor(enabled=True).execute({"messages": [{"role": "user", "content": "x"}]})

    result = asyncio.run(run())
    assert result["ok"] is False
    assert result["error_class"] == "upstream"


def test_chat_response_from_executor():
    result = {"ok": True, "text": "hi", "usage": {"input_tokens": 3, "output_tokens": 1}, "latency_ms": 1234, "model": "codex/gpt"}
    body = chat_response_from_executor("gateway-fast", result)
    assert body["choices"][0]["message"]["content"] == "hi"
    assert body["usage"]["total_tokens"] == 4
    assert body["x_executor"] == "codex-cli"
    assert body["model"] == "gateway-fast"