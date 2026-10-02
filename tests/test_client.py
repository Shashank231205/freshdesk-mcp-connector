import base64

import httpx
import pytest
import respx

from freshdesk_connector.client import FreshdeskClient, RateLimiter
from freshdesk_connector.config import Settings
from freshdesk_connector.errors import (
    AuthError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    UpstreamError,
)


async def test_sends_api_key_as_basic_auth(settings: Settings, api: respx.MockRouter) -> None:
    route = api.get("/agents/me").respond(200, json={"id": 1})

    async with FreshdeskClient(settings) as client:
        await client.verify_credentials()

    expected = base64.b64encode(b"test-key:X").decode()
    assert route.calls.last.request.headers["Authorization"] == f"Basic {expected}"


async def test_reports_next_page_from_link_header(
    settings: Settings, api: respx.MockRouter
) -> None:
    next_link = f'<{settings.base_url}/tickets?page=2>; rel="next"'
    api.get("/tickets").respond(200, json=[], headers={"Link": next_link})

    async with FreshdeskClient(settings) as client:
        response = await client.get("/tickets")

    assert response.has_next_page


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, AuthError), (404, NotFoundError)],
)
async def test_client_errors_are_not_retried(
    settings: Settings, api: respx.MockRouter, status: int, error: type[Exception]
) -> None:
    route = api.get("/tickets/1").respond(status)

    async with FreshdeskClient(settings) as client:
        with pytest.raises(error):
            await client.get("/tickets/1")

    assert route.call_count == 1


async def test_validation_error_names_the_field(settings: Settings, api: respx.MockRouter) -> None:
    body = {"errors": [{"field": "per_page", "message": "Must be a number", "code": "invalid"}]}
    api.get("/tickets").respond(400, json=body)

    async with FreshdeskClient(settings) as client:
        with pytest.raises(InvalidRequestError, match="per_page: Must be a number"):
            await client.get("/tickets")


async def test_retries_after_rate_limit(settings: Settings, api: respx.MockRouter) -> None:
    route = api.get("/tickets/1")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"id": 1}),
    ]

    async with FreshdeskClient(settings) as client:
        response = await client.get("/tickets/1")

    assert response.body == {"id": 1}
    assert route.call_count == 2


async def test_long_rate_limit_wait_is_returned_to_the_agent(
    settings: Settings, api: respx.MockRouter
) -> None:
    route = api.get("/tickets/1").respond(429, headers={"Retry-After": "45"})

    async with FreshdeskClient(settings) as client:
        with pytest.raises(RateLimitedError) as caught:
            await client.get("/tickets/1")

    assert caught.value.retry_after == 45
    assert route.call_count == 1


async def test_server_errors_are_retried_then_raised(
    settings: Settings, api: respx.MockRouter
) -> None:
    route = api.get("/tickets/1").respond(503)

    async with FreshdeskClient(settings) as client:
        with pytest.raises(UpstreamError):
            await client.get("/tickets/1")

    assert route.call_count == settings.max_retries + 1


async def test_timeout_becomes_upstream_error(settings: Settings, api: respx.MockRouter) -> None:
    api.get("/tickets/1").mock(side_effect=httpx.ConnectTimeout("timed out"))

    async with FreshdeskClient(settings) as client:
        with pytest.raises(UpstreamError, match="did not respond in time"):
            await client.get("/tickets/1")


async def test_rate_limiter_waits_when_server_reports_no_quota_left() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = RateLimiter(per_minute=60, clock=lambda: 0.0, sleep=fake_sleep)
    limiter.observe(httpx.Headers({"X-RateLimit-Remaining": "0"}))
    await limiter.acquire()

    assert sleeps == [pytest.approx(1.0)]


async def test_rate_limiter_charges_extra_credits() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = RateLimiter(per_minute=60, clock=lambda: 0.0, sleep=fake_sleep)
    await limiter.acquire()
    limiter.observe(httpx.Headers({"X-RateLimit-Used-CurrentRequest": "60"}))
    await limiter.acquire()

    assert sleeps, "a request costing the whole quota should make the next call wait"
