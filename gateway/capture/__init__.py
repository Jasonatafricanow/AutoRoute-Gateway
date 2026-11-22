"""Phase 0 shape-only recording gateway.

This package intentionally stores schemas/lengths rather than prompt, response,
or credential values.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .schema_extract import header_schema, request_schema, response_schema, schema_of


class CaptureRecorder:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def record(self, kind: str, schema: dict[str, Any]) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "schema": schema,
        }
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")


def _synthetic_response(model: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-capture",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "recording gateway synthetic response",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _forward_headers(request: Request) -> dict[str, str]:
    blocked = {"host", "content-length", "connection"}
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in blocked
    }


def create_capture_app(
    recorder: CaptureRecorder,
    upstream: str | None = None,
) -> FastAPI:
    app = FastAPI(title="AutoRoute Capture Gateway")

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        payload = await request.json()
        recorder.record(
            "request",
            {
                "headers": header_schema(dict(request.headers)),
                "body": request_schema(payload),
            },
        )

        stream = bool(payload.get("stream"))
        if upstream:
            target = f"{upstream.rstrip('/')}/v1/chat/completions"
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    target,
                    json=payload,
                    headers=_forward_headers(request),
                )

            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict):
                    recorder.record("response", response_schema(body))
            elif stream or "text/event-stream" in content_type:
                recorder.record(
                    "sse_first_chunk",
                    {"bytes": len(response.content), "content_type": content_type},
                )
                if "[DONE]" in response.text:
                    recorder.record("sse_done", {"seen": True})

            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=content_type or None,
            )

        synthetic = _synthetic_response(str(payload.get("model") or "capture"))
        if not stream:
            recorder.record("response", response_schema(synthetic))
            return JSONResponse(synthetic)

        first = {
            "id": "chatcmpl-capture",
            "object": "chat.completion.chunk",
            "model": synthetic["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "recording gateway synthetic response",
                    },
                    "finish_reason": None,
                }
            ],
        }
        last = {
            "id": "chatcmpl-capture",
            "object": "chat.completion.chunk",
            "model": synthetic["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        recorder.record("sse_first_chunk", response_schema(first))
        recorder.record("sse_done", {"seen": True})

        async def event_stream():
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(last, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


__all__ = [
    "CaptureRecorder",
    "create_capture_app",
    "header_schema",
    "request_schema",
    "response_schema",
    "schema_of",
]
