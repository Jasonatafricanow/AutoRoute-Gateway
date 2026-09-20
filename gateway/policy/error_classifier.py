"""Seven-class error classifier (§7) — the asset inherited from free-proxy.

Frozen semantics (v0.3.1):
- 429 short-term rate limit and long-term quota exhaustion must be distinguished.
- auth (401/403) marks the current credential AUTH_ERROR and routes to the next
  candidate — it is NOT a global STOP.
- model_not_found invalidates the current provider/model candidate → next candidate.
"""

from __future__ import annotations

from enum import Enum


class ErrorClass(str, Enum):
    AUTH = "auth"
    TOKEN_LIMIT = "token_limit"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    MODEL_NOT_FOUND = "model_not_found"
    NETWORK = "network"
    SERVER = "server"
    UNKNOWN = "unknown"


_TOKEN_LIMIT_TOKENS = (
    "maximum context length",
    "context length exceeded",
    "too many tokens",
    "max tokens",
    "maxoutputtokens",
    "prompt is too long",
    "payload too large",
    "contextwindowexceeded",
    "input length",
)

_AUTH_TOKENS = (
    "invalid api key",
    "unauthorized",
    "forbidden",
    "permission denied",
    "authentication",
    "incorrect api key",
    "api key not valid",
)

_MODEL_TOKENS = (
    "model not found",
    "unknown model",
    "unsupported model",
    "does not exist",
    "model_not_found",
    "no such model",
)

_QUOTA_TOKENS = (
    "quota exceeded",
    "insufficient quota",
    "insufficient credits",
    "billing",
    "exceeded your current quota",
    "insufficient_quota",
    "quota_exhausted",
    "out of credits",
)

_RATE_LIMIT_TOKENS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "retry later",
    "throttle",
    "slow down",
)

_NETWORK_TOKENS = (
    "network",
    "connection",
    "timed out",
    "timeout",
    "certificate verify failed",
    "local issuer certificate",
    "ssl",
    "tls",
    "connect error",
    "connection reset",
    "eof",
)


def classify_error(status: int | None, body_text: str = "", headers: dict[str, str] | None = None) -> ErrorClass:
    """Classify a provider failure into one of the seven error classes.

    Body-based signals take precedence over status codes (free-proxy semantics).
    429 is rate_limit by default, but becomes quota when the body carries
    quota-exhaustion signals — the frozen distinction between short-term
    throttling and long-term exhaustion.
    """
    text = (body_text or "").lower()
    headers = headers or {}

    if any(token in text for token in _TOKEN_LIMIT_TOKENS):
        return ErrorClass.TOKEN_LIMIT
    if any(token in text for token in _AUTH_TOKENS):
        return ErrorClass.AUTH
    if any(token in text for token in _MODEL_TOKENS):
        return ErrorClass.MODEL_NOT_FOUND
    if any(token in text for token in _QUOTA_TOKENS):
        return ErrorClass.QUOTA
    if any(token in text for token in _RATE_LIMIT_TOKENS):
        return ErrorClass.RATE_LIMIT
    if any(token in text for token in _NETWORK_TOKENS):
        return ErrorClass.NETWORK

    if status in (401, 403):
        return ErrorClass.AUTH
    if status == 404:
        return ErrorClass.MODEL_NOT_FOUND
    if status == 429:
        # Short-term rate limit; the body already ruled out quota exhaustion.
        return ErrorClass.RATE_LIMIT
    if status == 402 or "insufficient" in text or "quota" in text:
        return ErrorClass.QUOTA
    if status is not None and status >= 500:
        return ErrorClass.SERVER
    if status is not None and status >= 400:
        return ErrorClass.UNKNOWN
    # No status at all (transport-level failure) — network unless proven otherwise.
    if status is None:
        return ErrorClass.NETWORK
    return ErrorClass.UNKNOWN


def remediation_suggestion(error_class: ErrorClass) -> str:
    return {
        ErrorClass.AUTH: "API Key 无效或权限不足，该 credential 将被标记 AUTH_ERROR 并切换下一个候选。",
        ErrorClass.QUOTA: "额度不足或已耗尽，该 scope 将被标记 EXHAUSTED 并按 reset_policy 探测恢复。",
        ErrorClass.RATE_LIMIT: "触发短期限流，该 scope 进入 cooldown，稍后自动重新入池。",
        ErrorClass.MODEL_NOT_FOUND: "该 provider/model 组合不存在，候选失效并切换下一个候选。",
        ErrorClass.NETWORK: "网络连接失败，provider 健康度降级并切换下一个候选。",
        ErrorClass.TOKEN_LIMIT: "请求超过 token 限制，允许同候选重试后切换下一个候选。",
        ErrorClass.SERVER: "上游服务异常，切换下一个候选。",
        ErrorClass.UNKNOWN: "未知错误，切换下一个候选。",
    }[error_class]
