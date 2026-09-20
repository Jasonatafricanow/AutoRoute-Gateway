"""Config layer tests (§14): three-way separation, validation, matrix apply."""

from pathlib import Path

import pytest

from gateway.config import (
    ConfigError,
    apply_capability_matrix,
    load_capability_matrix,
    load_gateway_config,
    load_dotenv,
)
from gateway.config.schema import MissingSecretError, SecretResolver
from gateway.domain.capability import Capability

from .fakes import make_test_config, workdir


def test_three_way_separation():
    cfg = make_test_config()
    # policy in config; secrets only resolvable via resolver; db is runtime state
    assert cfg.providers["opencode"].keys[0].env == "OPENCODE_KEY_1"
    assert cfg.providers["opencode"].base_url == "https://ocg.test/v1"


def test_secret_resolver():
    r = SecretResolver({"OPENCODE_KEY_1": "sk-123"})
    assert r.require("OPENCODE_KEY_1") == "sk-123"
    with pytest.raises(MissingSecretError):
        r.require("OPENCODE_KEY_9")


def test_unknown_provider_rejected():
    raw = make_test_config()
    raw = type(raw)(
        routes={"bad": type(raw.routes["gateway-fast"])(name="bad", steps=[type(raw.routes["gateway-fast"].steps[0])(provider="ghost")])},
        providers=raw.providers, server=raw.server,
    )
    from gateway.config.loader import _validate

    with pytest.raises(ConfigError):
        _validate(raw)


def test_unknown_model_rejected():
    from gateway.config.loader import _validate

    raw = make_test_config()
    steps = list(raw.routes["gateway-fast"].steps)
    steps[0] = type(steps[0])(provider="amd", model="nonexistent")
    route = type(raw.routes["gateway-fast"])(name="gateway-fast", steps=steps)
    raw = type(raw)(routes={"gateway-fast": route}, providers=raw.providers, server=raw.server)
    with pytest.raises(ConfigError):
        _validate(raw)


def test_load_gateway_config_from_files():
    tmp = workdir("cfg")
    yaml_path = tmp / "gateway.yaml"
    yaml_path.write_text(
        """
routes:
  gateway-fast:
    providers: [amd]
providers:
  amd:
    type: openai_compatible
    base_url: https://amd.test/v1
    models: [{id: DS-Flash, default: true, capabilities: [text]}]
    keys: [{id: amd-01, env: AMD_API_KEY}]
""",
        encoding="utf-8",
    )
    env_path = tmp / ".env"
    env_path.write_text("AMD_API_KEY=secret-value\n# comment\n", encoding="utf-8")
    cfg, resolver = load_gateway_config(yaml_path, env_path)
    assert cfg.providers["amd"].base_url == "https://amd.test/v1"
    assert resolver.require("AMD_API_KEY") == "secret-value"


def test_load_dotenv_comments_and_export():
    env = load_dotenv("nonexistent", {})
    assert env == {}
    from gateway.config.schema import load_dotenv as ld

    d: dict = {}
    p = workdir("dotenv") / "dotenv.txt"
    p.write_text('export A=1\nB="2"\n# c\n', encoding="utf-8")
    ld(str(p), d)
    assert d == {"A": "1", "B": "2"}


def test_capability_matrix_apply_overrides_yaml():
    cfg = make_test_config()
    matrix = {"amd/DS-Flash": [Capability.TEXT, Capability.USAGE]}  # smoke-tested truth
    new = apply_capability_matrix(cfg, matrix)
    assert Capability.STREAM not in new.providers["amd"].models[0].capabilities
    assert Capability.TEXT in new.providers["amd"].models[0].capabilities
    # providers not in the matrix keep their declared caps
    assert Capability.STREAM in new.providers["modelscope"].models[0].capabilities


def test_load_capability_matrix_file():
    p = workdir("matrix") / "m.yaml"
    p.write_text("amd/DS-Flash: [text, usage]\n", encoding="utf-8")
    matrix = load_capability_matrix(p)
    assert matrix["amd/DS-Flash"] == {Capability.TEXT, Capability.USAGE}
