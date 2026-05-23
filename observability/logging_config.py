"""
Centralised structured logging configuration.

Features:
  - PII scrubbing (regex + field-level redaction)
  - JSON output in production, coloured console in development
  - ISO-8601 timestamps
  - Per-request trace_id via structlog contextvars
  - Audit-log tagging (log_type="audit") for SIEM routing
  - Callable from app.py at import time — idempotent (safe to call multiple times)
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

import structlog

APP_ENV: str = os.getenv("APP_ENV", "production")

# Fields whose values are always fully redacted regardless of content
_REDACTED_FIELDS: frozenset[str] = frozenset({
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "jwt", "authorization", "api_key", "private_key", "credit_card",
    "ssn", "social_security", "hashed_password",
})

# Regex patterns for content-level PII scrubbing applied to rendered log strings
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b(?:\+?1[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    (re.compile(r"\b4[0-9]{12}(?:[0-9]{3})?\b"), "[CARD_REDACTED]"),  # Visa
    (re.compile(r"\b5[1-5][0-9]{14}\b"), "[CARD_REDACTED]"),          # Mastercard
]


def _redact_fields(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Processor: redact sensitive field values."""
    for key in list(event_dict.keys()):
        if key.lower() in _REDACTED_FIELDS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def _scrub_pii(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Processor: apply regex PII scrubbing to all string values."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            for pattern, replacement in _PII_PATTERNS:
                value = pattern.sub(replacement, value)
            event_dict[key] = value
    return event_dict


def _add_service_context(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Processor: add static service metadata to every log event."""
    event_dict.setdefault("service", "ml-incident-api")
    event_dict.setdefault("env", APP_ENV)
    return event_dict


_configured = False


def configure_logging() -> None:
    """Bootstrap structlog. Idempotent — safe to call at module import."""
    global _configured
    if _configured:
        return

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service_context,
        _redact_fields,
        _scrub_pii,
    ]

    if APP_ENV == "production":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO if APP_ENV == "production" else logging.DEBUG)

    _configured = True


def audit(
    event: str,
    actor: str,
    resource: str,
    action: str,
    outcome: str,
    **kwargs: Any,
) -> None:
    """
    Emit a structured audit log event.

    All audit events carry log_type="audit" so downstream SIEM / log aggregators
    (Datadog, Splunk, CloudWatch Logs Insights) can route them independently.

    Args:
        event:    Short machine-readable event name, e.g. "incident.created".
        actor:    Username or service identity performing the action.
        resource: Resource identifier, e.g. "INC-ABC123" or "/incidents".
        action:   Verb, e.g. "create", "update", "delete", "login".
        outcome:  "success" | "failure" | "denied".
        **kwargs: Additional structured context.
    """
    log = structlog.get_logger("audit")
    log.info(
        event,
        actor=actor,
        resource=resource,
        action=action,
        outcome=outcome,
        log_type="audit",
        **kwargs,
    )
