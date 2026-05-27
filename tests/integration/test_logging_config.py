"""
tests/integration/test_logging_config.py
========================================
Observability integration tests for observability/logging_config.py.

Scope:
  1. configure_logging() selects a production JSON renderer when APP_ENV=production.
  2. configure_logging() selects a non-production console renderer otherwise.
  3. _pii_scrubber redacts sensitive keys and scrubs embedded PII.
  4. send_alert() emits a structured-log fallback when no delivery channels are configured.

Explicitly not tested here:
  - live Slack delivery
  - live PagerDuty delivery
  - thread-pool lifecycle internals

Source authority:
  - observability/logging_config.py
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from observability import logging_config


# ---------------------------------------------------------------------------
# PII scrubbing
# ---------------------------------------------------------------------------


def test_pii_scrubber_redacts_sensitive_keys_and_values():
    event = {
        "password": "super-secret",
        "token": "abc123",
        "email": "person@example.com",
        "note": "reach me at person@example.com or +1 (555) 123-4567",
        "nested": {"authorization": "Bearer eyJabc.def.ghi", "plain": "ok"},
    }

    scrubbed = logging_config._pii_scrubber(MagicMock(), "info", event)

    assert scrubbed["password"] == "[REDACTED]"
    assert scrubbed["token"] == "[REDACTED]"
    assert scrubbed["email"] == "[EMAIL_REDACTED]"
    assert "[EMAIL_REDACTED]" in scrubbed["note"]
    assert "[PHONE_REDACTED]" in scrubbed["note"]
    assert scrubbed["nested"]["authorization"] == "[REDACTED]"
    assert scrubbed["nested"]["plain"] == "ok"


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_uses_json_renderer_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    with patch("structlog.configure") as configure_mock, \
         patch("structlog.stdlib.ProcessorFormatter") as formatter_mock, \
         patch("logging.getLogger") as get_logger_mock:
        root_logger = MagicMock()
        noisy_logger = MagicMock()
        get_logger_mock.side_effect = lambda name=None: root_logger if name in (None, "") else noisy_logger  # noqa: E501

        logging_config.configure_logging()

    configure_mock.assert_called_once()
    formatter_mock.assert_called_once()
    root_logger.handlers = [MagicMock()]
    root_logger.setLevel.assert_called_once()


def test_configure_logging_uses_console_renderer_outside_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    with patch("structlog.configure") as configure_mock, \
         patch("structlog.stdlib.ProcessorFormatter") as formatter_mock, \
         patch("logging.getLogger") as get_logger_mock:
        root_logger = MagicMock()
        noisy_logger = MagicMock()
        get_logger_mock.side_effect = lambda name=None: root_logger if name in (None, "") else noisy_logger  # noqa: E501

        logging_config.configure_logging(log_level="DEBUG")

    configure_mock.assert_called_once()
    formatter_mock.assert_called_once()
    root_logger.setLevel.assert_called_once()


# ---------------------------------------------------------------------------
# send_alert fallback path
# ---------------------------------------------------------------------------


def test_send_alert_falls_back_to_structured_log_when_channels_disabled(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("PAGERDUTY_ROUTING_KEY", raising=False)

    with patch.object(logging_config._alert_log, "warning") as warning_mock, \
         patch.object(logging_config._alert_log, "error") as error_mock:
        logging_config.send_alert(
            "Model drift detected",
            level="warning",
            dedup_key="drift-123",
            model_version="v2",
            psi_score=0.31,
        )

    warning_mock.assert_called()
    error_mock.assert_not_called()
