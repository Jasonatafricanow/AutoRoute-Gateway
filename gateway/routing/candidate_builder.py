"""Candidate Builder (§6): enumerates RouteCandidates for a virtual model.

Enumeration = route policy order × provider models × key pool (priority,
reserve flag). No hard-coded PRIMARY_KEY/BACKUP_KEY anywhere (§10).
"""

from __future__ import annotations

from ..config.schema import GatewayConfig
from ..domain.candidate import RouteCandidate
from ..domain.credential import Credential
from ..domain.model import ConcreteModel
from ..domain.provider import Provider


class UnknownVirtualModelError(RuntimeError):
    pass


def _provider_entity(pid: str, cfg: GatewayConfig) -> Provider:
    pc = cfg.providers[pid]
    return Provider(
        provider_id=pc.id,
        kind=pc.type,  # type: ignore[arg-type]
        base_url=pc.base_url,
        enabled=pc.enabled,
        options=pc.options,
    )


def build_candidates(virtual_model_name: str, config: GatewayConfig) -> list[RouteCandidate]:
    """Enumerate ordered candidates for a virtual model (§6 router example order)."""
    route = config.routes.get(virtual_model_name)
    if route is None:
        raise UnknownVirtualModelError(f"unknown virtual model: {virtual_model_name}")

    candidates: list[RouteCandidate] = []
    for step in route.steps:
        pc = config.providers.get(step.provider)
        if pc is None or not pc.enabled:
            continue
        if pc.type == "executor" and not config.codex_enabled:
            continue  # codex stays out of the main route graph (§18)
        model_cfg = pc.model(step.model)
        if model_cfg is None:
            continue
        provider = _provider_entity(pc.id, config)
        concrete = ConcreteModel(
            model_id=model_cfg.id,
            provider_id=pc.id,
            capabilities=model_cfg.capabilities,
        )
        if pc.type == "executor":
            # Executor 无 credential（ChatGPT 登录态由 CLI 自己持有）——
            # 生成单个无 key 候选（§12/§18）
            candidates.append(
                RouteCandidate(
                    provider_id=pc.id,
                    credential_id=None,
                    concrete_model=concrete,
                    capabilities=model_cfg.capabilities,
                    executor_type="cli",
                    credential_priority=0,
                    reserve=False,
                )
            )
            continue
        ordered_keys = _order_keys(pc)
        for key_cfg in ordered_keys:
            candidates.append(
                RouteCandidate(
                    provider_id=pc.id,
                    credential_id=key_cfg.id,
                    concrete_model=concrete,
                    capabilities=model_cfg.capabilities,
                    executor_type=provider.executor_type,
                    credential_priority=key_cfg.priority,
                    reserve=key_cfg.reserve,
                )
            )
    return candidates


_RR_COUNTER = 0


def _order_keys(pc) -> list:
    """Order a provider's keys.

    priority (default): static priority sort, reserve keys last.
    round_robin: rotate the priority-ordered list per build so that, for
    providers with per-(key × model) quotas (e.g. Gemini), every healthy key
    gets a fair share of requests to the same model.
    """
    global _RR_COUNTER
    ordered = sorted(pc.keys, key=lambda k: (-k.priority, k.reserve))
    if pc.key_pool_strategy == "round_robin":
        _RR_COUNTER += 1
        offset = _RR_COUNTER % len(ordered) if ordered else 0
        ordered = ordered[offset:] + ordered[:offset]
    return ordered
