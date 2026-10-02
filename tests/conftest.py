from collections.abc import Iterator

import pytest
import respx

from freshdesk_connector.config import Settings

BASE_URL = "https://acme.freshdesk.com/api/v2"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        domain="acme",
        api_key="test-key",
        retry_backoff_seconds=0.001,
        max_retry_wait_seconds=1,
        max_text_chars=50,
        _env_file=None,
    )


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router
