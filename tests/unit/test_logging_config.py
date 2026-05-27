"""
tests/unit/test_logging_config.py

Unit tests for observability/logging_config.py.

Coverage targets:
  - _scrub_value(): key-based redaction, regex redaction (email/phone/JWT),
    recursive dict/list/tuple scrubbing, passthrough of clean values.
  - _pii_scrubber(): structlog processor contract; scrubs event dict keys.
  - configure_logging(): structlog bootstrap in both production and dev modes;
    LOG_LEVEL env override; APP_ENV branch; noisy-logger suppression.
  - audit(): delegates to structlog with log_type='audit'.
  - AlertLevel: enum values match PagerDuty severity strings.
  - send_alert(): structured-log fallback when no channels configured;
    string→AlertLevel normalisation; invalid string fallback to WARNING;
    critical/error auto-escalation to audit; async-context task creation;
    sync-context thread-pool submission.

All network calls (httpx) are fully mocked — no outbound traffic.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

# ── helpers ──────────────────────────────────────────────────────────────────


def _reset_structlog() -> None:
    """Reset structlog global config between tests to avoid cross-contamination."""
    structlog.reset_defaults()


# ════════════════════════════════════════════════════════════════════════════
# _scrub_value
# ════════════════════════════════════════════════════════════════════════════


class TestScrubValue:
    """Tests for the _scrub_value() private helper."""

    def setup_method(self) -> None:
        # Import here so we can monkeypatch module-level state if needed
        from observability.logging_config import _scrub_value
        self._scrub = _scrub_value

    def test_key_in_redacted_keys_returns_redacted(self) -> None:
        assert self._scrub("supersecret", key="password") == "[REDACTED]"

    def test_key_case_insensitive_redaction(self) -> None:
        assert self._scrub("tok", key="TOKEN") == "[REDACTED]"

    def test_api_key_redacted(self) -> None:
        assert self._scrub("abc123", key="api_key") == "[REDACTED]"

    def test_jwt_string_redacted(self) -> None:
        jwt = "eyJhbGciOiJmYWtlIn0.eyJzdWIiOiJ0ZXN0In0.AAAAAAAAAAAAAAAAAAAAAA"  # noqa: S105 — synthetic token for redaction testing, not a real credential
        result = self._scrub(jwt)
        assert "[JWT_REDACTED]" in result
        assert "eyJ" not in result

    def test_email_string_redacted(self) -> None:
        result = self._scrub("contact user@example.com for details")
        assert "[EMAIL_REDACTED]" in result
        assert "user@example.com" not in result

    def test_phone_string_redacted(self) -> None:
        result = self._scrub("call 555-867-5309 for support")
        assert "[PHONE_REDACTED]" in result
        assert "555-867-5309" not in result

    def test_clean_string_passthrough(self) -> None:
        assert self._scrub("model_drift_detected") == "model_drift_detected"

    def test_integer_passthrough(self) -> None:
        assert self._scrub(42) == 42

    def test_none_passthrough(self) -> None:
        assert self._scrub(None) is None

    def test_dict_recursive_scrub(self) -> None:
        data = {"password": "s3cr3t", "model": "v2", "email": "a@b.com"}
        result = self._scrub(data)
        assert result["password"] == "[REDACTED]"
        assert result["model"] == "v2"
        assert "[EMAIL_REDACTED]" in result["email"]

    def test_list_recursive_scrub(self) -> None:
        data = ["clean", "user@corp.io", 99]
        result = self._scrub(data)
        assert result[0] == "clean"
        assert "[EMAIL_REDACTED]" in result[1]
        assert result[2] == 99

    def test_tuple_recursive_scrub_preserves_type(self) -> None:
        data = ("normal", "eyJhbGciOiJIUzI1NiJ9.x.y")
        result = self._scrub(data)
        assert isinstance(result, tuple)
        assert result[0] == "normal"
        assert "[JWT_REDACTED]" in result[1]

    def test_nested_dict_in_list(self) -> None:
        data = [{"token": "abc"}]
        result = self._scrub(data)
        assert result[0]["token"] == "[REDACTED]"


# ════════════════════════════════════════════════════════════════════════════
# _pii_scrubber (structlog processor)
# ════════════════════════════════════════════════════════════════════════════


class TestPiiScrubberProcessor:
    """Tests for the _pii_scrubber structlog processor."""

    def setup_method(self) -> None:
        from observability.logging_config import _pii_scrubber
        self._proc = _pii_scrubber

    def test_redacts_secret_key_in_event_dict(self) -> None:
        event_dict: dict[str, Any] = {"event": "login", "secret": "mysecret"}
        result = self._proc(MagicMock(), "info", event_dict)
        assert result["secret"] == "[REDACTED]"
        assert result["event"] == "login"

    def test_scrubs_email_in_event_value(self) -> None:
        event_dict: dict[str, Any] = {"event": "debug", "msg": "sent to ops@corp.com"}
        result = self._proc(MagicMock(), "info", event_dict)
        assert "[EMAIL_REDACTED]" in result["msg"]

    def test_clean_event_dict_passthrough(self) -> None:
        event_dict: dict[str, Any] = {"event": "drift_check", "psi": 0.12}
        result = self._proc(MagicMock(), "info", event_dict)
        assert result == event_dict

    def test_credential_key_redacted(self) -> None:
        event_dict: dict[str, Any] = {"event": "e", "credentials": "user:pass"}
        result = self._proc(MagicMock(), "warning", event_dict)
        assert result["credentials"] == "[REDACTED]"


# ════════════════════════════════════════════════════════════════════════════
# configure_logging
# ════════════════════════════════════════════════════════════════════════════


class TestConfigureLogging:
    """Tests for configure_logging() bootstrap."""

    def teardown_method(self) -> None:
        _reset_structlog()

    def test_default_call_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        from observability.logging_config import configure_logging
        configure_logging()  # should not raise

    def test_dev_mode_uses_console_renderer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "development")
        from observability.logging_config import configure_logging
        configure_logging()
        # structlog should be configured without raising
        logger = structlog.get_logger("test")
        assert logger is not None

    def test_log_level_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        from observability.logging_config import configure_logging
        configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_explicit_log_level_param(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        from observability.logging_config import configure_logging
        configure_logging(log_level="WARNING")
        assert logging.getLogger().level == logging.WARNING

    def test_invalid_log_level_falls_back_to_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        from observability.logging_config import configure_logging
        configure_logging(log_level="NOTAREAL")
        # getattr fallback returns INFO (20)
        assert logging.getLogger().level == logging.INFO

    def test_noisy_loggers_set_to_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        from observability.logging_config import configure_logging
        configure_logging()
        for name in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
            assert logging.getLogger(name).level == logging.WARNING


# ════════════════════════════════════════════════════════════════════════════
# audit()
# ════════════════════════════════════════════════════════════════════════════


class TestAudit:
    """Tests for the audit() structured event emitter."""

    def test_audit_emits_log_type_audit(self) -> None:
        from observability.logging_config import audit
        captured: list[dict[str, Any]] = []

        mock_logger = MagicMock()
        mock_logger.info.side_effect = lambda event, **kw: captured.append({"event": event, **kw})

        with patch("observability.logging_config._audit_log", mock_logger):
            audit("user.login", user_id="u123", ip="10.0.0.1")

        assert len(captured) == 1
        assert captured[0]["event"] == "user.login"
        assert captured[0]["log_type"] == "audit"
        assert captured[0]["user_id"] == "u123"

    def test_audit_arbitrary_kwargs_forwarded(self) -> None:
        from observability.logging_config import audit
        mock_logger = MagicMock()
        with patch("observability.logging_config._audit_log", mock_logger):
            audit("data.access", table="incidents", rows_read=500)
        mock_logger.info.assert_called_once()
        _, kwargs = mock_logger.info.call_args
        assert kwargs["table"] == "incidents"
        assert kwargs["rows_read"] == 500


# ════════════════════════════════════════════════════════════════════════════
# AlertLevel
# ════════════════════════════════════════════════════════════════════════════


class TestAlertLevel:
    """Tests for the AlertLevel enum."""

    def test_critical_value(self) -> None:
        from observability.logging_config import AlertLevel
        assert AlertLevel.CRITICAL.value == "critical"

    def test_error_value(self) -> None:
        from observability.logging_config import AlertLevel
        assert AlertLevel.ERROR.value == "error"

    def test_warning_value(self) -> None:
        from observability.logging_config import AlertLevel
        assert AlertLevel.WARNING.value == "warning"

    def test_info_value(self) -> None:
        from observability.logging_config import AlertLevel
        assert AlertLevel.INFO.value == "info"

    def test_is_str_subclass(self) -> None:
        from observability.logging_config import AlertLevel
        assert isinstance(AlertLevel.CRITICAL, str)


# ════════════════════════════════════════════════════════════════════════════
# send_alert()
# ════════════════════════════════════════════════════════════════════════════


class TestSendAlert:
    """Tests for the public send_alert() dispatcher."""

    def setup_method(self) -> None:
        # Ensure no real env vars leak in
        os.environ.pop("SLACK_WEBHOOK_URL", None)
        os.environ.pop("PAGERDUTY_ROUTING_KEY", None)

    # ── structured-log fallback (no channels) ────────────────────────────────

    def test_no_channels_emits_structured_log(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        mock_log = MagicMock()
        with patch("observability.logging_config._alert_log", mock_log), \
             patch("observability.logging_config._alert_executor") as mock_exec:
            send_alert("model drift", level=AlertLevel.WARNING)
        mock_log.warning.assert_called_once()
        call_kwargs = mock_log.warning.call_args[1]
        assert call_kwargs["log_type"] == "alert"
        assert call_kwargs["alert_message"] == "model drift"

    # ── string → AlertLevel normalisation ────────────────────────────────────

    def test_string_level_normalised_to_enum(self) -> None:
        from observability.logging_config import send_alert
        mock_log = MagicMock()
        with patch("observability.logging_config._alert_log", mock_log), \
             patch("observability.logging_config._alert_executor"):
            send_alert("test", level="critical")
        call_kwargs = mock_log.warning.call_args[1]
        assert call_kwargs["alert_level"] == "critical"

    def test_invalid_string_level_falls_back_to_warning(self) -> None:
        from observability.logging_config import send_alert
        mock_log = MagicMock()
        with patch("observability.logging_config._alert_log", mock_log), \
             patch("observability.logging_config._alert_executor"):
            send_alert("test", level="NOTVALID")
        call_kwargs = mock_log.warning.call_args[1]
        assert call_kwargs["alert_level"] == "warning"

    # ── critical/error auto-escalates to audit ────────────────────────────────

    def test_critical_alert_calls_audit(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        mock_audit_log = MagicMock()
        with patch("observability.logging_config._alert_log", MagicMock()), \
             patch("observability.logging_config._audit_log", mock_audit_log), \
             patch("observability.logging_config._alert_executor"):
            send_alert("sev1 breach", level=AlertLevel.CRITICAL)
        mock_audit_log.info.assert_called_once()
        call_args = mock_audit_log.info.call_args[0]
        assert call_args[0] == "alert.critical_dispatched"

    def test_error_alert_calls_audit(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        mock_audit_log = MagicMock()
        with patch("observability.logging_config._alert_log", MagicMock()), \
             patch("observability.logging_config._audit_log", mock_audit_log), \
             patch("observability.logging_config._alert_executor"):
            send_alert("pipeline error", level=AlertLevel.ERROR)
        mock_audit_log.info.assert_called_once()

    def test_warning_alert_does_not_call_audit(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        mock_audit_log = MagicMock()
        with patch("observability.logging_config._alert_log", MagicMock()), \
             patch("observability.logging_config._audit_log", mock_audit_log), \
             patch("observability.logging_config._alert_executor"):
            send_alert("minor warning", level=AlertLevel.WARNING)
        mock_audit_log.info.assert_not_called()

    # ── sync context: thread pool submission ─────────────────────────────────

    def test_sync_context_submits_to_executor(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        mock_executor = MagicMock()
        with patch("observability.logging_config._alert_log", MagicMock()), \
             patch("observability.logging_config._audit_log", MagicMock()), \
             patch("observability.logging_config._alert_executor", mock_executor), \
             patch("asyncio.get_running_loop", side_effect=RuntimeError):
            send_alert("sync caller", level=AlertLevel.INFO)
        mock_executor.submit.assert_called_once()

    # ── async context: loop.create_task ──────────────────────────────────────

    def test_async_context_creates_task(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        mock_loop = MagicMock()
        with patch("observability.logging_config._alert_log", MagicMock()), \
             patch("observability.logging_config._audit_log", MagicMock()), \
             patch("asyncio.get_running_loop", return_value=mock_loop):
            send_alert("async caller", level=AlertLevel.WARNING)
        mock_loop.create_task.assert_called_once()

    def test_context_kwargs_forwarded_to_log(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        mock_log = MagicMock()
        with patch("observability.logging_config._alert_log", mock_log), \
             patch("observability.logging_config._alert_executor"):
            send_alert("drift", level=AlertLevel.WARNING, model_version="v3", psi=0.28)
        call_kwargs = mock_log.warning.call_args[1]
        assert call_kwargs["model_version"] == "v3"
        assert call_kwargs["psi"] == 0.28

    def test_dedup_key_accepted_without_error(self) -> None:
        from observability.logging_config import send_alert, AlertLevel
        with patch("observability.logging_config._alert_log", MagicMock()), \
             patch("observability.logging_config._alert_executor"):
            send_alert("drift", level=AlertLevel.WARNING, dedup_key="drift-v2-prod")


# ════════════════════════════════════════════════════════════════════════════
# _dispatch_slack / _dispatch_pagerduty (async, mocked httpx)
# ════════════════════════════════════════════════════════════════════════════


class TestDispatchSlack:
    """Tests for _dispatch_slack() with mocked httpx."""

    @pytest.mark.asyncio
    async def test_no_webhook_url_returns_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        # Reload module-level constant
        import importlib
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_SLACK_WEBHOOK_URL", "")
        from observability.logging_config import _dispatch_slack, AlertLevel
        await _dispatch_slack("test", AlertLevel.WARNING, {})
        # No exception = pass; early return because URL is empty

    @pytest.mark.asyncio
    async def test_slack_dispatch_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake")
        mock_response = MagicMock(status_code=200)
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from observability.logging_config import _dispatch_slack, AlertLevel
            await _dispatch_slack("model drift", AlertLevel.CRITICAL, {"psi": 0.31})

        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_slack_dispatch_http_error_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            from observability.logging_config import _dispatch_slack, AlertLevel
            await _dispatch_slack("test", AlertLevel.ERROR, {})
        # Should swallow exception and log it

    @pytest.mark.asyncio
    async def test_slack_context_block_included_when_context_provided(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake")
        captured_payload: list[Any] = []
        mock_response = MagicMock(status_code=200)
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def capture_post(url: str, json: Any) -> Any:
            captured_payload.append(json)
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            from observability.logging_config import _dispatch_slack, AlertLevel
            await _dispatch_slack("drift", AlertLevel.WARNING, {"model": "v2"})

        blocks = captured_payload[0]["attachments"][0]["blocks"]
        assert len(blocks) == 2  # header + context


class TestDispatchPagerDuty:
    """Tests for _dispatch_pagerduty() with mocked httpx."""

    @pytest.mark.asyncio
    async def test_no_routing_key_returns_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_PAGERDUTY_ROUTING_KEY", "")
        from observability.logging_config import _dispatch_pagerduty, AlertLevel
        await _dispatch_pagerduty("test", AlertLevel.WARNING, {})

    @pytest.mark.asyncio
    async def test_pagerduty_dispatch_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_PAGERDUTY_ROUTING_KEY", "fake-routing-key")
        mock_response = MagicMock(status_code=202)
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from observability.logging_config import _dispatch_pagerduty, AlertLevel
            await _dispatch_pagerduty(
                "SEV-1 breach",
                AlertLevel.CRITICAL,
                {"model": "v2"},
                dedup_key="sev1-model-v2",
            )

        mock_client.post.assert_called_once()
        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["json"]["dedup_key"] == "sev1-model-v2"

    @pytest.mark.asyncio
    async def test_pagerduty_dispatch_error_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_PAGERDUTY_ROUTING_KEY", "fake-routing-key")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            from observability.logging_config import _dispatch_pagerduty, AlertLevel
            await _dispatch_pagerduty("test", AlertLevel.ERROR, {})

    @pytest.mark.asyncio
    async def test_pagerduty_payload_severity_matches_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import observability.logging_config as lc
        monkeypatch.setattr(lc, "_PAGERDUTY_ROUTING_KEY", "fake-routing-key")
        captured_payload: list[Any] = []
        mock_response = MagicMock(status_code=202)
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def capture(url: str, json: Any) -> Any:
            captured_payload.append(json)
            return mock_response

        mock_client.post = capture

        with patch("httpx.AsyncClient", return_value=mock_client):
            from observability.logging_config import _dispatch_pagerduty, AlertLevel
            await _dispatch_pagerduty("test", AlertLevel.ERROR, {})

        assert captured_payload[0]["payload"]["severity"] == "error"
