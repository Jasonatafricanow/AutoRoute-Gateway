"""Capability Gate (§5): runs BEFORE the router; providers whose declared
capabilities do not cover the request requirement are excluded outright."""

from __future__ import annotations

from ..domain.candidate import RouteCandidate
from ..domain.capability import CapabilitySet


def capability_gate(candidates: list[RouteCandidate], required: CapabilitySet) -> list[RouteCandidate]:
    """Keep only candidates whose capabilities cover the required set.

    A request with tools + stream + vision requires {text, tools, stream,
    vision_input}; candidates not satisfying every one are excluded before any
    scoring happens (§5).
    """
    return [c for c in candidates if c.capabilities.covers(required)]
