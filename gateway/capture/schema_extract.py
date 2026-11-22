"""Shape-only schema extraction for the Phase 0 capture gateway.

The capture layer records structure and lengths only. Prompt/response text and
credentials must never be serialized into capture files.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "cookie",
    "set-cookie",
}


def schema_of(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "items": [schema_of(item) for item in value[:3]],
        }
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "keys": sorted(str(key) for key in value.keys()),
            "fields": {
                str(key): schema_of(item)
                for key, item in value.items()
            },
        }
    return {"type": type(value).__name__}


def header_schema(headers: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADERS:
            result[key] = "<redacted>"
        else:
            result[key] = {"length": len(str(value))}
    return result


def _content_schema(content: Any) -> Any:
    if isinstance(content, str):
        return {"type": "text", "length": len(content)}
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, Mapping):
                blocks.append(schema_of(part))
                continue
            part_type = str(part.get("type") or "unknown")
            if part_type == "text":
                blocks.append(
                    {
                        "type": "text",
                        "content": {
                            "type": "text",
                            "length": len(str(part.get("text") or "")),
                        },
                    }
                )
            elif part_type == "image_url":
                blocks.append({"type": "image_url"})
            else:
                blocks.append({"type": part_type})
        return {"type": "blocks", "length": len(content), "items": blocks}
    return schema_of(content)


def request_schema(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if key != "messages":
            result[key] = schema_of(value)
            continue

        messages = value if isinstance(value, list) else []
        roles: list[str] = []
        content_blocks: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "")
            roles.append(role)
            content_blocks.append(
                {
                    "role": role,
                    "content": _content_schema(message.get("content")),
                }
            )
        result["messages"] = {
            "count": len(messages),
            "roles": roles,
            "content_blocks": content_blocks,
        }
    return result


def response_schema(body: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in body.items():
        if key != "choices":
            result[key] = schema_of(value)
            continue

        choices = value if isinstance(value, list) else []
        first: dict[str, Any] = {}
        if choices and isinstance(choices[0], Mapping):
            choice = choices[0]
            first["finish_reason"] = choice.get("finish_reason")
            message = choice.get("message")
            if isinstance(message, Mapping):
                first["message"] = {
                    "role": message.get("role"),
                    "content": _content_schema(message.get("content")),
                }
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                first["delta"] = {
                    "role": delta.get("role"),
                    "content": _content_schema(delta.get("content")),
                }
        result["choices"] = {"count": len(choices), "first": first}
    return result
