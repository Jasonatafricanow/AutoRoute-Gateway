"""Concrete provider adapters.

amd / modelscope / opencode are OpenAI-compatible surface providers (share
openai_compatible.py base, §12). Endpoints and models come from gateway.yaml —
no hard-coded endpoints or keys (§10/§14). gemini is a standalone adapter
(Phase 2; capability manifest explicitly declared, §12).
"""

from __future__ import annotations

from typing import Protocol

from ..domain.model import ConcreteModel
from .base import ProviderAdapter


class AdapterFactory(Protocol):
    def __call__(self, provider_id: str, base_url: str, models: list[ConcreteModel]) -> ProviderAdapter: ...


class AmdAdapterFactory:
    @staticmethod
    def create(provider_id: str, base_url: str, models: list[ConcreteModel]) -> ProviderAdapter:
        from .openai_compatible import OpenAiCompatibleAdapter

        return OpenAiCompatibleAdapter(provider_id, base_url, models)


class ModelScopeAdapterFactory:
    @staticmethod
    def create(provider_id: str, base_url: str, models: list[ConcreteModel]) -> ProviderAdapter:
        from .openai_compatible import OpenAiCompatibleAdapter

        return OpenAiCompatibleAdapter(provider_id, base_url, models)


class OpenCodeAdapterFactory:
    @staticmethod
    def create(provider_id: str, base_url: str, models: list[ConcreteModel]) -> ProviderAdapter:
        from .openai_compatible import OpenAiCompatibleAdapter

        return OpenAiCompatibleAdapter(provider_id, base_url, models)


class GeminiAdapterFactory:
    @staticmethod
    def create(provider_id: str, base_url: str, models: list[ConcreteModel]) -> ProviderAdapter:
        from .gemini import GeminiAdapter

        return GeminiAdapter(provider_id, base_url, models)


ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "openai_compatible": AmdAdapterFactory.create,
    "amd": AmdAdapterFactory.create,
    "modelscope": ModelScopeAdapterFactory.create,
    "opencode": OpenCodeAdapterFactory.create,
    "gemini": GeminiAdapterFactory.create,
}


def build_adapter(kind: str, provider_id: str, base_url: str, models: list[ConcreteModel]) -> ProviderAdapter:
    factory = ADAPTER_FACTORIES.get(kind)
    if factory is None:
        raise KeyError(f"unknown provider type: {kind}")
    return factory(provider_id, base_url, models)
