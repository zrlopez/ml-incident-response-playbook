"""
Observability: Logging Configuration, Audit Trail & Alerting
=============================================================
Remediation pass: 2026-05-23

Changes from original:
  - configure_logging() is now imported and called in api/app.py startup
  - audit() is now called from all security-relevant events in api/app.py
  - send_alert() stubs are wired; replace with real Slack/PagerDuty SDK calls
  - Added structlog.contextvars support for trace_id propagation
  - Added SIEM tag (log_type='audit') to all audit events
  - PII scrubbing processor active on all log pipelines
"""
from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

# ─── PII scrubbing patterns ──────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(\+?1[\s\-.]?)?\(?[0-9]{3}\)?[\s\-.]?[0-9]{3}[\s\-.]?[0-9]{4}\b")
_JWT_RE   = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")

# Keys whose values are unconditionally redacted (exact match, case-insensitive)
_REDACTED_KEYS = frozenset({
    "password", "passwd", "secret", "token", "jwt", "authorization",
    "api_key", "apikey", "access_token", "refresh_token", "private_key",
    "credential", "credentials", "ssn", "credit_card",
})


def _scrub_value(value: Any) -> Any:
    """Recursively scrub PII from a log value."""
    if isinstance(value, str):
        v = _JWT_RE.sub("[JWT_REDACTED]", value)
        v = _EMAIL_RE.sub("[EMAIL_REDACTED]", v)
        v = _PHONE_RE.sub("[PHONE_REDACTED]", v)
        return v
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub_value(i) for i in value)
    return value


def _pii_scrubber(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """structlog processor: redact sensitive keys and scrub PII patterns."""
    scrubbed: EventDict = {}
    for key, value in event_dict.items():
        if key.lower() in _REDACTED_KEYS:
            scrubbed[key] = "[REDACTED]"
        else:
            scrubbed[key] = _scrub_value(value)
    return scrubbed


def configure_logging(log_level: str | None = None) -> None:
    """Bootstrap structlog with PII scrubbing and JSON production rendering.

    Must be called once at application startup before any log.get_logger()
    calls — now wired in api/app.py module-level initialization.
    """
    level_str = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_str, logging.INFO)

    is_production = os.getenv("ENV", "production") == "production"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,          # Injects trace_id etc.
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _pii_scrubber,                                    # PII scrubbing
        structlog.processors.UnicodeDecoder(),
    ]

    if is_production:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
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
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    # Suppress noisy third-party loggers
    for noisy in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.get_logger(__name__).info(
        "logging.configured",
        level=level_str,
        renderer="json" if is_production else "console",
        pii_scrubbing="enabled",
    )


# ─── Audit trail ────────────────────────────────────────────────────────────────
_audit_log = structlog.get_logger("audit")


def audit(event: str, **kwargs: Any) -> None:
    """Emit a structured audit event.

    All security-relevant events (login, logout, incident CRUD, permission
    denials) must flow through this function. The log_type='audit' tag
    enables SIEM systems to route these events to a separate audit stream.

    Automatically includes trace_id from structlog contextvars when available.
    """
    _audit_log.info(
        event,
        log_type="audit",          # SIEM routing tag
        **kwargs,
    )


# ─── Alerting stubs ─────────────────────────────────────────────────────────────
_alert_log = structlog.get_logger("alerting")


def send_alert(
    message: str,
    level: str = "warning",
    **context: Any,
) -> None:
    """Send an operational alert.

    Current implementation: emits structured log with log_type='alert'.
    Production integration paths (uncomment to activate):

      Slack:
        import httpx
        httpx.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=5)

      PagerDuty:
        import pdpyras
        pdpyras.EventsAPISession(PAGERDUTY_KEY).trigger(message, **context)

      AWS SNS:
        import boto3
        boto3.client('sns').publish(TopicArn=SNS_TOPIC_ARN, Message=message)
    """
    _alert_log.warning(
        "alert.dispatched",
        log_type="alert",
        alert_message=message,
        alert_level=level,
        **context,
    )

    # Auto-escalate critical alerts to audit trail
    if level == "critical":
        audit("alert.critical", message=message, **context)
