"""Virtual model and concrete model entities (§3/§4)."""

from __future__ import annotations

from dataclasses import dataclass

from .capability import CapabilitySet


@dataclass(frozen=True)
class VirtualModel:
    """Policy intent exposed to consumers (e.g. gateway-fast / gateway-deep).

    A VirtualModel is NOT a concrete model and NOT a capability requirement.
    """

    name: str
    #: ordered provider picks, each with a concrete model per provider
    description: str = ""


@dataclass(frozen=True)
class ConcreteModel:
    """A specific model on a specific provider (e.g. DS-Flash on amd)."""

    model_id: str
    provider_id: str
    #: declared capabilities (authoritative source: Gate B2 conformance matrix)
    capabilities: CapabilitySet = CapabilitySet()

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.model_id}"
