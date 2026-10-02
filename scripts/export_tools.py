"""Write the MCP tool specification to docs/tools.json, generated from the server code.

uv run python scripts/export_tools.py           # regenerate
uv run python scripts/export_tools.py --check   # fail if the committed spec is stale (CI)
"""

import asyncio
import json
import sys
from pathlib import Path

from freshdesk_connector.config import Settings
from freshdesk_connector.server import INSTRUCTIONS, create_server

SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "tools.json"


async def build_spec() -> str:
    # Listing tools does not start the server, so placeholder settings never reach the network.
    server = create_server(Settings(domain="example", api_key="unused", _env_file=None))
    tools = await server.list_tools()
    spec = {
        "server": "freshdesk",
        "instructions": INSTRUCTIONS,
        "tools": [tool.model_dump(by_alias=True, exclude_none=True, mode="json") for tool in tools],
    }
    return json.dumps(spec, indent=2) + "\n"


def main() -> None:
    spec = asyncio.run(build_spec())
    if "--check" in sys.argv:
        current = SPEC_PATH.read_text(encoding="utf-8") if SPEC_PATH.exists() else ""
        if current != spec:
            sys.exit("docs/tools.json is out of date. Run: uv run python scripts/export_tools.py")
        print("docs/tools.json is up to date.")
        return
    SPEC_PATH.write_text(spec, encoding="utf-8", newline="\n")
    print("Wrote docs/tools.json")


if __name__ == "__main__":
    main()
