"""Provider adapters (§12)."""

from .base import AdapterError, ExecutorAdapter, ProviderAdapter, UpstreamHttpError
from .registry import ADAPTER_FACTORIES, build_adapter

__all__ = [
    "AdapterError",
    "ExecutorAdapter",
    "ProviderAdapter",
    "UpstreamHttpError",
    "ADAPTER_FACTORIES",
    "build_adapter",
]
