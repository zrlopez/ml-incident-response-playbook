"""
src/config.py
=============
Pydantic-settings configuration for the ML Incident Response Platform.

All secrets use SecretStr to prevent accidental logging. Access the raw
value only at the point of use via .get_secret_value().

Remediation changelog:
  SEC-01   jwt_secret_key promoted to SecretStr + reject_placeholder validator.
  LOW-01   slack_webhook_url promoted to SecretStr — Incoming Webhook URLs
           are bearer credentials and must be masked in repr/logs.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


def _reject_placeholder(v: SecretStr, field_name: str, min_len: int = 32) -> SecretStr:
    """
    Reject placeholder values and enforce a minimum length.

    Called from @field_validator for every secret field so that starting
    the application with an unconfigured secret fails fast with a clear
    error rather than silently accepting an insecure value.
    """
    _PLACEHOLDER_PATTERNS = [
        r"(?i)replace.with",
        r"(?i)your.secret",
        r"(?i)change.me",
        r"(?i)placeholder",
        r"(?i)todo",
        r"(?i)example",
        r"(?i)fixme",
        r"(?i)insert",
        r"(?i)enter",
    ]
    raw = v.get_secret_value()
    for pattern in _PLACEHOLDER_PATTERNS:
        if re.search(pattern, raw):
            raise ValueError(
                f"{field_name} contains a placeholder value "
                f"(matched pattern {pattern!r}). "
                f"Set a real secret before starting the application."
            )
    if len(raw) < min_len:
        raise ValueError(
            f"{field_name} is too short: got {len(raw)} chars, "
            f"minimum is {min_len}."
        )
    return v


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All secrets use SecretStr to prevent accidental logging. Access the
    raw value only at the point of use via field.get_secret_value().
    """

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── JWT (HS256 dev/CI fallback) ────────────────────────────────────────
    jwt_secret_key: SecretStr = Field(
        ...,
        description=(
            "HMAC signing secret for HS256 JWT tokens (dev/CI only). "
            "Minimum 32 characters. Use RS256 + RSA keys in production."
        ),
    )

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def _validate_jwt_secret(cls, v: SecretStr) -> SecretStr:
        return _reject_placeholder(v, "JWT_SECRET_KEY", min_len=32)

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./incidents.db",
        description="Async SQLAlchemy database URL.",
    )

    # ── Redis ─────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used by the JWT denylist.",
    )

    # ── Auth stub users (dev/CI only) ─────────────────────────────────────
    dev_admin_password: SecretStr = Field(
        default=SecretStr(""),
        description="Dev/CI admin password. Empty string disables stub user creation.",
    )
    dev_analyst_password: SecretStr = Field(
        default=SecretStr(""),
        description="Dev/CI analyst password. Empty string disables stub user creation.",
    )
    dev_operator_password: SecretStr = Field(
        default=SecretStr(""),
        description="Dev/CI operator password. Empty string disables stub user creation.",
    )

    # ── Environment ───────────────────────────────────────────────────────
    environment: str = Field(
        default="development",
        description="Runtime environment tag (development | staging | production).",
    )

    # ── Observability ─────────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = Field(
        default="http://otel-collector:4317",
        description="OpenTelemetry OTLP gRPC endpoint.",
    )
    disable_otel: bool = Field(
        default=False,
        description="Set true to disable OTel tracing in local dev without Docker.",
    )

    # ── Alerting ──────────────────────────────────────────────────────────
    alert_email: str = Field(
        default="oncall@yourorg.com",
        description="Recipient for incident alert emails from the Airflow DAG.",
    )
    # LOW-01: Webhook URLs are bearer credentials — must be SecretStr so
    # they are masked ('**********') in all repr(), str(), and log output.
    slack_webhook_url: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Optional Slack Incoming Webhook URL for dual-channel alerting. "
            "Treat as a secret — never log or expose in API responses."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the application settings singleton.

    HIGH-D: This function is lru_cache-wrapped. The cache is cleared between
    test functions by the autouse fixture in tests/conftest.py:

        @pytest.fixture(autouse=True)
        def _clear_settings_lru_cache():
            get_settings.cache_clear()
            yield
            get_settings.cache_clear()

    NEVER call os.environ directly in application code. Always use:
        settings = get_settings()
        settings.some_field
    """
    return Settings()  # type: ignore[call-arg]
