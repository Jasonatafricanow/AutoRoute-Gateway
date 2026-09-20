"""Scorer tests (§9): three orthogonal dimensions, scope isolation.

The most dangerous implementation error (P0-3): key1 exhausted must never
mark the whole provider EXHAUSTED.
"""

from gateway.domain.state import CredentialState, HealthState, QuotaState, StateSubject
from gateway.routing import build_candidates, score_candidates
from gateway.state.store import InMemoryStateStore

from .fakes import make_test_config


def test_default_ordering_healthy_first():
    cfg = make_test_config()
    cands = build_candidates("gateway-fast", cfg)
    store = InMemoryStateStore()
    scored = score_candidates(cands, store)
    assert [s.candidate.provider_id for s in scored[:2]] == ["amd", "modelscope"]


def test_key1_exhausted_does_not_kill_key2():
    cfg = make_test_config()
    cands = build_candidates("gateway-fast", cfg)
    store = InMemoryStateStore()
    # key1 quota exhausted at credential scope
    store.set_quota(
        StateSubject(provider_id="opencode", credential_id="ocg-01", concrete_model="DS-Flash"),
        QuotaState.EXHAUSTED,
    )
    scored = score_candidates(cands, store)
    ocg1 = next(s for s in scored if s.candidate.credential_id == "ocg-01")
    ocg2 = next(s for s in scored if s.candidate.credential_id == "ocg-02")
    assert ocg1.usable is False
    # key2 (reserve) remains usable — it can take over (§10)
    assert ocg2.usable is True
    # amd and modelscope unaffected by the opencode credential observation
    amd = next(s for s in scored if s.candidate.provider_id == "amd")
    assert amd.usable is True


def test_provider_down_excludes_all_keys_of_provider():
    cfg = make_test_config()
    cands = build_candidates("gateway-fast", cfg)
    store = InMemoryStateStore()
    store.set_health(StateSubject(provider_id="amd", concrete_model="DS-Flash"), HealthState.DOWN)
    scored = score_candidates(cands, store)
    for s in scored:
        if s.candidate.provider_id == "amd":
            assert s.usable is False


def test_auth_error_credential_is_unusable_but_provider_healthy():
    cfg = make_test_config()
    cands = build_candidates("gateway-fast", cfg)
    store = InMemoryStateStore()
    store.set_credential(StateSubject(provider_id="opencode", credential_id="ocg-01"), CredentialState.AUTH_ERROR)
    scored = score_candidates(cands, store)
    ocg1 = next(s for s in scored if s.candidate.credential_id == "ocg-01")
    ocg2 = next(s for s in scored if s.candidate.credential_id == "ocg-02")
    assert ocg1.usable is False
    assert ocg2.usable is True


def test_three_states_are_orthogonal_on_one_subject():
    """One key can be HEALTHY + LOW + ENABLED at the same time (§9)."""
    cfg = make_test_config()
    cands = build_candidates("gateway-fast", cfg)
    store = InMemoryStateStore()
    subj = StateSubject(provider_id="amd", credential_id="amd-01", concrete_model="DS-Flash")
    store.set_health(subj, HealthState.HEALTHY)
    store.set_quota(subj, QuotaState.LOW)
    store.set_credential(subj, CredentialState.ENABLED)
    scored = score_candidates(cands, store)
    amd = next(s for s in scored if s.candidate.provider_id == "amd")
    assert amd.usable is True
    assert "quota=LOW" in amd.reason
