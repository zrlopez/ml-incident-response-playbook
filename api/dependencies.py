"""
api/dependencies.py
===================
FastAPI dependency providers and auth business logic.

R-GOD Step 5: Extracted from api/app.py.  Contains:
  - Module-level _denylist / _user_repo globals  (R-C03 marker: replace with app.state)
  - _record_login_failure()    brute-force counter helper
  - authenticate_user()        credential verification
  - get_current_user()         FastAPI dependency
  - require_role()             RBAC dependency factory
  - get_user_repo()            app.state helper
  - get_denylist()             app.state helper

R-C03 NOTE: _denylist and _user_repo are still bare module-level mutable globals
carried over from app.py.  The shared-state race on worker restart will be
remediated in R-C03 by converting these to request.app.state reads throughout.
Do not add new consumers of the bare globals — use get_denylist() / get_user_repo()
FastAPI deps instead so R-C03 is a single-file change.
"""
from __future__ import annotations

from typing import Annotated, Any, Callable, Dict, Optional

import structlog
from fastapi import Depends, HTTPException, Request, status

from api.config import LOGIN_FAILURE_THRESHOLD, LOGIN_FAILURE_WINDOW_SECONDS
from api.redis_denylist import RedisDenylist, DenylistUnavailableError
from api.config import oauth2_scheme
from src.auth.tokens import decode_token
from src.auth.password import verify_password, hash_password
from src.users.repository import AbstractUserRepository

log = structlog.get_logger(__name__)

# ── R-C03: shared-state race — replace both globals with app.state injection ────
_denylist: RedisDenylist | None = None
_user_repo: AbstractUserRepository | None = None


async def _record_login_failure(redis_client: Any, failure_key: str) -> None:
    """
    Increment the brute-force counter for a given client key and set its expiry.

    R-C09: Extracted helper; eliminates copy-paste of the Redis pipeline block.
    Failures are always silent — a Redis outage must never block the login attempt.
    """
    try:
        pipe = redis_client.pipeline()
        await pipe.incr(failure_key)
        await pipe.expire(failure_key, LOGIN_FAILURE_WINDOW_SECONDS)
        await pipe.execute()
    except Exception as exc:
        log.warning("auth.brute_force_counter_write_failed", error=str(exc))


async def authenticate_user(
    username: str,
    password: str,
    client_ip: str = "unknown",
) -> Optional[Dict[str, Any]]:
    _failure_key = f"login_failures:{client_ip}"
    _redis = _denylist._client if _denylist is not None else None

    if _redis is not None:
        try:
            _failure_count = await _redis.get(_failure_key)
            if _failure_count is not None and int(_failure_count) >= LOGIN_FAILURE_THRESHOLD:
                log.warning(
                    "auth.brute_force_blocked",
                    client_ip=client_ip,
                    failure_count=int(_failure_count),
                    log_type="audit",
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed login attempts. Please try again later.",
                    headers={"Retry-After": str(LOGIN_FAILURE_WINDOW_SECONDS)},
                )
        except HTTPException:
            raise
        except Exception as _redis_exc:
            log.warning("auth.brute_force_counter_unavailable", error=str(_redis_exc))

    # ── PostgresUserRepository path ────────────────────────────────────────────
    if _user_repo is not None:
        result = await _user_repo.authenticate(username, password)
        if result is None:
            if _redis is not None:
                await _record_login_failure(_redis, _failure_key)
            return None
        if _redis is not None:
            try:
                await _redis.delete(_failure_key)
            except Exception:
                pass
        return result.to_dict()

    # ── In-memory _USERS stub path (development/test only) ─────────────────
    from api.stub_users import _USERS
    user = _USERS.get(username)
    if not user:
        try:
            verify_password(password, hash_password("dummy-constant-time"))
        except Exception:
            pass
        if _redis is not None:
            await _record_login_failure(_redis, _failure_key)
        return None

    if user.get("disabled"):
        return None

    if not verify_password(password, user["hashed_password"]):
        if _redis is not None:
            await _record_login_failure(_redis, _failure_key)
        return None

    if _redis is not None:
        try:
            await _redis.delete(_failure_key)
        except Exception:
            pass
    return user


def get_user_repo(request: Request) -> AbstractUserRepository:
    """
    FastAPI dependency: return the AbstractUserRepository attached to app.state.
    Used by GDPR routes and endpoints needing direct repository access.
    """
    repo = getattr(request.app.state, "user_repo", None)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User repository not available.",
        )
    return repo


def get_denylist(request: Request) -> RedisDenylist | None:
    """
    Helper: return the RedisDenylist attached to app.state.
    Used by GDPR erasure to revoke the current token on account deletion.
    """
    return _denylist


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    request: Request,
) -> Dict[str, Any]:
    payload = decode_token(token)
    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type",
        )
    jti = payload.get("jti", "")

    # R-S04: Fail-open on denylist read errors.
    revoked = False
    if _denylist is not None:
        try:
            revoked = await _denylist.is_revoked(jti)
        except DenylistUnavailableError:
            log.warning(
                "auth.denylist_read_unavailable",
                jti=jti,
                action="fail_open",
                log_type="audit",
                hint="Redis denylist unreachable — revocation check skipped. "
                     "Investigate Redis connectivity immediately.",
            )
    else:
        log.warning(
            "auth.denylist_not_initialised",
            jti=jti,
            action="fail_open",
            log_type="audit",
        )

    if revoked:
        log.warning("auth.revoked_token_access", jti=jti, log_type="audit")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    username: str = payload.get("sub", "")
    if _user_repo is not None:
        _record = await _user_repo.get_by_username(username)
        user: dict | None = _record.to_dict() if _record is not None else None
    else:
        from api.stub_users import _USERS
        user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )
    return {**user, "jti": jti}


def require_role(*roles: str) -> Callable[..., Any]:
    """FastAPI dependency factory for role-based access control."""
    async def _checker(
        current_user: Annotated[dict, Depends(get_current_user)],
    ) -> Dict[str, Any]:
        if current_user["role"] not in roles:
            log.warning(
                "auth.access_denied",
                username=current_user["username"],
                role=current_user["role"],
                required_roles=roles,
                log_type="audit",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user['role']}' is not authorised for this action.",
            )
        return current_user
    return _checker
