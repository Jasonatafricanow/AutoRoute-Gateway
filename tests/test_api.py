"""API-level tests: acceptance cases 1-4, 8-9 (§23) through the FastAPI app
with scripted fake adapters (no network)."""

import json

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.domain.state import CredentialState, QuotaState, StateSubject
from gateway.providers.base import UpstreamHttpError
from gateway.service import GatewayService
from gateway.state.store import InMemoryStateStore

from .fakes import FakeAdapter, FakeStream, chunk, completion_body, make_test_config, make_test_resolver


def _make_service(store=None):
    config = make_test_config()
    store = store or InMemoryStateStore()
    service = GatewayService(config, make_test_resolver(), store)
    # replace real adapters with fakes
    service._adapters = {
        "amd": FakeAdapter("amd"),
        "modelscope": FakeAdapter("modelscope"),
        "opencode": FakeAdapter("opencode"),
    }
    return config, service, store


def _client(config, service):
    app = create_app(config, make_test_resolver(), service.store, service=service)
    return TestClient(app)


def test_acceptance_case1_amd_normal():
    config, service, store = _make_service()
    service._adapters["amd"].script = [completion_body("amd-ok")]
    client = _client(config, service)
    resp = client.post("/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "amd-ok"
    # consumer sees the virtual model, never the concrete resource (§20)
    assert body["model"] == "gateway-fast"
    # correct key was used for the correct provider
    assert service._adapters["amd"].secrets_seen == ["k-amd"]


def test_acceptance_case2_amd_down_modelscope_success():
    config, service, store = _make_service()
    service._adapters["amd"].script = [UpstreamHttpError(500, "upstream exploded")]
    service._adapters["modelscope"].script = [completion_body("ms-ok")]
    client = _client(config, service)
    resp = client.post("/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "ms-ok"
    assert store.get_health(StateSubject(provider_id="amd")) != "UNKNOWN"


def test_acceptance_case3_free_all_down_opencode_success():
    config, service, store = _make_service()
    service._adapters["amd"].script = [UpstreamHttpError(500, "down")]
    service._adapters["modelscope"].script = [UpstreamHttpError(500, "down")]
    service._adapters["opencode"].script = [completion_body("ocg-ok")]
    client = _client(config, service)
    resp = client.post("/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "ocg-ok"


def test_acceptance_case4_key1_exhausted_key2_takes_over():
    config, service, store = _make_service()
    # make free providers unavailable so the opencode pool is reached
    service._adapters["amd"].script = [UpstreamHttpError(500, "down")]
    service._adapters["modelscope"].script = [UpstreamHttpError(500, "down")]
    service._adapters["opencode"].script = [
        UpstreamHttpError(429, "you exceeded your current quota, please check your plan and billing details"),
        completion_body("reserve-key-ok"),
    ]
    client = _client(config, service)
    resp = client.post("/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "reserve-key-ok"
    # ocg-01 exhausted at credential scope; ocg-02 unaffected and used
    calls = [c for c in service._adapters["opencode"].calls]
    assert calls[0][1] == "DS-Flash"
    assert service._adapters["opencode"].secrets_seen == ["k-ocg1", "k-ocg2"]
    assert store.get_quota(StateSubject(provider_id="opencode", credential_id="ocg-01", concrete_model="DS-Flash")) == QuotaState.EXHAUSTED


def test_all_candidates_fail_returns_502():
    config, service, store = _make_service()
    for a in service._adapters.values():
        a.script = [UpstreamHttpError(500, "down")]
    client = _client(config, service)
    resp = client.post("/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "server"


def test_unknown_virtual_model_404():
    config, service, store = _make_service()
    client = _client(config, service)
    resp = client.post("/v1/chat/completions", json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 404


def test_stream_precommit_failure_falls_back_transparently():
    """Acceptance case 9: upstream dies before the first chunk → transparent fallback."""
    config, service, store = _make_service()
    service._adapters["amd"].script = [UpstreamHttpError(500, "died instantly")]
    service._adapters["modelscope"].script = [
        FakeStream([chunk("", role="assistant"), chunk("hello"), chunk("", finish="stop")])
    ]
    client = _client(config, service)
    with client.stream("POST", "/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}], "stream": True}) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "hello" in text
    assert "data: [DONE]" in text
    # modelscope really was used
    assert service._adapters["modelscope"].calls[0][0] == "stream"


def test_stream_postcommit_failure_terminates_without_replay():
    """Acceptance case 8: ≥1 chunk already sent → terminate, NO cross-provider replay."""
    config, service, store = _make_service()
    service._adapters["amd"].script = [
        FakeStream([chunk("", role="assistant"), chunk("partial output"), chunk("more")], fail_after=UpstreamHttpError(500, "mid-stream death"))
    ]
    # modelscope would happily answer — but it must NOT be called
    service._adapters["modelscope"].script = [FakeStream([chunk("SHOULD-NOT-APPEAR")])]
    client = _client(config, service)
    with client.stream("POST", "/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}], "stream": True}) as resp:
        text = "".join(resp.iter_text())
    assert "partial output" in text
    assert "SHOULD-NOT-APPEAR" not in text
    # no [DONE] on a partial stream (§8 terminate semantics)
    assert "data: [DONE]" not in text
    assert service._adapters["modelscope"].calls == []
    # PARTIAL_STREAM_FAILURE recorded
    events = store.events
    assert any(e.get("action") == "partial_stream_failure" for e in events)


def test_v1_models_and_status_endpoints():
    config, service, store = _make_service()
    client = _client(config, service)
    assert client.get("/v1/models").json()["data"][0]["id"] == "gateway-fast"
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    assert client.get("/routes").json()["gateway-fast"][0]["provider"] == "amd"
    providers = client.get("/providers").json()
    assert providers["opencode"]["keys"][1]["reserve"] is True


def test_auth_token_required_when_configured():
    from gateway.config.schema import ServerConfig

    config = make_test_config()
    config = type(config)(
        routes=config.routes, providers=config.providers,
        server=ServerConfig(bind="127.0.0.1", port=8700, database_path=":memory:", auth_token="tok-123"),
        capability_matrix_path=None, codex_enabled=False, log_dir="logs",
    )
    service = GatewayService(config, make_test_resolver(), InMemoryStateStore())
    service._adapters = {"amd": FakeAdapter("amd", script=[completion_body("ok")])}
    app = create_app(config, make_test_resolver(), service.store, service=service)
    client = TestClient(app)
    assert client.post("/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "x"}]}).status_code == 401
    ok = client.post(
        "/v1/chat/completions",
        json={"model": "gateway-fast", "messages": [{"role": "user", "content": "x"}]},
        headers={"Authorization": "Bearer tok-123"},
    )
    assert ok.status_code == 200


def test_structured_output_capability_routing():
    """response_format requires structured_output; only gateway-deep/Pro has it."""
    config, service, store = _make_service()
    service._adapters["opencode"].script = [completion_body("json-ok")]
    client = _client(config, service)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gateway-deep",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "json_object"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "json-ok"
    # only opencode was reached (Pro is the only structured_output-capable model)
    assert service._adapters["amd"].calls == []
    assert service._adapters["modelscope"].calls == []


def test_structured_output_unavailable_400():
    config, service, store = _make_service()
    client = _client(config, service)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gateway-fast",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "json_object"},
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "capability_unsatisfiable"


def test_tools_request_no_capable_provider_400():
    """gateway-fast's DS-Flash pool lacks tools → capability gate → 400."""
    config, service, store = _make_service()
    client = _client(config, service)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gateway-fast",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "capability_unsatisfiable"
    assert service._adapters["amd"].calls == []


def test_tools_request_routes_to_capable_provider():
    """gateway-deep/Pro declares tools → routed to opencode."""
    config, service, store = _make_service()
    service._adapters["opencode"].script = [completion_body("tool-ok")]
    client = _client(config, service)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gateway-deep",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "tool-ok"
