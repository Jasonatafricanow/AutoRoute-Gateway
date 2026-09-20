"""State model (§9): three orthogonal state dimensions bound to a StateSubject.

Frozen decisions:
- health / quota / credential are three separate orthogonal dimensions.
- Every observation binds to a StateSubject scope (provider_id +
  optional credential_id + optional concrete_model) to prevent error spread
  (key1 exhausted must never mark the whole provider EXHAUSTED).
- Quota reset follows a provider-specific reset_policy; never an unconditional
  local-date reset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class HealthState(str, Enum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class QuotaState(str, Enum):
    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    LOW = "LOW"
    RATE_LIMITED = "RATE_LIMITED"
    EXHAUSTED = "EXHAUSTED"


class CredentialState(str, Enum):
    ENABLED = "ENABLED"
    RESERVE = "RESERVE"
    DISABLED = "DISABLED"
    AUTH_ERROR = "AUTH_ERROR"


class ResetPolicyType(str, Enum):
    KNOWN_SCHEDULE = "known_schedule"
    OBSERVED = "observed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResetPolicy:
    """Quota reset semantics (§11). Default unknown — never assume."""

    type: ResetPolicyType = ResetPolicyType.UNKNOWN
    timezone: str | None = None
    time: str | None = None


@dataclass(frozen=True)
class StateSubject:
    """Scope of a state observation (§9 State Scope)."""

    provider_id: str
    credential_id: str | None = None
    concrete_model: str | None = None

    def __str__(self) -> str:
        parts = [self.provider_id]
        if self.credential_id:
            parts.append(f"cred={self.credential_id}")
        if self.concrete_model:
            parts.append(f"model={self.concrete_model}")
        return "/".join(parts)


@dataclass
class StateObservation:
    """One observation applied to a StateSubject."""

    subject: StateSubject
    observed_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    reason: str = ""
    #: optional cooldown until timestamp (health / rate-limit cooldowns)
    cooldown_until: datetime | None = None


@dataclass
class HealthObservation(StateObservation):
    state: HealthState = HealthState.UNKNOWN


@dataclass
class QuotaObservation(StateObservation):
    state: QuotaState = QuotaState.UNKNOWN
    reset_policy: ResetPolicy = ResetPolicy()


@dataclass
class CredentialObservation(StateObservation):
    state: CredentialState = CredentialState.ENABLED


@dataclass(frozen=True)
class QuotaInfo:
    """Result of ProviderAdapter.quota_info() (§11 quota sensor levels)."""

    level: Literal["EXACT", "HEADER_DERIVED", "LOCALLY_ESTIMATED", "ERROR_INFERRED", "UNKNOWN"] = "UNKNOWN"
    remaining: int | None = None
    limit: int | None = None
    reset_at: datetime | None = None
    raw: dict = field(default_factory=dict)
