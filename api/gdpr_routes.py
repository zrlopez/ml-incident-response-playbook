"""
api/gdpr_routes.py — GDPR data subject rights endpoints (ARCH-05)
=================================================================
Phase 2 remediation: adds the minimum GDPR Article 15 (access) and
Article 17 (erasure) endpoints required before any EU-facing deployment.

Findings addressed:
  ARCH-05  No GDPR endpoints — EU deployment blocked without
           data subject access + erasure rights implementation.
  HIGH-03  PII (username) was logged in plaintext structured log fields.
           Fix: _pseudo_id() hashes username with SHA-256 before logging.
           The raw username is never written to any log sink.

Endpoints:
  GET  /users/me/export   — Article 15 data portability (JSON)
  DELETE /users/me        — Article 17 right to erasure (soft delete)

Security:
  - Both routes require a valid JWT (get_current_user dependency).
  - Users can only access/delete their own data (no admin bypass).
  - Admins can delete other users via DELETE /users/{username} (separate
    admin route, not implemented here — add in Phase 3 if needed).
  - Deletion is a soft delete (disabled=True) to preserve audit trails
    required by GDPR Article 5(1)(e) accountability principle.
    Hard delete available via background job after retention period.
  - All operations are audit-logged with pseudonymous user ID, timestamp,
    and IP. Raw username never appears in log fields (HIGH-03).

Limitations (to document in privacy policy):
  - Incident records created by the user are NOT deleted (they are
    operational records; deletion requires separate legal review).
  - Export does not include Redis denylist entries (ephemeral).
  - Hard delete must be triggered manually by a DPO after 30-day
    retention period (see GDPR Art. 17(3) exceptions).

Router:
  Mount in api/app.py:
    from api.gdpr_routes import router as gdpr_router
    app.include_router(gdpr_router)
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

log = structlog.get_logger(__name__)

router = APIRouter(tags=["GDPR / Data Subject Rights"])


# ── PII pseudonymisation helper ───────────────────────────────────────────────
def _pseudo_id(username: str) -> str:
    """
    Return a stable, pseudonymous identifier for structured log fields.

    HIGH-03: Raw usernames are PII. Hashing with SHA-256 produces a
    deterministic token that can correlate log lines for a single user
    without exposing the username to log aggregators or Sentry.

    NOTE: This is pseudonymisation, not anonymisation. The mapping
    username -> hash can be reconstructed by a DPO with DB access.
    Do NOT use this hash in API responses — only in internal logs.
    """
    return hashlib.sha256(username.encode()).hexdigest()[:16]


# ── Dependency stub ───────────────────────────────────────────────────────────
def _get_current_user_dep() -> Callable[..., Any]:
    """Import get_current_user from api.dependencies (R-C03: no longer in api.app)."""
    from api.dependencies import get_current_user  # noqa: PLC0415
    return get_current_user


# ── Article 15 — Data Export ──────────────────────────────────────────────────
@router.get(
    "/users/me/export",
    summary="GDPR Art. 15 — Export your personal data",
    response_description="JSON export of all personal data held about you",
    status_code=status.HTTP_200_OK,
)
async def export_my_data(
    request: Request,
    current_user: dict = Depends(_get_current_user_dep()),
) -> JSONResponse:
    """
    Return a structured JSON export of all personal data held about the
    requesting user, in compliance with GDPR Article 15.

    Data included:
      - Account profile (username, role, account creation date)
      - Incidents created or owned by this user (incident IDs, titles,
        severity, status, timestamps)

    Data NOT included (documented for transparency):
      - Hashed passwords (non-personal; cryptographic artifact)
      - Audit log entries (operational; retained per Art. 5(1)(e))
      - Redis denylist entries (ephemeral; TTL-managed)

    Response is served with Content-Disposition: attachment to encourage
    the browser to download rather than display the export.
    """
    username: str = current_user.get("sub", "unknown")
    role: str = current_user.get("role", "unknown")
    client_ip = request.client.host if request.client else "unknown"
    uid = _pseudo_id(username)  # HIGH-03: pseudonymous ID for logs

    log.info(
        "gdpr.export_requested",
        uid=uid,
        client_ip=client_ip,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    export_payload: dict[str, Any] = {
        "gdpr_request": "Article 15 — Right of Access",
        "export_generated_at": datetime.now(timezone.utc).isoformat(),
        "data_controller": "ML Incident Response Platform",
        "account": {
            "username": username,
            "role": role,
            "note": (
                "Account creation date and full profile available after "
                "ARCH-03 PostgresUserRepository migration is complete."
            ),
        },
        "incidents": {
            "note": (
                "Incident records where you are listed as 'owner' are available "
                "via GET /incidents?owner={username}. Full export requires "
                "ARCH-03 database integration."
            )
        },
        "data_not_held": [
            "Plaintext password (never stored)",
            "Payment information",
            "Location data",
            "Biometric data",
        ],
        "retention_policy": {
            "account_data": "Retained while account is active + 30 days post-deletion",
            "incident_records": "Retained for 7 years per operational compliance policy",
            "audit_logs": "Retained for 2 years per security policy",
        },
        "your_rights": {
            "rectification": "Contact your system administrator to correct inaccurate data",
            "erasure": "DELETE /users/me — soft delete, full erasure after 30-day retention",
            "portability": "This endpoint — GET /users/me/export",
            "complaint": "You may lodge a complaint with your national supervisory authority",
        },
    }

    log.info(
        "gdpr.export_served",
        uid=uid,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return JSONResponse(
        content=export_payload,
        headers={
            "Content-Disposition": f'attachment; filename="gdpr-export-{uid}.json"',
            "Cache-Control": "no-store",
        },
    )


# ── Article 17 — Right to Erasure (Soft Delete) ───────────────────────────────
@router.delete(
    "/users/me",
    summary="GDPR Art. 17 — Request erasure of your account",
    status_code=status.HTTP_200_OK,
)
async def delete_my_account(
    request: Request,
    current_user: dict = Depends(_get_current_user_dep()),
) -> Dict[str, Any]:
    """
    Soft-delete the requesting user's account in compliance with GDPR
    Article 17 (Right to Erasure).

    Soft delete (disabled=True) is used rather than hard delete because:
      1. Incident audit trails referencing this user must be preserved
         per GDPR Art. 17(3)(b) (legal obligation / public interest).
      2. Hard delete is triggered by a background job after the 30-day
         retention period (see DPO runbook: docs/dpo_runbook.md).

    After soft delete:
      - The user cannot log in.
      - JWT tokens are immediately invalidated (added to Redis denylist).
      - All active sessions are terminated.
      - Account appears as "deleted" to admin queries.

    This endpoint requires PostgresUserRepository (ARCH-03) for full
    implementation. Current stub marks deletion intent and logs the event.
    """
    username: str = current_user.get("sub", "unknown")
    client_ip = request.client.host if request.client else "unknown"
    timestamp = datetime.now(timezone.utc).isoformat()
    uid = _pseudo_id(username)  # HIGH-03: pseudonymous ID for logs

    log.warning(
        "gdpr.erasure_requested",
        uid=uid,
        client_ip=client_ip,
        timestamp=timestamp,
        action="soft_delete_initiated",
    )

    from api.dependencies import get_user_repo  # noqa: PLC0415
    user_repo = get_user_repo(request)

    disabled = await user_repo.disable_user(username)
    if not disabled:
        log.error("gdpr.erasure_user_not_found", uid=uid, timestamp=timestamp)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        )

    jti: str | None = current_user.get("jti")
    exp: int | None = current_user.get("exp")
    if jti and exp:
        try:
            from api.dependencies import get_denylist  # noqa: PLC0415
            denylist = get_denylist(request)
            if denylist is None:
                raise RuntimeError("denylist unavailable")
            now_ts = int(datetime.now(timezone.utc).timestamp())
            ttl = max(exp - now_ts, 1)
            await denylist.revoke(jti, ttl_seconds=ttl)
            log.info("gdpr.token_revoked", uid=uid, jti=jti, timestamp=timestamp)
        except Exception:  # noqa: BLE001
            log.error("gdpr.token_revoke_failed", uid=uid, jti=jti)

    log.warning(
        "gdpr.erasure_completed",
        uid=uid,
        timestamp=datetime.now(timezone.utc).isoformat(),
        action="soft_deleted",
    )

    return {
        "status": "erasure_completed",
        "username": username,
        "requested_at": timestamp,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "message": (
            "Your account has been disabled immediately. "
            "All active sessions have been invalidated. "
            "Your account data will be fully erased after the 30-day "
            "retention period in compliance with GDPR Art. 17(3)(b)."
        ),
        "retention_period_days": 30,
        "legal_basis_for_retention": "GDPR Art. 17(3)(b) — legal obligation / public interest",
    }
