"""Runtime settings, read from environment variables or a local .env file."""

import re
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class Settings(BaseSettings):
    """Connector configuration. Every field maps to a FRESHDESK_* environment variable."""

    model_config = SettingsConfigDict(
        env_prefix="FRESHDESK_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    domain: str = Field(description="Helpdesk subdomain, e.g. 'acme' for acme.freshdesk.com.")
    api_key: SecretStr = Field(
        min_length=1, description="API key of the agent the connector acts as."
    )

    timeout_seconds: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.5, gt=0)
    max_retry_wait_seconds: float = Field(default=30.0, gt=0)
    rate_limit_per_minute: int = Field(default=50, gt=0)
    max_concurrency: int = Field(default=4, gt=0)

    cache_ttl_seconds: float = Field(default=60.0, ge=0)
    cache_max_entries: int = Field(default=1024, gt=0)

    session_call_budget: int = Field(default=200, gt=0)
    default_page_size: int = Field(default=30, ge=1, le=100)
    max_text_chars: int = Field(default=2000, gt=0)
    mask_pii: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("domain")
    @classmethod
    def _normalise_domain(cls, value: str) -> str:
        subdomain = value.strip().lower().removeprefix("https://").rstrip("/")
        subdomain = subdomain.removesuffix(".freshdesk.com")
        if not _SUBDOMAIN.fullmatch(subdomain):
            raise ValueError("must be a Freshdesk subdomain such as 'acme' or 'acme.freshdesk.com'")
        return subdomain

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}.freshdesk.com/api/v2"
