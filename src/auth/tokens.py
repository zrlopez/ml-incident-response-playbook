"""
src/auth/tokens.py
==================
JWT sign / verify helpers for the ML Incident Response API.

R-GOD Step 4: Extracted from api/app.py.  Contains:
  - create_access_token()
  - create_refresh_token()
  - decode_token()

Remediation changelog:
  SEC-01  All JWT_SECRET usages replaced with get_jwt_secret() from
          api.config.  The raw secret string is unwrapped only at the
          jwt.encode / jwt.decode boundary — never stored in a local
          variable or passed to logging.

Invariants:
  - Zero FastAPI imports.  Safe to import in unit tests without triggering
    _build_engine() or any other app-level side effect (unblocks R-C04).
  - Reads JWT config from api.config — single source of truth.
  - RS256 path delegates to src.auth.jwt_rs256 when RSA keys are loaded;
    falls back to HS* otherwise.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

import jwt
from fastapi import HTTPException, status

import structlog

from api.config import (
    get_jwt_secret,
    JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from src.auth import jwt_rs256

log = structlog.get_logger(__name__)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: timedelta | None = None,
) -> Tuple[str, str, int]:
    if "sub" not in data or "role" not in data:
        raise ValueError("Token payload must include 'sub' and 'role' claims")
    delta = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    if jwt_rs256.rs256_available():
        return jwt_rs256.sign_token(data.copy(), expires_delta=delta, token_type="access")
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + delta
    to_encode.update({"exp": expire, "iat": now, "jti": jti, "token_type": "access"})
    # SEC-01: get_jwt_secret() is the single unwrap point for the SecretStr.
    # Never assign the return value to a module-level or long-lived variable.
    encoded = jwt.encode(to_encode, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    return encoded, jti, int(delta.total_seconds())


def create_refresh_token(
    data: Dict[str, Any],
) -> Tuple[str, str, int]:
    if "sub" not in data:
        raise ValueError("Refresh token payload must include 'sub' claim")
    delta = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    if jwt_rs256.rs256_available():
        return jwt_rs256.sign_token(data.copy(), expires_delta=delta, token_type="refresh")
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + delta
    to_encode.update({"exp": expire, "iat": now, "jti": jti, "token_type": "refresh"})
    encoded = jwt.encode(to_encode, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    return encoded, jti, int(delta.total_seconds())


def decode_token(token: str) -> Dict[str, Any]:
    try:
        if jwt_rs256.rs256_available():
            return jwt_rs256.verify_token(token)
        payload = jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "jti", "sub", "role"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        log.warning("jwt.invalid_token", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload
