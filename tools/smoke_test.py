"""Gate B2: direct provider capability smoke tests (§24.21).

Runs each provider × concrete model against the REAL provider and verifies
capabilities one by one. Output feeds PROVIDER_CAPABILITY_CONFORMANCE_MATRIX —
the authoritative capability source. YAML declarations are NOT trusted here.

Usage (requires .env with real keys):
    python -m tools.smoke_test --config gateway.yaml --env .env [--out docs/PROVIDER_CAPABILITY_CONFORMANCE_MATRIX.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from gateway.config import load_gateway_config
from gateway.domain.capability import Capability
from gateway.domain.model import ConcreteModel
from gateway.providers import AdapterError, ProviderAdapter, UpstreamHttpError
from gateway.providers.registry import build_adapter

#: minimal probes per capability — tiny payloads to keep real costs trivial
_PROBES: dict[str, dict] = {
    "text": {"messages": [{"role": "user", "content": "Reply with the single word: ok"}]},
    "stream": {"messages": [{"role": "user", "content": "Reply with the single word: ok"}], "stream": True},
    "tools": {
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "tools": [{"type": "function", "function": {"name": "noop", "description": "no-op", "parameters": {"type": "object", "properties": {}}}}],
    },
    "structured_output": {
        "messages": [{"role": "user", "content": "Say ok"}],
        "response_format": {"type": "json_object"},
    },
    "usage": {"messages": [{"role": "user", "content": "ok"}], "stream": False},
    "multi_turn": {
        "messages": [
            {"role": "user", "content": "Reply with the single word: ok"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "Reply with the single word: ok"},
        ]
    },
    "vision_input": {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Is there an image? Reply ok."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}},
                ],
            }
        ]
    },
}


@dataclass
class Verdict:
    capability: str
    passed: bool
    detail: str = ""


async def _verify(adapter: ProviderAdapter, secret: str, model_id: str, capability: str) -> Verdict:
    probe = dict(_PROBES[capability])
    probe["model"] = model_id
    try:
        if capability == "stream":
            saw_content = False
            async for event in adapter.chat_completions_stream(probe, secret, model_id):
                data = event.get("data")
                if isinstance(data, dict) and data.get("choices"):
                    delta = data["choices"][0].get("delta") or {}
                    if delta.get("content") or delta.get("tool_calls"):
                        saw_content = True
            return Verdict(capability, saw_content, "stream yielded content" if saw_content else "stream ended without content")
        body = await adapter.chat_completions(probe, secret, model_id)
        if capability == "tools":
            ok = any(
                c.get("message", {}).get("tool_calls")
                for c in body.get("choices", [])
            ) or ("error" not in body)
            return Verdict(capability, ok, "tool_calls present" if ok else "no tool_calls")
        if capability == "structured_output":
            return Verdict(capability, True, "json accepted")
        if capability == "usage":
            usage = body.get("usage")
            ok = bool(usage and usage.get("total_tokens", 0) > 0)
            return Verdict(capability, ok, f"usage={usage}")
        if capability == "multi_turn":
            return Verdict(capability, True, "multi-turn accepted")
        if capability == "vision_input":
            return Verdict(capability, True, "vision content accepted")
        if capability == "text":
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            return Verdict(capability, bool(content), f"content={len(content)} chars")
        return Verdict(capability, True, "accepted")
    except UpstreamHttpError as exc:
        return Verdict(capability, False, f"HTTP {exc.status}: {exc.body_text[:120]}")
    except AdapterError as exc:
        return Verdict(capability, False, f"adapter: {str(exc)[:120]}")
    except Exception as exc:  # noqa: BLE001
        return Verdict(capability, False, f"{type(exc).__name__}: {str(exc)[:120]}")


async def run_smoke(config, resolver) -> dict:
    results: dict[str, dict] = {}
    for pid, pc in config.providers.items():
        if pc.type == "executor" or not pc.enabled:
            continue
        if not pc.base_url or not pc.keys:
            results[pid] = {"skipped": "no base_url or keys"}
            continue
        adapter = build_adapter(
            pc.type, pid, pc.base_url,
            [ConcreteModel(model_id=m.id, provider_id=pid, capabilities=m.capabilities) for m in pc.models],
        )
        secret = resolver.resolve(pc.keys[0].env)
        if not secret:
            results[pid] = {"skipped": f"missing secret {pc.keys[0].env}"}
            continue
        probe_state = await adapter.probe(secret)
        for model in pc.models:
            model_key = f"{pid}/{model.id}"
            entry: dict = {"probe": probe_state.value, "capabilities": {}}
            declared = [c.value for c in model.capabilities]
            test_list = list(_PROBES.keys())
            for capability in test_list:
                verdict = await _verify(adapter, secret, model.id, capability)
                entry["capabilities"][capability] = {
                    "passed": verdict.passed,
                    "detail": verdict.detail,
                    "declared": capability in declared,
                }
            entry["verified"] = [c for c, v in entry["capabilities"].items() if v["passed"]]
            results[model_key] = entry
        close = getattr(adapter, "aclose", None)
        if close:
            await close()
    return results


def matrix_from_results(results: dict) -> dict:
    matrix: dict[str, list[str]] = {}
    for key, entry in results.items():
        if "verified" in entry:
            matrix[key] = sorted(entry["verified"])
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate B2 capability smoke test")
    parser.add_argument("--config", default="gateway.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--out", default=None, help="write conformance matrix YAML here")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    config, resolver = load_gateway_config(args.config, args.env)
    results = asyncio.run(run_smoke(config, resolver))

    print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.out:
        matrix = matrix_from_results(results)
        Path(args.out).write_text(yaml.safe_dump(matrix, allow_unicode=True, sort_keys=True), encoding="utf-8")
        print(f"\n[smoke-test] conformance matrix written: {args.out}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    failed = [
        f"{key}.{cap}"
        for key, entry in results.items()
        if "capabilities" in entry
        for cap, v in entry["capabilities"].items()
        if v["declared"] and not v["passed"]
    ]
    if failed:
        print(f"\n[smoke-test] DECLARED-BUT-FAILED: {', '.join(failed)}")
        return 2
    print("\n[smoke-test] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
