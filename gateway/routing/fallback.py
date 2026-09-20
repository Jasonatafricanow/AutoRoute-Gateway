"""Fallback Engine (§7): decides continue/stop after candidate failures.

Frozen semantics:
- retry same candidate only for token_limit, bounded by per_candidate_retry_limit
- auth → credential AUTH_ERROR (provider+credential scope) → next candidate, NOT global stop
- model_not_found → provider+model scope DOWN → next candidate
- rate_limit (429 short-term) → cooldown on provider+credential+model scope → next candidate
- quota exhausted → provider+credential scope EXHAUSTED → next candidate
- network/server → provider health DEGRADED with cooldown → next candidate
- stop only when no eligible candidate remains or hard_attempt_ceiling is hit
- streaming commit semantics (§8): failure after ≥1 downstream chunk = partial
  stream failure → immediate stop, no cross-provider replay
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..domain.candidate import RouteCandidate
from ..domain.state import CredentialState, HealthState, QuotaState, StateSubject
from ..policy.error_classifier import ErrorClass
from ..policy.retry_policy import FallbackAction, RetryPolicy, next_action
from ..state.store import StateStore
from .scorer import ScoredCandidate


class NoEligibleCandidateError(RuntimeError):
    """All candidates were tried (or became ineligible) without success."""

    def __init__(self, virtual_model: str, attempts: int, last_error: ErrorClass | None = None):
        super().__init__(f"no eligible candidate left for {virtual_model!r} after {attempts} attempts (last error: {last_error})")
        self.virtual_model = virtual_model
        self.attempts = attempts
        self.last_error = last_error


class HardAttemptCeilingError(RuntimeError):
    """Safety valve (§7): program-bug protection, not a policy cap."""


@dataclass
class CandidateFailure(Exception):
    """A classified candidate failure raised by attempt_fn."""

    error_class: ErrorClass
    status: int | None = None
    message: str = ""
    #: True when the failure happened after ≥1 downstream chunk was sent (§8)
    committed: bool = False
    retry_after_seconds: float | None = None


@dataclass
class FallbackResult:
    success: bool
    virtual_model: str
    candidate: RouteCandidate | None
    attempts: int
    fallback_count: int
    events: list[dict] = field(default_factory=list)
    error_class: ErrorClass | None = None
    partial_stream_failure: bool = False


class FallbackEngine:
    """Runs candidates in order until success or stop. Router only sorts;
    this engine decides what happens after each failure (§6/§7)."""

    def __init__(
        self,
        store: StateStore,
        retry_policy: RetryPolicy | None = None,
        rate_limit_cooldown_seconds: float = 60.0,
        network_cooldown_seconds: float = 30.0,
        server_cooldown_seconds: float = 30.0,
        clock=time.monotonic,
    ):
        self._store = store
        self._policy = retry_policy or RetryPolicy()
        self._rate_limit_cooldown = rate_limit_cooldown_seconds
        self._network_cooldown = network_cooldown_seconds
        self._server_cooldown = server_cooldown_seconds
        self._clock = clock

    @staticmethod
    def subject(candidate: RouteCandidate) -> StateSubject:
        return StateSubject(
            provider_id=candidate.provider_id,
            credential_id=candidate.credential_id,
            concrete_model=candidate.concrete_model.model_id,
        )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _cooldown(self, seconds: float, extra: float = 0.0) -> datetime:
        return self._now() + timedelta(seconds=seconds + extra)

    def _eligible(self, candidate: RouteCandidate) -> bool:
        subj = self.subject(candidate)
        health = self._store.get_health(subj)
        quota = self._store.get_quota(subj)
        credential = self._store.get_credential(subj)
        if health == HealthState.DOWN:
            return False
        if quota == QuotaState.EXHAUSTED:
            return False
        if credential in (CredentialState.DISABLED, CredentialState.AUTH_ERROR):
            return False
        cooldown = self._store.get_cooldown(subj)
        if cooldown is not None and cooldown > self._now():
            return False
        return True

    def _apply_failure(self, candidate: RouteCandidate, failure: CandidateFailure) -> None:
        subj = self.subject(candidate)
        provider_subj = StateSubject(provider_id=candidate.provider_id)
        provider_model_subj = StateSubject(
            provider_id=candidate.provider_id, concrete_model=candidate.concrete_model.model_id
        )
        if failure.error_class == ErrorClass.AUTH:
            # v0.3.1 P0-1: disable THIS credential only, never the provider
            self._store.set_credential(
                StateSubject(provider_id=candidate.provider_id, credential_id=candidate.credential_id),
                CredentialState.AUTH_ERROR,
                reason=failure.message or "auth",
            )
        elif failure.error_class == ErrorClass.QUOTA:
            self._store.set_quota(
                StateSubject(provider_id=candidate.provider_id, credential_id=candidate.credential_id),
                QuotaState.EXHAUSTED,
                reason=failure.message or "quota exhausted",
            )
        elif failure.error_class == ErrorClass.RATE_LIMIT:
            extra = failure.retry_after_seconds or 0.0
            self._store.set_quota(
                subj,
                QuotaState.RATE_LIMITED,
                reason=failure.message or "rate limit",
                cooldown_until=self._cooldown(self._rate_limit_cooldown, extra),
            )
        elif failure.error_class == ErrorClass.MODEL_NOT_FOUND:
            self._store.set_health(
                provider_model_subj,
                HealthState.DOWN,
                reason=failure.message or "model not found",
            )
        elif failure.error_class == ErrorClass.NETWORK:
            self._store.set_health(
                provider_subj,
                HealthState.DEGRADED,
                reason=failure.message or "network",
                cooldown_until=self._cooldown(self._network_cooldown),
            )
        elif failure.error_class == ErrorClass.SERVER:
            self._store.set_health(
                provider_subj,
                HealthState.DEGRADED,
                reason=failure.message or "server",
                cooldown_until=self._cooldown(self._server_cooldown),
            )
        # token_limit / unknown: no state mutation

    def _apply_success(self, candidate: RouteCandidate) -> None:
        subj = StateSubject(
            provider_id=candidate.provider_id,
            concrete_model=candidate.concrete_model.model_id,
        )
        # success observation: provider+model scope is healthy (§9 scope isolation)
        self._store.set_health(subj, HealthState.HEALTHY, reason="success")

    async def run(self, virtual_model: str, scored: list[ScoredCandidate], attempt_fn, request_id: str = "") -> FallbackResult:
        """Attempt candidates in order. attempt_fn(candidate) -> result or raises CandidateFailure."""
        events: list[dict] = []
        attempts = 0
        fallback_count = 0
        last_error: ErrorClass | None = None

        for sc in scored:
            candidate = sc.candidate
            if not sc.usable or not self._eligible(candidate):
                continue
            per_candidate_retries = 0
            while True:
                if attempts >= self._policy.hard_attempt_ceiling:
                    raise HardAttemptCeilingError(
                        f"hard_attempt_ceiling {self._policy.hard_attempt_ceiling} reached for {virtual_model!r}"
                    )
                attempts += 1
                start = self._clock()
                try:
                    result = await attempt_fn(candidate)
                    self._apply_success(candidate)
                    events.append(self._event(request_id, virtual_model, candidate, attempts, None, "success", self._clock() - start))
                    self._store.record_route_event(events[-1])
                    return FallbackResult(
                        success=True, virtual_model=virtual_model, candidate=candidate,
                        attempts=attempts, fallback_count=fallback_count, events=events,
                    )
                except CandidateFailure as failure:
                    latency = self._clock() - start
                    if failure.committed:
                        # §8: STREAM_COMMITTED — terminate, no cross-provider replay
                        events.append(self._event(request_id, virtual_model, candidate, attempts, failure.error_class, "stop_committed_stream", latency))
                        self._store.record_route_event(events[-1])
                        return FallbackResult(
                            success=False, virtual_model=virtual_model, candidate=candidate,
                            attempts=attempts, fallback_count=fallback_count, events=events,
                            error_class=failure.error_class, partial_stream_failure=True,
                        )
                    self._apply_failure(candidate, failure)
                    action = next_action(failure.error_class, per_candidate_retries, self._policy)
                    events.append(self._event(request_id, virtual_model, candidate, attempts, failure.error_class, action.value, latency))
                    self._store.record_route_event(events[-1])
                    last_error = failure.error_class
                    if action == FallbackAction.RETRY_SAME_CANDIDATE:
                        per_candidate_retries += 1
                        continue
                    fallback_count += 1
                    if (
                        self._policy.route_candidate_limit is not None
                        and fallback_count >= self._policy.route_candidate_limit
                    ):
                        raise NoEligibleCandidateError(virtual_model, attempts, last_error)
                    break
                except Exception as exc:  # noqa: BLE001 — attempt_fn contract breach is treated as server-class
                    latency = self._clock() - start
                    failure = CandidateFailure(ErrorClass.SERVER, message=str(exc)[:200])
                    self._apply_failure(candidate, failure)
                    events.append(self._event(request_id, virtual_model, candidate, attempts, ErrorClass.SERVER, "next_candidate", latency))
                    self._store.record_route_event(events[-1])
                    last_error = ErrorClass.SERVER
                    fallback_count += 1
                    break
        raise NoEligibleCandidateError(virtual_model, attempts, last_error)

    @staticmethod
    def _event(request_id: str, virtual_model: str, candidate: RouteCandidate, attempt: int, error_class: ErrorClass | None, action: str, latency: float) -> dict:
        return {
            "request_id": request_id,
            "virtual_model": virtual_model,
            "attempt": attempt,
            "provider_id": candidate.provider_id,
            "credential_id": candidate.credential_id,
            "model": candidate.concrete_model.model_id,
            "error_class": error_class.value if error_class else None,
            "action": action,
            "latency_ms": round(latency * 1000, 1),
        }
