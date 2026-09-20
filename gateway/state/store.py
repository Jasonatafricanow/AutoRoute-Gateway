"""StateStore protocol: what the routing layer needs from the state layer.

Production implementation is SQLite (gateway/state/sqlite.py); tests use an
in-memory fake. All observations are bound to a StateSubject scope (§9).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain.state import (
    CredentialState,
    HealthState,
    QuotaState,
    StateSubject,
)


class StateStore(Protocol):
    """Runtime state (gateway.db). Never configuration, never secrets."""

    # --- health ---
    def get_health(self, subject: StateSubject) -> HealthState: ...
    def set_health(self, subject: StateSubject, state: HealthState, reason: str = "", cooldown_until: datetime | None = None) -> None: ...

    # --- quota ---
    def get_quota(self, subject: StateSubject) -> QuotaState: ...
    def set_quota(self, subject: StateSubject, state: QuotaState, reason: str = "", cooldown_until: datetime | None = None) -> None: ...

    # --- credential ---
    def get_credential(self, subject: StateSubject) -> CredentialState: ...
    def set_credential(self, subject: StateSubject, state: CredentialState, reason: str = "") -> None: ...

    # --- cooldown (max across health/quota for the subject) ---
    def get_cooldown(self, subject: StateSubject) -> datetime | None: ...

    # --- route events (observability §15) ---
    def record_route_event(self, event: dict) -> None: ...

    # --- health/status endpoints ---
    def snapshot(self) -> dict: ...


class InMemoryStateStore:
    """Non-persistent store for tests and dev."""

    def __init__(self) -> None:
        self._health: dict[tuple, tuple[HealthState, datetime | None]] = {}
        self._quota: dict[tuple, tuple[QuotaState, datetime | None]] = {}
        self._credential: dict[tuple, CredentialState] = {}
        self.events: list[dict] = []

    @staticmethod
    def _key(subject: StateSubject) -> tuple:
        return (subject.provider_id, subject.credential_id, subject.concrete_model)

    def _fallback_get(self, mapping: dict, subject: StateSubject):
        """Most-specific → least-specific scope lookup (§9 scope isolation).

        health: (p,c,m) → (p,m) → (p)
        quota:  (p,c,m) → (p,c) → (p)
        A provider-scope DOWN must be visible to every key of that provider.
        """
        keys = [(subject.provider_id, subject.credential_id, subject.concrete_model),
                (subject.provider_id, None, subject.concrete_model),
                (subject.provider_id, subject.credential_id, None),
                (subject.provider_id, None, None)]
        for k in keys:
            if k in mapping:
                return mapping[k]
        return None

    def get_health(self, subject: StateSubject) -> HealthState:
        row = self._fallback_get(self._health, subject)
        return row[0] if row else HealthState.UNKNOWN

    def set_health(self, subject, state, reason="", cooldown_until=None):
        self._health[self._key(subject)] = (state, cooldown_until)

    def get_quota(self, subject: StateSubject) -> QuotaState:
        row = self._fallback_get(self._quota, subject)
        return row[0] if row else QuotaState.UNKNOWN

    def set_quota(self, subject, state, reason="", cooldown_until=None):
        self._quota[self._key(subject)] = (state, cooldown_until)

    def get_credential(self, subject: StateSubject) -> CredentialState:
        return self._credential.get(
            (subject.provider_id, subject.credential_id), CredentialState.ENABLED
        )

    def set_credential(self, subject, state, reason=""):
        self._credential[(subject.provider_id, subject.credential_id)] = state

    def get_cooldown(self, subject: StateSubject) -> datetime | None:
        health_row = self._fallback_get(self._health, subject)
        quota_row = self._fallback_get(self._quota, subject)
        times = []
        if health_row and health_row[1]:
            times.append(health_row[1])
        if quota_row and quota_row[1]:
            times.append(quota_row[1])
        return max(times) if times else None

    def record_route_event(self, event: dict) -> None:
        self.events.append(dict(event))

    def snapshot(self) -> dict:
        return {
            "health": {str(k): v[0].value for k, v in self._health.items()},
            "quota": {str(k): v[0].value for k, v in self._quota.items()},
            "credential": {str(k): v.value for k, v in self._credential.items()},
            "events": list(self.events),
        }
