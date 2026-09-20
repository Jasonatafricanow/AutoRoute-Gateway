"""Configuration: three-way separation (gateway.yaml / .env / gateway.db)."""

from .loader import (
    ConfigError,
    apply_capability_matrix,
    load_capability_matrix,
    load_gateway_config,
)
from .schema import (
    GatewayConfig,
    KeyConfig,
    ModelConfig,
    MissingSecretError,
    ProviderConfig,
    RouteConfig,
    RouteStep,
    SecretResolver,
    ServerConfig,
    gateway_config_from_dict,
    load_dotenv,
)

__all__ = [
    "ConfigError",
    "apply_capability_matrix",
    "load_capability_matrix",
    "load_gateway_config",
    "GatewayConfig",
    "KeyConfig",
    "ModelConfig",
    "MissingSecretError",
    "ProviderConfig",
    "RouteConfig",
    "RouteStep",
    "SecretResolver",
    "ServerConfig",
    "gateway_config_from_dict",
    "load_dotenv",
]
