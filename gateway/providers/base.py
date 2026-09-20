"""Adapter contracts (§12, v0.3.1 unified).

API providers implement ProviderAdapter; non-API / CLI resources (Codex)
implement ExecutorAdapter — Codex does not masquerade as an OpenAI provider.
A RouteCandidate resolves to executor_type = api | cli.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Protocol

from ..domain.capability import CapabilitySet
from ..domain.model import ConcreteModel
from ..domain.state import HealthState, QuotaInfo

ChatPayload = dict[str, Any]


class UpstreamHttpError(RuntimeError):
    """Non-2xx upstream response; carries the raw status/body for classification."""

    def __init__(self, status: int, body_text: str, headers: dict[str, str] | None = None):
        super().__init__(f"upstream HTTP {status}: {body_text[:200]}")
        self.status = status
        self.body_text = body_text
        self.headers = headers or {}


class AdapterError(RuntimeError):
    """Adapter-level failure (config, protocol) — treated as server-class."""


class ProviderAdapter(ABC):
    """API provider contract (§12)."""

    provider_id: str

    @abstractmethod
    def list_models(self) -> list[ConcreteModel]: ...

    @abstractmethod
    def capability_manifest(self, model_id: str) -> CapabilitySet:
        """Declared capabilities — from the Gate B2 conformance matrix, never
        wishful YAML (§24.21)."""

    @abstractmethod
    async def chat_completions(self, payload: ChatPayload, credential_secret: str, model_id: str) -> dict:
        """Non-streaming completion; returns the upstream JSON body verbatim."""

    @abstractmethod
    async def chat_completions_stream(self, payload: ChatPayload, credential_secret: str, model_id: str) -> AsyncIterator[dict]:
        """Streaming completion; yields parsed SSE events (dicts)."""

    @abstractmethod
    async def probe(self, credential_secret: str) -> HealthState: ...

    @abstractmethod
    async def quota_info(self, credential_secret: str) -> QuotaInfo | None: ...


class ExecutorAdapter(ABC):
    """CLI / non-API executor contract (§12/§18)."""

    executor_type: str = "cli"

    @abstractmethod
    async def execute(self, task: dict) -> dict:
        """Run one task on the executor; returns a result dict."""

    @abstractmethod
    def capability_manifest(self) -> CapabilitySet:
        """Capabilities verified by POC — declared capability-by-capability (§18)."""
