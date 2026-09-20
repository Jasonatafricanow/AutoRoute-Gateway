"""Fallback engine tests (§7 + v0.3.1 errata P0-1/P0-2 + §8/§9)."""

import asyncio

import pytest

from gateway.domain.state import CredentialState, HealthState, QuotaState, StateSubject
from gateway.policy.error_classifier import ErrorClass
from gateway.providers.base import UpstreamHttpError
from gateway.routing import (
    CandidateFailure,
    FallbackEngine,
    HardAttemptCeilingError,
    NoEligibleCandidateError,
    build_candidates,
    score_candidates,
)
from gateway.state.store import InMemoryStateStore

from .fakes import completion_body, make_test_config


def _scored(virtual_model="gateway-fast", cfg=None):
    cfg = cfg or make_test_config()
    cands = build_candidates(virtual_model, cfg)
    return score_candidates(cands, InMemoryStateStore())


@pytest.mark.asyncio
async def test_success_first_candidate():
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()

    async def attempt(c):
        return completion_body("ok")

    result = await engine.run("gateway-fast", scored, attempt, "req-1")
    assert result.success is True
    assert result.candidate.provider_id == "amd"
    assert result.fallback_count == 0


@pytest.mark.asyncio
async def test_amd_down_falls_to_modelscope():
    """Acceptance case 2."""
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()
    order = iter(["amd", "modelscope"])

    async def attempt(c):
        name = next(order)
        assert c.provider_id == name
        if name == "amd":
            raise CandidateFailure(ErrorClass.NETWORK, message="connection refused")
        return completion_body("ok")

    result = await engine.run("gateway-fast", scored, attempt, "req-2")
    assert result.success is True
    assert result.candidate.provider_id == "modelscope"
    assert result.fallback_count == 1
    # amd provider health degraded at provider scope
    assert store.get_health(StateSubject(provider_id="amd")) == HealthState.DEGRADED


@pytest.mark.asyncio
async def test_auth_marks_credential_not_provider_and_continues():
    """P0-1: auth = credential AUTH_ERROR → next candidate, NOT global stop."""
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()
    called = []

    async def attempt(c):
        called.append(c.credential_id)
        if c.provider_id == "amd":
            raise CandidateFailure(ErrorClass.AUTH, status=401, message="invalid api key")
        return completion_body("ok")

    result = await engine.run("gateway-fast", scored, attempt, "req-3")
    assert result.success is True
    assert result.candidate.provider_id == "modelscope"
    cred = store.get_credential(StateSubject(provider_id="amd", credential_id="amd-01"))
    assert cred == CredentialState.AUTH_ERROR
    # provider itself untouched — no global stop happened
    assert store.get_health(StateSubject(provider_id="amd")) == HealthState.UNKNOWN


@pytest.mark.asyncio
async def test_quota_exhausted_scope_is_credential_only():
    """P0-3: key1 exhausted must not mark key2 or the provider."""
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()
    path = []

    async def attempt(c):
        path.append(c.credential_id)
        if c.credential_id == "ocg-01":
            raise CandidateFailure(ErrorClass.QUOTA, status=429, message="you exceeded your current quota")
        if c.credential_id == "ocg-02":
            return completion_body("ok")
        raise CandidateFailure(ErrorClass.SERVER)

    result = await engine.run("gateway-fast", scored, attempt, "req-4")
    assert result.success is True
    assert result.candidate.credential_id == "ocg-02"
    assert store.get_quota(StateSubject(provider_id="opencode", credential_id="ocg-01", concrete_model="DS-Flash")) == QuotaState.EXHAUSTED
    # key2 NOT exhausted
    assert store.get_quota(StateSubject(provider_id="opencode", credential_id="ocg-02", concrete_model="DS-Flash")) != QuotaState.EXHAUSTED
    # provider scope untouched
    assert store.get_quota(StateSubject(provider_id="opencode")) != QuotaState.EXHAUSTED


@pytest.mark.asyncio
async def test_token_limit_retries_same_candidate_then_moves_on():
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()
    n = {"amd": 0}

    async def attempt(c):
        if c.provider_id == "amd":
            n["amd"] += 1
            if n["amd"] < 3:  # initial + 2 same-candidate retries = 3 attempts total
                raise CandidateFailure(ErrorClass.TOKEN_LIMIT, status=400, message="maximum context length exceeded")
            return completion_body("ok")
        raise CandidateFailure(ErrorClass.SERVER)

    result = await engine.run("gateway-fast", scored, attempt, "req-5")
    assert result.success is True
    assert n["amd"] == 3
    assert result.attempts == 3
    assert result.fallback_count == 0  # same-candidate retries are not fallbacks


@pytest.mark.asyncio
async def test_stop_when_pool_exhausted():
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()

    async def attempt(c):
        raise CandidateFailure(ErrorClass.SERVER, status=500)

    with pytest.raises(NoEligibleCandidateError) as excinfo:
        await engine.run("gateway-fast", scored, attempt, "req-6")
    # amd + modelscope fail → provider-scope DEGRADED + cooldown; opencode's
    # ocg-01 fails → opencode provider degraded too, so the reserve key on the
    # SAME provider is also ineligible (shared endpoint) → stop after 3 attempts
    assert excinfo.value.attempts == 3
    assert excinfo.value.last_error == ErrorClass.SERVER


@pytest.mark.asyncio
async def test_committed_stream_failure_stops_immediately():
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()
    calls = []

    async def attempt(c):
        calls.append(c.provider_id)
        raise CandidateFailure(ErrorClass.NETWORK, committed=True, message="mid-stream")

    result = await engine.run("gateway-fast", scored, attempt, "req-7")
    assert result.success is False
    assert result.partial_stream_failure is True
    assert len(calls) == 1  # no cross-provider replay (§8)


@pytest.mark.asyncio
async def test_hard_ceiling_safety_valve():
    """Program-bug protection: unbounded same-candidate retries hit the ceiling."""
    store = InMemoryStateStore()
    from gateway.policy.retry_policy import RetryPolicy

    # allow effectively unbounded same-candidate retries so the ceiling is what stops us
    engine = FallbackEngine(store, retry_policy=RetryPolicy(per_candidate_retry_limit=1000, hard_attempt_ceiling=50))
    scored = _scored()

    async def attempt(c):
        raise CandidateFailure(ErrorClass.TOKEN_LIMIT, message="ctx")

    with pytest.raises(HardAttemptCeilingError):
        await engine.run("gateway-fast", scored, attempt, "req-8")


@pytest.mark.asyncio
async def test_rate_limited_candidate_cooldown_blocks_reuse():
    store = InMemoryStateStore()
    engine = FallbackEngine(store)
    scored = _scored()

    async def attempt(c):
        raise CandidateFailure(ErrorClass.RATE_LIMIT, status=429, message="rate limit")

    with pytest.raises(NoEligibleCandidateError):
        await engine.run("gateway-fast", scored, attempt, "req-9")
    subj = StateSubject(provider_id="amd", credential_id="amd-01", concrete_model="DS-Flash")
    assert store.get_quota(subj) == QuotaState.RATE_LIMITED
    assert store.get_cooldown(subj) is not None
