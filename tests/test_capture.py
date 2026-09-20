"""Phase 0 capture tests (§19): schema only — prompt content and secrets
must never land in the capture file."""

import json

from fastapi.testclient import TestClient

from gateway.capture import CaptureRecorder, create_capture_app, schema_of
from gateway.capture.schema_extract import header_schema, request_schema, response_schema

from .fakes import workdir


def test_schema_of_string_has_no_value():
    node = schema_of("hello secret world")
    assert node == {"type": "string", "length": 18}
    assert "value" not in node


def test_auth_header_redacted():
    headers = {"Authorization": "Bearer sk-abc123", "Content-Type": "application/json", "X-Foo": "bar"}
    schema = header_schema(headers)
    assert schema["Authorization"] == "<redacted>"
    assert schema["Content-Type"] == {"length": 16}
    assert "sk-abc123" not in json.dumps(schema)


def test_request_schema_keeps_no_prompt_content():
    payload = {
        "model": "gateway-fast",
        "messages": [
            {"role": "system", "content": "TOP SECRET system prompt"},
            {"role": "user", "content": "user message with private data"},
        ],
        "stream": True,
        "temperature": 0.7,
    }
    schema = request_schema(payload)
    blob = json.dumps(schema, ensure_ascii=False)
    assert "TOP SECRET" not in blob
    assert "private data" not in blob
    # but structure is recorded
    assert schema["messages"]["roles"] == ["system", "user"]
    assert schema["messages"]["content_blocks"][0]["content"] == {"type": "text", "length": 24}


def test_response_schema_shape_only():
    body = {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "model": "gateway-fast",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "sensitive answer text"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    schema = response_schema(body)
    blob = json.dumps(schema, ensure_ascii=False)
    assert "sensitive answer text" not in blob
    assert schema["choices"]["first"]["finish_reason"] == "stop"


def _client(dir_path):
    recorder = CaptureRecorder(dir_path / "capture.jsonl")
    app = create_capture_app(recorder, upstream=None)
    return TestClient(app), recorder


def test_capture_app_records_request_and_synthetic_response():
    dir_path = workdir("capture")
    client, recorder = _client(dir_path)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gateway-fast", "messages": [{"role": "user", "content": "my private prompt"}]},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "recording gateway synthetic response"

    lines = [json.loads(l) for l in (dir_path / "capture.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = {l["kind"] for l in lines}
    assert kinds == {"request", "response"}
    blob = (dir_path / "capture.jsonl").read_text(encoding="utf-8")
    assert "my private prompt" not in blob
    assert "Bearer secret" not in blob
    assert "secret" not in blob.lower()


def test_capture_app_stream_synthetic():
    dir_path = workdir("capture-stream")
    client, recorder = _client(dir_path)
    resp = client.post("/v1/chat/completions", json={"model": "gateway-fast", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert resp.status_code == 200
    body = resp.text
    assert "data: [DONE]" in body
    assert "recording gateway synthetic response" in body
    lines = [json.loads(l) for l in (dir_path / "capture.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = {l["kind"] for l in lines}
    assert {"request", "sse_first_chunk", "sse_done"} <= kinds
