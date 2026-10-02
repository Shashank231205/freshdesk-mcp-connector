"""End-to-end tests over the MCP protocol: client -> server -> service -> mocked Freshdesk."""

import json
from typing import Any

import pytest
import respx
from factories import ticket
from mcp import Client

from freshdesk_connector.config import Settings
from freshdesk_connector.errors import AuthError
from freshdesk_connector.server import create_server

EXPECTED_TOOLS = {
    "list_tickets",
    "get_ticket",
    "search_tickets",
    "list_ticket_conversations",
    "get_contact",
    "search_contacts",
    "search_contacts_by_name",
}


@pytest.fixture
def authenticated(api: respx.MockRouter) -> respx.MockRouter:
    api.get("/agents/me").respond(200, json={"id": 1})
    return api


def error_payload(result: Any) -> dict[str, Any]:
    """The SDK prefixes tool errors with "Error executing tool <name>: "; the JSON follows."""
    assert result.is_error
    text: str = result.content[0].text
    payload: dict[str, Any] = json.loads(text[text.index("{") :])
    return payload


async def test_exposes_only_read_only_tools(
    settings: Settings, authenticated: respx.MockRouter
) -> None:
    async with Client(create_server(settings)) as client:
        tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in tools)
    assert all(tool.output_schema for tool in tools)


async def test_get_ticket_returns_structured_record(
    settings: Settings, authenticated: respx.MockRouter
) -> None:
    authenticated.get("/tickets/101").respond(200, json=ticket())

    async with Client(create_server(settings)) as client:
        result = await client.call_tool("get_ticket", {"ticket_id": 101})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["status"] == "open"


async def test_search_tickets_accepts_structured_filters(
    settings: Settings, authenticated: respx.MockRouter
) -> None:
    route = authenticated.get("/search/tickets").respond(
        200, json={"results": [ticket()], "total": 1}
    )

    async with Client(create_server(settings)) as client:
        result = await client.call_tool(
            "search_tickets", {"filters": {"status": ["open"], "priority": ["high", "urgent"]}}
        )

    assert not result.is_error
    query = route.calls.last.request.url.params["query"]
    assert query == '"status:2 AND (priority:3 OR priority:4)"'


async def test_connector_errors_reach_the_agent_as_structured_json(
    settings: Settings, authenticated: respx.MockRouter
) -> None:
    authenticated.get("/tickets/999").respond(404)

    async with Client(create_server(settings)) as client:
        result = await client.call_tool("get_ticket", {"ticket_id": 999})

    payload = error_payload(result)
    assert payload["error"] == "not_found"
    assert payload["hint"]


async def test_invalid_arguments_are_rejected_before_any_api_call(
    settings: Settings, authenticated: respx.MockRouter
) -> None:
    route = authenticated.get("/search/tickets")

    async with Client(create_server(settings)) as client:
        result = await client.call_tool("search_tickets", {"filters": {"tag": "x' OR status:5"}})

    assert result.is_error
    assert not route.called


async def test_session_budget_stops_runaway_agents(
    settings: Settings, authenticated: respx.MockRouter
) -> None:
    authenticated.get("/tickets/101").respond(200, json=ticket())
    limited = settings.model_copy(update={"session_call_budget": 2})

    async with Client(create_server(limited)) as client:
        for _ in range(2):
            assert not (await client.call_tool("get_ticket", {"ticket_id": 101})).is_error
        result = await client.call_tool("get_ticket", {"ticket_id": 101})

    assert error_payload(result)["error"] == "session_budget_exceeded"


async def test_startup_fails_fast_on_bad_credentials(
    settings: Settings, api: respx.MockRouter
) -> None:
    api.get("/agents/me").respond(401)
    server = create_server(settings)

    with pytest.raises(AuthError):
        async with Client(server):
            pass
