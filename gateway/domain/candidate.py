"""RouteCandidate (§3): the routing unit."""

from __future__ import annotations

from dataclasses import dataclass

from .capability import CapabilitySet
from .model import ConcreteModel


@dataclass(frozen=True)
class RouteCandidate:
    """provider × credential × concrete_model with declared capabilities."""

    provider_id: str
    credential_id: str
    concrete_model: ConcreteModel
    capabilities: CapabilitySet
    #: api | cli — resolved from the provider kind (§12)
    executor_type: str
    #: higher = preferred within the same provider
    credential_priority: int = 100
    #: reserve keys are tried only after primaries fail (§10)
    reserve: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.credential_id}/{self.concrete_model.model_id}"
