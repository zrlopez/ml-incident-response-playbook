"""
Audit logging module — security-critical event stream.

This module provides a dedicated structured log stream for events that
security teams, compliance audits, and SIEM systems need to consume
independently of application logs.

Audit events are emitted to a separate logger (``audit``) which can be
routed to a separate sink (file, stream, external SIEM) via structlog
configuration in ``src/logger.py``.

Design decisions:
- All method parameters are keyword-only to prevent positional argument
  confusion on security-critical calls.
- Email/username fields accept pre-hashed values only. Never log raw PII.
  Callers are responsible for hashing before passing to audit_log methods.
- Fields are explicit (not **kwargs) to prevent accidental PII leakage
  via unbounded payload capture.
- Module-level singleton ``audit_log`` is the intended import target.

Usage::

    from src.audit import audit_log

    audit_log.auth_success(user_id="uuid", ip_address="1.2.3.4")
    audit_log.auth_failure(reason="invalid_password", ip_address="1.2.3.4")
    audit_log.token_revoked(user_id="uuid", reason="logout", token_jti="jti")
    audit_log.rate_limit_exceeded(
        user_id="uuid", ip_address="1.2.3.4",
        endpoint="/api/v1/incidents", limit=60, window_seconds=60,
    )
    audit_log.privilege_check_failed(
        user_id="uuid", required_role="admin",
        actual_role="analyst", endpoint="/api/v1/admin/users",
    )

CI-52: introduced 2026-05-28 (Phase 1 security hardening).
"""

from __future__ import annotations

import structlog

_audit_logger: structlog.BoundLogger = structlog.get_logger("audit")


class AuditLog:
    """
    Typed interface to the structured audit log stream.

    All methods emit JSON-structured events to the ``audit`` logger.
    Use the module-level ``audit_log`` singleton rather than instantiating
    this class directly.
    """

    def auth_success(
        self,
        *,
        user_id: str,
        ip_address: str,
        user_agent: str | None = None,
        mfa_used: bool = False,
    ) -> None:
        """Emit an authentication success event."""
        _audit_logger.info(
            "authentication.success",
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            mfa_used=mfa_used,
            event_type="auth",
            outcome="success",
        )

    def auth_failure(
        self,
        *,
        reason: str,
        ip_address: str,
        attempted_email_hash: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """
        Emit an authentication failure event.

        ``attempted_email_hash`` must be a SHA-256 hex digest of the
        attempted email address. Never pass raw email addresses here.
        """
        _audit_logger.warning(
            "authentication.failure",
            reason=reason,
            ip_address=ip_address,
            attempted_email_hash=attempted_email_hash,
            user_agent=user_agent,
            event_type="auth",
            outcome="failure",
        )

    def token_revoked(
        self,
        *,
        user_id: str,
        reason: str,
        token_jti: str,
    ) -> None:
        """
        Emit a token revocation event.

        ``token_jti`` is the JWT ``jti`` claim — not the full token string.
        Never log raw JWT tokens.
        """
        _audit_logger.info(
            "token.revoked",
            user_id=user_id,
            reason=reason,
            token_jti=token_jti,
            event_type="token",
            outcome="revoked",
        )

    def rate_limit_exceeded(
        self,
        *,
        user_id: str | None,
        ip_address: str,
        endpoint: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        """Emit a rate limit exceeded event."""
        _audit_logger.warning(
            "rate_limit.exceeded",
            user_id=user_id,
            ip_address=ip_address,
            endpoint=endpoint,
            limit=limit,
            window_seconds=window_seconds,
            event_type="rate_limit",
            outcome="blocked",
        )

    def privilege_check_failed(
        self,
        *,
        user_id: str,
        required_role: str,
        actual_role: str,
        endpoint: str,
    ) -> None:
        """Emit an authorization denied event."""
        _audit_logger.warning(
            "authorization.denied",
            user_id=user_id,
            required_role=required_role,
            actual_role=actual_role,
            endpoint=endpoint,
            event_type="authz",
            outcome="denied",
        )


# Module-level singleton — import this, not the class directly.
# This avoids multiple logger instances and keeps call sites clean.
audit_log: AuditLog = AuditLog()
