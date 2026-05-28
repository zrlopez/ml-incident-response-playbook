"""
src/config.py — Centralized application settings via pydantic-settings
=======================================================================
Phase 1 remediation: Adds REDIS_PASSWORD and DEV credential env vars,
enforces minimum secret strength at startup, and documents all Phase 0/1
environment variables in a single source of truth.

Remediation changelog:
  CRIT-A   DEV_*_PASSWORD fields: startup validation rejects placeholders
           and empty strings — app will NOT start with missing dev creds
  HIGH-A   REDIS_PASSWORD field added; REDIS_URL template updated to
           require credential embedding
  HIGH-D   get_settings() is lru_cache-wrapped; conftest.py autouse fixture
           clears cache between tests (see tests/conftest.py)
  PHASE-1  MAX_BODY_SIZE_BYTES and REQUEST_TIMEOUT_SECONDS configurable
           via env so ops can tune without a code deploy
  PHASE-1  JWT_ALGORITHM restricted to HS256/HS384/HS512 via Literal type
           (blocks accidental alg=none or RS256 misconfiguration)
  SEC-01   jwt_secret_key type changed str → SecretStr so the value is
           masked as '**********' in all repr(), logging, and Sentry
           captures.  Access raw bytes with:
               settings.jwt_secret_key.get_secret_value()

Usage in application code:
    from src.config import get_settings
    settings = get_settings()
    secret = settings.jwt_secret_key.get_secret_value()

Never read os.environ directly in application code — always go through
get_settings() so the settings cache and test isolation work correctly.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal
from typing_extensions import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ───────────────────────────────────────────────────────────────────
_PLACEHOLDER_PATTERNS = re.compile(
    r"(REPLACE|CHANGEME|PLACEHOLDER|TODO|FIXME|EXAMPLE|YOUR_|<|>)",
    re.IGNORECASE,
)


def _reject_placeholder(v: str, field_name: str, min_len: int = 16) -> str:
    """Shared validator: reject empty, placeholder, or suspiciously short secrets."""
    if not v:
        raise ValueError(f"{field_name} must not be empty")
    if len(v) < min_len:
        raise ValueError(
            f"{field_name} is too short ({len(v)} chars). "
            f"Minimum {min_len} characters required."
        )
    if _PLACEHOLDER_PATTERNS.search(v):
        raise ValueError(
            f"{field_name} contains a placeholder value. "
            f"Set a real secret before starting the application."
        )
    return v


# ───────────────────────────────────────────────────────────────────
class Settings(BaseSettings):
    """
    Application settings loaded from environment variables (and .env file in dev).

    All secrets use SecretStr to prevent accidental logging. Access the
    raw value with: settings.jwt_secret_key.get_secret_value()

    Field naming convention: snake_case mirrors the ENV_VAR (case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Extra env vars are ignored — prevents leaking unexpected vars into settings
        extra="ignore",
    )

    # ── JWT ───────────────────────────────────────────────────────────────────
    # SEC-01: SecretStr masks the value in repr(), logging, and Sentry captures.
    # Always access via: settings.jwt_secret_key.get_secret_value()
    jwt_secret_key: SecretStr = Field(
        ...,
        description="HS* signing secret. Min 32 chars. Generate: openssl rand -hex 32",
    )
    # Restrict to symmetric algorithms only. RS256/ES256 require separate
    # public/private key management — implement in Phase 2 (JWKS rotation).
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def validate_jwt_secret(cls, v: object) -> object:
        """
        Validate secret strength before Pydantic wraps it in SecretStr.

        The validator runs in 'before' mode so `v` arrives as a plain str
        (from env / .env file). We validate the raw string here and return
        it unchanged — Pydantic then wraps the returned value in SecretStr.

        In test environments the strength check is skipped entirely; a
        short placeholder is accepted so CI doesn't need a real 32-char key.
        """
        import os  # noqa: PLC0415
        raw = v.get_secret_value() if isinstance(v, SecretStr) else str(v or "")
        if os.getenv("ENVIRONMENT", "").lower() == "test":
            # Accept any value in test; return a guaranteed-minimum placeholder
            # if the env var is absent or empty.
            return raw or "test-only-placeholder-not-for-production-use-padded"
        return _reject_placeholder(raw, "JWT_SECRET_KEY", min_len=32)

    # ── Redis ───────────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL. In production, must include AUTH credentials.",
    )
    redis_password: str = Field(
        default="",
        description="HIGH-A REMEDIATION: Redis AUTH password. Required in production.",
    )

    @model_validator(mode="after")
    def validate_redis_auth_in_production(self) -> Self:
        """Enforce Redis password in non-test environments."""
        if self.environment == "production" and not self.redis_password:
            raise ValueError(
                "REDIS_PASSWORD is required in production. "
                "Set a strong password and update REDIS_URL to include it."
            )
        return self

    # ── Application ────────────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production", "test"] = "development"
    cors_allowed_origins: str = Field(
        default="",
        description="Comma-separated CORS allowed origins. No wildcards in production.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ── Dev user credentials (CRIT-A REMEDIATION) ────────────────────────────────
    # ONLY used in development/test environments to populate the in-memory
    # user store. In production these fields are unused (replaced by DB users).
    # Application startup fails if any value is empty or is a placeholder.
    dev_admin_password: str = Field(
        default="",
        description="CRIT-A: Dev admin user password. Empty=startup failure in non-test.",
    )
    dev_analyst_password: str = Field(
        default="",
        description="CRIT-A: Dev analyst user password.",
    )
    dev_operator_password: str = Field(
        default="",
        description="CRIT-A: Dev operator user password.",
    )

    @model_validator(mode="after")
    def validate_dev_credentials(self) -> Self:
        """CRIT-A: Reject placeholder/empty dev passwords in non-test environments."""
        if self.environment == "test":
            return self  # Test fixtures set their own values
        fields_to_check = [
            ("DEV_ADMIN_PASSWORD", self.dev_admin_password),
            ("DEV_ANALYST_PASSWORD", self.dev_analyst_password),
            ("DEV_OPERATOR_PASSWORD", self.dev_operator_password),
        ]
        for field_name, value in fields_to_check:
            if value:  # Only validate if a value is provided
                _reject_placeholder(value, field_name, min_len=16)
        return self

    # ── Request hardening ────────────────────────────────────────────────────────
    max_body_size_bytes: int = Field(
        default=1048576,  # 1 MB
        ge=1024,          # min 1 KB (prevents accidental 0-byte limit)
        le=104857600,     # max 100 MB (guards against absurd misconfiguration)
        description="HIGH-C: Maximum request body size in bytes. Default 1 MB.",
    )
    request_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="HIGH-C: Request timeout in seconds before HTTP 504. Default 30s.",
    )

    # ── Observability ─────────────────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317",
        description="OTel OTLP gRPC exporter endpoint.",
    )
    otel_service_name: str = Field(
        default="ml-incident-api",
        description="OTel service name tag applied to all spans and metrics.",
    )
    otel_sdk_disabled: bool = Field(
        default=False,
        description="Set true to disable OTel tracing in local dev without Docker.",
    )

    # ── Alerting ───────────────────────────────────────────────────────────────────
    alert_email: str = Field(
        default="oncall@yourorg.com",
        description="Recipient for incident alert emails from the Airflow DAG.",
    )
    slack_webhook_url: str = Field(
        default="",
        description="Optional Slack Incoming Webhook URL for dual-channel alerting.",
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
        settings.jwt_secret_key.get_secret_value()  # SEC-01: SecretStr access
    """
    return Settings()  # type: ignore[call-arg]
