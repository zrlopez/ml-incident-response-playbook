"""
api/routers/auth.py
===================
Authentication routes for the ML Incident Response API.

R-GOD Step 8: Extracted from api/app.py.
R-C03 COMPLETE: All _deps._ global reads replaced with Depends(get_denylist)
                and request.app.state access via FastAPI dependency injection.
                Routes no longer declare bare `request: Request` — FastAPI
                would interpret that as a required body field on non-first
                positional parameters, causing 422s.

  POST /auth/token    — issue access + refresh token pair (rate limited 5/min)
  POST /auth/refresh  — rotate refresh token (rate limited 5/min, ARCH-08)
  POST /auth/logout   — revoke current access token via Redis denylist (ARCH-09)
"""
from __future__ import annotations

from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from api.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    limiter,
    oauth2_scheme,
)
from api.schemas import Token
from api.dependencies import (
    authenticate_user,
    get_current_user,
    get_denylist,
)
from api.redis_denylist import RedisDenylist
from src.auth.tokens import create_access_token, create_refresh_token, decode_token

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=Token)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    denylist=Depends(get_denylist),
) -> Token:
    """Issue an access + refresh token pair. Rate limited to 5/min per IP."""
    client_ip = request.client.host if request.client else "unknown"
    user_repo = getattr(request.app.state, "user_repo", None)
    user = await authenticate_user(
        form.username,
        form.password,
        client_ip=client_ip,
        denylist=denylist,
        user_repo=user_repo,
    )
    if not user:
        log.warning("auth.login_failed", username=form.username, log_type="audit")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_data = {"sub": user["username"], "role": user["role"]}
    access_token, access_jti, access_ttl = create_access_token(token_data)
    refresh_token, refresh_jti, _ = create_refresh_token(token_data)
    log.info(
        "auth.login_success",
        username=user["username"],
        role=user["role"],
        jti=access_jti,
        log_type="audit",
    )
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=access_ttl,
    )


@router.post("/refresh", response_model=Token)
@limiter.limit("5/minute")
async def refresh_token_endpoint(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    denylist=Depends(get_denylist),
) -> Token:
    """
    Exchange a valid refresh token for a new access + refresh token pair.
    Rate limited to 5/min per IP (ARCH-08).
    """
    payload = decode_token(token)
    if payload.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type — submit a refresh token",
        )
    old_jti = payload.get("jti", "")
    if denylist is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
            headers={"Retry-After": "30"},
        )
    if await denylist.is_revoked(old_jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )
    username: str = payload.get("sub", "")
    user_repo = getattr(request.app.state, "user_repo", None)
    if user_repo is not None:
        _record = await user_repo.get_by_username(username)
        user: dict | None = _record.to_dict() if _record is not None else None
    else:
        from api.stub_users import _USERS
        user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )
    await denylist.revoke(
        old_jti,
        ttl_seconds=int(timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS).total_seconds()),
    )
    token_data = {"sub": user["username"], "role": user["role"]}
    access_token, access_jti, access_ttl = create_access_token(token_data)
    new_refresh_token, new_refresh_jti, _ = create_refresh_token(token_data)
    log.info(
        "auth.token_refreshed",
        username=user["username"],
        old_jti=old_jti,
        new_jti=access_jti,
        log_type="audit",
    )
    return Token(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=access_ttl,
    )


@router.post("/logout", status_code=204)
async def logout(
    current_user: Annotated[dict, Depends(get_current_user)],
    denylist=Depends(get_denylist),
) -> None:
    """
    Revoke the caller's current access token immediately via the Redis denylist.
    No role gate — any authenticated principal must be able to log out (ARCH-09).
    """
    jti = current_user["jti"]
    ttl = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    if denylist is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )
    await denylist.revoke(jti, ttl_seconds=ttl)
    log.info("auth.logout", username=current_user["username"], jti=jti, log_type="audit")
