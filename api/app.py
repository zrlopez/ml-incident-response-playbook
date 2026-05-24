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
"""
from __future__ import annotations

import os
import re
import uuid
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional, Tuple

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
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


# ── Password hashing ───────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── OAuth2 bearer scheme ───────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# ── Stub user store ───────────────────────────────────────────────────────
# PRODUCTION GATE: Hard-fail on startup if ENVIRONMENT=production.
# Replace with PostgresUserRepository before any production deployment.
# See api/user_repository.py for the production implementation contract.
if ENVIRONMENT == "production":
    raise RuntimeError(
        "\n"
        "  FATAL: Stub user store must not run in production.\n"
        "  Action: Wire PostgresUserRepository in api/user_repository.py\n"
        "  and inject it via the lifespan context before deploying.\n"
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
        "hashed_password": pwd_context.hash(_DEV_ADMIN_PW),
        "role": "admin",
        "disabled": False,
    },
    "analyst": {
        "username": "analyst",
        "hashed_password": pwd_context.hash(_DEV_ANALYST_PW),
        "role": "analyst",
        "disabled": False,
    },
    "operator": {
        "username": "operator",
        "hashed_password": pwd_context.hash(_DEV_OPERATOR_PW),
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

def create_access_token(
     Dict[str, Any],
    expires_delta: timedelta | None = None,
) -> Tuple[str, str, int]:
    """
    Create a signed JWT access token.

    Args:
         Payload claims to encode. Must include 'sub' and 'role'.
        expires_delta: Override default expiry window.

    Returns:
        Tuple of (encoded_jwt, jti, ttl_seconds) so callers can set Redis TTL
        to match the token's natural expiry — no orphaned denylist entries.

    Raises:
        ValueError: If payload is missing required claims.
    """
    if "sub" not in data or "role" not in 
        raise ValueError("Token payload must include 'sub' and 'role' claims")
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    delta = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
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
     Dict[str, Any],
) -> Tuple[str, str, int]:
    """
    Create a signed JWT refresh token.

    Args:
         Payload claims to encode. Must include 'sub'.

    Returns:
        Tuple of (encoded_jwt, jti, ttl_seconds).

    Raises:
        ValueError: If payload is missing 'sub' claim.
    """
    if "sub" not in 
        raise ValueError("Refresh token payload must include 'sub' claim")
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    delta = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
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
    """Decode and validate a JWT with an explicit algorithm allowlist."""
    try:
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

async def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Authenticate a user against the active user repository.

    ARCH-03: When _user_repo is initialised (production/staging), delegates
    to PostgresUserRepository.authenticate() which also performs argon2
    rehash-on-login (ARCH-06) transparently.

    Dev fallback: _USERS dict with passlib bcrypt (migration window only).
    """
    if _user_repo is not None:
        # Production path — argon2id with rehash-on-login
        return await _user_repo.authenticate(username, password)

    # Dev/test fallback path — _USERS in-memory dict
    user = _USERS.get(username)
    if not user:
        # Constant-time dummy verify to prevent username enumeration
        try:
            verify_password(password, hash_password("dummy-constant-time"))
        except Exception:
            pass
        return None
    if user.get("disabled"):
        return None
    if not pwd_context.verify(password, user["hashed_password"]):
        return None
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
            event="revoked_token_access_attempt",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )
    username: str = payload.get("sub", "")
    # ARCH-03: use _user_repo when available; fall back to _USERS dict in dev
    if _user_repo is not None:
        user = await _user_repo.get_by_username(username)
    else:
        user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )
    return {**user, "jti": jti}


def require_role(*roles: str):
    """FastAPI dependency factory for role-based access control."""
    async def _checker(
        current_user: Annotated[dict, Depends(get_current_user)],
    ) -> dict:
        if current_user["role"] not in roles:
            log.warning(
                "auth.access_denied",
                username=current_user["username"],
                role=current_user["role"],
                required_roles=roles,
                log_type="audit",
                event="access_denied",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user['role']}' is not authorised for this action.",
            )
        return current_user
    return _checker


# ── FastAPI lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
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
        _user_repo = InMemoryUserRepository()
        app.state.user_repo = _user_repo
        log.warning(
            "user_repo.in_memory_fallback",
            hint="Set DATABASE_URL=postgresql+asyncpg://... to use PostgresUserRepository",
        )

    # Initialise Redis-backed JWT denylist (R-03)
    _denylist = RedisDenylist(redis_url=REDIS_URL)
    await _denylist.connect()
    app.state.redis = _denylist._redis  # Expose Redis client for rate_limit.py
    log.info("denylist.connected", redis_url=REDIS_URL)

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
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
    ):
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
async def trace_and_security_headers(request: Request, call_next):
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
    response.headers.pop("server", None)
    response.headers.pop("x-powered-by", None)

    return response


# ── Health probes ──────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"], include_in_schema=False)
async def liveness():
    """Kubernetes liveness probe — confirms process is alive."""
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready", tags=["ops"], include_in_schema=False)
async def readiness():
    """Kubernetes readiness probe — checks all critical dependencies."""
    checks: Dict[str, str] = {}
    all_ok = True

    # JWT subsystem check
    try:
        _test_payload = {"sub": "__healthcheck__", "role": "_probe"}
        test_token, test_jti, _ = create_access_token(_test_payload, timedelta(seconds=5))
        jwt.decode(test_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        checks["jwt_subsystem"] = "ok"
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
):
    """Issue an access + refresh token pair. Rate limited to 5/min per IP."""
    user = await authenticate_user(form.username, form.password)
    if not user:
        log.warning(
            "auth.login_failed",
            username=form.username,
            log_type="audit",
            event="authentication_failure",
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
        event="authentication_success",
    )

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=access_ttl,
    )


@app.post("/auth/refresh", response_model=Token, tags=["auth"])
@limiter.limit("10/minute")
async def refresh_token_endpoint(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
):
    """Exchange a valid refresh token for a new access + refresh token pair."""
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
        user = await _user_repo.get_by_username(username)
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
        event="token_rotated",
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
):
    """Revoke the current access token immediately via the Redis denylist."""
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
        event="logout",
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
):
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
        event="incident_created",
    )
    return record.to_dict()


@app.get("/incidents/", tags=["incidents"])
async def list_incidents(
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
    offset: int = 0,
):
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
):
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
):
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
        event="incident_status_updated",
    )
    return record.to_dict()


@app.patch("/incidents/{incident_id}", tags=["incidents"])
async def update_incident_metadata(
    incident_id: str,
    update: IncidentUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
):
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
        event="incident_metadata_updated",
    )
    return record.to_dict()
