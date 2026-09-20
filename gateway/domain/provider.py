"""Provider entity (§3). Provider and Credential are independent entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ProviderKind = Literal["openai_compatible", "gemini", "executor"]


@dataclass(frozen=True)
class Provider:
    """A service provider (amd / modelscope / opencode / gemini / codex...)."""

    provider_id: str
    kind: ProviderKind
    #: API base url for api-kind providers; None for cli/executor providers
    base_url: str | None = None
    enabled: bool = True
    #: extra static metadata (headers, query params...) — transport details only
    options: dict = field(default_factory=dict)

    @property
    def executor_type(self) -> str:
        """RouteCandidate-level executor_type: api | cli (§12)."""
        return "cli" if self.kind == "executor" else "api"
