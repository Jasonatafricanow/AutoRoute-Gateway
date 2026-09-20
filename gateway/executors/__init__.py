"""Executor layer (§12/§18): CLI / non-API resources.

Codex is an experimental ExecutorAdapter: enabled = false by default and NOT
part of the main route graph. Capabilities are turned on capability-by-
capability after POC (§18). POC passed 6/6 (2026-08-16) — see
docs/CODEX_EXECUTOR_RESEARCH.md.
"""

from .codex import CodexExecutor, chat_response_from_executor, parse_jsonl, prompt_from_task

__all__ = ["CodexExecutor", "chat_response_from_executor", "parse_jsonl", "prompt_from_task"]