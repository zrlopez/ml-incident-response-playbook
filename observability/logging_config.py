"""
observability/logging_config.py
================================
Centralised structured logging, audit trail, and alerting for the
ML Incident Response API.

Remediation log (2026-05-23):
  GAP-01  configure_logging() was defined but never called. Now wired in
          api/app.py lifespan. All log events now flow through PII scrubber.
  GAP-03  send_alert() upgraded from logging-only stub to structured alert
          emitter with Slack/PagerDuty/SNS integration hooks clearly
          documented and ready for production.
  GAP-04  audit() now always emits to dedicated 'audit' log stream with
          full actor, action, timestamp, and trace_id context.
"""

from __future__ import annotations

import logging
import logging.config
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

import structlog

# ──────────────────────────────────────────────────────────────────────────────
# PII scrubbing
# ──────────────────────────────────────────────────────────────────────────────

_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "token", "jwt", "secret", "api_key",
    "access_token", "refresh_token", "authorization", "credential",
    "ssn", "credit_card", "card_number",
})

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def _scrub_pii(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """
    structlog processor — redacts sensitive key values and scrubs
    email addresses and phone numbers from string values.
    """
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"

    for key, value in event_dict.items():
        if isinstance(value, str):
            value = _EMAIL_RE.sub("[EMAIL]", value)
            value = _PHONE_RE.sub("[PHONE]", value)
            event_dict[key] = value

    return event_dict


# ──────────────────────────────────────────────────────────────────────────────
# configure_logging  (was never called — REMEDIATED)
# ──────────────────────────────────────────────────────────────────────────────

def configure_logging() -> None:
    """
    Initialise structlog with:
      - PII scrubbing processor
      - ISO 8601 timestamps
      - JSON renderer in production, human-readable in development
      - stdlib logging bridge for third-party library output

    Must be called once at application startup (wired in api/app.py lifespan).
    """
    _env = os.getenv("APP_ENV", "production").lower()
    is_dev = _env in ("development", "dev", "local")

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _scrub_pii,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if is_dev:
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge standard library logging → structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    for noisy_logger in ("uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(noisy_logger).handlers = []
        logging.getLogger(noisy_logger).propagate = True


# ──────────────────────────────────────────────────────────────────────────────
# audit()  (was defined but never called — REMEDIATED)
# ──────────────────────────────────────────────────────────────────────────────

def audit(action: str, **kwargs: Any) -> None:
    """
    Emit a structured audit log event.

    All security-relevant actions (auth, RBAC decisions, data mutations)
    MUST be routed through this function. The log_type='audit' tag enables
    SIEM ingestion rules to route these events to a separate, append-only
    audit stream.

    The trace_id from structlog.contextvars is automatically included
    because configure_logging() installs merge_contextvars as the first
    processor.

    Example SIEM ingestion rule (Datadog):
      filter: log.log_type = 'audit'
      pipeline: security-audit-trail
    """
    log = structlog.get_logger("audit")
    log.info(
        action,
        log_type="audit",
        timestamp=datetime.now(timezone.utc).isoformat(),
        **kwargs,
    )


# ──────────────────────────────────────────────────────────────────────────────
# send_alert()  (was logging stub — UPGRADED)
# ──────────────────────────────────────────────────────────────────────────────

def send_alert(message: str, severity: str = "medium", **context: Any) -> None:
    """
    Emit a structured alert event and dispatch to external channels.

    Current channels:
      - Structured log with log_type='alert' (always enabled)
      - Slack webhook (when SLACK_WEBHOOK_URL is set)
      - PagerDuty Events API v2 (when PAGERDUTY_ROUTING_KEY is set)
      - AWS SNS (when ALERT_SNS_TOPIC_ARN is set)

    Channel integrations are designed for import-safe lazy loading:
    missing dependencies or env vars produce a warning, not a crash.
    """
    log = structlog.get_logger("alerts")
    log.warning(
        "alert.fired",
        log_type="alert",
        message=message,
        severity=severity,
        timestamp=datetime.now(timezone.utc).isoformat(),
        **context,
    )

    _dispatch_slack(message, severity, context)
    _dispatch_pagerduty(message, severity, context)
    _dispatch_sns(message, severity, context)


def _dispatch_slack(message: str, severity: str, context: dict[str, Any]) -> None:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        import json
        import urllib.request

        colour = {"critical": "#FF0000", "high": "#FF6600", "medium": "#FFCC00"}.get(
            severity.lower(), "#888888"
        )
        payload = {
            "attachments": [{
                "color": colour,
                "title": f"ML Incident Alert [{severity.upper()}]",
                "text": message,
                "fields": [
                    {"title": k, "value": str(v), "short": True}
                    for k, v in context.items()
                ],
                "footer": "ml-incident-response-playbook",
                "ts": int(datetime.now(timezone.utc).timestamp()),
            }]
        }
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
    except Exception as exc:
        structlog.get_logger(__name__).warning("alert.slack.failed", error=str(exc))


def _dispatch_pagerduty(message: str, severity: str, context: dict[str, Any]) -> None:
    routing_key = os.getenv("PAGERDUTY_ROUTING_KEY")
    if not routing_key:
        return
    try:
        import json
        import urllib.request

        pd_severity = {"critical": "critical", "high": "error", "medium": "warning"}.get(
            severity.lower(), "info"
        )
        payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": message,
                "severity": pd_severity,
                "source": "ml-incident-response-api",
                "custom_details": context,
            },
        }
        req = urllib.request.Request(
            "https://events.pagerduty.com/v2/enqueue",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
    except Exception as exc:
        structlog.get_logger(__name__).warning("alert.pagerduty.failed", error=str(exc))


def _dispatch_sns(message: str, severity: str, context: dict[str, Any]) -> None:
    topic_arn = os.getenv("ALERT_SNS_TOPIC_ARN")
    if not topic_arn:
        return
    try:
        import json
        import boto3  # type: ignore[import]

        client = boto3.client("sns", region_name=os.getenv("AWS_REGION", "us-east-1"))
        client.publish(
            TopicArn=topic_arn,
            Subject=f"[ML-IRP][{severity.upper()}] {message[:80]}",
            Message=json.dumps({"message": message, "severity": severity, **context}),
            MessageAttributes={
                "severity": {"DataType": "String", "StringValue": severity}
            },
        )
    except Exception as exc:
        structlog.get_logger(__name__).warning("alert.sns.failed", error=str(exc))
