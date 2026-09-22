"""SQLite state store tests (§9/§14): scope-bound rows, cooldown, snapshot."""

import pytest

from gateway.domain.state import CredentialState, HealthState, QuotaState, StateSubject
from gateway.state.sqlite import SqliteStateStore

from .fakes import workdir


@pytest.fixture()
def store():
    s = SqliteStateStore(workdir("sqlite") / "test.db")
    yield s
    s.close()


def test_scope_isolation(store):
    """P0-3: key-level observations never bleed into provider scope."""
    key1 = StateSubject(provider_id="opencode", credential_id="ocg-01", concrete_model="DS-Flash")
    key2 = StateSubject(provider_id="opencode", credential_id="ocg-02", concrete_model="DS-Flash")
    provider = StateSubject(provider_id="opencode")

    store.set_quota(key1, QuotaState.EXHAUSTED, reason="quota")
    assert store.get_quota(key1) == QuotaState.EXHAUSTED
    assert store.get_quota(key2) == QuotaState.UNKNOWN
    assert store.get_quota(provider) == QuotaState.UNKNOWN


def test_cooldown_and_events(store):
    subj = StateSubject(provider_id="amd", credential_id="amd-01", concrete_model="DS-Flash")
    from datetime import datetime, timedelta, timezone

    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    store.set_quota(subj, QuotaState.RATE_LIMITED, cooldown_until=future)
    cd = store.get_cooldown(subj)
    assert cd is not None and cd > datetime.now(timezone.utc)

    store.record_route_event({"request_id": "r1", "virtual_model": "gateway-fast", "attempt": 1, "provider_id": "amd", "error_class": "rate_limit", "action": "next_candidate"})
    snap = store.snapshot()
    assert snap["quota"][0]["state"] == "RATE_LIMITED"
    assert len(snap["credential"]) == 0


def test_credential_state_update(store):
    subj = StateSubject(provider_id="opencode", credential_id="ocg-01")
    store.set_credential(subj, CredentialState.AUTH_ERROR, reason="401")
    assert store.get_credential(subj) == CredentialState.AUTH_ERROR
    # different key unaffected
    assert store.get_credential(StateSubject(provider_id="opencode", credential_id="ocg-02")) == CredentialState.ENABLED


def test_health_default_unknown(store):
    assert store.get_health(StateSubject(provider_id="amd")) == HealthState.UNKNOWN

def test_provider_level_health_upsert_has_single_row(store):
    provider = StateSubject(provider_id="amd")
    store.set_health(provider, HealthState.DEGRADED, reason="first")
    store.set_health(provider, HealthState.HEALTHY, reason="second")

    snap = store.snapshot()
    rows = [r for r in snap["health"] if r["provider_id"] == "amd" and r["model"] is None]
    assert len(rows) == 1
    assert rows[0]["state"] == "HEALTHY"


def test_provider_level_quota_upsert_has_single_row(store):
    provider = StateSubject(provider_id="amd")
    store.set_quota(provider, QuotaState.RATE_LIMITED, reason="first")
    store.set_quota(provider, QuotaState.EXHAUSTED, reason="second")

    snap = store.snapshot()
    rows = [
        r for r in snap["quota"]
        if r["provider_id"] == "amd"
        and r["credential_id"] is None
        and r["model"] is None
    ]
    assert len(rows) == 1
    assert rows[0]["state"] == "EXHAUSTED"
