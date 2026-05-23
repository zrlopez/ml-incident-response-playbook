"""logging_config.py — Centralized structured logging configuration (remediation initiative)

Fixes applied:
  - structlog replaces ad-hoc print()/logging.info() calls
  - JSON output in production; pretty-printed in development
  - PII scrubbing processor: redacts email, phone, token fields
  - Log level configurable via LOG_LEVEL env var
  - Audit log helper for security-relevant events
  - No plaintext secrets in log records
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_API_ENV = os.getenv("API_ENV", "development")
_IS_PRODUCTION = _API_ENV == "production"

# ---------------------------------------------------------------------------
# PII Scrubbing Processor
# ---------------------------------------------------------------------------
# Fields that should NEVER appear in log output as plaintext
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "authorization",
    "jwt", "api_key", "apikey", "credit_card", "ssn",
    "access_token", "refresh_token", "private_key",
})

# Simple regex patterns for value-level scrubbing
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b(\+?1[\s.-]?)?\(?[0-9]{3}\)?[\s.-]?[0-9]{3}[\s.-]?[0-9]{4}\b")


def _scrub_pii(logger, method, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor that redacts sensitive keys and value patterns.

    Mutates event_dict in-place (structlog convention).
    """
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"
            continue
        val = event_dict.get(key)
        if isinstance(val, str):
            val = _EMAIL_RE.sub("[EMAIL_REDACTED]", val)
            val = _PHONE_RE.sub("[PHONE_REDACTED]", val)
            event_dict[key] = val
    return event_dict


# ---------------------------------------------------------------------------
# Configure structlog
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    """Call once at application startup to configure structlog and stdlib logging."""
    log_level = getattr(logging, _LOG_LEVEL, logging.INFO)

    # Shared processors for all environments
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _scrub_pii,
        structlog.processors.StackInfoRenderer(),
    ]

    if _IS_PRODUCTION:
        # JSON output for production (log aggregation: Datadog, Splunk, Loki, etc.)
        renderer = structlog.processors.JSONRenderer()
    else:
        # Human-readable console output for development
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
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

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    # Suppress noisy third-party loggers
    for noisy in ["uvicorn.access", "httpx", "httpcore"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Audit logging helper
# ---------------------------------------------------------------------------

_audit_log = structlog.get_logger("audit")


def audit(
    action: str,
    actor: str,
    resource: str,
    outcome: str,
    **extra: Any,
) -> None:
    """Emit a structured audit log event for security-relevant actions.

    All audit events are tagged with ``log_type="audit"`` for easy SIEM filtering.

    Args:
        action:   What was attempted (e.g., 'incident.create', 'auth.login').
        actor:    Who performed the action (user ID / service account).
        resource: What resource was acted upon (e.g., incident ID).
        outcome:  'success' | 'failure' | 'denied'.
        **extra:  Additional context (severity, IP, etc.).
    """
    _audit_log.info(
        action,
        log_type="audit",
        actor=actor,
        resource=resource,
        outcome=outcome,
        **extra,
    )


# ---------------------------------------------------------------------------
# Alerting hook (stub for Slack / PagerDuty / SNS integration)
# ---------------------------------------------------------------------------

_alert_log = structlog.get_logger("alert")


def send_alert(
    title: str,
    severity: str,
    details: dict[str, Any],
    channel: str = "#ml-incidents",
) -> None:
    """Log a structured alert. Wire to real alerting channel in production.

    TODO(prod): Replace body with Slack webhook, PagerDuty event, or SNS publish.

    Args:
        title:    Alert title (short, descriptive).
        severity: 'SEV-1' | 'SEV-2' | 'SEV-3' | 'SEV-4'.
        details:  Dict of context/evidence.
        channel:  Target Slack channel or PagerDuty service name.
    """
    _alert_log.warning(
        "alert.fired",
        log_type="alert",
        title=title,
        severity=severity,
        channel=channel,
        **{k: v for k, v in details.items() if k.lower() not in _SENSITIVE_KEYS},
    )
    # TODO(prod):
    # webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    # requests.post(webhook_url, json={"text": f"*{severity}* {title}", "channel": channel})
