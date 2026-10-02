"""Fill a Freshdesk trial account with fictional tickets so the connector has data to read.

This is a development tool and the only code in the repo that writes to Freshdesk. The
connector itself is read-only. Requester emails use example.com, a domain reserved for
documentation, so no real person is ever contacted.

    uv run python scripts/seed.py            # skips if demo data already exists
    uv run python scripts/seed.py --force    # add another copy
"""

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from freshdesk_connector.config import Settings
from freshdesk_connector.models import PRIORITY_CODES, SOURCE_NAMES, STATUS_CODES

DATA_PATH = Path(__file__).resolve().parent / "seed_data.json"
SEED_TAG = "demo-seed"
SOURCE_CODES = {name: code for code, name in SOURCE_NAMES.items()}


def main() -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    settings = Settings()  # reads FRESHDESK_DOMAIN and FRESHDESK_API_KEY from .env
    auth = httpx.BasicAuth(settings.api_key.get_secret_value(), "X")

    with httpx.Client(
        base_url=settings.base_url, auth=auth, timeout=settings.timeout_seconds
    ) as http:
        if _already_seeded(http) and "--force" not in sys.argv:
            print(f"Tickets tagged '{SEED_TAG}' already exist. Use --force to add more.")
            return

        follow_ups = 0
        for spec in data["tickets"]:
            customer = data["customers"][spec["customer"]]
            created = _request(http, "POST", "/tickets", json=_ticket_payload(spec, customer))
            ticket_id = created.json()["id"]
            print(f"ticket {ticket_id}: {spec['subject']}")

            for follow_up in spec.get("follow_ups", []):
                if follow_up["kind"] == "reply":
                    path, payload = f"/tickets/{ticket_id}/reply", {"body": follow_up["body"]}
                else:
                    path, payload = f"/tickets/{ticket_id}/notes", {**follow_up, "private": True}
                    payload.pop("kind")
                _request(http, "POST", path, json=payload)
                follow_ups += 1

    print(f"Seeded {len(data['tickets'])} tickets and {follow_ups} replies or notes.")
    print("Freshdesk search can take a few minutes to index new tickets.")


def _ticket_payload(spec: dict[str, Any], customer: dict[str, str]) -> dict[str, Any]:
    return {
        **customer,
        "subject": spec["subject"],
        "description": spec["description"],
        "status": STATUS_CODES[spec["status"]],
        "priority": PRIORITY_CODES[spec["priority"]],
        "source": SOURCE_CODES[spec["source"]],
        "tags": [*spec["tags"], SEED_TAG],
    }


def _already_seeded(http: httpx.Client) -> bool:
    query = f"\"tag:'{SEED_TAG}'\""
    response = _request(http, "GET", "/search/tickets", params={"query": query})
    return int(response.json().get("total", 0)) > 0


def _request(http: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    while True:
        response = http.request(method, path, **kwargs)
        if response.status_code != 429:
            break
        wait = int(response.headers.get("Retry-After", "60"))
        print(f"Rate limited, waiting {wait}s")
        time.sleep(wait)
    if response.is_error:
        sys.exit(f"{method} {path} failed with HTTP {response.status_code}: {response.text[:300]}")
    return response


if __name__ == "__main__":
    main()
