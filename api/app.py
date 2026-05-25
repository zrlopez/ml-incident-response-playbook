"""
ML Incident Response API — Hardened Production Build
=====================================================
Remediation: 2026-05-23
Findings addressed:
  CRIT-01  broken create_access_token parameter
  HIGH-01  python-jose → PyJWT (CVE-2024-33663)
  HIGH-02  SlowAPI rate limiting on /auth/token
  HIGH-03  Distributed trace_id middleware
  MED-01   CORS allowlist empty-string parse bug
  MED-02   uuid4 incident IDs (replaces timestamp collision)
  MED-03   Token revocation denylist (in-memory → Redis-backed)
  MED-04   /ready probe with real dependency health checks
  MED-05   audit() wired to all security-relevant events
  MED-06   configure_logging() called at startup
  R-03     Redis-backed distributed JWT denylist replaces process-local set
  R-20     OTel tracing bootstrap via observability/otel_setup.py
  ARCH-01  RS256 token signing when RSA_PRIVATE_KEY_PEM is set (Phase 2)
  ARCH-07  _USERS stub allowlist guard — blocks staging bypass (Phase 2)
  ARCH-08  /auth/refresh rate limit lowered to 5/min (matches /auth/token)
  ARCH-09  /auth/logout docstring clarified — all roles may revoke own token
  ARCH-10  Distributed brute-force counter (Redis) on login failures
"""
from __future__ import annotations

import os
import re
import uuid
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Tuple

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
# passlib removed (ARCH-02): hash_password / verify_password from src.auth.password
# provide argon2id hashing; see src/auth/password.py for migration notes.
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import structlog

from observability.logging_config import configure_logging
from observability.otel_setup import configure_otel, shutdown_otel
from api.redis_denylist import RedisDenylist
from api.gdpr_routes import router as gdpr_router
from api.rate_limit import check_user_rate_limit
from src.users.repository import PostgresUserRepository, AbstractUserRepository
from src.auth.password import hash_password, verify_password, maybe_rehash
from src.auth import jwt_rs256
from src.incident_tracker import (
    IncidentRepository,
    IncidentStatus,
    SeverityLevel,
    get_session,
)
from sqlalchemy.ext.asyncio import AsyncSession

# ── Logging bootstrap ──────────────────────────────────────────────────────
configure_logging()  # PII scrubbing and JSON rendering active
log = structlog.get_logger(__name__)

# ── Environment / config ───────────────────────────────────────────────────
JWT_SECRET: str = os.environ["JWT_SECRET_KEY"]  # hard-fail if absent
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ARCH-10: Distributed brute-force protection — configurable via environment.
# LOGIN_FAILURE_THRESHOLD: max consecutive failures per IP before 429 is returned.
# LOGIN_FAILURE_WINDOW_SECONDS: sliding window TTL for the Redis failure counter.
# Defaults are conservative (10 failures / 60s); tighten in high-risk deployments.
LOGIN_FAILURE_THRESHOLD: int = int(os.getenv("LOGIN_FAILURE_THRESHOLD", "10"))
LOGIN_FAILURE_WINDOW_SECONDS: int = int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "60"))

# Validate algorithm allowlist — prevent algorithm confusion attacks
_ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512"}
if JWT_ALGORITHM not in _ALLOWED_ALGORITHMS:
    raise ValueError(f"JWT_ALGORITHM must be one of {_ALLOWED_ALGORITHMS}, got '{JWT_ALGORITHM}'")

# ── CORS ───────────────────────────────────────────────────────────────────
_raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: List[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Rate limiter ───────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Redis JWT denylist (distributed, TTL-backed) ───────────────────────────
# CRIT-B REMEDIATION (Phase 0): Sync wrappers revoke_token() and is_token_revoked()
# removed. All denylist interactions are now natively async via _denylist directly.
# DenylistUnavailableError imported from redis_denylist to avoid re-definition.
from api.redis_denylist import DenylistUnavailableError  # noqa: E402

# Module-level singleton; initialised in lifespan startup, closed in shutdown.
_denylist: RedisDenylist | None = None

# ARCH-03: PostgresUserRepository singleton — replaces _USERS dict in production.
# Initialised in lifespan when ENVIRONMENT != 'development'.
# InMemoryUserRepository (from src.users.repository) is used in dev/test.
_user_repo: AbstractUserRepository | None = None


# ── OAuth2 bearer scheme ───────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# ── Stub user store ───────────────────────────────────────────────────────
# ARCH-07: Explicit allowlist guard — only 'development' and 'test' may run
# the in-memory _USERS stub. Any other ENVIRONMENT value (including 'staging',
# 'uat', 'preprod', or a typo) raises a FATAL error at startup.
# Rationale: the previous guard (ENVIRONMENT == 'production') was a single-value
# blocklist; a misconfigured staging deployment with ENVIRONMENT='staging' would
# silently run the stub user store with in-memory credentials.
_STUB_ALLOWED_ENVIRONMENTS = {"development", "test"}
if ENVIRONMENT not in _STUB_ALLOWED_ENVIRONMENTS:
    raise RuntimeError(
        "\n"
        f"  FATAL: In-memory _USERS stub is not permitted in ENVIRONMENT='{ENVIRONMENT}'.\n"
        "  The stub is only safe in: development, test.\n"
        "\n"
        "  If this is a production/staging deployment:\n"
        "    Action: Wire PostgresUserRepository in api/user_repository.py\n"
        "    and set DATABASE_URL=postgresql+asyncpg://... before starting.\n"
        "\n"
        "  If this is a local environment incorrectly named:\n"
        "    Fix: Set ENVIRONMENT=development in your .env file.\n"
    )

# CRIT-A REMEDIATION (Phase 0): All plaintext fallback passwords removed.
# Application fails loudly at startup if any DEV_*_PASSWORD is unset in non-production.
# Rationale: a silent fallback to 'admin-dev-only' etc. is a credential stuffing vector
# if the service is accidentally deployed to staging with ENVIRONMENT != 'production'.

def _require_dev_password(env_var: str) -> str:
    """
    Return the value of env_var, or raise a RuntimeError with a clear remediation
    instruction. No fallback strings — ever.
    """
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise RuntimeError(
            f"\n"
            f"  FATAL: {env_var} is not set.\n"
            f"  No fallback password is allowed — predictable dev credentials\n"
            f"  are a credential stuffing vector on misconfigured environments.\n"
            f"\n"
            f"  Fix: Add to your .env file:\n"
            f"    {env_var}=$(openssl rand -hex 16)\n"
        )
    if len(value) < 12:
        raise RuntimeError(
            f"{env_var} is too short (< 12 chars). "
            f"Generate a secure value: openssl rand -hex 16"
        )
    return value


_DEV_ADMIN_PW    = _require_dev_password("DEV_ADMIN_PASSWORD")
_DEV_ANALYST_PW  = _require_dev_password("DEV_ANALYST_PASSWORD")
_DEV_OPERATOR_PW = _require_dev_password("DEV_OPERATOR_PASSWORD")

_USERS: Dict[str, Dict[str, Any]] = {
    "admin": {
        "username": "admin",
        "hashed_password": hash_password(_DEV_ADMIN_PW),
        "role": "admin",
        "disabled": False,
    },
    "analyst": {
        "username": "analyst",
        "hashed_password": hash_password(_DEV_ANALYST_PW),
        "role": "analyst",
        "disabled": False,
    },
    "operator": {
        "username": "operator",
        "hashed_password": hash_password(_DEV_OPERATOR_PW),
        "role": "operator",
        "disabled": False,
    },
}

# ── Stub incident store ───────────────────────────────────────────────────
# TODO(prod): replace with IncidentRepository from src/incident_tracker.py
# _INCIDENTS stub REMOVED — replaced by IncidentRepository (see incident routes below)


# ── Pydantic models ────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    sub: str
    role: str
    jti: str
    exp: int
    iat: int
    token_type: str = "access"


class IncidentCreate(BaseModel):
    """
    Request body for POST /incidents.
    Fields aligned with IncidentRepository.create() signature.
    """
    title: str = Field(..., min_length=5, max_length=200)
    severity: str = Field(...)
    category: str = Field(..., min_length=2, max_length=100)
    owner: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()


class StatusUpdate(BaseModel):
    """
    Request body for PATCH /incidents/{id}/status.
    Only the status field is accepted; all other fields are immutable via this endpoint.
    """
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"open", "investigating", "mitigating", "resolved", "closed"}
        if v.lower() not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return v.lower()


class IncidentUpdate(BaseModel):
    """Request body for PATCH /incidents/{id} (metadata-only updates)."""
    resolution_notes: str | None = Field(default=None, max_length=10000)
    severity: str | None = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()


# ── JWT helpers ────────────────────────────────────────────────────────────
#
# ARCH-01 (Phase 2): create_access_token, create_refresh_token, and decode_token
# now route through jwt_rs256 when RS256 keys are loaded (RSA_PRIVATE_KEY_PEM set).
# The HS256 path is retained as a fallback for dev/CI environments where
# RSA_PRIVATE_KEY_PEM is intentionally absent.
#
# Routing logic:
#   jwt_rs256.rs256_available() → True  ⟹  RS256 path (sign_token / verify_token)
#   jwt_rs256.rs256_available() → False ⟹  HS256 path (PyJWT direct, existing code)
#
# Keys are loaded in the lifespan startup hook; rs256_available() is safe to call
# at any point after startup.

def create_access_token(
    data: Dict[str, Any],
    expires_delta: timedelta | None = None,
) -> Tuple[str, str, int]:
    """
    Create a signed JWT access token.

    Routes through RS256 (jwt_rs256.sign_token) when RSA keys are loaded;
    falls back to HS256 (PyJWT direct) in dev/CI.

    Args:
        data: Payload claims to encode. Must include 'sub' and 'role'.
        expires_delta: Override default expiry window.

    Returns:
        Tuple of (encoded_jwt, jti, ttl_seconds) so callers can set Redis TTL
        to match the token's natural expiry — no orphaned denylist entries.

    Raises:
        ValueError: If payload is missing required claims.
    """
    if "sub" not in data or "role" not in data:
        raise ValueError("Token payload must include 'sub' and 'role' claims")
    delta = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    # ARCH-01: Prefer RS256 when keys are loaded.
    if jwt_rs256.rs256_available():
        return jwt_rs256.sign_token(data.copy(), expires_delta=delta, token_type="access")

    # HS256 fallback (dev/CI only)
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + delta
    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": jti,
        "token_type": "access",
    })
    encoded = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded, jti, int(delta.total_seconds())


def create_refresh_token(
    data: Dict[str, Any],
) -> Tuple[str, str, int]:
    """
    Create a signed JWT refresh token.

    Routes through RS256 (jwt_rs256.sign_token) when RSA keys are loaded;
    falls back to HS256 (PyJWT direct) in dev/CI.

    Args:
        data: Payload claims to encode. Must include 'sub'.

    Returns:
        Tuple of (encoded_jwt, jti, ttl_seconds).

    Raises:
        ValueError: If payload is missing 'sub' claim.
    """
    if "sub" not in data:
        raise ValueError("Refresh token payload must include 'sub' claim")
    delta = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    # ARCH-01: Prefer RS256 when keys are loaded.
    if jwt_rs256.rs256_available():
        return jwt_rs256.sign_token(data.copy(), expires_delta=delta, token_type="refresh")

    # HS256 fallback (dev/CI only)
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + delta
    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": jti,
        "token_type": "refresh",
    })
    encoded = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded, jti, int(delta.total_seconds())


def decode_token(token: str) -> Dict[str, Any]:
    """
    Decode and validate a JWT.

    Routes through RS256 (jwt_rs256.verify_token) when keys are loaded;
    falls back to HS256 (PyJWT direct) in dev/CI.
    Both paths enforce an explicit algorithm allowlist — no 'none' or
    algorithm-confusion attacks possible in either branch.
    """
    try:
        # ARCH-01: Prefer RS256 verification path when keys are loaded.
        if jwt_rs256.rs256_available():
            return jwt_rs256.verify_token(token)

        # HS256 fallback (dev/CI only)
        payload = jwt.decode(
            token,
            JWT_SECRET,
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


# ── Auth helpers ───────────────────────────────────────────────────────────

async def authenticate_user(
    username: str,
    password: str,
    client_ip: str = "unknown",
) -> Optional[Dict[str, Any]]:
    """
    Authenticate a user against the active user repository.

    ARCH-10: Distributed brute-force protection — before credential verification,
    check a Redis INCR counter keyed by client IP. If the failure count exceeds
    LOGIN_FAILURE_THRESHOLD within LOGIN_FAILURE_WINDOW_SECONDS, raise HTTP 429
    immediately without touching the credential store.

    On a failed authentication the counter is incremented with a sliding TTL.
    On success the counter is deleted so legitimate users are not locked out
    after a single typo followed by a correct password.

    ARCH-03: When _user_repo is initialised (production/staging), delegates
    to PostgresUserRepository.authenticate() which also performs argon2
    rehash-on-login (ARCH-06) transparently.

    Dev fallback: _USERS dict with passlib bcrypt (migration window only).
    """
    # ARCH-10: Distributed brute-force check (Redis counter, per-IP)
    _failure_key = f"login_failures:{client_ip}"
    if _denylist is not None and _denylist._client is not None:
        try:
            _failure_count = await _denylist._client.get(_failure_key)
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
            # Fail-open on Redis unavailability — do not block legitimate logins
            # if the counter store is temporarily unreachable. The SlowAPI
            # per-IP rate limit on /auth/token remains active as a backstop.
            log.warning("auth.brute_force_counter_unavailable", error=str(_redis_exc))

    if _user_repo is not None:
        # Production path — argon2id with rehash-on-login
        result = await _user_repo.authenticate(username, password)
        if result is None:
            # Increment failure counter with sliding TTL
            if _denylist is not None and _denylist._client is not None:
                try:
                    pipe = _denylist._client.pipeline()
                    await pipe.incr(_failure_key)
                    await pipe.expire(_failure_key, LOGIN_FAILURE_WINDOW_SECONDS)
                    await pipe.execute()
                except Exception:
                    pass  # Counter unavailable — fail-open, SlowAPI backstop active
            return None
        # Success — clear the failure counter so a single typo doesn't persist
        if _denylist is not None and _denylist._client is not None:
            try:
                await _denylist._client.delete(_failure_key)
            except Exception:
                pass
        return result.to_dict()

    # Dev/test fallback path — _USERS in-memory dict
    user = _USERS.get(username)
    if not user:
        # Constant-time dummy verify to prevent username enumeration
        try:
            verify_password(password, hash_password("dummy-constant-time"))
        except Exception:
            pass
        if _denylist is not None and _denylist._client is not None:
            try:
                pipe = _denylist._client.pipeline()
                await pipe.incr(_failure_key)
                await pipe.expire(_failure_key, LOGIN_FAILURE_WINDOW_SECONDS)
                await pipe.execute()
            except Exception:
                pass
        return None
    if user.get("disabled"):
        return None
    if not verify_password(password, user["hashed_password"]):
        if _denylist is not None and _denylist._client is not None:
            try:
                pipe = _denylist._client.pipeline()
                await pipe.incr(_failure_key)
                await pipe.expire(_failure_key, LOGIN_FAILURE_WINDOW_SECONDS)
                await pipe.execute()
            except Exception:
                pass
        return None
    # Success — clear counter
    if _denylist is not None and _denylist._client is not None:
        try:
            await _denylist._client.delete(_failure_key)
        except Exception:
            pass
    return user


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
    try:
        # CRIT-B FIX: await async-native is_revoked() directly
        revoked = await _denylist.is_revoked(jti) if _denylist else False
        if _denylist is None:
            raise DenylistUnavailableError("Denylist not initialised")
    except DenylistUnavailableError:
        # Fail-closed: denylist unreachable — return 503, not 401, so clients retry
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
            headers={"Retry-After": "30"},
        )
    if revoked:
        log.warning(
            "auth.revoked_token_access",
            jti=jti,
            log_type="audit",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )
    username: str = payload.get("sub", "")
    # ARCH-03: use _user_repo when available; fall back to _USERS dict in dev
    if _user_repo is not None:
        _record = await _user_repo.get_by_username(username)
        user: dict | None = _record.to_dict() if _record is not None else None
    else:
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


# ── FastAPI lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _denylist

    # ── Startup ────────────────────────────────────────────────────────────
    log.info("api.startup", environment=ENVIRONMENT, algorithm=JWT_ALGORITHM)

    global _user_repo

    # CR-1: Verify DB connectivity + Alembic migration state (no create_all)
    try:
        from src.incident_tracker import init_db  # noqa: E402
        await init_db()
    except Exception as _db_exc:
        log.error("api.startup.db_check_failed", error=str(_db_exc))
        raise

    # ARCH-03: Wire PostgresUserRepository when DATABASE_URL is a real Postgres URL.
    # Dev fallback: InMemoryUserRepository seeded from environment variables.
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("postgresql"):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: PLC0415
        _pg_engine = create_async_engine(database_url, pool_pre_ping=True)
        _pg_session_factory = async_sessionmaker(_pg_engine, expire_on_commit=False)
        _user_repo = PostgresUserRepository(session_factory=_pg_session_factory)
        app.state.user_repo = _user_repo
        log.info("user_repo.postgres_wired", environment=ENVIRONMENT)
    else:
        from src.users.repository import InMemoryUserRepository  # noqa: PLC0415
        _user_repo = InMemoryUserRepository(users=_USERS)
        app.state.user_repo = _user_repo
        log.warning(
            "user_repo.in_memory_fallback",
            hint="Set DATABASE_URL=postgresql+asyncpg://... to use PostgresUserRepository",
        )

    # Initialise Redis-backed JWT denylist (R-03)
    _denylist = RedisDenylist(redis_url=REDIS_URL)
    await _denylist.connect()
    app.state.redis = _denylist._client  # Expose Redis client for rate_limit.py
    log.info("denylist.connected", redis_url=REDIS_URL)

    # ARCH-01: Load RS256 key pair when RSA_PRIVATE_KEY_PEM is set.
    # Falls back to HS256 gracefully if env var is absent (dev/CI).
    # In production: inject RSA_PRIVATE_KEY_PEM from secrets manager (ARCH-04).
    _rs256_active = jwt_rs256.load_keys()
    if _rs256_active:
        app.include_router(jwt_rs256.jwks_router)  # serve /.well-known/jwks.json
        log.info("jwt.rs256_active", key_id=jwt_rs256._key_id)
    else:
        log.warning(
            "jwt.hs256_fallback_active",
            hint="Set RSA_PRIVATE_KEY_PEM to upgrade to RS256 (ARCH-01)",
        )

    # Bootstrap OpenTelemetry tracing (R-20)
    configure_otel(
        service_name=os.getenv("OTEL_SERVICE_NAME", "ml-incident-api"),
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        environment=ENVIRONMENT,
    )
    log.info("otel.configured")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    await _denylist.close()
    shutdown_otel()
    log.info("api.shutdown")


app = FastAPI(
    title="ML Incident Response API",
    version="2.1.0",
    description="Production-hardened ML incident management API with JWT auth, RBAC, and audit logging.",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENVIRONMENT", "development") != "production" else None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# ARCH-03 / ARCH-05: Mount GDPR data subject rights endpoints
# Routes: GET /users/me/export  (Art. 15 access)
#         DELETE /users/me      (Art. 17 erasure)
app.include_router(gdpr_router)

# -- CR-2: Map InvalidTransitionError -> HTTP 409 Conflict -------------------
# InvalidTransitionError is raised by IncidentRepository.update_status() when
# a caller requests a transition forbidden by the domain state machine.
# Returning 409 (not 422) is intentional: the request is syntactically valid
# but semantically conflicts with the current resource state (RFC 9110 s15.5.10).
try:
    from src.incident_tracker import InvalidTransitionError  # noqa: E402

    @app.exception_handler(InvalidTransitionError)
    async def _invalid_transition_handler(
        request: Request, exc: InvalidTransitionError
    ) -> JSONResponse:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=409,
            content={
                "error": "invalid_transition",
                "detail": str(exc),
                "hint": "Check the allowed_transitions for the current incident status.",
            },
        )
except ImportError:
    pass  # Graceful: incident_tracker not yet imported at module load in some test configs

# ── Request hardening middleware (Phase 0: HIGH-C, MED-E, timeout) ────────────
# Starlette applies middleware in reverse registration order.
# Registration here: SecurityHeaders (outermost) -> MaxBodySize -> RequestTimeout
from api.middleware import (  # noqa: E402
    MaxBodySizeMiddleware,
    SecurityHeadersMiddleware,
    RequestTimeoutMiddleware,
)
app.add_middleware(SecurityHeadersMiddleware, environment=ENVIRONMENT)
app.add_middleware(MaxBodySizeMiddleware)
app.add_middleware(RequestTimeoutMiddleware)

# ── CORS middleware ────────────────────────────────────────────────────────
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )


# ── Trace + security headers middleware ───────────────────────────────────
@app.middleware("http")
async def trace_and_security_headers(request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
    trace_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        method=request.method,
        path=str(request.url.path),
        client_ip=request.client.host if request.client else "unknown",
    )

    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    log.info(
        "http.request",
        status_code=response.status_code,
        duration_ms=duration_ms,
    )

    structlog.contextvars.clear_contextvars()

    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cache-Control"] = "no-store"
    if "server" in response.headers:
        del response.headers["server"]
    if "x-powered-by" in response.headers:
        del response.headers["x-powered-by"]

    return response


# ── Health probes ──────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"], include_in_schema=False)
async def liveness() -> Dict[str, str]:
    """Kubernetes liveness probe — confirms process is alive."""
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready", tags=["ops"], include_in_schema=False)
async def readiness() -> JSONResponse:
    """Kubernetes readiness probe — checks all critical dependencies."""
    checks: Dict[str, str] = {}
    all_ok = True

    # JWT subsystem check — exercises the active signing path (RS256 or HS256)
    try:
        _test_payload = {"sub": "__healthcheck__", "role": "_probe"}
        test_token, test_jti, _ = create_access_token(_test_payload, timedelta(seconds=5))
        # Verify using the same active path
        if jwt_rs256.rs256_available():
            jwt_rs256.verify_token(test_token)
        else:
            jwt.decode(test_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        checks["jwt_subsystem"] = "ok"
        checks["jwt_algorithm"] = "RS256" if jwt_rs256.rs256_available() else "HS256"
    except Exception as exc:
        checks["jwt_subsystem"] = f"error: {exc}"
        all_ok = False

    # Redis denylist check
    try:
        if _denylist is not None:
            await _denylist.ping()
            checks["redis_denylist"] = "ok"
        else:
            checks["redis_denylist"] = "not_initialised"
            all_ok = False
    except Exception as exc:
        checks["redis_denylist"] = f"error: {exc}"
        all_ok = False

    # Required environment variable check
    for var in ["JWT_SECRET_KEY"]:
        checks[f"env_{var}"] = "ok" if os.getenv(var) else "missing"
        if not os.getenv(var):
            all_ok = False

    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ── Auth routes ────────────────────────────────────────────────────────────

@app.post("/auth/token", response_model=Token, tags=["auth"])
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    """Issue an access + refresh token pair. Rate limited to 5/min per IP."""
    client_ip = request.client.host if request.client else "unknown"
    user = await authenticate_user(form.username, form.password, client_ip=client_ip)
    if not user:
        log.warning(
            "auth.login_failed",
            username=form.username,
            log_type="audit",
        )
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


@app.post("/auth/refresh", response_model=Token, tags=["auth"])
@limiter.limit("5/minute")
async def refresh_token_endpoint(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> Token:
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    ARCH-08: Rate limited to 5/minute per IP — aligned with /auth/token to
    prevent asymmetric token-rotation brute-force attacks.
    """
    payload = decode_token(token)
    if payload.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type — submit a refresh token",
        )
    old_jti = payload.get("jti", "")
    if _denylist is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
            headers={"Retry-After": "30"},
        )
    # CRIT-B FIX: await async-native check
    if await _denylist.is_revoked(old_jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    username: str = payload.get("sub", "")
    # ARCH-03: use _user_repo when available
    if _user_repo is not None:
        _record = await _user_repo.get_by_username(username)
        user: dict | None = _record.to_dict() if _record is not None else None
    else:
        user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    # Rotate: await the revocation write to confirm before issuing new tokens
    await _denylist.revoke(
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


@app.post("/auth/logout", status_code=204, tags=["auth"])
async def logout(
    current_user: Annotated[dict, Depends(get_current_user)],
) -> None:
    """
    Revoke the caller's current access token immediately via the Redis denylist.

    ARCH-09: No require_role() guard is applied — this is intentional.
    Any authenticated principal (admin, analyst, operator) must be able to
    revoke their own token without restriction. Adding a role gate here would
    prevent lower-privileged users from logging out, which is a security
    anti-pattern. Audit log entry is emitted on every successful call.
    """
    jti = current_user["jti"]
    ttl = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    if _denylist is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )
    # CRIT-B FIX: await confirmed write — revocation is durable before response returns
    await _denylist.revoke(jti, ttl_seconds=ttl)
    log.info(
        "auth.logout",
        username=current_user["username"],
        jti=jti,
        log_type="audit",
    )


# ── Incident routes (repository-backed) ───────────────────────────────────
#
# All four routes use FastAPI Depends(get_session) to receive a scoped
# async SQLAlchemy session and construct an IncidentRepository per request.
# The _INCIDENTS in-memory stub has been fully removed.
#
# Status transitions are enforced by the domain state machine via
# IncidentRepository.update_status(); violations raise InvalidTransitionError
# which is mapped to HTTP 409 by the exception handler registered above.


@app.post("/incidents/", status_code=201, tags=["incidents"],
          dependencies=[Depends(check_user_rate_limit("incidents"))])
async def create_incident(
    incident: IncidentCreate,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dict[str, Any]:
    """Create a new incident in OPEN status. Requires analyst or admin role."""
    repo = IncidentRepository(session)
    try:
        severity_enum = SeverityLevel(incident.severity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid severity '{incident.severity}'. Must be one of SEV-1..SEV-4.",
        )

    record = await repo.create(
        title=incident.title,
        severity=severity_enum,
        category=incident.category,
        owner=incident.owner,
        description=incident.description,
    )
    log.info(
        "incident.created",
        incident_id=record.id,
        severity=record.severity.value,
        category=record.category,
        created_by=current_user["username"],
        log_type="audit",
    )
    return record.to_dict()


@app.get("/incidents/", tags=["incidents"])
async def list_incidents(
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """List open incidents (most recent first). Requires analyst, operator, or admin."""
    repo = IncidentRepository(session)
    incidents = await repo.list_open(limit=min(limit, 200))
    page = incidents[offset: offset + limit]
    log.info(
        "incident.list",
        returned=len(page),
        requested_by=current_user["username"],
    )
    return {"total": len(incidents), "offset": offset, "limit": limit, "incidents": [i.to_dict() for i in page]}


@app.get("/incidents/{incident_id}", tags=["incidents"])
async def get_incident(
    incident_id: str,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dict[str, Any]:
    """Retrieve a single incident by UUID. Requires analyst, operator, or admin."""
    repo = IncidentRepository(session)
    record = await repo.get(incident_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )
    return record.to_dict()


@app.patch("/incidents/{incident_id}/status", tags=["incidents"])
async def update_incident_status(
    incident_id: str,
    update: StatusUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dict[str, Any]:
    """
    Transition an incident to a new lifecycle status.

    Valid transitions are enforced by the domain state machine.
    Invalid transitions return HTTP 409 Conflict with an 'invalid_transition' error body.
    Requires operator or admin role.
    """
    repo = IncidentRepository(session)
    try:
        new_status_enum = IncidentStatus(update.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown status '{update.status}'.",
        )

    # IncidentRepository.get() used first so we can return a clean 404
    existing = await repo.get(incident_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    # InvalidTransitionError propagates to the registered 409 exception handler
    record = await repo.update_status(
        incident_id=incident_id,
        new_status=new_status_enum,
    )
    log.info(
        "incident.status_updated",
        incident_id=incident_id,
        new_status=record.status.value,
        updated_by=current_user["username"],
        log_type="audit",
    )
    return record.to_dict()


@app.patch("/incidents/{incident_id}", tags=["incidents"])
async def update_incident_metadata(
    incident_id: str,
    update: IncidentUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dict[str, Any]:
    """
    Update mutable incident metadata (resolution_notes, severity).
    Does NOT change lifecycle status — use PATCH /incidents/{id}/status for that.
    Requires operator or admin role.
    """
    repo = IncidentRepository(session)
    record = await repo.get(incident_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    if update.severity is not None:
        try:
            record.severity = SeverityLevel(update.severity)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid severity '{update.severity}'.",
            )

    if update.resolution_notes is not None:
        record.description = update.resolution_notes

    log.info(
        "incident.metadata_updated",
        incident_id=incident_id,
        updated_by=current_user["username"],
        log_type="audit",
    )
    return record.to_dict()
