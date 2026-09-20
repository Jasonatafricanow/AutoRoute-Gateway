"""Configuration schema (§14): three-way separation, frozen.

gateway.yaml = policy/routing (routes, providers, keys, capability declarations,
               reset_policy) — static strategy, no secrets.
.env         = secrets (AMD_API_KEY / MODELSCOPE_API_KEY / OPENCODE_KEY_1 /
               OPENCODE_KEY_2 / GEMINI_API_KEY + startup settings).
gateway.db   = runtime state (SQLite), never configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.capability import Capability, CapabilitySet
from ..domain.state import ResetPolicy, ResetPolicyType


@dataclass(frozen=True)
class KeyConfig:
    """One credential in a provider's key pool (§10)."""

    id: str
    env: str
    priority: int = 100
    reserve: bool = False


@dataclass(frozen=True)
class ModelConfig:
    """One concrete model on a provider."""

    id: str
    #: declared capabilities. Authoritative source is the Gate B2 conformance
    #: matrix; yaml declarations are defaults/dev-time only (frozen #21).
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    default: bool = False


@dataclass(frozen=True)
class ProviderConfig:
    """Provider policy config (no secrets)."""

    id: str
    type: str  # openai_compatible | gemini | executor
    base_url: str | None = None
    models: list[ModelConfig] = field(default_factory=list)
    keys: list[KeyConfig] = field(default_factory=list)
    enabled: bool = True
    reset_policy: ResetPolicy = field(default_factory=ResetPolicy)
    #: "priority"（默认，按 priority 静态排序）| "round_robin"（对选定模型，在 healthy keys 间轮值）
    #: Gemini 等按 (key × model) 独立限额的服务应用 round_robin（§11 quota 粒度）
    key_pool_strategy: str = "priority"
    options: dict = field(default_factory=dict)

    def default_model(self) -> ModelConfig | None:
        for m in self.models:
            if m.default:
                return m
        return self.models[0] if self.models else None

    def model(self, model_id: str | None) -> ModelConfig | None:
        if model_id is None:
            return self.default_model()
        for m in self.models:
            if m.id == model_id:
                return m
        return None


@dataclass(frozen=True)
class RouteStep:
    """One provider pick inside a route policy."""

    provider: str
    #: concrete model override; provider default when None
    model: str | None = None


@dataclass(frozen=True)
class RouteConfig:
    """A virtual model's route policy (§4/§6)."""

    name: str
    description: str = ""
    #: ordered provider steps (route policy order)
    steps: list[RouteStep] = field(default_factory=list)


@dataclass(frozen=True)
class ServerConfig:
    """bind/address fully configurable (§16); never assume localhost."""

    bind: str = "127.0.0.1"
    port: int = 8700
    database_path: str = "gateway.db"
    #: gateway auth token; required for non-loopback deployment (§24.22)
    auth_token: str | None = None
    #: optional network ACL (allow-listed IPs/CIDRs); empty = loopback only
    allow_ips: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GatewayConfig:
    routes: dict[str, RouteConfig]
    providers: dict[str, ProviderConfig]
    server: ServerConfig = field(default_factory=ServerConfig)
    #: optional Gate B2 conformance matrix path (authoritative capability source)
    capability_matrix_path: str | None = None
    codex_enabled: bool = False
    log_dir: str = "logs"


def _capability_set(raw) -> CapabilitySet:
    if raw is None:
        return CapabilitySet()
    if isinstance(raw, str):
        raw = [raw]
    return CapabilitySet({Capability(c) for c in raw})


def _reset_policy(raw: dict | None) -> ResetPolicy:
    if not raw:
        return ResetPolicy()
    return ResetPolicy(
        type=ResetPolicyType(raw.get("type", "unknown")),
        timezone=raw.get("timezone"),
        time=raw.get("time"),
    )


def _provider(raw_id: str, raw: dict) -> ProviderConfig:
    models = []
    for m in raw.get("models") or []:
        if isinstance(m, str):
            models.append(ModelConfig(id=m, default=len(models) == 0))
        else:
            models.append(
                ModelConfig(
                    id=m["id"],
                    capabilities=_capability_set(m.get("capabilities")),
                    default=bool(m.get("default", len(models) == 0)),
                )
            )
    keys = []
    for k in raw.get("keys") or []:
        if isinstance(k, str):
            keys.append(KeyConfig(id=k, env=k))
        else:
            keys.append(
                KeyConfig(
                    id=k.get("id", k["env"]),
                    env=k["env"],
                    priority=int(k.get("priority", 100)),
                    reserve=bool(k.get("reserve", False)),
                )
            )
    return ProviderConfig(
        id=raw_id,
        type=raw.get("type", "openai_compatible"),
        base_url=raw.get("base_url"),
        models=models,
        keys=keys,
        enabled=bool(raw.get("enabled", True)),
        reset_policy=_reset_policy(raw.get("reset_policy")),
        key_pool_strategy=raw.get("key_pool_strategy", "priority"),
        options=raw.get("options") or {},
    )


def _route(raw_name: str, raw: dict) -> RouteConfig:
    steps = []
    for step in raw.get("providers") or []:
        if isinstance(step, str):
            steps.append(RouteStep(provider=step))
        else:
            steps.append(RouteStep(provider=step["provider"], model=step.get("model")))
    return RouteConfig(name=raw_name, description=raw.get("description", ""), steps=steps)


def gateway_config_from_dict(raw: dict) -> GatewayConfig:
    server_raw = raw.get("server") or {}
    auth = server_raw.get("auth") or {}
    server = ServerConfig(
        bind=server_raw.get("bind", "127.0.0.1"),
        port=int(server_raw.get("port", 8700)),
        database_path=server_raw.get("database_path", "gateway.db"),
        auth_token=auth.get("token") or os.environ.get("GATEWAY_AUTH_TOKEN"),
        allow_ips=list(server_raw.get("allow_ips") or []),
    )
    return GatewayConfig(
        routes={name: _route(name, r) for name, r in (raw.get("routes") or {}).items()},
        providers={pid: _provider(pid, p) for pid, p in (raw.get("providers") or {}).items()},
        server=server,
        capability_matrix_path=raw.get("capability_matrix_path"),
        codex_enabled=bool(raw.get("codex", {}).get("enabled", False)),
        log_dir=raw.get("log_dir", "logs"),
    )


class SecretResolver:
    """Resolves credential secrets from environment variables at request time.

    The resolved value never enters state, logs, or route_events.
    """

    def __init__(self, env: dict[str, str] | None = None):
        self._env = dict(env) if env is not None else dict(os.environ)

    def resolve(self, env_var: str) -> str | None:
        value = self._env.get(env_var)
        if value is None:
            value = os.environ.get(env_var)
        return value or None

    def require(self, env_var: str) -> str:
        value = self.resolve(env_var)
        if not value:
            raise MissingSecretError(env_var)
        return value


class MissingSecretError(RuntimeError):
    def __init__(self, env_var: str):
        super().__init__(f"missing secret: environment variable {env_var!r} is not set")
        self.env_var = env_var


def load_dotenv(path: str | Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """Minimal .env loader (KEY=VALUE lines, comments, optional export prefix)."""
    target = env if env is not None else {}
    p = Path(path)
    if not p.exists():
        return target
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        target[key] = value
    return target
