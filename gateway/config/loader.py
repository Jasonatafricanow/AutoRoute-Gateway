"""Config loading: gateway.yaml + .env + optional capability matrix."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..domain.capability import Capability, CapabilitySet
from .schema import (
    GatewayConfig,
    ProviderConfig,
    RouteConfig,
    SecretResolver,
    gateway_config_from_dict,
    load_dotenv,
)


class ConfigError(RuntimeError):
    pass


def load_gateway_config(yaml_path: str | Path, env_path: str | Path | None = None, env: dict[str, str] | None = None) -> tuple[GatewayConfig, SecretResolver]:
    """Load policy config from gateway.yaml and secrets from .env.

    Returns (config, secret_resolver). Runtime state stays in SQLite (gateway.db).
    """
    yp = Path(yaml_path)
    if not yp.exists():
        raise ConfigError(f"gateway.yaml not found: {yp}")
    raw = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    config = gateway_config_from_dict(raw)

    merged_env: dict[str, str] = {}
    if env_path:
        load_dotenv(env_path, merged_env)
    if env:
        merged_env.update(env)
    resolver = SecretResolver(merged_env)

    _validate(config)
    return config, resolver


def load_capability_matrix(path: str | Path) -> dict[str, CapabilitySet]:
    """Load the Gate B2 conformance matrix (authoritative capability source, frozen #21).

    Matrix shape:
        amd/DeepSeek-V4-Flash: [text, stream, usage]
        opencode/Pro: [text, stream, tools, structured_output, usage]
    """
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"capability matrix not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    matrix: dict[str, CapabilitySet] = {}
    for key, caps in raw.items():
        if isinstance(caps, str):
            caps = [caps]
        matrix[str(key)] = CapabilitySet({Capability(c) for c in caps})
    return matrix


def apply_capability_matrix(config: GatewayConfig, matrix: dict[str, CapabilitySet]) -> GatewayConfig:
    """Return a config whose provider models carry matrix-verified capabilities."""
    providers: dict[str, ProviderConfig] = {}
    for pid, p in config.providers.items():
        models = []
        for m in p.models:
            key = f"{pid}/{m.id}"
            caps = matrix.get(key, m.capabilities)
            models.append(type(m)(id=m.id, capabilities=caps, default=m.default))
        providers[pid] = type(p)(
            id=p.id, type=p.type, base_url=p.base_url, models=models, keys=p.keys,
            enabled=p.enabled, reset_policy=p.reset_policy, options=p.options,
        )
    return type(config)(
        routes=config.routes, providers=providers, server=config.server,
        capability_matrix_path=config.capability_matrix_path,
        codex_enabled=config.codex_enabled, log_dir=config.log_dir,
    )


def _validate(config: GatewayConfig) -> None:
    for name, route in config.routes.items():
        if not route.steps:
            raise ConfigError(f"route {name!r} has no provider steps")
        for step in route.steps:
            provider = config.providers.get(step.provider)
            if provider is None:
                raise ConfigError(f"route {name!r} references unknown provider {step.provider!r}")
            if not provider.enabled and provider.type != "executor":
                raise ConfigError(f"route {name!r} references disabled provider {step.provider!r}")
            if provider.type == "executor" and not config.codex_enabled:
                raise ConfigError(f"route {name!r} references executor provider {step.provider!r} but codex is disabled (§18)")
            if provider.type != "executor" and not provider.base_url:
                raise ConfigError(f"provider {step.provider!r} has no base_url")
            if provider.type != "executor" and not provider.keys:
                raise ConfigError(f"provider {step.provider!r} has no keys")
            if step.model and provider.model(step.model) is None:
                raise ConfigError(f"provider {step.provider!r} has no model {step.model!r}")
