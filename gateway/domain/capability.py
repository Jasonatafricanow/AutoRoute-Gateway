"""Capability model (§4)."""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    """Request/response capability requirements. First version list (§4)."""

    TEXT = "text"
    VISION_INPUT = "vision_input"
    STREAM = "stream"
    TOOLS = "tools"
    STRUCTURED_OUTPUT = "structured_output"
    MULTI_TURN = "multi_turn"
    USAGE = "usage"


class CapabilitySet(frozenset[Capability]):
    """Immutable capability set with subset checks."""

    def __new__(cls, capabilities: frozenset[Capability] | set[Capability] | list[Capability] | tuple[Capability, ...] | None = None):
        return super().__new__(cls, capabilities or ())

    @classmethod
    def all_of(cls, *capabilities: Capability) -> "CapabilitySet":
        return cls(set(capabilities))

    def covers(self, required: "CapabilitySet") -> bool:
        """True when every required capability is present in this set."""
        return required.issubset(self)


def required_capabilities_from_request(
    *,
    stream: bool = False,
    tools: bool = False,
    structured_output: bool = False,
    vision_input: bool = False,
    multi_turn: bool = False,
    usage: bool = True,
    text: bool = True,
) -> CapabilitySet:
    """Derive the capability requirement from a concrete request (§2 pipeline: capability requirement derived from request)."""
    caps: set[Capability] = set()
    if text:
        caps.add(Capability.TEXT)
    if stream:
        caps.add(Capability.STREAM)
    if tools:
        caps.add(Capability.TOOLS)
    if structured_output:
        caps.add(Capability.STRUCTURED_OUTPUT)
    if vision_input:
        caps.add(Capability.VISION_INPUT)
    if multi_turn:
        caps.add(Capability.MULTI_TURN)
    if usage:
        caps.add(Capability.USAGE)
    return CapabilitySet(caps)
