"""
api/rate_limit.py — Per-user sliding window rate limiting (ARCH-07)
===================================================================
Phase 2 remediation: adds Redis-backed per-user rate limits on top of
the global SlowAPI rate limiter added in Phase 0 (MED-C).

Findings addressed:
  ARCH-07  Global rate limiting only (200/minute on all endpoints).
           A single compromised account can exhaust the global budget,
           causing denial-of-service for all other users.
           Per-user limits provide:
             - Account-level abuse isolation
             - Tiered limits by role (admin > operator > analyst)
             - Incident creation flood protection

Design:
  - Sliding window counter using Redis INCR + EXPIRE.
  - Key format: ratelimit:{endpoint_group}:{username}
  - Limits are configurable per role via RATE_LIMIT_* env vars.
  - Graceful degradation: if Redis is unavailable, the limiter
    logs a warning and ALLOWS the request (fail open). Adjust
    RATE_LIMIT_FAIL_CLOSED=true to block on Redis outage.

Default limits (requests / window_seconds):
  admin:    300 / 60
  operator: 200 / 60
  analyst:  100 / 60
  (global SlowAPI limit: 200 / 60 per IP, still active)

Usage in route handlers:
    from api.rate_limit import check_user_rate_limit

    @app.post("/incidents")
    async def create_incident(
        ...,
        _rl: None = Depends(check_user_rate_limit("incidents")),
    ):
        ...

    Or apply globally via middleware (see mount instructions below).
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict

import structlog
from fastapi import Depends, HTTPException, Request, status

log = structlog.get_logger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────────────────────

_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
_FAIL_CLOSED = os.getenv("RATE_LIMIT_FAIL_CLOSED", "false").lower() in ("true", "1", "yes")

# Requests per window per user, by role
_ROLE_LIMITS: dict[str, int] = {
    "admin":    int(os.getenv("RATE_LIMIT_ADMIN",    "300")),
    "operator": int(os.getenv("RATE_LIMIT_OPERATOR", "200")),
    "analyst":  int(os.getenv("RATE_LIMIT_ANALYST",  "100")),
    # Fallback for unknown/unset roles
    "default":  int(os.getenv("RATE_LIMIT_DEFAULT",  "60")),
}


def _get_limit_for_role(role: str) -> int:
    return _ROLE_LIMITS.get(role, _ROLE_LIMITS["default"])


async def _check_limit(
    request: Request,
    endpoint_group: str,
    username: str,
    role: str,
) -> None:
    """
    Core sliding window check. Uses Redis INCR + EXPIRE.

    Key: ratelimit:{endpoint_group}:{username}
    On first request in window: SET key=1 EX window_seconds
    On subsequent requests:     INCR key (EXPIRE already set)
    """
    # Access the denylist Redis connection if available via app state
    redis_client = getattr(request.app.state, "redis", None)

    if redis_client is None:
        if _FAIL_CLOSED:
            log.error(
                "rate_limit.redis_unavailable",
                endpoint_group=endpoint_group,
                username=username,
                action="blocked_fail_closed",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiting service unavailable. Try again shortly.",
            )
        # Fail open: log warning, allow request
        log.warning(
            "rate_limit.redis_unavailable_fail_open",
            endpoint_group=endpoint_group,
            username=username,
        )
        return

    key = f"ratelimit:{endpoint_group}:{username}"
    limit = _get_limit_for_role(role)

    try:
        # Atomic INCR — returns new count after increment
        count: int = await redis_client.incr(key)
        if count == 1:
            # First request in this window — set expiry
            await redis_client.expire(key, _WINDOW_SECONDS)

        if count > limit:
            log.warning(
                "rate_limit.exceeded",
                username=username,
                role=role,
                endpoint_group=endpoint_group,
                count=count,
                limit=limit,
                window_seconds=_WINDOW_SECONDS,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: {limit} requests per "
                    f"{_WINDOW_SECONDS}s for role '{role}'. "
                    f"Try again in {_WINDOW_SECONDS} seconds."
                ),
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )
    except HTTPException:
        raise  # Re-raise rate limit HTTP exceptions
    except Exception as exc:
        if _FAIL_CLOSED:
            log.error(
                "rate_limit.redis_error_fail_closed",
                error=str(exc),
                username=username,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiting error. Try again shortly.",
            ) from exc
        log.warning(
            "rate_limit.redis_error_fail_open",
            error=str(exc),
            username=username,
        )
        # Fail open on unexpected Redis errors
        return


def check_user_rate_limit(endpoint_group: str) -> Callable[..., Any]:
    """
    FastAPI dependency factory. Returns a Depends-compatible async function.

    Usage:
        @app.post("/incidents")
        async def create_incident(
            payload: IncidentCreate,
            _rl: None = Depends(check_user_rate_limit("incidents")),
            current_user: dict = Depends(get_current_user),
        ):
            ...

    The dependency extracts username and role from the JWT via get_current_user.
    """
    async def _dependency(
        request: Request,
        current_user: dict = Depends(_get_current_user_lazy()),
    ) -> None:
        username: str = current_user.get("sub", "anonymous")
        role: str = current_user.get("role", "default")
        await _check_limit(request, endpoint_group, username, role)

    return _dependency


def _get_current_user_lazy() -> Any:
    """Lazy import to avoid circular dependency with api.app."""
    try:
        from api.app import get_current_user  # noqa: PLC0415
        return get_current_user
    except ImportError:
        # Return a no-op placeholder during testing / import order issues
        async def _noop() -> Dict[str, Any]:
            return {"sub": "unknown", "role": "default"}
        return _noop
