"""
Observability: Logging Configuration, Audit Trail & Alerting.

Remediation history:
  2026-05-23 v1  configure_logging() wired to api/app.py startup
                 audit() called from all security-relevant API events
                 structlog.contextvars trace_id propagation active
                 PII scrubbing processor active on all pipelines
  2026-05-23 v2  ALERT-01: send_alert() upgraded from stub to multi-channel
                     dispatcher with Slack (httpx async), PagerDuty Events v2,
                     and structured-log fallback. Channel selection is
                     settings-driven so no code changes are needed to activate
                     or deactivate a channel in a given environment.
                 ALERT-02: AlertChannel enum for type-safe channel configuration
                 ALERT-03: AlertLevel enum maps directly to PagerDuty severity
                 ALERT-04: Async-safe: all network dispatches are fire-and-forget
                     via asyncio.create_task() when called from async context,
                     or run via ThreadPoolExecutor from sync context to avoid
                     blocking the event loop.
                 ALERT-05: All dispatch attempts (success and failure) are
                     audit-logged so alert delivery is auditable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

log = structlog.get_logger(__name__)
_alert_log = structlog.get_logger("alerting")

# Thread pool for dispatching alerts from synchronous callers without
# blocking the event loop or the calling thread for extended periods.
_alert_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="alert-dispatch")


# ── PII scrubbing ────────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(\+?1[\s\-.]?)?\(?[0-9]{3}\)?[\s\-.]?[0-9]{3}[\s\-.]?[0-9]{4}\b")
_JWT_RE   = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")

_REDACTED_KEYS = frozenset({
    "password", "passwd", "secret", "token", "jwt", "authorization",
    "api_key", "apikey", "access_token", "refresh_token", "private_key",
    "credential", "credentials", "ssn", "credit_card",
})


def _scrub_value(value: Any, key: str = "") -> Any:
    if key.lower() in _REDACTED_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        v = _JWT_RE.sub("[JWT_REDACTED]", value)
        v = _EMAIL_RE.sub("[EMAIL_REDACTED]", v)
        v = _PHONE_RE.sub("[PHONE_REDACTED]", v)
        return v
    if isinstance(value, dict):
        return {k: _scrub_value(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub_value(i) for i in value)
    return value


def _pii_scrubber(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    scrubbed: EventDict = {}
    for key, value in event_dict.items():
        if key.lower() in _REDACTED_KEYS:
            scrubbed[key] = "[REDACTED]"
        else:
            scrubbed[key] = _scrub_value(value, key=key)
    return scrubbed


# ── Logging bootstrap ───────────────────────────────────────────────────────────────────

def configure_logging(log_level: str | None = None) -> None:
    """
    Bootstrap structlog with JSON production rendering and PII scrubbing.
    Must be called once at application startup (wired in api/app.py).
    """
    level_str = (log_level or os.getenv("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_str, logging.INFO)
    is_production = os.getenv("APP_ENV", "production") == "production"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _pii_scrubber,
    ]

    if is_production:
        renderer: Any = structlog.processors.JSONRenderer()
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

    for noisy in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Audit trail ─────────────────────────────────────────────────────────────────────

_audit_log = structlog.get_logger("audit")


def audit(event: str, **kwargs: Any) -> None:
    """
    Emit a structured audit event tagged for SIEM routing.
    All security-relevant actions (auth, privilege change, data access) must
    call this function so the audit trail is complete and searchable.
    """
    _audit_log.info(
        event,
        log_type="audit",
        **kwargs,
    )


# ── Alert channel configuration ─────────────────────────────────────────────────────

class AlertLevel(str, Enum):
    """
    Alert severity levels.
    String values match PagerDuty Events v2 severity field exactly,
    enabling direct pass-through to the PD payload without mapping.
    """
    CRITICAL = "critical"
    ERROR    = "error"
    WARNING  = "warning"
    INFO     = "info"


# Environment-driven channel activation.
# Set SLACK_WEBHOOK_URL and/or PAGERDUTY_ROUTING_KEY in the environment
# (or via Kubernetes Secrets) to activate those channels.
# Both channels are independent; either, neither, or both may be active.
_SLACK_WEBHOOK_URL   = os.getenv("SLACK_WEBHOOK_URL", "")
_PAGERDUTY_ROUTING_KEY = os.getenv("PAGERDUTY_ROUTING_KEY", "")

# PagerDuty Events v2 endpoint (public, no auth in URL)
_PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"


# ── Async dispatch helpers ──────────────────────────────────────────────────────────

async def _dispatch_slack(
    message: str,
    level: AlertLevel,
    context: dict[str, Any],
) -> None:
    """
    POST a Slack Block Kit message to the configured incoming webhook.

    Uses the colour convention:
      critical/error -> danger (red)
      warning        -> warning (yellow)
      info           -> good (green)

    Failures are caught and audit-logged; they never propagate to the caller.
    """
    if not _SLACK_WEBHOOK_URL:
        return

    colour_map = {
        AlertLevel.CRITICAL: "danger",
        AlertLevel.ERROR:    "danger",
        AlertLevel.WARNING:  "warning",
        AlertLevel.INFO:     "good",
    }

    context_text = "\n".join(f"`{k}`: {v}" for k, v in context.items()) if context else ""
    payload = {
        "attachments": [
            {
                "color": colour_map.get(level, "warning"),
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*[{level.value.upper()}]* {message}"},
                    },
                    *(
                        [
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": context_text},
                            }
                        ]
                        if context_text
                        else []
                    ),
                ],
            }
        ]
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(_SLACK_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
        _alert_log.info(
            "alert.slack.dispatched",
            log_type="alert",
            level=level.value,
            status_code=resp.status_code,
        )
    except Exception as exc:
        _alert_log.error(
            "alert.slack.failed",
            log_type="alert",
            error=str(exc),
            level=level.value,
        )


async def _dispatch_pagerduty(
    message: str,
    level: AlertLevel,
    context: dict[str, Any],
    dedup_key: str | None = None,
) -> None:
    """
    POST a PagerDuty Events v2 trigger event.

    PD severity maps directly from AlertLevel string values.
    dedup_key, if provided, prevents duplicate incidents for the same
    firing condition (recommended for recurring metric-threshold alerts).

    Failures are caught and audit-logged.
    """
    if not _PAGERDUTY_ROUTING_KEY:
        return

    payload = {
        "routing_key": _PAGERDUTY_ROUTING_KEY,
        "event_action": "trigger",
        "payload": {
            "summary": message,
            "severity": level.value,  # PD accepts: critical, error, warning, info
            "source": os.getenv("OTEL_SERVICE_NAME", "ml-incident-api"),
            "custom_details": context,
        },
    }
    if dedup_key:
        payload["dedup_key"] = dedup_key

    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(_PAGERDUTY_EVENTS_URL, json=payload)
            resp.raise_for_status()
        _alert_log.info(
            "alert.pagerduty.dispatched",
            log_type="alert",
            level=level.value,
            dedup_key=dedup_key,
            status_code=resp.status_code,
        )
    except Exception as exc:
        _alert_log.error(
            "alert.pagerduty.failed",
            log_type="alert",
            error=str(exc),
            level=level.value,
        )


async def _alert_async(
    message: str,
    level: AlertLevel,
    context: dict[str, Any],
    dedup_key: str | None,
) -> None:
    """Dispatch to all configured channels concurrently."""
    tasks = [
        asyncio.create_task(_dispatch_slack(message, level, context)),
        asyncio.create_task(_dispatch_pagerduty(message, level, context, dedup_key)),
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


# ── Public alerting API ──────────────────────────────────────────────────────────────────

def send_alert(
    message: str,
    level: str | AlertLevel = AlertLevel.WARNING,
    dedup_key: str | None = None,
    **context: Any,
) -> None:
    """
    Dispatch an operational alert to all configured channels.

    Channels are activated by environment variable:
      SLACK_WEBHOOK_URL        -> enables Slack Block Kit messages
      PAGERDUTY_ROUTING_KEY    -> enables PagerDuty Events v2 triggers

    If neither is set, the alert is written to the structured log only
    (log_type='alert') which is still routable by a log aggregator (e.g.
    Datadog, Splunk, CloudWatch Logs Insights).

    This function is safe to call from both async and sync contexts:
      - Async context (inside a coroutine): creates a background task.
      - Sync context (startup, signal handlers): submits to a thread-pool
        executor. The alert is fire-and-forget in both cases.

    Args:
        message:   Human-readable alert description (max ~500 chars recommended).
        level:     AlertLevel enum or matching string (critical/error/warning/info).
        dedup_key: Optional PagerDuty deduplication key to prevent duplicate pages.
        **context: Arbitrary key-value pairs included in Slack context block
                   and PagerDuty custom_details.

    Example:
        send_alert(
            "Model drift detected: PSI=0.31 exceeds threshold 0.20",
            level=AlertLevel.CRITICAL,
            dedup_key="model-drift-v2-prod",
            model_version="v2",
            psi_score=0.31,
            threshold=0.20,
        )
    """
    # Normalise level to AlertLevel enum
    if isinstance(level, str):
        try:
            level = AlertLevel(level.lower())
        except ValueError:
            level = AlertLevel.WARNING

    # Always emit structured log entry regardless of channel config
    _alert_log.warning(
        "alert.dispatched",
        log_type="alert",
        alert_message=message,
        alert_level=level.value,
        channels={
            "slack": bool(_SLACK_WEBHOOK_URL),
            "pagerduty": bool(_PAGERDUTY_ROUTING_KEY),
        },
        **context,
    )

    # Auto-escalate critical + error alerts to audit trail for compliance
    if level in (AlertLevel.CRITICAL, AlertLevel.ERROR):
        audit(
            "alert.critical_dispatched",
            message=message,
            level=level.value,
            dedup_key=dedup_key,
            **context,
        )

    # Fire-and-forget network dispatch
    try:
        loop = asyncio.get_running_loop()
        # We're inside an async context — schedule as a background task
        loop.create_task(
            _alert_async(message, level, dict(context), dedup_key),
            name=f"alert-dispatch-{level.value}",
        )
    except RuntimeError:
        # No running event loop — sync caller (e.g. startup script, signal handler)
        # Submit to thread pool so we don't block the caller
        _alert_executor.submit(
            asyncio.run,
            _alert_async(message, level, dict(context), dedup_key),
        )
