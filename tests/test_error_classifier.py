"""Error classifier tests: seven classes + the 429/quota distinction (§7)."""

from gateway.policy.error_classifier import ErrorClass, classify_error


def test_body_tokens_take_precedence():
    assert classify_error(200, "maximum context length exceeded") == ErrorClass.TOKEN_LIMIT
    assert classify_error(200, "invalid api key provided") == ErrorClass.AUTH
    assert classify_error(200, "the model does not exist") == ErrorClass.MODEL_NOT_FOUND
    assert classify_error(200, "you exceeded your current quota") == ErrorClass.QUOTA
    assert classify_error(200, "rate limit reached, retry later") == ErrorClass.RATE_LIMIT
    assert classify_error(200, "connection timed out") == ErrorClass.NETWORK


def test_status_based():
    assert classify_error(401, "") == ErrorClass.AUTH
    assert classify_error(403, "") == ErrorClass.AUTH
    assert classify_error(404, "") == ErrorClass.MODEL_NOT_FOUND
    assert classify_error(429, "") == ErrorClass.RATE_LIMIT
    assert classify_error(402, "") == ErrorClass.QUOTA
    assert classify_error(500, "boom") == ErrorClass.SERVER
    assert classify_error(502, "bad gateway") == ErrorClass.SERVER


def test_429_vs_quota_distinction():
    """Frozen: short-term 429 must not collapse into long-term exhaustion."""
    assert classify_error(429, "rate limit exceeded") == ErrorClass.RATE_LIMIT
    assert classify_error(429, "you exceeded your current quota, please check your plan and billing details") == ErrorClass.QUOTA
    assert classify_error(429, "insufficient_quota") == ErrorClass.QUOTA


def test_no_status_is_network():
    assert classify_error(None, "") == ErrorClass.NETWORK


def test_unknown():
    assert classify_error(400, "something strange") == ErrorClass.UNKNOWN


def test_case_insensitive():
    assert classify_error(401, "Unauthorized") == ErrorClass.AUTH
    assert classify_error(429, "Rate Limit") == ErrorClass.RATE_LIMIT
