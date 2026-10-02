from datetime import date

import pytest
from pydantic import ValidationError

from freshdesk_connector.models import ContactSearch, TicketSearch


def test_builds_quoted_query_from_filters() -> None:
    search = TicketSearch(status=["open", "pending"], priority=["urgent"], tag="refund")

    assert search.to_query() == "\"(status:2 OR status:3) AND priority:4 AND tag:'refund'\""


def test_date_range_uses_inclusive_operators() -> None:
    search = TicketSearch(created_from=date(2026, 9, 1), created_to=date(2026, 9, 30))

    assert search.to_query() == "\"created_at:>'2026-09-01' AND created_at:<'2026-09-30'\""


def test_contact_query_uses_exact_match() -> None:
    assert ContactSearch(email="priya@example.com").to_query() == "\"email:'priya@example.com'\""


def test_requires_at_least_one_filter() -> None:
    with pytest.raises(ValidationError, match="at least one filter"):
        TicketSearch()


@pytest.mark.parametrize("value", ["refund' OR status:5", 'x" AND y', "back\\slash"])
def test_rejects_values_that_could_break_out_of_quotes(value: str) -> None:
    with pytest.raises(ValidationError):
        TicketSearch(tag=value)


def test_rejects_empty_filter_lists() -> None:
    with pytest.raises(ValidationError):
        TicketSearch(status=[])


def test_rejects_reversed_date_range() -> None:
    later, earlier = date(2026, 9, 30), date(2026, 9, 1)

    with pytest.raises(ValidationError, match="created_from must be on or before"):
        TicketSearch(created_from=later, created_to=earlier)


def test_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        TicketSearch.model_validate({"status": ["escalated"]})
