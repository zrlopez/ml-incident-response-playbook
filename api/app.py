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
from typing import Annotated, Any

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
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Rate limiter ───────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Redis JWT denylist (distributed, TTL-backed) ───────────────────────────
# Module-level singleton; initialised in lifespan, used in is_token_revoked().
_denylist: RedisDenylist | None = None


def revoke_token(jti: str, expires_in: int) -> None:
    """
    Add a JWT ID to the denylist with an explicit TTL (seconds).
    Falls back to raising RuntimeError if the denylist is not initialised
    so logout failures are loud rather than silent.
    """
    if _denylist is None:
        raise RuntimeError("Token denylist not initialised — lifespan may not have run")
    _denylist.revoke(jti, ttl_seconds=expires_in)


def is_token_revoked(jti: str) -> bool:
    if _denylist is None:
        log.error("denylist.not_initialised", jti=jti)
        return False  # fail-open in degenerate state; alert fires via Redis monitor
    return _denylist.is_revoked(jti)


# ── Password hashing ───────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── OAuth2 bearer scheme ───────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# ── Stub user store ───────────────────────────────────────────────────────
# TODO(prod): replace with async DB query via SQLAlchemy / asyncpg.
_USERS: dict[str, dict[str, Any]] = {
    "admin": {
        "username": "admin",
        "hashed_password": pwd_context.hash("admin-dev-only"),
        "role": "admin",
        "disabled": False,
    },
    "analyst": {
        "username": "analyst",
        "hashed_password": pwd_context.hash("analyst-dev-only"),
        "role": "analyst",
        "disabled": False,
    },
    "operator": {
        "username": "operator",
        "hashed_password": pwd_context.hash("operator-dev-only"),
        "role": "operator",
        "disabled": False,
    },
}

# ── Stub incident store ───────────────────────────────────────────────────
# TODO(prod): replace with IncidentRepository from src/incident_tracker.py
_INCIDENTS: dict[str, dict[str, Any]] = {}


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


# ── JWT helpers ────────────────────────────────────────────────────────────

def create_access_token(
     dict[str, Any],
    expires_delta: timedelta | None = None,
) -> tuple[str, str, int]:
    """
    Returns (encoded_jwt, jti, ttl_seconds) so callers can set Redis TTL
    to match the token's natural expiry — no orphaned denylist entries.
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
     dict[str, Any],
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
    user = _USERS.get(username)
    if not user:
        pwd_context.dummy_verify()  # constant-time: prevent username enumeration
        return None
    if user.get("disabled"):
        return None
    if not pwd_context.verify(password, user["hashed_password"]):
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

    # Initialise Redis-backed JWT denylist (R-03)
    _denylist = RedisDenylist(redis_url=REDIS_URL)
    await _denylist.connect()
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
    checks: dict[str, str] = {}
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
    if is_token_revoked(old_jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    username: str = payload.get("sub", "")
    user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    # Rotate: revoke the old refresh token before issuing new pair
    revoke_token(old_jti, expires_in=int(timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS).total_seconds()))

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
    revoke_token(jti, expires_in=ttl)
    log.info(
        "auth.logout",
        username=current_user["username"],
        jti=jti,
        log_type="audit",
        event="logout",
    )


# ── Incident routes ────────────────────────────────────────────────────────

@app.post("/incidents", status_code=201, tags=["incidents"])
async def create_incident(
    incident: IncidentCreate,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin"))],
):
    """Create a new incident record. Requires analyst or admin role."""
    incident_id = f"INC-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "incident_id": incident_id,
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "affected_system": incident.affected_system,
        "status": "open",
        "created_by": current_user["username"],
        "created_at": now,
        "updated_at": now,
    }
    _INCIDENTS[incident_id] = record

    log.info(
        "incident.created",
        incident_id=incident_id,
        severity=incident.severity,
        affected_system=incident.affected_system,
        created_by=current_user["username"],
        log_type="audit",
        event="incident_created",
    )
    return record


@app.get("/incidents", tags=["incidents"])
async def list_incidents(
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    status_filter: str | None = None,
    severity_filter: str | None = None,
    limit: int = Field(default=50, ge=1, le=200),
    offset: int = Field(default=0, ge=0),
):
    """List incidents with optional status/severity filtering and pagination."""
    incidents = list(_INCIDENTS.values())

    if status_filter:
        incidents = [i for i in incidents if i["status"] == status_filter]
    if severity_filter:
        incidents = [i for i in incidents if i["severity"] == severity_filter.upper()]

    total = len(incidents)
    page = incidents[offset: offset + limit]

    log.info(
        "incident.list",
        total=total,
        returned=len(page),
        requested_by=current_user["username"],
    )
    return {"total": total, "offset": offset, "limit": limit, "incidents": page}


@app.get("/incidents/{incident_id}", tags=["incidents"])
async def get_incident(
    incident_id: str,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
):
    """Retrieve a single incident by ID."""
    if not re.fullmatch(r"INC-[A-F0-9]{12}", incident_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid incident_id format",
        )
    record = _INCIDENTS.get(incident_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )
    return record


@app.patch("/incidents/{incident_id}", tags=["incidents"])
async def update_incident(
    incident_id: str,
    update: IncidentUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
):
    """Update incident status/severity/resolution notes. Requires operator or admin."""
    if not re.fullmatch(r"INC-[A-F0-9]{12}", incident_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid incident_id format",
        )
    record = _INCIDENTS.get(incident_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    changes: dict[str, Any] = {}
    if update.status is not None:
        changes["status"] = {"from": record.get("status"), "to": update.status}
        record["status"] = update.status
    if update.resolution_notes is not None:
        record["resolution_notes"] = update.resolution_notes
        changes["resolution_notes"] = "updated"
    if update.severity is not None:
        changes["severity"] = {"from": record.get("severity"), "to": update.severity}
        record["severity"] = update.severity

    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    record["updated_by"] = current_user["username"]

    log.info(
        "incident.updated",
        incident_id=incident_id,
        changes=changes,
        updated_by=current_user["username"],
        log_type="audit",
        event="incident_updated",
    )
    return record
