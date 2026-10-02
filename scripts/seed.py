"""Fill a Freshdesk trial account with fictional tickets so the connector has data to read.

This is a development tool and the only code in the repo that writes to Freshdesk. The
connector itself is read-only. Requester emails use example.com, a domain reserved for
documentation, so no real person is ever contacted.

Safe to run again: tickets whose subject already exists with the seed tag are skipped, so
only tickets added to seed_data.json since the last run are created.

    uv run python scripts/seed.py
"""

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from freshdesk_connector.client import tls_context
from freshdesk_connector.config import Settings
from freshdesk_connector.models import PRIORITY_CODES, SOURCE_NAMES, STATUS_CODES

DATA_PATH = Path(__file__).resolve().parent / "seed_data.json"
SEED_TAG = "demo-seed"
MAX_LIST_PAGES = 10  # 1,000 tickets; a demo account never gets near it
SOURCE_CODES = {name: code for code, name in SOURCE_NAMES.items()}


def main() -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    settings = Settings()  # reads FRESHDESK_DOMAIN and FRESHDESK_API_KEY from .env
    auth = httpx.BasicAuth(settings.api_key.get_secret_value(), "X")

    with httpx.Client(
        base_url=settings.base_url,
        auth=auth,
        timeout=settings.timeout_seconds,
        verify=tls_context(),
    ) as http:
        existing = _seeded_subjects(http)
        missing = [t for t in data["tickets"] if t["subject"] not in existing]
        if not missing:
            print("All demo tickets already exist.")
            return

        follow_ups = 0
        for spec in missing:
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

    total = len(data["tickets"])
    print(
        f"Created {len(missing)} of {total} demo tickets ({total - len(missing)} already "
        f"existed) and {follow_ups} replies or notes."
    )
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


def _seeded_subjects(http: httpx.Client) -> set[str]:
    """Subjects of tickets this script created earlier.

    Uses the ticket list rather than search, because search can lag minutes behind.
    """
    subjects: set[str] = set()
    params: dict[str, str | int] = {"updated_since": "2000-01-01T00:00:00Z", "per_page": 100}
    for page in range(1, MAX_LIST_PAGES + 1):
        response = _request(http, "GET", "/tickets", params={**params, "page": page})
        subjects |= {t["subject"] for t in response.json() if SEED_TAG in (t.get("tags") or [])}
        if "next" not in response.links:
            break
    return subjects


def _request(http: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    while True:
        response = http.request(method, path, **kwargs)
        if response.status_code != 429:
            break
        wait = float(response.headers.get("Retry-After", "60"))
        print(f"Rate limited, waiting {wait:.0f}s")
        time.sleep(wait)
    if response.is_error:
        sys.exit(f"{method} {path} failed with HTTP {response.status_code}: {response.text[:300]}")
    return response


if __name__ == "__main__":
    main()
