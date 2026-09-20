"""Scorer (§6): ranks candidates by the three orthogonal state dimensions.

health / quota / credential states are read per StateSubject scope. The router
only sorts — it never decides what happens after a failure (Fallback Engine).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.candidate import RouteCandidate
from ..domain.state import CredentialState, HealthState, QuotaState, StateSubject
from ..state.store import StateStore

#: weights (higher = better). State dimensions are orthogonal (§9).
_HEALTH_SCORE = {HealthState.HEALTHY: 1.0, HealthState.UNKNOWN: 0.7, HealthState.DEGRADED: 0.4, HealthState.DOWN: -1.0}
_QUOTA_SCORE = {QuotaState.AVAILABLE: 1.0, QuotaState.UNKNOWN: 0.8, QuotaState.LOW: 0.6, QuotaState.RATE_LIMITED: 0.2, QuotaState.EXHAUSTED: -1.0}
_CREDENTIAL_SCORE = {CredentialState.ENABLED: 1.0, CredentialState.RESERVE: 0.9, CredentialState.DISABLED: -1.0, CredentialState.AUTH_ERROR: -1.0}


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: RouteCandidate
    score: float
    usable: bool
    reason: str


def score_candidates(candidates: list[RouteCandidate], store: StateStore) -> list[ScoredCandidate]:
    """Score and order candidates; usable candidates first, reserve keys last."""

    def subject(c: RouteCandidate) -> StateSubject:
        return StateSubject(
            provider_id=c.provider_id,
            credential_id=c.credential_id,
            concrete_model=c.concrete_model.model_id,
        )

    scored: list[ScoredCandidate] = []
    for c in candidates:
        subj = subject(c)
        health = store.get_health(subj)
        quota = store.get_quota(subj)
        credential = store.get_credential(subj)

        hs, qs, cs = _HEALTH_SCORE[health], _QUOTA_SCORE[quota], _CREDENTIAL_SCORE[credential]
        usable = hs >= 0 and qs >= 0 and cs >= 0
        score = (hs * 0.5) + (qs * 0.3) + (cs * 0.2)
        # reserve keys are preserved: usable but always ordered after non-reserve (§10)
        if c.reserve:
            score -= 0.5
        reasons = []
        if health != HealthState.HEALTHY:
            reasons.append(f"health={health.value}")
        if quota not in (QuotaState.AVAILABLE, QuotaState.UNKNOWN):
            reasons.append(f"quota={quota.value}")
        if credential not in (CredentialState.ENABLED, CredentialState.RESERVE):
            reasons.append(f"credential={credential.value}")
        scored.append(ScoredCandidate(candidate=c, score=score, usable=usable, reason=",".join(reasons) or "ok"))

    # stable ordering: usable first, reserve keys last; both groups keep the
    # config route order (input order). State dimensions only gate usability —
    # they never reorder the configured fallback chain, otherwise a provider
    # that succeeded once (HEALTHY) would jump ahead of the configured chain.
    usable_regular = [s for s in scored if s.usable and not s.candidate.reserve]
    usable_reserve = [s for s in scored if s.usable and s.candidate.reserve]
    unusable = [s for s in scored if not s.usable]
    return usable_regular + usable_reserve + unusable
