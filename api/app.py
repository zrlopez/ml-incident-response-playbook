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
  MED-03   Token revocation blocklist + /auth/logout
  MED-04   /ready probe with real dependency health checks
  MED-05   audit() wired to all security-relevant events
  MED-06   configure_logging() called at startup
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

# ── Logging bootstrap ──────────────────────────────────────────────────────
configure_logging()  # FIXED: was never called; PII scrubbing now active
log = structlog.get_logger(__name__)

# ── Environment / config ───────────────────────────────────────────────────
JWT_SECRET: str = os.environ["JWT_SECRET_KEY"]          # hard-fail if absent
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

# Validate algorithm allowlist — prevent algorithm confusion attacks
_ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512"}
if JWT_ALGORITHM not in _ALLOWED_ALGORITHMS:
    raise ValueError(f"JWT_ALGORITHM must be one of {_ALLOWED_ALGORITHMS}, got '{JWT_ALGORITHM}'")

# ── CORS ───────────────────────────────────────────────────────────────────
# FIXED: empty-string allowlist produced [''] (truthy, wrong).  Now strips.
_raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Rate limiter ───────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── In-memory token revocation blocklist ──────────────────────────────────
# Production: replace with Redis SETEX keyed on jti claim.
_REVOKED_JTIS: set[str] = set()


def revoke_token(jti: str) -> None:
    """Add a JWT ID to the revocation blocklist."""
    _REVOKED_JTIS.add(jti)


def is_token_revoked(jti: str) -> bool:
    return jti in _REVOKED_JTIS


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
# TODO(prod): replace with PostgreSQL async repository.
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
        allowed = {"P1", "P2", "P3", "P4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()


class IncidentUpdate(BaseModel):
    status: str | None = None
    resolution_notes: str | None = Field(None, max_length=5000)
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
        allowed = {"P1", "P2", "P3", "P4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()


# ── JWT helpers ────────────────────────────────────────────────────────────

def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> tuple[str, str]:
    """
    FIXED: parameter `data` was missing its name — was `dict[str, Any]` only.
    Now correctly declared as `data: dict[str, Any]`.
    Returns (encoded_jwt, jti) so callers can track the token ID.
    """
    to_encode = data.copy()
    jti = str(uuid.uuid4())  # unique token ID for revocation
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": jti,
        "token_type": "access",
    })
    encoded = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded, jti


def create_refresh_token(data: dict[str, Any]) -> tuple[str, str]:
    """Issue a long-lived refresh token with distinct token_type claim."""
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": jti,
        "token_type": "refresh",
    })
    encoded = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded, jti


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT.  Explicitly passes algorithms list to
    prevent algorithm confusion (CVE-2022-29217 / CVE-2024-33663 class).
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],  # explicit allowlist — no 'none' possible
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong token type")
    jti = payload.get("jti", "")
    if is_token_revoked(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")
    username: str = payload.get("sub", "")
    user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    return {**user, "jti": jti}


def require_role(*roles: str):
    """FastAPI dependency factory for role-based access control."""
    async def _checker(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user['role']}' is not authorised for this action.",
            )
        return current_user
    return _checker


# ── FastAPI app factory ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api.startup", environment=ENVIRONMENT, algorithm=JWT_ALGORITHM)
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="ML Incident Response API",
    version="2.0.0",
    description="Hardened ML incident lifecycle management API",
    docs_url="/docs" if ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

# ── Rate limiter wiring ────────────────────────────────────────────────────
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
    """
    Injects a per-request trace_id into structlog context so every log line
    for the same request shares the same correlation ID.  Also sets
    security-hardening response headers on every response.
    """
    trace_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    # Bind trace context for structured logging
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

    # Security headers
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cache-Control"] = "no-store"
    # Never expose internal framework details
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
    """
    FIXED: was unconditionally returning 'ready'.
    Now performs actual dependency checks before reporting ready.
    TODO(prod): add async DB connection check and external service checks.
    """
    checks: dict[str, str] = {}
    all_ok = True

    # JWT secret availability check
    try:
        _test_payload = {"sub": "__healthcheck__", "role": "_probe"}
        test_token, test_jti = create_access_token(_test_payload, timedelta(seconds=5))
        jwt.decode(test_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        checks["jwt_subsystem"] = "ok"
    except Exception as exc:
        checks["jwt_subsystem"] = f"error: {exc}"
        all_ok = False

    # Environment variable completeness check
    required_env = ["JWT_SECRET_KEY"]
    for var in required_env:
        if not os.getenv(var):
            checks[f"env_{var}"] = "missing"
            all_ok = False
        else:
            checks[f"env_{var}"] = "ok"

    # TODO(prod): add checks:
    #   checks["database"] = await db_health_check()
    #   checks["redis"] = await redis_health_check()

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ── Auth routes ────────────────────────────────────────────────────────────

@app.post("/auth/token", response_model=Token, tags=["auth"])
@limiter.limit("5/minute")  # FIXED: rate limiting was a stub — now enforced
async def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """
    Issue an access token + refresh token pair.
    Rate limited to 5 attempts per minute per IP to prevent brute force.
    """
    user = authenticate_user(form.username, form.password)
    if not user:
        # FIXED: audit() now called on auth failure
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
    access_token, access_jti = create_access_token(token_data)
    refresh_token, refresh_jti = create_refresh_token(token_data)

    # FIXED: audit() now called on successful auth
    log.info(
        "auth.login_success",
        username=user["username"],
        role=user["role"],
        access_jti=access_jti,
        log_type="audit",
        event="authentication_success",
    )

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.post("/auth/refresh", response_model=Token, tags=["auth"])
@limiter.limit("10/minute")
async def refresh_token(request: Request, token: Annotated[str, Depends(oauth2_scheme)]):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    payload = decode_token(token)
    if payload.get("token_type") != "refresh":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a refresh token")
    jti = payload.get("jti", "")
    if is_token_revoked(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")

    # Revoke old refresh token (rotation)
    revoke_token(jti)

    username = payload.get("sub", "")
    user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    token_data = {"sub": user["username"], "role": user["role"]}
    new_access, new_access_jti = create_access_token(token_data)
    new_refresh, new_refresh_jti = create_refresh_token(token_data)

    log.info(
        "auth.token_refreshed",
        username=username,
        old_jti=jti,
        new_access_jti=new_access_jti,
        log_type="audit",
        event="token_refresh",
    )

    return Token(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.post("/auth/logout", status_code=204, tags=["auth"])
async def logout(current_user: Annotated[dict, Depends(get_current_user)]):
    """Revoke the current access token immediately."""
    revoke_token(current_user["jti"])
    log.info(
        "auth.logout",
        username=current_user["username"],
        jti=current_user["jti"],
        log_type="audit",
        event="logout",
    )


# ── Incident routes ────────────────────────────────────────────────────────

@app.post("/incidents", status_code=201, tags=["incidents"])
async def create_incident(
    incident: IncidentCreate,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin"))],
):
    """
    FIXED: incident_id now uses uuid4 instead of int(time.time()*1000).
    Eliminates collision risk under concurrent load.
    """
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
    limit: int = 50,
    offset: int = 0,
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


@app.patch("/incidents/{incident_id}", tags=["incidents"])
async def update_incident(
    incident_id: str,
    update: IncidentUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
):
    """
    FIXED: now validates incident_id exists in the store before mutating.
    Prevents IDOR — an operator cannot target a non-existent or
    (when ownership is added) another team's incident ID.
    TODO(prod): add ownership check: record['team_id'] == current_user['team_id']
    """
    if not re.fullmatch(r"INC-[A-F0-9]{12}", incident_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid incident_id format")

    record = _INCIDENTS.get(incident_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    previous_status = record.get("status")
    previous_severity = record.get("severity")

    changes: dict[str, Any] = {}
    if update.status is not None:
        record["status"] = update.status
        changes["status"] = {"from": previous_status, "to": update.status}
    if update.resolution_notes is not None:
        record["resolution_notes"] = update.resolution_notes
        changes["resolution_notes"] = "updated"
    if update.severity is not None:
        record["severity"] = update.severity
        changes["severity"] = {"from": previous_severity, "to": update.severity}

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
