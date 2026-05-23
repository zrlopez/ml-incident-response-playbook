"""
Application configuration — enterprise-grade pydantic-settings implementation.

Remediation: R-12
Replaces the original static dataclass with a pydantic-settings BaseSettings
class that reads from environment variables and .env files with validation.

Priority order (highest → lowest):
  1. Environment variables
  2. .env.{APP_ENV} file   (e.g. .env.production)
  3. .env file
  4. Field defaults

Usage:
    from src.config import get_settings
    settings = get_settings()  # cached singleton

    # FastAPI dependency injection:
    from fastapi import Depends
    def my_route(settings: Settings = Depends(get_settings)): ...
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All application configuration in one validated, type-safe class.

    Required fields (no default) will raise ValidationError at startup
    if not provided — fail-fast is intentional.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", f".env.{os.getenv('APP_ENV', 'production')}"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────────────
    project_name: str = "ml-incident-response-playbook"
    app_env: str = Field(
        default="production",
        pattern="^(development|staging|production)$",
    )
    log_level: str = Field(
        default="INFO",
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
    )
    api_host: str = Field(default="127.0.0.1")  # Override via API_HOST=0.0.0.0 in orchestrated runtime
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_workers: int = Field(default=1, ge=1, le=32)

    # ── JWT ──────────────────────────────────────────────────────────────────────────
    # Required — no default intentionally. Startup fails if not set.
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=30)

    # ── Database ───────────────────────────────────────────────────────────────────
    database_url: str = Field(default="sqlite+aiosqlite:///./incidents.db")
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=50)
    db_pool_pre_ping: bool = Field(default=True)

    # ── Redis (token denylist) ──────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_connect_timeout: int = Field(default=5, ge=1, le=30)
    redis_socket_timeout: int = Field(default=5, ge=1, le=30)

    # ── CORS ────────────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins. Empty = deny all CORS.
    cors_allowed_origins: str = Field(default="")

    # ── Rate limiting ───────────────────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)
    rate_limit_auth_per_minute: int = Field(default=10, ge=1, le=100)

    # ── Monitoring thresholds ───────────────────────────────────────────────────────
    drift_threshold: float = Field(default=0.20, ge=0.01, le=1.0)
    volume_drop_pct: float = Field(default=0.30, ge=0.01, le=1.0)
    latency_baseline_ms: float = Field(default=200.0, ge=1.0)
    max_data_staleness_hours: float = Field(default=2.0, ge=0.1, le=72.0)
    model_accuracy_slo: float = Field(default=0.92, ge=0.0, le=1.0)

    # ── Alerting ─────────────────────────────────────────────────────────────────────
    alert_email: str = Field(default="")
    slack_webhook_url: str = Field(default="")

    # ── OpenTelemetry ───────────────────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = Field(default="http://localhost:4317")
    otel_service_name: str = Field(default="ml-incident-api")

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_key_entropy(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "JWT secret key must be at least 32 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @field_validator("app_env")
    @classmethod
    def warn_development_in_production(cls, v: str) -> str:
        # Validation only; caller is responsible for logging warnings.
        return v

    def get_cors_origins(self) -> list[str]:
        """Return CORS allowed origins as a list."""
        if not self.cors_allowed_origins:
            return []
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached Settings singleton.

    Thread-safe due to Python GIL and lru_cache semantics.
    Cache is invalidated only by process restart — intentional for production.

    For testing, invalidate with: get_settings.cache_clear()
    """
    return Settings()
