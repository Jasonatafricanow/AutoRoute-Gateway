"""OpenAI-compatible HTTP surface (/v1/*) — Phase 1 MVP (§20).

POST /v1/chat/completions: non-stream + stream (SSE)
GET  /v1/models: virtual models
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..routing import NoEligibleCandidateError, UnknownVirtualModelError
from ..service import GatewayService, PartialStreamFailure, RequestValidationError

log = logging.getLogger("gateway")
router = APIRouter()


def _error_json(status: int, message: str, error_class: str | None = None) -> JSONResponse:
    body = {"error": {"message": message, "type": "gateway_error", "code": error_class or "gateway_error"}}
    return JSONResponse(status_code=status, content=body)


@router.post("/chat/completions")
async def chat_completions(request: Request):
    service: GatewayService = request.app.state.service
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _error_json(400, "invalid JSON body")

    if not isinstance(payload, dict):
        return _error_json(400, "body must be a JSON object")

    virtual_model = payload.get("model")
    if not virtual_model or not isinstance(virtual_model, str):
        return _error_json(400, "missing or invalid 'model' (virtual model name)")

    stream = bool(payload.get("stream"))

    try:
        if stream:
            async def sse() -> str:
                try:
                    async for event in service.chat_stream(virtual_model, payload):
                        data = event.get("data")
                        if data == "[DONE]":
                            yield "data: [DONE]\n\n"
                            return
                        if isinstance(data, dict):
                            data = dict(data)
                            data["model"] = virtual_model
                            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                except PartialStreamFailure:
                    # §8: terminate the stream without [DONE]; the client sees a
                    # truncated stream and decides whether to retry the request.
                    return
                except NoEligibleCandidateError as exc:
                    payload_err = {"error": {"message": str(exc), "type": "gateway_error", "code": exc.last_error.value if exc.last_error else "no_candidate"}}
                    yield f"data: {json.dumps(payload_err, ensure_ascii=False)}\n\n"
                    return
                except UnknownVirtualModelError as exc:
                    payload_err = {"error": {"message": str(exc), "type": "gateway_error", "code": "unknown_model"}}
                    yield f"data: {json.dumps(payload_err, ensure_ascii=False)}\n\n"
                    return

            return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
        return JSONResponse(content=await service.chat(virtual_model, payload))
    except UnknownVirtualModelError as exc:
        return _error_json(404, str(exc), "unknown_model")
    except RequestValidationError as exc:
        return _error_json(400, str(exc), "capability_unsatisfiable")
    except NoEligibleCandidateError as exc:
        log.warning("gateway exhausted candidates: %s", exc)
        return _error_json(502, "all upstream candidates failed", exc.last_error.value if exc.last_error else None)
    except Exception as exc:  # noqa: BLE001
        log.exception("unhandled gateway error")
        return _error_json(500, f"gateway internal error: {type(exc).__name__}")


@router.get("/models")
async def list_models(request: Request):
    service: GatewayService = request.app.state.service
    return {"object": "list", "data": service.list_virtual_models()}
