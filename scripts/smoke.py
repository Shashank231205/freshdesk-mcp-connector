"""Live end-to-end check against a real Freshdesk account, over the real MCP stdio transport.

It launches the connector exactly as an agent platform would, calls every tool, and
checks error handling and caching. Run scripts/seed.py first.

    uv run python scripts/smoke.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWN_EMAIL = "priya.sharma@example.com"  # created by scripts/seed.py
KNOWN_NAME = "sharma"
MISSING_TICKET_ID = 999_999_999


@dataclass
class Smoke:
    client: Client
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """Call a tool and return its structured result (or error payload) and latency."""
        started = time.perf_counter()
        result = await self.client.call_tool(tool, args)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if not result.is_error:
            return result.structured_content or {}, elapsed_ms
        text = "".join(c.text for c in result.content if isinstance(c, TextContent))
        start = text.find("{")
        return (json.loads(text[start:]) if start >= 0 else {"error": text}), elapsed_ms

    def record(self, name: str, passed: bool, detail: str) -> None:
        self.results.append((name, passed, detail))

    async def run(self) -> None:
        tools = (await self.client.list_tools()).tools
        read_only = all(t.annotations and t.annotations.read_only_hint for t in tools)
        self.record("7 read-only tools", len(tools) == 7 and read_only, f"{len(tools)} tools")

        tickets, ms = await self.call("list_tickets", {"per_page": 5})
        items = tickets.get("items", [])
        self.record("list_tickets", bool(items), f"{len(items)} items, {ms:.0f} ms")
        if not items:
            return
        first_id = items[0]["id"]

        ticket, cold_ms = await self.call("get_ticket", {"ticket_id": first_id})
        self.record("get_ticket", ticket.get("id") == first_id, f"{cold_ms:.0f} ms")

        _, warm_ms = await self.call("get_ticket", {"ticket_id": first_id})
        self.record("repeat read cached", warm_ms < cold_ms, f"{cold_ms:.0f} -> {warm_ms:.1f} ms")

        thread, ms = await self.call("list_ticket_conversations", {"ticket_id": first_id})
        self.record("list_ticket_conversations", "items" in thread, f"{ms:.0f} ms")

        filters = {"status": ["open", "pending"], "priority": ["high", "urgent"]}
        found, ms = await self.call("search_tickets", {"filters": filters})
        self.record("search_tickets", "total" in found, f"total={found.get('total')}, {ms:.0f} ms")

        contact, ms = await self.call("get_contact", {"contact_id": ticket.get("requester_id")})
        email = str(contact.get("email") or "")
        self.record("get_contact masks PII", "***" in email, f"email={email}, {ms:.0f} ms")

        matches, ms = await self.call("search_contacts", {"filters": {"email": KNOWN_EMAIL}})
        self.record("search_contacts", matches.get("total", 0) >= 1, f"{ms:.0f} ms")

        named, ms = await self.call("search_contacts_by_name", {"name": KNOWN_NAME})
        names = [m["name"] for m in named.get("items", [])]
        self.record("search_contacts_by_name", "Priya Sharma" in names, f"{ms:.0f} ms")

        missing, _ = await self.call("get_ticket", {"ticket_id": MISSING_TICKET_ID})
        self.record("unknown id -> not_found", missing.get("error") == "not_found", "")

        injected, _ = await self.call("search_tickets", {"filters": {"tag": "x' OR status:5"}})
        self.record("query injection rejected", "error" in injected, "")


async def main() -> int:
    # The stdio transport passes only a minimal environment to the child, so forward the
    # connector's own settings explicitly. Running from the repo root lets it find .env.
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "freshdesk_connector.server"],
        env={k: v for k, v in os.environ.items() if k.startswith("FRESHDESK_")},
        cwd=REPO_ROOT,
    )
    async with Client(server) as client:
        smoke = Smoke(client)
        await smoke.run()

    width = max(len(name) for name, _, _ in smoke.results)
    for name, passed, detail in smoke.results:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<{width}}  {detail}")
    failed = sum(not passed for _, passed, _ in smoke.results)
    print(f"\n{len(smoke.results) - failed}/{len(smoke.results)} checks passed.")
    if failed:
        print("Search checks can fail for a few minutes after seeding while Freshdesk indexes.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
