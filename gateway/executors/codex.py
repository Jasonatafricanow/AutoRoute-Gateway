"""Codex experimental executor (§18) — POC 完成版（2026-08-16）。

POC 已通过（6/6）：文本、--json usage、--output-schema、vision、sandbox。
本实现按 POC 落地：

- execute(): async subprocess 调 `codex exec --json --ephemeral
  --skip-git-repo-check --sandbox read-only`，解析 JSONL 事件流
  取最后 agent_message（POC 发现：exit code=0 也可能任务被拒，
  结果判定 = 最后 agent_message 非空 + 无 error/turn.failed 事件）
- capability_manifest(): POC 验证的能力按 capability-by-capability 开
  （§18）：text / structured_output / multi_turn。stream 不给——CLI 非
  真流式；vision_input 留 Phase 2（传图需要额外的文件路径处理）。

命令路径参数注意：Windows 下原生程序不认 git-bash 的 /tmp 路径，
必须传 C:\\... 原生路径。
"""

from __future__ import annotations

import asyncio
import os
import json
import logging
import time
from typing import Any

from ..domain.capability import Capability, CapabilitySet
from ..providers.base import AdapterError, ExecutorAdapter

log = logging.getLogger("gateway")

CODEX_BIN = "codex"
EXEC_TIMEOUT_SECONDS = 180.0
#: 固定指令（经 cmd /c 拼接，必须是常量——用户内容一律走 stdin，防注入）
EXEC_PROMPT_ARG = "Answer the user request below. Output the final answer only."


def prompt_from_task(task: dict) -> str:
    """Flatten an OpenAI-style chat payload into a plain prompt for codex exec."""
    messages = task.get("messages") or []
    texts: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    # Phase 2: 传图支持待定义（需把 image 落盘成文件路径再传）
                    texts.append("[image]")
    return "\n".join(texts).strip()


def parse_jsonl(raw: str) -> tuple[str | None, dict | None, str | None]:
    """Parse `codex exec --json` stdout.

    Returns (final_agent_message, usage_dict, error_text).
    POC 发现：exit code=0 也可能任务被拒，所以结果判定必须看事件流。
    """
    text: str | None = None
    usage: dict | None = None
    error: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text") or None
        elif kind == "turn.completed":
            usage = ev.get("usage") or None
        elif kind in ("turn.failed", "error"):
            error = (
                ev.get("error")
                or ev.get("message")
                or (ev.get("item") or {}).get("text")
                or json.dumps(ev, ensure_ascii=False)[:300]
            )
    return text, usage, error


class CodexExecutor(ExecutorAdapter):
    executor_type = "cli"

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def capability_manifest(self) -> CapabilitySet:
        # POC 验证后按 capability-by-capability 开启（§18）
        if not self.enabled:
            return CapabilitySet()
        return CapabilitySet.all_of(
            Capability.TEXT,
            Capability.STRUCTURED_OUTPUT,
            Capability.MULTI_TURN,
            Capability.USAGE,  # POC 验证：turn.completed.usage 提供 tokens
        )

    async def execute(self, task: dict) -> dict:
        if not self.enabled:
            raise AdapterError("Codex executor is disabled (§18)")
        prompt = prompt_from_task(task)
        if not prompt:
            return {"ok": False, "error_class": "invalid_request", "message": "empty prompt"}

        cmd = [
            CODEX_BIN,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            EXEC_PROMPT_ARG,
        ]
        # Windows: npm 全局命令是 .cmd shim，CreateProcess 不能直接执行 →
        # 经 cmd /c 启动；用户内容走 stdin，杜绝参数拼接注入
        if os.name == "nt":
            cmd = ["cmd.exe", "/c", *cmd]
        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=EXEC_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error_class": "timeout",
                "message": f"codex exec timed out after {EXEC_TIMEOUT_SECONDS}s",
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        except FileNotFoundError:
            return {"ok": False, "error_class": "server", "message": "codex CLI not installed"}
        latency_ms = int((time.perf_counter() - started) * 1000)

        out_text = stdout.decode(errors="replace")
        err_text = stderr.decode(errors="replace")
        text, usage, error = parse_jsonl(out_text)
        log.debug("codex exec: rc=%s latency=%sms text=%r error=%r", proc.returncode, latency_ms, (text or "")[:60], error)

        # POC 发现：rc=0 也可能任务被拒（sandbox 拒绝写文件等）——事件流为准
        if error or (proc.returncode != 0 and not text):
            msg = error or (err_text or f"codex exit {proc.returncode}")[:300]
            return {
                "ok": False,
                "error_class": "upstream",
                "message": msg,
                "latency_ms": latency_ms,
                "usage": usage,
            }
        if not text:
            return {
                "ok": False,
                "error_class": "server",
                "message": "codex exec produced no agent message",
                "latency_ms": latency_ms,
                "usage": usage,
            }
        return {
            "ok": True,
            "text": text,
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "latency_ms": latency_ms,
            "model": "codex/gpt",
        }


def chat_response_from_executor(virtual_model: str, result: dict) -> dict:
    """Wrap a successful executor result into OpenAI chat.completions shape."""
    usage = result.get("usage") or {}
    return {
        "id": "chatcmpl-codex",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": virtual_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.get("text", "")},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
            "total_tokens": int(
                (usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                + (usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            ),
        },
        "x_executor": "codex-cli",
        "x_latency_ms": result.get("latency_ms"),
    }