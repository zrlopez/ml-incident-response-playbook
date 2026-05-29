"""
Unit tests for src/audit.py — AuditLog structured event emitter.

Strategy: patch structlog.get_logger at the module level so we capture
the exact call arguments without any real I/O. Each test asserts both
the log method used (info vs warning) and the structured fields emitted.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.audit import AuditLog, audit_log


@pytest.fixture()
def mock_logger():
    """Patch the module-level _audit_logger with a MagicMock."""
    with patch("src.audit._audit_logger") as mock:
        yield mock


class TestAuditLogSingleton:
    def test_singleton_is_audit_log_instance(self):
        assert isinstance(audit_log, AuditLog)


class TestAuthSuccess:
    def test_emits_info_with_required_fields(self, mock_logger):
        audit_log.auth_success(user_id="u-123", ip_address="1.2.3.4")
        mock_logger.info.assert_called_once()
        _, kwargs = mock_logger.info.call_args
        assert kwargs["user_id"] == "u-123"
        assert kwargs["ip_address"] == "1.2.3.4"
        assert kwargs["event_type"] == "auth"
        assert kwargs["outcome"] == "success"

    def test_emits_info_event_name(self, mock_logger):
        audit_log.auth_success(user_id="u-123", ip_address="1.2.3.4")
        args, _ = mock_logger.info.call_args
        assert args[0] == "authentication.success"

    def test_optional_fields_default_to_none_and_false(self, mock_logger):
        audit_log.auth_success(user_id="u-1", ip_address="10.0.0.1")
        _, kwargs = mock_logger.info.call_args
        assert kwargs["user_agent"] is None
        assert kwargs["mfa_used"] is False

    def test_mfa_used_and_user_agent_passed_through(self, mock_logger):
        audit_log.auth_success(
            user_id="u-1",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            mfa_used=True,
        )
        _, kwargs = mock_logger.info.call_args
        assert kwargs["user_agent"] == "Mozilla/5.0"
        assert kwargs["mfa_used"] is True


class TestAuthFailure:
    def test_emits_warning(self, mock_logger):
        audit_log.auth_failure(reason="invalid_password", ip_address="1.2.3.4")
        mock_logger.warning.assert_called_once()

    def test_emits_correct_event_name(self, mock_logger):
        audit_log.auth_failure(reason="invalid_password", ip_address="1.2.3.4")
        args, _ = mock_logger.warning.call_args
        assert args[0] == "authentication.failure"

    def test_fields_populated(self, mock_logger):
        audit_log.auth_failure(
            reason="account_locked",
            ip_address="5.6.7.8",
            attempted_email_hash="abc123",
            user_agent="curl/7.0",
        )
        _, kwargs = mock_logger.warning.call_args
        assert kwargs["reason"] == "account_locked"
        assert kwargs["ip_address"] == "5.6.7.8"
        assert kwargs["attempted_email_hash"] == "abc123"
        assert kwargs["user_agent"] == "curl/7.0"
        assert kwargs["event_type"] == "auth"
        assert kwargs["outcome"] == "failure"

    def test_optional_fields_default_to_none(self, mock_logger):
        audit_log.auth_failure(reason="bad_password", ip_address="1.1.1.1")
        _, kwargs = mock_logger.warning.call_args
        assert kwargs["attempted_email_hash"] is None
        assert kwargs["user_agent"] is None


class TestTokenRevoked:
    def test_emits_info(self, mock_logger):
        audit_log.token_revoked(user_id="u-1", reason="logout", token_jti="jti-abc")
        mock_logger.info.assert_called_once()

    def test_emits_correct_event_name(self, mock_logger):
        audit_log.token_revoked(user_id="u-1", reason="logout", token_jti="jti-abc")
        args, _ = mock_logger.info.call_args
        assert args[0] == "token.revoked"

    def test_fields_populated(self, mock_logger):
        audit_log.token_revoked(user_id="u-99", reason="session_expired", token_jti="jti-xyz")
        _, kwargs = mock_logger.info.call_args
        assert kwargs["user_id"] == "u-99"
        assert kwargs["reason"] == "session_expired"
        assert kwargs["token_jti"] == "jti-xyz"
        assert kwargs["event_type"] == "token"
        assert kwargs["outcome"] == "revoked"


class TestRateLimitExceeded:
    def test_emits_warning(self, mock_logger):
        audit_log.rate_limit_exceeded(
            user_id="u-1", ip_address="1.2.3.4",
            endpoint="/api/v1/incidents", limit=60, window_seconds=60,
        )
        mock_logger.warning.assert_called_once()

    def test_emits_correct_event_name(self, mock_logger):
        audit_log.rate_limit_exceeded(
            user_id="u-1", ip_address="1.2.3.4",
            endpoint="/api/v1/incidents", limit=60, window_seconds=60,
        )
        args, _ = mock_logger.warning.call_args
        assert args[0] == "rate_limit.exceeded"

    def test_fields_populated(self, mock_logger):
        audit_log.rate_limit_exceeded(
            user_id=None, ip_address="9.9.9.9",
            endpoint="/api/v1/auth/login", limit=10, window_seconds=30,
        )
        _, kwargs = mock_logger.warning.call_args
        assert kwargs["user_id"] is None
        assert kwargs["ip_address"] == "9.9.9.9"
        assert kwargs["endpoint"] == "/api/v1/auth/login"
        assert kwargs["limit"] == 10
        assert kwargs["window_seconds"] == 30
        assert kwargs["event_type"] == "rate_limit"
        assert kwargs["outcome"] == "blocked"


class TestPrivilegeCheckFailed:
    def test_emits_warning(self, mock_logger):
        audit_log.privilege_check_failed(
            user_id="u-1", required_role="admin",
            actual_role="analyst", endpoint="/api/v1/admin/users",
        )
        mock_logger.warning.assert_called_once()

    def test_emits_correct_event_name(self, mock_logger):
        audit_log.privilege_check_failed(
            user_id="u-1", required_role="admin",
            actual_role="analyst", endpoint="/api/v1/admin/users",
        )
        args, _ = mock_logger.warning.call_args
        assert args[0] == "authorization.denied"

    def test_fields_populated(self, mock_logger):
        audit_log.privilege_check_failed(
            user_id="u-42", required_role="superadmin",
            actual_role="viewer", endpoint="/api/v1/admin/purge",
        )
        _, kwargs = mock_logger.warning.call_args
        assert kwargs["user_id"] == "u-42"
        assert kwargs["required_role"] == "superadmin"
        assert kwargs["actual_role"] == "viewer"
        assert kwargs["endpoint"] == "/api/v1/admin/purge"
        assert kwargs["event_type"] == "authz"
        assert kwargs["outcome"] == "denied"
