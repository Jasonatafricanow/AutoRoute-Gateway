"""Routing layer: resolver gate → candidate builder → scorer → fallback engine."""

from .candidate_builder import UnknownVirtualModelError, build_candidates
from .capability_gate import capability_gate
from .fallback import (
    CandidateFailure,
    FallbackEngine,
    FallbackResult,
    HardAttemptCeilingError,
    NoEligibleCandidateError,
)
from .scorer import ScoredCandidate, score_candidates

__all__ = [
    "UnknownVirtualModelError",
    "build_candidates",
    "capability_gate",
    "CandidateFailure",
    "FallbackEngine",
    "FallbackResult",
    "HardAttemptCeilingError",
    "NoEligibleCandidateError",
    "ScoredCandidate",
    "score_candidates",
]
