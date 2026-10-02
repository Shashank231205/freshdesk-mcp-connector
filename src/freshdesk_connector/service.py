"""Read operations exposed to the agent: fetch through the cache, then shape compact records.

Shaping is where context size is controlled. Codes become words, long text is cut to a
configured length, and personal data is masked unless the merchant turns masking off.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from freshdesk_connector.cache import TTLCache
from freshdesk_connector.client import ApiResponse, FreshdeskClient, Params
from freshdesk_connector.config import Settings
from freshdesk_connector.errors import UpstreamError
from freshdesk_connector.models import (
    PRIORITY_CODES,
    SEARCH_MAX_PAGES,
    SEARCH_PAGE_SIZE,
    SOURCE_NAMES,
    STATUS_CODES,
    Contact,
    ContactMatch,
    ContactMatches,
    ContactSearch,
    Conversation,
    Page,
    Ticket,
    TicketSearch,
    TicketSummary,
)

TicketOrder = Literal["created_at", "updated_at", "due_by", "status"]

_PHONE_DIGITS_SHOWN = 4  # enough to confirm a number with the customer, too few to use it
_STATUS_NAMES = {code: name for name, code in STATUS_CODES.items()}
_PRIORITY_NAMES = {code: name for name, code in PRIORITY_CODES.items()}


class FreshdeskService:
    def __init__(self, client: FreshdeskClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._cache: TTLCache[ApiResponse] = TTLCache(
            settings.cache_ttl_seconds, settings.cache_max_entries
        )

    async def list_tickets(
        self,
        *,
        page: int = 1,
        per_page: int | None = None,
        updated_since: datetime | None = None,
        requester_id: int | None = None,
        company_id: int | None = None,
        order_by: TicketOrder = "updated_at",
        descending: bool = True,
    ) -> Page[TicketSummary]:
        params: dict[str, str | int] = {
            "page": page,
            "per_page": per_page or self._settings.default_page_size,
            "order_by": order_by,
            "order_type": "desc" if descending else "asc",
        }
        if updated_since is not None:
            params["updated_since"] = _utc_timestamp(updated_since)
        if requester_id is not None:
            params["requester_id"] = requester_id
        if company_id is not None:
            params["company_id"] = company_id

        response = await self._get("/tickets", params)
        with _expected_shape():
            items = [self._ticket_summary(raw) for raw in response.body]
        return Page(items=items, page=page, has_more=response.has_next_page)

    async def get_ticket(self, ticket_id: int) -> Ticket:
        response = await self._get(f"/tickets/{ticket_id}")
        with _expected_shape():
            return self._ticket(response.body)

    async def search_tickets(self, search: TicketSearch, page: int = 1) -> Page[TicketSummary]:
        response = await self._get("/search/tickets", {"query": search.to_query(), "page": page})
        with _expected_shape():
            items = [self._ticket_summary(raw) for raw in response.body["results"]]
            total = int(response.body["total"])
        return Page(items=items, page=page, has_more=_search_has_more(page, total), total=total)

    async def list_conversations(
        self, ticket_id: int, *, page: int = 1, per_page: int | None = None
    ) -> Page[Conversation]:
        params = {"page": page, "per_page": per_page or self._settings.default_page_size}
        response = await self._get(f"/tickets/{ticket_id}/conversations", params)
        with _expected_shape():
            items = [self._conversation(raw) for raw in response.body]
        return Page(items=items, page=page, has_more=response.has_next_page)

    async def get_contact(self, contact_id: int) -> Contact:
        response = await self._get(f"/contacts/{contact_id}")
        with _expected_shape():
            return self._contact(response.body)

    async def search_contacts(self, search: ContactSearch, page: int = 1) -> Page[Contact]:
        response = await self._get("/search/contacts", {"query": search.to_query(), "page": page})
        with _expected_shape():
            items = [self._contact(raw) for raw in response.body["results"]]
            total = int(response.body["total"])
        return Page(items=items, page=page, has_more=_search_has_more(page, total), total=total)

    async def search_contacts_by_name(self, name: str) -> ContactMatches:
        # Freshdesk's autocomplete also returns phone numbers; only id and name are exposed.
        response = await self._get("/contacts/autocomplete", {"term": name})
        with _expected_shape():
            return ContactMatches(
                items=[ContactMatch(id=raw["id"], name=raw["name"]) for raw in response.body]
            )

    async def _get(self, path: str, params: Params | None = None) -> ApiResponse:
        key = (path, tuple(sorted((params or {}).items())))
        return await self._cache.get_or_load(key, lambda: self._client.get(path, params))

    def _ticket_summary(self, raw: dict[str, Any]) -> TicketSummary:
        return TicketSummary.model_validate(self._ticket_fields(raw))

    def _ticket(self, raw: dict[str, Any]) -> Ticket:
        description, truncated = self._truncate(raw.get("description_text") or "")
        return Ticket.model_validate(
            {
                **self._ticket_fields(raw),
                "source": SOURCE_NAMES.get(raw["source"], f"unknown_{raw['source']}"),
                "company_id": raw.get("company_id"),
                "description": description,
                "description_truncated": truncated,
                "cc_emails": [self._email(e) for e in raw.get("cc_emails") or []],
                "custom_fields": raw.get("custom_fields") or {},
            }
        )

    def _ticket_fields(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": raw["id"],
            "subject": raw.get("subject") or "",
            "status": _STATUS_NAMES.get(raw["status"], f"custom_{raw['status']}"),
            "priority": _PRIORITY_NAMES.get(raw["priority"], f"unknown_{raw['priority']}"),
            "type": raw.get("type"),
            "requester_id": raw.get("requester_id"),
            "responder_id": raw.get("responder_id"),
            "group_id": raw.get("group_id"),
            "tags": raw.get("tags") or [],
            "created_at": raw["created_at"],
            "updated_at": raw["updated_at"],
            "due_by": raw.get("due_by"),
        }

    def _conversation(self, raw: dict[str, Any]) -> Conversation:
        body, truncated = self._truncate(raw.get("body_text") or "")
        from_email = raw.get("from_email")
        return Conversation.model_validate(
            {
                "id": raw["id"],
                "body": body,
                "body_truncated": truncated,
                "incoming": raw["incoming"],
                "private": raw["private"],
                "user_id": raw.get("user_id"),
                "from_email": self._email(from_email) if from_email else None,
                "created_at": raw["created_at"],
            }
        )

    def _contact(self, raw: dict[str, Any]) -> Contact:
        email, phone, mobile = raw.get("email"), raw.get("phone"), raw.get("mobile")
        return Contact.model_validate(
            {
                "id": raw["id"],
                "name": raw.get("name") or "",
                "email": self._email(email) if email else None,
                "phone": self._phone(phone) if phone else None,
                "mobile": self._phone(mobile) if mobile else None,
                "company_id": raw.get("company_id"),
                "active": raw.get("active", False),
                "created_at": raw["created_at"],
                "updated_at": raw["updated_at"],
            }
        )

    def _truncate(self, text: str) -> tuple[str, bool]:
        limit = self._settings.max_text_chars
        return (text[:limit], True) if len(text) > limit else (text, False)

    def _email(self, value: str) -> str:
        if not self._settings.mask_pii:
            return value
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}" if domain else "***"

    def _phone(self, value: str) -> str:
        if not self._settings.mask_pii:
            return value
        visible = _PHONE_DIGITS_SHOWN
        return f"***{value[-visible:]}" if len(value) > visible else "***"


def _search_has_more(page: int, total: int) -> bool:
    return page < SEARCH_MAX_PAGES and page * SEARCH_PAGE_SIZE < total


def _utc_timestamp(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _expected_shape() -> Iterator[None]:
    """Turn a malformed Freshdesk payload into a clean error instead of a crash."""
    try:
        yield
    except (KeyError, TypeError, ValueError) as exc:  # pydantic's ValidationError is a ValueError
        raise UpstreamError("Freshdesk returned a response in an unexpected format.") from exc
