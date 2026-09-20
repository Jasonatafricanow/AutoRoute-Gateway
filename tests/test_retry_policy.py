"""Retry policy tests (§7): token_limit retries same candidate; everything
else moves on; fixed small caps that truncate the pool are forbidden."""

from gateway.policy.error_classifier import ErrorClass
from gateway.policy.retry_policy import FallbackAction, RetryPolicy, next_action

P = RetryPolicy(per_candidate_retry_limit=2, route_candidate_limit=None, hard_attempt_ceiling=50)


def test_token_limit_retries_same_candidate_under_limit():
    assert next_action(ErrorClass.TOKEN_LIMIT, 0, P) == FallbackAction.RETRY_SAME_CANDIDATE
    assert next_action(ErrorClass.TOKEN_LIMIT, 1, P) == FallbackAction.RETRY_SAME_CANDIDATE


def test_token_limit_moves_on_after_limit():
    assert next_action(ErrorClass.TOKEN_LIMIT, 2, P) == FallbackAction.NEXT_CANDIDATE


def test_all_other_classes_move_to_next_candidate():
    for cls in (ErrorClass.AUTH, ErrorClass.RATE_LIMIT, ErrorClass.QUOTA, ErrorClass.MODEL_NOT_FOUND, ErrorClass.NETWORK, ErrorClass.SERVER, ErrorClass.UNKNOWN):
        assert next_action(cls, 0, P) == FallbackAction.NEXT_CANDIDATE, cls


def test_default_policy_values():
    p = RetryPolicy()
    assert p.per_candidate_retry_limit == 2
    assert p.route_candidate_limit is None  # all eligible — pool must not be truncated
    assert p.hard_attempt_ceiling == 50
