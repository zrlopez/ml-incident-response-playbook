"""
Unified logger factory — remediation R-split-log.

Previous implementation used stdlib logging with a bare StreamHandler,
creating a split-log stream alongside the structlog processor chain.
This meant any module importing from src.logger bypassed PII scrubbing,
field redaction, and service context injection.

This module now delegates entirely to structlog, ensuring every log event
travels the same processor pipeline defined in observability/logging_config.py.

Usage:
    from src.logger import get_logger
    log = get_logger(__name__)
    log.info("incident.created", incident_id="abc", severity="SEV-2")
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.logging_config import configure_logging


def get_logger(name: str) -> Any:
    """
    Return a structlog BoundLogger bound to the given name.

    Ensures logging is configured (idempotent) before returning the logger.
    All log events flow through:
      - Field-level redaction (_REDACTED_FIELDS)
      - Regex PII scrubbing (_PII_PATTERNS)
      - Service context injection (service, env)
      - ISO-8601 timestamp addition
      - JSON output (production) or coloured console (development)

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        Configured structlog BoundLogger.
    """
    configure_logging()  # Idempotent — safe to call at every module import
    return structlog.get_logger(name)
