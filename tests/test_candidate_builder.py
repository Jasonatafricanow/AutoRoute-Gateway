"""Candidate builder tests (§6/§10): enumeration order, reserve keys,
multi-key pool, executor_type, codex exclusion."""

from gateway.routing import UnknownVirtualModelError, build_candidates

from .fakes import make_test_config


def test_gateway_fast_order_matches_baseline_router_example():
    cfg = make_test_config()
    cands = build_candidates("gateway-fast", cfg)
    keys = [(c.provider_id, c.credential_id, c.concrete_model.model_id) for c in cands]
    # §6 example: AMD/key1 → ModelScope/key1 → opencode/key1 → opencode/key2(reserve)
    assert keys == [
        ("amd", "amd-01", "DS-Flash"),
        ("modelscope", "ms-01", "DS-Flash"),
        ("opencode", "ocg-01", "DS-Flash"),
        ("opencode", "ocg-02", "DS-Flash"),
    ]
    assert cands[3].reserve is True
    assert all(c.executor_type == "api" for c in cands)


def test_unknown_virtual_model_raises():
    cfg = make_test_config()
    try:
        build_candidates("nope", cfg)
        raise AssertionError("should have raised")
    except UnknownVirtualModelError:
        pass


def test_gateway_deep_uses_opencode_pro_and_skips_amd():
    cfg = make_test_config()
    cands = build_candidates("gateway-deep", cfg)
    keys = [(c.provider_id, c.credential_id, c.concrete_model.model_id) for c in cands]
    assert keys == [("opencode", "ocg-01", "Pro"), ("opencode", "ocg-02", "Pro")]
    # deep must not route through AMD/ModelScope (§6 gateway-deep)
    assert all(k[0] == "opencode" for k in keys)


def test_new_key_only_changes_config():
    """Acceptance case 5: adding key3 is config-only, no code change."""
    raw = make_test_config()
    # add key3 to opencode
    from gateway.config.schema import KeyConfig

    ocg = raw.providers["opencode"]
    raw = type(raw)(
        routes=raw.routes,
        providers={**raw.providers, "opencode": type(ocg)(
            id=ocg.id, type=ocg.type, base_url=ocg.base_url, models=ocg.models,
            keys=[*ocg.keys, KeyConfig(id="ocg-03", env="OPENCODE_KEY_3", priority=75)],
            enabled=ocg.enabled, reset_policy=ocg.reset_policy, options=ocg.options,
        )},
        server=raw.server,
    )
    cands = build_candidates("gateway-fast", raw)
    opencode_keys = [c.credential_id for c in cands if c.provider_id == "opencode"]
    assert opencode_keys == ["ocg-01", "ocg-03", "ocg-02"]  # priority order, reserve last
