"""Retry / attempt budget (§7, v0.3.1).

Frozen decisions:
- per_candidate_retry_limit: retries inside one candidate (default 2).
- route_candidate_limit: how many distinct candidates a route may try
  (default None = all eligible). A fixed small cap that truncates the pool is
  FORBIDDEN (P0-2).
- hard_attempt_ceiling: safety valve against program bugs (default 50).
- token_limit is the only error class that retries the same candidate.
- auth / model_not_found / rate_limit / quota / network / server → next candidate.
- stop only when no eligible candidate remains or the hard ceiling is hit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .error_classifier import ErrorClass


class FallbackAction(str, Enum):
    RETRY_SAME_CANDIDATE = "retry_same_candidate"
    NEXT_CANDIDATE = "next_candidate"
    STOP = "stop"


@dataclass(frozen=True)
class RetryPolicy:
    per_candidate_retry_limit: int = 2
    route_candidate_limit: int | None = None  # None = all eligible
    hard_attempt_ceiling: int = 50


def next_action(error_class: ErrorClass, per_candidate_retries_used: int, policy: RetryPolicy) -> FallbackAction:
    """Decide the fallback action for one candidate failure (§7).

    Only token_limit retries the same candidate, and only while under the
    per-candidate retry limit. Every other class moves to the next candidate;
    the Fallback Engine decides STOP when the pool is empty or the hard
    ceiling is reached.
    """
    if error_class == ErrorClass.TOKEN_LIMIT and per_candidate_retries_used < policy.per_candidate_retry_limit:
        return FallbackAction.RETRY_SAME_CANDIDATE
    return FallbackAction.NEXT_CANDIDATE
