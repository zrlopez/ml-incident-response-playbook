"""
ML Incident Response API — Hardened Production Build
=====================================================
Remediation: 2026-05-23
Findings addressed:
  CRIT-01    broken create_access_token parameter
  HIGH-01    python-jose → PyJWT (CVE-2024-33663)
  HIGH-02    SlowAPI rate limiting on /auth/token
  HIGH-03    Distributed trace_id middleware
  MED-01     CORS allowlist empty-string parse bug
  MED-02     uuid4 incident IDs (replaces timestamp collision)
  MED-03     Token revocation denylist (in-memory → Redis-backed)
  MED-04     /ready probe with real dependency health checks
  MED-05     audit() wired to all security-relevant events
  MED-06     configure_logging() called at startup
  R-03       Redis-backed distributed JWT denylist replaces process-local set
  R-20       OTel tracing bootstrap via observability/otel_setup.py
  ARCH-01    RS256 token signing when RSA_PRIVATE_KEY_PEM is set
  ARCH-07    _USERS stub allowlist guard — blocks staging bypass
  ARCH-08    /auth/refresh rate limit lowered to 5/min
  ARCH-09    /auth/logout docstring clarified — all roles may revoke own token
  ARCH-10    Distributed brute-force counter (Redis) on login failures
  OPEN-01    Explicit updated_at write on metadata PATCH route (2026-05-24)
  API-SVC-01 Incident routes delegate to IncidentService (2026-05-25)
  API-RESP-01 Incident routes return typed IncidentResponse Pydantic models (2026-05-25)
  API-CURSOR-01 list_incidents uses DB-level cursor pagination via before_id (2026-05-25)
  API-KEY-01 RS256KeyStore attached to app.state.key_store at startup (2026-05-25)
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
from src.auth.key_store import RS256KeyStore
from src.incident_tracker import (
    IncidentStatus,
    SeverityLevel,
    InvalidTransitionError,
    get_session,
)
from src.services.incident_service import IncidentService
from src.schemas.incident import IncidentResponse, IncidentListResponse
from sqlalchemy.ext.asyncio import AsyncSession

# ── Logging bootstrap ────────────────────────────────────────────
configure_logging()  # PII scrubbing and JSON rendering active
log = structlog.get_logger(__name__)

# ── Environment / config ───────────────────────────────────────────
JWT_SECRET: str = os.environ["JWT_SECRET_KEY"]  # hard-fail if absent
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ARCH-10: Distributed brute-force protection
LOGIN_FAILURE_THRESHOLD: int = int(os.getenv("LOGIN_FAILURE_THRESHOLD", "10"))
LOGIN_FAILURE_WINDOW_SECONDS: int = int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "60"))

_ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512"}
if JWT_ALGORITHM not in _ALLOWED_ALGORITHMS:
    raise ValueError(f"JWT_ALGORITHM must be one of {_ALLOWED_ALGORITHMS}, got '{JWT_ALGORITHM}'")

# ── CORS ──────────────────────────────────────────────────────────────
_raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: List[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Rate limiter ─────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Redis JWT denylist ────────────────────────────────────────────────────
from api.redis_denylist import DenylistUnavailableError  # noqa: E402

_denylist: RedisDenylist | None = None
_user_repo: AbstractUserRepository | None = None

# ── OAuth2 bearer scheme ──────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# ── Stub user store ──────────────────────────────────────────────────────────────
# ARCH-07: Explicit allowlist guard
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


def _require_dev_password(env_var: str) -> str:
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


# ── Pydantic models ─────────────────────────────────────────────────────────────
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


# ── JWT helpers ─────────────────────────────────────────────────────────────
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
    encoded = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
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
    encoded = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded, jti, int(delta.total_seconds())


def decode_token(token: str) -> Dict[str, Any]:
    try:
        if jwt_rs256.rs256_available():
            return jwt_rs256.verify_token(token)
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


# ── Auth helpers ─────────────────────────────────────────────────────────────
async def authenticate_user(
    username: str,
    password: str,
    client_ip: str = "unknown",
) -> Optional[Dict[str, Any]]:
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
            log.warning("auth.brute_force_counter_unavailable", error=str(_redis_exc))

    if _user_repo is not None:
        result = await _user_repo.authenticate(username, password)
        if result is None:
            if _denylist is not None and _denylist._client is not None:
                try:
                    pipe = _denylist._client.pipeline()
                    await pipe.incr(_failure_key)
                    await pipe.expire(_failure_key, LOGIN_FAILURE_WINDOW_SECONDS)
                    await pipe.execute()
                except Exception:
                    pass
            return None
        if _denylist is not None and _denylist._client is not None:
            try:
                await _denylist._client.delete(_failure_key)
            except Exception:
                pass
        return result.to_dict()

    user = _USERS.get(username)
    if not user:
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
        revoked = await _denylist.is_revoked(jti) if _denylist else False
        if _denylist is None:
            raise DenylistUnavailableError("Denylist not initialised")
    except DenylistUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
            headers={"Retry-After": "30"},
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


# ── FastAPI lifespan ───────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _denylist, _user_repo

    log.info("api.startup", environment=ENVIRONMENT, algorithm=JWT_ALGORITHM)

    # CR-1: Verify DB connectivity + Alembic migration state
    try:
        from src.incident_tracker import init_db
        await init_db()
    except Exception as _db_exc:
        log.error("api.startup.db_check_failed", error=str(_db_exc))
        raise

    # ARCH-03: Wire PostgresUserRepository when DATABASE_URL is a real Postgres URL
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("postgresql"):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        _pg_engine = create_async_engine(database_url, pool_pre_ping=True)
        _pg_session_factory = async_sessionmaker(_pg_engine, expire_on_commit=False)
        _user_repo = PostgresUserRepository(session_factory=_pg_session_factory)
        app.state.user_repo = _user_repo
        log.info("user_repo.postgres_wired", environment=ENVIRONMENT)
    else:
        from src.users.repository import InMemoryUserRepository
        _user_repo = InMemoryUserRepository(users=_USERS)
        app.state.user_repo = _user_repo
        log.warning(
            "user_repo.in_memory_fallback",
            hint="Set DATABASE_URL=postgresql+asyncpg://... to use PostgresUserRepository",
        )

    # Initialise Redis-backed JWT denylist (R-03)
    _denylist = RedisDenylist(redis_url=REDIS_URL)
    await _denylist.connect()
    app.state.redis = _denylist._client
    log.info("denylist.connected", redis_url=REDIS_URL)

    # API-KEY-01: Load RS256KeyStore and attach to app.state.
    # jwt_rs256.load_keys() is still called for backward compat with the existing
    # rs256_available() / sign_token() / verify_token() wrappers.
    # RS256KeyStore is the canonical injection point for new routes and tests.
    _rs256_active = jwt_rs256.load_keys()
    if _rs256_active:
        try:
            app.state.key_store = RS256KeyStore.from_env()
            log.info("jwt.key_store_loaded", key_id=app.state.key_store.key_id)
        except Exception as _ks_exc:
            # Non-fatal: key_store unavailable means new routes fall back gracefully
            log.warning("jwt.key_store_load_failed", error=str(_ks_exc))
            app.state.key_store = None
        app.include_router(jwt_rs256.jwks_router)
        log.info("jwt.rs256_active", key_id=jwt_rs256._key_id)
    else:
        app.state.key_store = None
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

    # ── Shutdown ─────────────────────────────────────────────────────────
    await _denylist.close()
    shutdown_otel()
    log.info("api.shutdown")


app = FastAPI(
    title="ML Incident Response API",
    version="2.2.0",
    description=(
        "Production-hardened ML incident management API with JWT auth, "
        "RBAC, cursor pagination, and structured audit logging."
    ),
    lifespan=lifespan,
    docs_url="/docs" if ENVIRONMENT != "production" else None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.include_router(gdpr_router)

# CR-2: Map InvalidTransitionError → HTTP 409 Conflict
@app.exception_handler(InvalidTransitionError)
async def _invalid_transition_handler(
    request: Request, exc: InvalidTransitionError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "invalid_transition",
            "detail": str(exc),
            "hint": "Check the allowed_transitions for the current incident status.",
        },
    )


from api.middleware import (  # noqa: E402
    MaxBodySizeMiddleware,
    SecurityHeadersMiddleware,
    RequestTimeoutMiddleware,
)
app.add_middleware(SecurityHeadersMiddleware, environment=ENVIRONMENT)
app.add_middleware(MaxBodySizeMiddleware)
app.add_middleware(RequestTimeoutMiddleware)

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def trace_and_security_headers(
    request: Request, call_next: Callable[..., Awaitable[Response]]
) -> Response:
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
    log.info("http.request", status_code=response.status_code, duration_ms=duration_ms)
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


# ── Health probes ────────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"], include_in_schema=False)
async def liveness() -> Dict[str, str]:
    """Kubernetes liveness probe — confirms process is alive."""
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready", tags=["ops"], include_in_schema=False)
async def readiness() -> JSONResponse:
    """Kubernetes readiness probe — checks all critical dependencies."""
    checks: Dict[str, str] = {}
    all_ok = True
    try:
        _test_payload = {"sub": "__healthcheck__", "role": "_probe"}
        test_token, test_jti, _ = create_access_token(_test_payload, timedelta(seconds=5))
        if jwt_rs256.rs256_available():
            jwt_rs256.verify_token(test_token)
        else:
            jwt.decode(test_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        checks["jwt_subsystem"] = "ok"
        checks["jwt_algorithm"] = "RS256" if jwt_rs256.rs256_available() else "HS256"
    except Exception as exc:
        checks["jwt_subsystem"] = f"error: {exc}"
        all_ok = False
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


# ── Auth routes ─────────────────────────────────────────────────────────────
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


@app.post("/auth/refresh", response_model=Token, tags=["auth"])
@limiter.limit("5/minute")
async def refresh_token_endpoint(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
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
    if _denylist is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
            headers={"Retry-After": "30"},
        )
    if await _denylist.is_revoked(old_jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )
    username: str = payload.get("sub", "")
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
    No role gate is applied — any authenticated principal must be able to log out
    (ARCH-09). Audit log entry emitted on every successful call.
    """
    jti = current_user["jti"]
    ttl = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    if _denylist is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )
    await _denylist.revoke(jti, ttl_seconds=ttl)
    log.info("auth.logout", username=current_user["username"], jti=jti, log_type="audit")


# ── Incident routes (IncidentService-backed) ────────────────────────────────────
#
# API-SVC-01: All four routes delegate to IncidentService.
# API-RESP-01: All routes return typed IncidentResponse via response_model=.
# API-CURSOR-01: GET /incidents/ uses DB-level cursor pagination (before_id).
#
# Status transitions are enforced by the domain state machine via
# IncidentService.transition_status() → IncidentRepository.update_status().
# InvalidTransitionError → HTTP 409 via the exception handler above.


@app.post(
    "/incidents/",
    status_code=201,
    response_model=IncidentResponse,
    tags=["incidents"],
    dependencies=[Depends(check_user_rate_limit("incidents"))],
)
async def create_incident(
    incident: IncidentCreate,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """Create a new incident in OPEN status. Requires analyst or admin role."""
    try:
        severity_enum = SeverityLevel(incident.severity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid severity '{incident.severity}'. Must be one of SEV-1..SEV-4.",
        )
    service = IncidentService(session)
    record = await service.open_incident(
        title=incident.title,
        severity=severity_enum,
        category=incident.category,
        opened_by=current_user["username"],
        owner=incident.owner,
        description=incident.description,
    )
    return IncidentResponse.model_validate(record.to_dict())


@app.get(
    "/incidents/",
    response_model=IncidentListResponse,
    tags=["incidents"],
)
async def list_incidents(
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
    before_id: Optional[str] = None,
) -> IncidentListResponse:
    """
    List open incidents, newest-first, with cursor-based pagination.

    API-CURSOR-01: Replaces the previous offset-based slice. The DB-level
    cursor predicate is evaluated in the repository, not in Python. Clients
    use the returned next_cursor as the ?before_id= parameter for the next page.
    When next_cursor is null, the caller has reached the last page.

    Query params:
      limit     — Page size (default 50, max 1000 enforced at repo layer).
      before_id — Cursor from the previous page's next_cursor field.
    """
    service = IncidentService(session)
    try:
        page = await service.list_open(limit=limit, before_id=before_id)
    except ValueError as exc:
        # Repository raises ValueError if before_id cursor does not exist
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    incidents = [IncidentResponse.model_validate(i.to_dict()) for i in page]
    next_cursor = incidents[-1].id if len(incidents) == limit else None
    log.info(
        "incident.list",
        returned=len(incidents),
        has_next_page=next_cursor is not None,
        requested_by=current_user["username"],
    )
    return IncidentListResponse(
        incidents=incidents,
        next_cursor=next_cursor,
        count=len(incidents),
    )


@app.get(
    "/incidents/{incident_id}",
    response_model=IncidentResponse,
    tags=["incidents"],
)
async def get_incident(
    incident_id: str,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """Retrieve a single incident by UUID. Requires analyst, operator, or admin."""
    service = IncidentService(session)
    record = await service.get_incident(incident_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )
    return IncidentResponse.model_validate(record.to_dict())


@app.patch(
    "/incidents/{incident_id}/status",
    response_model=IncidentResponse,
    tags=["incidents"],
)
async def update_incident_status(
    incident_id: str,
    update: StatusUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """
    Transition an incident to a new lifecycle status.
    Valid transitions enforced by the domain state machine.
    Invalid transitions return HTTP 409 Conflict. Requires operator or admin role.
    """
    try:
        new_status_enum = IncidentStatus(update.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown status '{update.status}'.",
        )
    service = IncidentService(session)
    # 404 check before attempting transition
    if await service.get_incident(incident_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )
    # InvalidTransitionError propagates to the registered 409 exception handler
    record = await service.transition_status(
        incident_id=incident_id,
        new_status=new_status_enum,
        transitioned_by=current_user["username"],
    )
    return IncidentResponse.model_validate(record.to_dict())


@app.patch(
    "/incidents/{incident_id}",
    response_model=IncidentResponse,
    tags=["incidents"],
)
async def update_incident_metadata(
    incident_id: str,
    update: IncidentUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """
    Update mutable incident metadata (resolution_notes, severity).
    Does NOT change lifecycle status — use PATCH /incidents/{id}/status for that.
    Requires operator or admin role.

    OPEN-01: updated_at is explicitly set here after any field mutation.
    SQLAlchemy's onupdate= hook does not fire on ORM-level attribute assignments
    followed by a session flush. Without the explicit write, updated_at remains
    stale after metadata changes, corrupting time-based metric queries.
    """
    service = IncidentService(session)
    record = await service.get_incident(incident_id)
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
    # OPEN-01: Explicit timestamp
    record.updated_at = datetime.now(timezone.utc)
    log.info(
        "incident.metadata_updated",
        incident_id=incident_id,
        updated_by=current_user["username"],
        log_type="audit",
    )
    return IncidentResponse.model_validate(record.to_dict())
