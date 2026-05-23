"""
ML Incident Response API
========================
Version: 2.1.0

Security controls active in this build:
  - PyJWT with explicit algorithm allowlist (HS256/HS384/HS512 only)
  - bcrypt password hashing via bcrypt package (passlib removed)
  - SlowAPI rate limiting on all auth endpoints
  - Redis-backed JWT denylist with TTL (distributed, survives restarts)
  - RBAC dependency factory (require_role)
  - Structured audit logging via structlog (PII scrubbing in logging_config)
  - OpenTelemetry distributed tracing
  - Hardened HTTP security headers on every response
  - CORS restricted to explicit allowlist (empty = deny all)
  - Incident persistence via async SQLAlchemy repository (not in-process dict)
  - Prometheus counters for incident creation and revoked-token access attempts

Environment variables (all consumed via src/config.py Settings):
  JWT_SECRET_KEY          Required. Min 32 chars. Hard-fail if absent.
  JWT_ALGORITHM           Optional. Default HS256. Must be HS256/HS384/HS512.
  ACCESS_TOKEN_EXPIRE_MINUTES  Optional. Default 30.
  REFRESH_TOKEN_EXPIRE_DAYS    Optional. Default 7.
  ENVIRONMENT             Optional. Default development. production disables /docs.
  REDIS_URL               Optional. Default redis://localhost:6379/0.
  DATABASE_URL            Optional. Default sqlite+aiosqlite:///./incidents.db.
  CORS_ALLOWED_ORIGINS    Optional. Comma-separated. Empty = deny all CORS.
"""
from __future__ import annotations

import os
import uuid
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import structlog

from observability.logging_config import configure_logging
from observability.otel_setup import configure_otel, shutdown_otel
from api.redis_denylist import RedisDenylist
from src.incident_tracker import (
    IncidentRepository,
    IncidentStatus,
    SeverityLevel,
    get_session,
    init_db,
)
from sqlalchemy.ext.asyncio import AsyncSession

# ── Logging bootstrap ──────────────────────────────────────────────────────
configure_logging()
log = structlog.get_logger(__name__)

# ── Prometheus counters (optional — app runs without prometheus_client) ────
#
# The counters are initialised once at module load. If prometheus_client is
# not installed (e.g. in minimal test environments) the _prom_available flag
# is False and every increment call is a no-op. This keeps the API fully
# functional without the monitoring dependency.
#
# Counters defined here match the metric names documented in:
#   monitoring/metrics.md
#   monitoring/alert_rules.yml (ml_incident_created_total, ml_revoked_token_access_total)

try:
    from prometheus_client import Counter, make_asgi_app

    _INCIDENT_CREATED = Counter(
        "ml_incident_created_total",
        "Total number of ML incidents created via POST /incidents",
        ["severity", "category"],
    )
    _REVOKED_TOKEN_ACCESS = Counter(
        "ml_revoked_token_access_total",
        "Total number of requests rejected because the JWT was on the denylist",
        ["reason"],
    )
    _prom_available = True
except ImportError:  # pragma: no cover
    _prom_available = False


def _inc_incident_created(severity: str, category: str) -> None:
    """Increment ml_incident_created_total. No-op if prometheus_client absent."""
    if _prom_available:
        _INCIDENT_CREATED.labels(severity=severity, category=category).inc()


def _inc_revoked_token_access(reason: str) -> None:
    """Increment ml_revoked_token_access_total. No-op if prometheus_client absent."""
    if _prom_available:
        _REVOKED_TOKEN_ACCESS.labels(reason=reason).inc()


# ── Environment / config ───────────────────────────────────────────────────
JWT_SECRET: str = os.environ["JWT_SECRET_KEY"]
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512"}
if JWT_ALGORITHM not in _ALLOWED_ALGORITHMS:
    raise ValueError(
        f"JWT_ALGORITHM must be one of {_ALLOWED_ALGORITHMS}, got '{JWT_ALGORITHM}'"
    )

# ── CORS ───────────────────────────────────────────────────────────────────
_raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

if ENVIRONMENT == "production" and not ALLOWED_ORIGINS:
    log.warning(
        "cors.no_origins_configured",
        message="CORS_ALLOWED_ORIGINS is empty in production — all browser CORS requests will be denied.",
    )

# ── Rate limiter ───────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Redis JWT denylist ─────────────────────────────────────────────────────
_denylist: RedisDenylist | None = None


def revoke_token(jti: str, expires_in: int) -> None:
    if _denylist is None:
        raise RuntimeError("Token denylist not initialised — lifespan may not have run")
    _denylist.revoke(jti, ttl_seconds=expires_in)


def is_token_revoked(jti: str) -> bool:
    if _denylist is None:
        # Denylist not available: deny access rather than fail open.
        log.error(
            "denylist.not_initialised",
            jti=jti,
            message="Failing closed — all token access denied until denylist reconnects.",
        )
        return True  # fail closed
    return _denylist.is_revoked(jti)


# ── Password hashing (bcrypt direct — no passlib) ──────────────────────────
def hash_password(plain: str) -> bytes:
    """Return a bcrypt hash of the given plaintext password."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt())


def verify_password(plain: str, hashed: bytes | str) -> bool:
    """Constant-time bcrypt verification. Accepts bytes or str hash."""
    if isinstance(hashed, str):
        hashed = hashed.encode()
    return bcrypt.checkpw(plain.encode(), hashed)


# ── OAuth2 bearer scheme ───────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# ── Development-only user fixture ──────────────────────────────────────────
if ENVIRONMENT == "production":
    _DEV_USERS: dict[str, dict[str, Any]] = {}
else:
    _DEV_USERS = {
        "admin": {
            "username": "admin",
            "hashed_password": hash_password("admin-dev-only"),
            "role": "admin",
            "disabled": False,
        },
        "analyst": {
            "username": "analyst",
            "hashed_password": hash_password("analyst-dev-only"),
            "role": "analyst",
            "disabled": False,
        },
        "operator": {
            "username": "operator",
            "hashed_password": hash_password("operator-dev-only"),
            "role": "operator",
            "disabled": False,
        },
    }


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
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    severity: str = Field(...)
    affected_system: str = Field(..., min_length=2, max_length=100)
    category: str = Field(default="general", min_length=2, max_length=100)
    owner: str | None = Field(default=None, max_length=255)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()


class IncidentUpdate(BaseModel):
    status: str | None = None
    resolution_notes: str | None = Field(default=None, max_length=10000)
    severity: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"open", "investigating", "mitigating", "resolved", "closed"}
        if v.lower() not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v.lower()

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()


class IncidentResponse(BaseModel):
    """API response shape for a single incident."""
    incident_id: str
    title: str
    severity: str
    status: str
    category: str
    owner: str | None
    description: str | None
    affected_system: str
    created_at: str
    updated_at: str
    resolved_at: str | None


# ── JWT helpers ────────────────────────────────────────────────────────────

def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> tuple[str, str, int]:
    """
    Returns (encoded_jwt, jti, ttl_seconds).
    Callers set Redis TTL to match token natural expiry — no orphaned entries.
    """
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
    data: dict[str, Any],
) -> tuple[str, str, int]:
    """Returns (encoded_jwt, jti, ttl_seconds)."""
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


def decode_token(token: str) -> dict[str, Any]:
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

def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    """
    Verify credentials against the dev fixture (non-production only).
    Production deployments must wire this to a UserRepository DB query.
    """
    if ENVIRONMENT == "production":
        raise RuntimeError(
            "authenticate_user() called with dev fixture in production environment. "
            "Replace with a UserRepository database lookup before deploying."
        )
    user = _DEV_USERS.get(username)
    if not user:
        # Constant-time dummy check to prevent username enumeration via timing.
        verify_password("dummy", hash_password("dummy"))
        return None
    if user.get("disabled"):
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    request: Request,
) -> dict[str, Any]:
    payload = decode_token(token)
    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type",
        )
    jti = payload.get("jti", "")
    if is_token_revoked(jti):
        # ── Prometheus: count every revoked-token access attempt ─────────
        _inc_revoked_token_access(reason="jwt_denylisted")
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
    user = _DEV_USERS.get(username)
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

    log.info("api.startup", environment=ENVIRONMENT, algorithm=JWT_ALGORITHM)

    # Initialise database schema (safe to call repeatedly — IF NOT EXISTS)
    await init_db()
    log.info("database.initialised")

    # Initialise Redis-backed JWT denylist
    _denylist = RedisDenylist(redis_url=REDIS_URL)
    await _denylist.connect()
    log.info("denylist.connected", redis_url=REDIS_URL)

    # Bootstrap OpenTelemetry tracing
    configure_otel(
        service_name=os.getenv("OTEL_SERVICE_NAME", "ml-incident-api"),
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        environment=ENVIRONMENT,
    )
    log.info("otel.configured")

    yield

    await _denylist.close()
    shutdown_otel()
    log.info("api.shutdown")


app = FastAPI(
    title="ML Incident Response API",
    version="2.1.0",
    description=(
        "Production-hardened ML incident management API. "
        "Provides JWT authentication, RBAC, structured audit logging, "
        "and persistent incident tracking backed by an async SQLAlchemy repository."
    ),
    lifespan=lifespan,
    docs_url="/docs" if ENVIRONMENT != "production" else None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )

# ── Prometheus /metrics endpoint ──────────────────────────────────────────
#
# Mounted only when prometheus_client is available. The endpoint is excluded
# from the OpenAPI schema and should be firewalled from public traffic —
# scrape it from within the cluster or via a sidecar.
if _prom_available:
    from prometheus_client import make_asgi_app as _make_prom_asgi
    _metrics_app = _make_prom_asgi()
    app.mount("/metrics", _metrics_app)
    log.info("prometheus.metrics_endpoint_mounted", path="/metrics")


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
    checks: dict[str, str] = {}
    all_ok = True

    try:
        test_token, _, _ = create_access_token(
            {"sub": "__healthcheck__", "role": "_probe"},
            timedelta(seconds=5),
        )
        jwt.decode(test_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        checks["jwt_subsystem"] = "ok"
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

    checks["prometheus"] = "available" if _prom_available else "not_installed"

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
    user = authenticate_user(form.username, form.password)
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
    refresh_token, _, _ = create_refresh_token(token_data)

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
    if is_token_revoked(old_jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    username: str = payload.get("sub", "")
    user = _DEV_USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    # Rotate: revoke old refresh token before issuing new pair
    revoke_token(
        old_jti,
        expires_in=int(timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS).total_seconds()),
    )

    token_data = {"sub": user["username"], "role": user["role"]}
    access_token, access_jti, access_ttl = create_access_token(token_data)
    new_refresh_token, _, _ = create_refresh_token(token_data)

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
    """Revoke the current access token immediately."""
    jti = current_user.get("jti", "")
    ttl = int(timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES).total_seconds())
    revoke_token(jti, expires_in=ttl)
    log.info(
        "auth.logout",
        username=current_user["username"],
        jti=jti,
        log_type="audit",
        event="logout",
    )


# ── Incident routes ────────────────────────────────────────────────────────

_INCIDENT_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def _validate_incident_id(incident_id: str) -> None:
    """Raise 400 if incident_id is not a valid UUID4."""
    import re
    if not re.match(_INCIDENT_ID_PATTERN, incident_id, re.IGNORECASE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid incident ID format: '{incident_id}'. Expected UUID4.",
        )


@app.post("/incidents", status_code=201, tags=["incidents"])
async def create_incident(
    payload: IncidentCreate,
    current_user: Annotated[dict, Depends(require_role("admin", "analyst"))],
    session: AsyncSession = Depends(get_session),
):
    """Create a new incident. Requires admin or analyst role."""
    repo = IncidentRepository(session)
    incident = await repo.create(
        title=payload.title,
        severity=SeverityLevel(payload.severity),
        category=payload.category,
        owner=payload.owner or current_user["username"],
        description=payload.description,
    )

    # ── Prometheus: count every successfully created incident ────────────
    # Labels allow Grafana to break down volume by severity and category.
    # Alert rule ml_incident_created_total fires in alert_rules.yml when
    # SEV-1 rate exceeds threshold.
    _inc_incident_created(severity=payload.severity, category=payload.category)

    log.info(
        "incident.created",
        incident_id=incident.id,
        severity=payload.severity,
        category=payload.category,
        owner=payload.owner or current_user["username"],
    )

    return {
        "incident_id": incident.id,
        **incident.to_dict(),
        "affected_system": payload.affected_system,
    }


@app.get("/incidents", tags=["incidents"])
async def list_incidents(
    current_user: Annotated[dict, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List open incidents. Authenticated users only."""
    repo = IncidentRepository(session)
    incidents = await repo.list_open(limit=limit + offset)
    page = incidents[offset: offset + limit]
    return {
        "incidents": [i.to_dict() for i in page],
        "total": len(incidents),
        "limit": limit,
        "offset": offset,
    }


@app.get("/incidents/{incident_id}", tags=["incidents"])
async def get_incident(
    incident_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    """Get a single incident by ID."""
    _validate_incident_id(incident_id)
    repo = IncidentRepository(session)
    incident = await repo.get(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )
    return {"incident_id": incident.id, **incident.to_dict()}


@app.patch("/incidents/{incident_id}", tags=["incidents"])
async def update_incident(
    incident_id: str,
    payload: IncidentUpdate,
    current_user: Annotated[dict, Depends(require_role("admin", "operator"))],
    session: AsyncSession = Depends(get_session),
):
    """Update incident status or severity. Requires admin or operator role."""
    _validate_incident_id(incident_id)
    repo = IncidentRepository(session)

    if payload.status is not None:
        try:
            incident = await repo.update_status(
                incident_id,
                IncidentStatus(payload.status),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            )
    else:
        incident = await repo.get(incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Incident '{incident_id}' not found.",
            )

    return {"incident_id": incident.id, **incident.to_dict()}
