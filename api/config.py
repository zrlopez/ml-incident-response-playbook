"""
api/config.py
=============
Centralised configuration for the ML Incident Response API.

R-GOD Step 1: All os.environ / os.getenv reads, the JWT algorithm allowlist
guard, CORS origin parse, SlowAPI limiter, and OAuth2 bearer scheme extracted
from api/app.py.  Nothing in this module imports from FastAPI route layer —
it is safe to import in tests without spinning up the application.

Remediation changelog:
  SEC-01  JWT_SECRET wrapped in SecretStr; get_jwt_secret() is the sole
          authorised access point for the raw bytes.  Direct attribute
          access (JWT_SECRET.get_secret_value()) is intentionally verbose
          to make accidental logging obvious in code review.
  R-P11   SlowAPI rate-limit key replaced — raw IPs no longer stored in
          Redis limiter state. _rate_limit_key() hashes the best-available
          client identifier before it enters SlowAPI state.

Invariants:
  - Hard-fails at import time if JWT_SECRET_KEY is absent.
  - Hard-fails at import time if JWT_ALGORITHM is not in _ALLOWED_ALGORITHMS.
  - No side-effectful DB or Redis connections here; those live in api/lifespan.py.
  - JWT_SECRET is SecretStr — never log, serialize, or embed it in responses.
"""
from __future__ import annotations

import hashlib
import os
from typing import List

from pydantic import SecretStr
from fastapi import Request
from fastapi.security import OAuth2PasswordBearer
from slowapi import Limiter

# ── JWT ───────────────────────────────────────────────────────────────────────
# SEC-01: Wrap the raw env value in SecretStr immediately on read so it is
# masked ('**********') in all repr(), str(), logging, and Sentry captures
# from this point forward.  Use get_jwt_secret() to obtain the raw bytes
# in the one place that actually needs them (jwt.encode / jwt.decode).
_raw_jwt_secret: str = os.environ["JWT_SECRET_KEY"]  # hard-fail if absent
JWT_SECRET: SecretStr = SecretStr(_raw_jwt_secret)
del _raw_jwt_secret  # remove the plain-str reference immediately


def get_jwt_secret() -> str:
    """
    Return the raw JWT signing secret.

    This is the single authorised call-site for unwrapping JWT_SECRET.
    Centralising the unwrap means:
      - grep for get_jwt_secret() to audit all places the secret is used
      - accidental JWT_SECRET usage in logging is masked at the SecretStr layer
      - future rotation (e.g. moving to a secrets manager) has one change point
    """
    return JWT_SECRET.get_secret_value()


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
def _rate_limit_key(request: Request) -> str:
    """Return a privacy-preserving rate-limit key for SlowAPI.

    R-P11 (Cycle 2): do not store raw client IPs in Redis or in-memory rate
    limiter state. We hash the best-available client identifier so requests
    from the same client still bucket deterministically without persisting PII.

    Key precedence:
      1. request.client.host (most common for direct app traffic)
      2. X-Forwarded-For first hop (proxy deployments / ingress)
      3. literal "unknown" fallback
    """
    client_host = request.client.host if request.client else None
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    raw_identifier = client_host or forwarded_for or "unknown"
    return hashlib.sha256(raw_identifier.encode()).hexdigest()[:16]


limiter: Limiter = Limiter(key_func=_rate_limit_key, default_limits=["200/minute"])

# ── OAuth2 bearer scheme ──────────────────────────────────────────────────────
oauth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(tokenUrl="/auth/token")
