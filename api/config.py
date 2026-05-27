"""
api/config.py
=============
Centralised configuration for the ML Incident Response API.

R-GOD Step 1: All os.environ / os.getenv reads, the JWT algorithm allowlist
guard, CORS origin parse, SlowAPI limiter, and OAuth2 bearer scheme extracted
from api/app.py.  Nothing in this module imports from FastAPI route layer —
it is safe to import in tests without spinning up the application.

Invariants:
  - Hard-fails at import time if JWT_SECRET_KEY is absent.
  - Hard-fails at import time if JWT_ALGORITHM is not in _ALLOWED_ALGORITHMS.
  - No side-effectful DB or Redis connections here; those live in api/lifespan.py.
"""
from __future__ import annotations

import os
from typing import List

from fastapi.security import OAuth2PasswordBearer
from slowapi import Limiter
from slowapi.util import get_remote_address

# ── JWT ───────────────────────────────────────────────────────────────────────
JWT_SECRET: str = os.environ["JWT_SECRET_KEY"]  # hard-fail if absent
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# R-C02: RS256 added — "none" and other weak algorithms intentionally absent.
_ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512"}
if JWT_ALGORITHM not in _ALLOWED_ALGORITHMS:
    raise ValueError(
        f"JWT_ALGORITHM must be one of {sorted(_ALLOWED_ALGORITHMS)}, "
        f"got '{JWT_ALGORITHM}'. Weak or unsigned algorithms (e.g. 'none', 'HS1') "
        "are not permitted."
    )

# ── Environment ───────────────────────────────────────────────────────────────
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Brute-force protection (ARCH-10) ─────────────────────────────────────────
LOGIN_FAILURE_THRESHOLD: int = int(os.getenv("LOGIN_FAILURE_THRESHOLD", "10"))
LOGIN_FAILURE_WINDOW_SECONDS: int = int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "60"))

# ── CORS ──────────────────────────────────────────────────────────────────────
_raw_origins: str = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: List[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Rate limiter (shared across app and routers) ──────────────────────────────
limiter: Limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── OAuth2 bearer scheme ──────────────────────────────────────────────────────
oauth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(tokenUrl="/auth/token")
