"""Capability gate tests (§5): exclusion happens before the router."""

from gateway.domain.candidate import RouteCandidate
from gateway.domain.capability import Capability, CapabilitySet
from gateway.domain.model import ConcreteModel
from gateway.routing.capability_gate import capability_gate


def _candidate(name: str, caps: set[Capability]) -> RouteCandidate:
    return RouteCandidate(
        provider_id="p", credential_id="k", concrete_model=ConcreteModel(model_id=name, provider_id="p"),
        capabilities=CapabilitySet(caps), executor_type="api",
    )


def test_gate_keeps_only_covering_candidates():
    text_only = _candidate("text-only", {Capability.TEXT, Capability.USAGE})
    text_stream = _candidate("text-stream", {Capability.TEXT, Capability.STREAM, Capability.USAGE})
    full = _candidate("full", {Capability.TEXT, Capability.STREAM, Capability.TOOLS, Capability.VISION_INPUT, Capability.USAGE})

    required = CapabilitySet.all_of(Capability.TEXT, Capability.TOOLS, Capability.STREAM, Capability.VISION_INPUT)
    kept = capability_gate([text_only, text_stream, full], required)
    assert kept == [full]


def test_gate_excludes_when_stream_missing():
    text_only = _candidate("t", {Capability.TEXT, Capability.USAGE})
    assert capability_gate([text_only], CapabilitySet.all_of(Capability.TEXT, Capability.STREAM)) == []


def test_empty_required_passes_all():
    c = _candidate("t", {Capability.TEXT})
    assert capability_gate([c], CapabilitySet()) == [c]
