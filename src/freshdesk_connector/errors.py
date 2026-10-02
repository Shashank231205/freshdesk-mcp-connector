"""Typed errors. Each one carries a stable code and a hint the calling agent can act on."""

from typing import Any


class ConnectorError(Exception):
    code = "connector_error"
    hint = "Retry later. If the problem persists, report it to the merchant's admin."
    retryable = False

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


class AuthError(ConnectorError):
    code = "auth_failed"
    hint = "The API key or domain is wrong. Ask the merchant to check FRESHDESK_API_KEY."


class PermissionDeniedError(ConnectorError):
    code = "permission_denied"
    hint = "The API key's agent cannot see this record. Do not retry."


class NotFoundError(ConnectorError):
    code = "not_found"
    hint = "Check the id. Use a list or search tool to find valid ids."


class InvalidRequestError(ConnectorError):
    code = "invalid_request"
    hint = "Fix the arguments and call again."


class RateLimitedError(ConnectorError):
    code = "rate_limited"
    retryable = True

    def __init__(self, retry_after: float) -> None:
        super().__init__(
            f"Freshdesk rate limit reached; retry in {retry_after:.0f}s.",
            hint=f"Wait {retry_after:.0f} seconds before the next call.",
        )
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "retry_after_seconds": round(self.retry_after)}


class UpstreamError(ConnectorError):
    code = "upstream_unavailable"
    retryable = True


class BudgetExceededError(ConnectorError):
    code = "session_budget_exceeded"
    hint = "This session has used its call budget. Summarise what you have so far."
