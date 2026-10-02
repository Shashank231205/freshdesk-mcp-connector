"""HTTP access to the Freshdesk v2 API: auth, timeouts, rate limiting, retries and error mapping.

The client only issues GET requests, so the connector cannot change data in the helpdesk.
"""

import asyncio
import logging
import math
import random
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx
import truststore

from freshdesk_connector import __version__
from freshdesk_connector.config import Settings
from freshdesk_connector.errors import (
    AuthError,
    ConnectorError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    UpstreamError,
)

logger = logging.getLogger(__name__)

Params = Mapping[str, str | int]


@dataclass(frozen=True, slots=True)
class ApiResponse:
    body: Any
    has_next_page: bool


def tls_context() -> ssl.SSLContext:
    """Verify certificates against the operating system's trust store.

    Corporate proxies and antivirus tools often re-sign HTTPS traffic with a root
    certificate installed in the OS store. Using that store keeps full verification on
    in those environments instead of failing or requiring verification to be disabled.
    """
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class RateLimiter:
    """Token bucket that keeps the connector under the account's per-minute API quota.

    Freshdesk shares one quota across every integration on the account, so after each
    response the bucket is reconciled with what the server reports.
    """

    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._capacity = float(per_minute)
        self._tokens = float(per_minute)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            self._refill()
            if self._tokens < 1:
                await self._sleep((1 - self._tokens) / self._refill_rate)
                self._refill()
            self._tokens -= 1

    def observe(self, headers: httpx.Headers) -> None:
        total = _number_header(headers, "X-RateLimit-Total")
        if total and total != self._capacity:
            self._capacity = total
            self._tokens = min(self._tokens, self._capacity)
        used = _number_header(headers, "X-RateLimit-Used-CurrentRequest")
        if used and used > 1:
            self._tokens -= used - 1
        remaining = _number_header(headers, "X-RateLimit-Remaining")
        if remaining is not None:
            self._tokens = min(self._tokens, remaining)

    @property
    def _refill_rate(self) -> float:
        return self._capacity / 60

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._refill_rate)
        self._updated = now


class FreshdeskClient:
    """Async, read-only Freshdesk client. Use it as an async context manager."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._limiter = RateLimiter(settings.rate_limit_per_minute)
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            auth=httpx.BasicAuth(settings.api_key.get_secret_value(), "X"),
            headers={
                "Accept": "application/json",
                "User-Agent": f"freshdesk-mcp-connector/{__version__}",
            },
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            verify=tls_context(),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def verify_credentials(self) -> None:
        """Fail fast at startup when the domain or API key is wrong."""
        await self.get("/agents/me")

    async def get(self, path: str, params: Params | None = None) -> ApiResponse:
        """GET a path under /api/v2, retrying transient failures with backoff."""
        attempt = 0
        while True:
            try:
                return await self._send(path, params, attempt)
            except ConnectorError as error:
                wait = self._retry_delay(error, attempt)
                if wait is None:
                    raise
                await asyncio.sleep(wait)
                attempt += 1

    async def _send(self, path: str, params: Params | None, attempt: int) -> ApiResponse:
        await self._limiter.acquire()
        started = time.perf_counter()
        try:
            async with self._semaphore:
                response = await self._http.get(path, params=params)
        except httpx.TimeoutException as exc:
            _log_request(path, attempt, started, status=None)
            raise UpstreamError("Freshdesk did not respond in time.") from exc
        except httpx.TransportError as exc:
            _log_request(path, attempt, started, status=None)
            raise UpstreamError(
                f"Could not reach {self._settings.domain}.freshdesk.com.",
                hint="Check FRESHDESK_DOMAIN and network access, then retry.",
            ) from exc

        self._limiter.observe(response.headers)
        _log_request(path, attempt, started, response.status_code, response.headers)
        if not response.is_success:
            raise _error_from_response(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError("Freshdesk returned a response that is not JSON.") from exc
        return ApiResponse(body=body, has_next_page="next" in response.links)

    def _retry_delay(self, error: ConnectorError, attempt: int) -> float | None:
        if not error.retryable or attempt >= self._settings.max_retries:
            return None
        if isinstance(error, RateLimitedError):
            wait = error.retry_after
        else:
            backoff = self._settings.retry_backoff_seconds * 2**attempt
            # Jitter spreads out retries from concurrent calls; it needs no secure randomness.
            wait = backoff + random.uniform(0, backoff)  # noqa: S311
        return wait if wait <= self._settings.max_retry_wait_seconds else None


def _error_from_response(response: httpx.Response) -> ConnectorError:
    status = response.status_code
    if status == 401:
        return AuthError("Freshdesk rejected the credentials.")
    if status == 403:
        return PermissionDeniedError("The API key is not allowed to access this resource.")
    if status == 404:
        return NotFoundError("Freshdesk has no record at this path.")
    if status == 429:
        retry_after = _number_header(response.headers, "Retry-After")
        # Freshdesk's quota window is one minute, so a full window is the safe fallback.
        return RateLimitedError(retry_after=60.0 if retry_after is None else retry_after)
    if status in (400, 422):
        return InvalidRequestError(
            f"Freshdesk rejected the request: {_describe_validation(response)}"
        )
    if status >= 500:
        return UpstreamError(f"Freshdesk returned HTTP {status}.")
    return ConnectorError(f"Unexpected HTTP {status} from Freshdesk.")


def _describe_validation(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "invalid request"
    details = [
        f"{item.get('field', '?')}: {item.get('message', '?')}" for item in body.get("errors", [])
    ]
    return "; ".join(details) or str(body.get("description", "invalid request"))


def _number_header(headers: httpx.Headers, name: str) -> float | None:
    # Freshdesk sends rate-limit headers as decimals, e.g. "50.0".
    try:
        value = float(headers[name])
    except (KeyError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _log_request(
    path: str,
    attempt: int,
    started: float,
    status: int | None,
    headers: httpx.Headers | None = None,
) -> None:
    # Query params are deliberately not logged: they can contain customer emails.
    logger.info(
        "freshdesk_request",
        extra={
            "path": path,
            "status": status,
            "attempt": attempt,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "rate_limit_remaining": _number_header(headers, "X-RateLimit-Remaining")
            if headers is not None
            else None,
        },
    )
