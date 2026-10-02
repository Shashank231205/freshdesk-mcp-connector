"""Builders for Freshdesk API payloads. All records are fictional."""

from typing import Any


def ticket(ticket_id: int = 101, **overrides: Any) -> dict[str, Any]:
    return {
        "id": ticket_id,
        "subject": "Refund not received",
        "status": 2,
        "priority": 3,
        "source": 1,
        "type": "Refund",
        "requester_id": 501,
        "responder_id": None,
        "group_id": None,
        "company_id": None,
        "tags": ["refund"],
        "cc_emails": ["priya.sharma@example.com"],
        "custom_fields": {"order_id": "ORD-1001"},
        "description_text": "I was charged twice for order ORD-1001.",
        "created_at": "2026-09-28T10:00:00Z",
        "updated_at": "2026-09-29T08:30:00Z",
        "due_by": "2026-10-01T10:00:00Z",
        **overrides,
    }


def contact(contact_id: int = 501, **overrides: Any) -> dict[str, Any]:
    return {
        "id": contact_id,
        "name": "Priya Sharma",
        "email": "priya.sharma@example.com",
        "phone": "+91 98765 43210",
        "mobile": None,
        "company_id": None,
        "active": True,
        "created_at": "2026-01-15T09:00:00Z",
        "updated_at": "2026-09-28T10:00:00Z",
        **overrides,
    }


def conversation(conversation_id: int = 9001, **overrides: Any) -> dict[str, Any]:
    return {
        "id": conversation_id,
        "body_text": "We have started the refund. It takes 5-7 working days.",
        "incoming": False,
        "private": False,
        "user_id": 77,
        "from_email": "support@acme.example",
        "created_at": "2026-09-28T12:00:00Z",
        **overrides,
    }
