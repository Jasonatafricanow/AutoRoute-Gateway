"""Gateway service: request pipeline orchestration.

Pipeline (§2):
    request → VirtualModel resolve → capability derivation → Candidate Builder
    → Capability Gate → Scorer → Router (sort only) → Executor → Fallback Engine

Streaming commit semantics (§8):
    - pre-commit failure (no downstream chunk yet): transparent fallback
    - post-commit failure (≥1 valid chunk sent): terminate stream, record
      PARTIAL_STREAM_FAILURE, never replay across providers
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator

from .config.schema import GatewayConfig, SecretResolver
from .domain.capability import CapabilitySet, required_capabilities_from_request
from .domain.candidate import RouteCandidate
from .domain.model import ConcreteModel
from .policy.error_classifier import ErrorClass, classify_error
from .executors import chat_response_from_executor
from .providers import AdapterError, ExecutorAdapter, ProviderAdapter, UpstreamHttpError
from .providers.registry import build_adapter
from .routing import (
    CandidateFailure,
    FallbackEngine,
    NoEligibleCandidateError,
    UnknownVirtualModelError,
    build_candidates,
    capability_gate,
    score_candidates,
)
from .state.store import StateStore

log = logging.getLogger("gateway")


class RequestValidationError(RuntimeError):
    pass


class PartialStreamFailure(RuntimeError):
    """Raised by chat_stream after a post-commit upstream failure was recorded.

    The stream has already delivered ≥1 chunk; no replay happens (§8). The API
    layer must terminate the SSE without a [DONE] marker.
    """

    def __init__(self, error_class: ErrorClass):
        super().__init__(f"partial stream failure ({error_class.value})")
        self.error_class = error_class


def _capabilities_from_payload(payload: dict) -> CapabilitySet:
    messages = payload.get("messages") or []
    has_vision = False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    has_vision = True
    return required_capabilities_from_request(
        stream=bool(payload.get("stream")),
        tools=bool(payload.get("tools")),
        structured_output=bool(payload.get("response_format")),
        vision_input=has_vision,
        multi_turn=bool(messages and len(messages) > 1),
        usage=bool(payload.get("stream_options", {}).get("include_usage", True) or not payload.get("stream")),
    )


def _is_valid_chunk(event: dict) -> bool:
    """A 'valid downstream chunk' (§8): substantive content or tool call."""
    data = event.get("data")
    if not isinstance(data, dict):
        return False
    if data.get("data") == "[DONE]":
        return False
    choices = data.get("choices")
    if not choices:
        return False
    delta = choices[0].get("delta") or {}
    if delta.get("content"):
        return True
    if delta.get("tool_calls"):
        return True
    if delta.get("reasoning_content"):
        return True
    return False


def _sanitize(text: str, limit: int = 300) -> str:
    text = (text or "").replace("Bearer ", "").replace("bearer ", "")
    return text[:limit]


class StreamHandle:
    """A selected stream candidate: buffers the first event so the engine can
    decide pre-commit fallback before anything reaches the client."""

    def __init__(self, candidate: RouteCandidate, events: AsyncIterator[dict], first: dict | None):
        self.candidate = candidate
        self._events = events
        self._first = first
        self.committed = False

    async def __aiter__(self) -> AsyncIterator[dict]:
        if self._first is not None:
            yield self._first
            self._first = None
        async for event in self._events:
            if not self.committed and _is_valid_chunk(event):
                self.committed = True
            yield event


class GatewayService:
    def __init__(
        self,
        config: GatewayConfig,
        resolver: SecretResolver,
        store: StateStore,
        fallback: FallbackEngine | None = None,
    ):
        self.config = config
        self.resolver = resolver
        self.store = store
        self.fallback = fallback or FallbackEngine(store)
        self._adapters: dict[str, ProviderAdapter] = {}
        self._executors: dict[str, ExecutorAdapter] = {}
        self._build_adapters()

    # --- adapters ---
    def _build_adapters(self) -> None:
        for pid, pc in self.config.providers.items():
            if not pc.enabled:
                continue
            if pc.type == "executor":
                if self.config.codex_enabled and pid == "codex":
                    from .executors import CodexExecutor

                    self._executors[pid] = CodexExecutor(enabled=True)
                continue
            models = [ConcreteModel(model_id=m.id, provider_id=pid, capabilities=m.capabilities) for m in pc.models]
            self._adapters[pid] = build_adapter(pc.type, pid, pc.base_url or "", models)

    def adapter_for(self, candidate: RouteCandidate) -> ProviderAdapter:
        return self._adapters[candidate.provider_id]

    def executor_for(self, candidate: RouteCandidate) -> ExecutorAdapter:
        exc = self._executors.get(candidate.provider_id)
        if exc is None:
            raise AdapterError(f"no executor for provider {candidate.provider_id!r}")
        return exc

    # --- helpers ---
    def _secret(self, candidate: RouteCandidate) -> str:
        key_cfg = self.config.providers[candidate.provider_id]
        key = next(k for k in key_cfg.keys if k.id == candidate.credential_id)
        return self.resolver.require(key.env)

    def _candidate_failure(self, exc: Exception) -> CandidateFailure:
        if isinstance(exc, UpstreamHttpError):
            error_class = classify_error(exc.status, exc.body_text, exc.headers)
            return CandidateFailure(error_class, status=exc.status, message=_sanitize(exc.body_text))
        if isinstance(exc, AdapterError):
            error_class = classify_error(None, str(exc))
            return CandidateFailure(error_class, message=_sanitize(str(exc)))
        return CandidateFailure(ErrorClass.SERVER, message=_sanitize(str(exc)))

    # --- main entry: non-stream ---
    async def chat(self, virtual_model: str, payload: dict) -> dict:
        request_id = str(uuid.uuid4())
        required = _capabilities_from_payload(payload)
        candidates = build_candidates(virtual_model, self.config)
        candidates = capability_gate(candidates, required)
        if not candidates:
            raise RequestValidationError(
                f"no candidate satisfies required capabilities {sorted(c.value for c in required)} for {virtual_model!r}"
            )
        scored = score_candidates(candidates, self.store)
        holder: dict = {}

        async def attempt(candidate: RouteCandidate) -> dict:
            if candidate.executor_type == "cli":
                executor = self.executor_for(candidate)
                try:
                    result = await executor.execute(payload)
                except Exception as exc:  # noqa: BLE001
                    raise self._candidate_failure(exc) from exc
                if not result.get("ok"):
                    raise AdapterError(result.get("message", "executor failed"))
                body = chat_response_from_executor(virtual_model, result)
                holder["body"] = body
                return body
            adapter = self.adapter_for(candidate)
            secret = self._secret(candidate)
            try:
                body = await adapter.chat_completions(payload, secret, candidate.concrete_model.model_id)
            except Exception as exc:  # noqa: BLE001
                raise self._candidate_failure(exc) from exc
            body = dict(body)
            body["model"] = virtual_model
            holder["body"] = body
            return body

        await self.fallback.run(virtual_model, scored, attempt, request_id)
        # attempt events were recorded by the engine itself; nothing to replay here
        return holder["body"]

    # --- main entry: stream ---
    async def chat_stream(self, virtual_model: str, payload: dict) -> AsyncIterator[dict]:
        request_id = str(uuid.uuid4())
        required = _capabilities_from_payload(payload)
        candidates = build_candidates(virtual_model, self.config)
        candidates = capability_gate(candidates, required)
        if not candidates:
            raise RequestValidationError(
                f"no candidate satisfies required capabilities {sorted(c.value for c in required)} for {virtual_model!r}"
            )
        scored = score_candidates(candidates, self.store)
        selected: StreamHandle | None = None
        attempts = 0
        for sc in scored:
            candidate = sc.candidate
            if not sc.usable or not self.fallback._eligible(candidate):  # noqa: SLF001
                continue
            while True:
                if attempts >= self.fallback._policy.hard_attempt_ceiling:  # noqa: SLF001
                    raise NoEligibleCandidateError(virtual_model, attempts)
                attempts += 1
                adapter = self.adapter_for(candidate)
                secret = self._secret(candidate)
                try:
                    events = adapter.chat_completions_stream(payload, secret, candidate.concrete_model.model_id)
                    first = await events.__anext__()
                except StopAsyncIteration:
                    # empty stream = never committed; move to the next candidate
                    self.store.record_route_event(
                        {"request_id": request_id, "virtual_model": virtual_model, "attempt": attempts,
                         "provider_id": candidate.provider_id, "credential_id": candidate.credential_id,
                         "model": candidate.concrete_model.model_id, "error_class": "server", "action": "next_candidate"}
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    failure = self._candidate_failure(exc)
                    self.fallback._apply_failure(candidate, failure)  # noqa: SLF001
                    self.store.record_route_event(
                        {"request_id": request_id, "virtual_model": virtual_model, "attempt": attempts,
                         "provider_id": candidate.provider_id, "credential_id": candidate.credential_id,
                         "model": candidate.concrete_model.model_id, "error_class": failure.error_class.value, "action": "next_candidate"}
                    )
                    break
                selected = StreamHandle(candidate, events, first)
                if _is_valid_chunk(first or {}):
                    selected.committed = True
                break
            if selected is not None:
                break

        if selected is None:
            raise NoEligibleCandidateError(virtual_model, attempts)

        # post-commit phase: no cross-provider replay is possible from here on
        try:
            async for event in selected:
                if event.get("data") == "[DONE]":
                    # stream completed cleanly — record before the terminal marker
                    # leaves the generator (the SSE layer returns on [DONE], which
                    # closes this generator early via aclose(), so try/else would
                    # never fire here).
                    self.store.record_route_event(
                        {"request_id": request_id, "virtual_model": virtual_model, "attempt": attempts,
                         "provider_id": selected.candidate.provider_id, "credential_id": selected.candidate.credential_id,
                         "model": selected.candidate.concrete_model.model_id,
                         "error_class": None, "action": "success"}
                    )
                yield event
        except Exception as exc:  # noqa: BLE001
            failure = self._candidate_failure(exc)
            log.warning(
                "PARTIAL_STREAM_FAILURE request=%s provider=%s key=%s model=%s committed=%s error=%s",
                request_id, selected.candidate.provider_id, selected.candidate.credential_id,
                selected.candidate.concrete_model.model_id, selected.committed, failure.error_class.value,
            )
            self.store.record_route_event(
                {"request_id": request_id, "virtual_model": virtual_model, "attempt": attempts,
                 "provider_id": selected.candidate.provider_id, "credential_id": selected.candidate.credential_id,
                 "model": selected.candidate.concrete_model.model_id,
                 "error_class": failure.error_class.value, "action": "partial_stream_failure"}
            )
            raise PartialStreamFailure(failure.error_class) from exc

    # --- models / status ---
    def list_virtual_models(self) -> list[dict]:
        return [{"id": name, "object": "model", "owned_by": "gateway"} for name in self.config.routes]

    def routes_overview(self) -> dict:
        return {
            name: [{"provider": s.provider, "model": s.model} for s in route.steps]
            for name, route in self.config.routes.items()
        }

    def providers_overview(self) -> dict:
        overview = {}
        snapshot = self.store.snapshot()
        for pid, pc in self.config.providers.items():
            provider_info: dict[str, Any] = {
                "type": pc.type,
                "base_url": pc.base_url,
                "enabled": pc.enabled,
                "models": [{"id": m.id, "capabilities": sorted(c.value for c in m.capabilities)} for m in pc.models],
                "keys": [],
            }
            for k in pc.keys:
                subject_key = (pid, k.id, None)
                cred_rows = [r for r in snapshot["credential"] if r["provider_id"] == pid and r["credential_id"] == k.id]
                provider_info["keys"].append(
                    {
                        "id": k.id,
                        "env": k.env,
                        "priority": k.priority,
                        "reserve": k.reserve,
                        "state": cred_rows[0]["state"] if cred_rows else "ENABLED",
                    }
                )
            overview[pid] = provider_info
        return overview

    async def aclose(self) -> None:
        for adapter in self._adapters.values():
            close = getattr(adapter, "aclose", None)
            if close:
                await close()
