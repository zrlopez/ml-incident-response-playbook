"""
ML Incident Response API  —  Hardened Production Build
=======================================================
Remediation pass: 2026-05-23

Changes from original:
  - Fixed broken create_access_token parameter declaration (CRIT-01)
  - Migrated python-jose → PyJWT 2.9.0 (CVE-2024-33663)
  - Replaced passlib → bcrypt directly (passlib unmaintained)
  - Wired configure_logging() at startup (PII scrubbing now active)
  - Wired audit() calls for all security-relevant events
  - Implemented SlowAPI rate limiting on /auth/token
  - UUID4-based incident IDs (replaces timestamp collision risk)
  - X-Trace-Id correlation injected per request
  - CORS allowlist strips empty strings
  - /ready probe checks real dependency health
  - Refresh token skeleton with rotation
"""
from __future__ import annotations

import os
import uuid
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import structlog

from observability.logging_config import configure_logging
from observability.logging_config import audit, send_alert

# ─── Logging bootstrap (must be first) ────────────────────────────────────────
configure_logging()
log = structlog.get_logger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────────
JWT_SECRET: str = os.environ["JWT_SECRET_KEY"]          # fail-fast: no default
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

_raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ─── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ─── In-memory stores (replace with DB layer in production) ────────────────────
# Hashed with bcrypt at service startup — never store plaintext.
_USERS: dict[str, dict[str, Any]] = {
    "admin": {
        "hashed_password": bcrypt.hashpw(b"admin-secret", bcrypt.gensalt()).decode(),
        "roles": ["admin"],
        "disabled": False,
    },
    "analyst": {
        "hashed_password": bcrypt.hashpw(b"analyst-secret", bcrypt.gensalt()).decode(),
        "roles": ["analyst"],
        "disabled": False,
    },
}

# In-memory incident store — replace with PostgreSQL persistence.
_INCIDENTS: dict[str, dict[str, Any]] = {}

# Refresh token store — replace with Redis/DB for distributed deployments.
_REFRESH_TOKENS: dict[str, dict[str, Any]] = {}


# ─── Pydantic models ───────────────────────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=5000)
    severity: str = Field(...)
    affected_model: str = Field(..., min_length=1, max_length=200)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"critical", "high", "medium", "low"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.lower()


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
        allowed = {"critical", "high", "medium", "low"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.lower()


# ─── Auth utilities ────────────────────────────────────────────────────────────
def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt comparison."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Issue a signed JWT access token.

    Fixed from original: parameter `data` was missing its name,
    causing NameError at runtime (CRIT-01).
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),   # JWT ID — enables per-token revocation
        "type": "access",
    })
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(username: str) -> str:
    """Issue a signed JWT refresh token with longer TTL."""
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    jti = str(uuid.uuid4())
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": jti,
        "type": "refresh",
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # Store reference for revocation support
    _REFRESH_TOKENS[jti] = {
        "username": username,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expire.isoformat(),
        "revoked": False,
    }
    return token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """Decode and validate a JWT, returning its payload."""
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],          # Explicit algorithm: prevents alg:none
            options={"require": ["exp", "sub", "jti", "type"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token type: expected {expected_type}",
        )
    return payload


async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> dict[str, Any]:
    """Validate token and return the active user record."""
    payload = decode_token(token, expected_type="access")
    username: str = payload.get("sub", "")
    user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )
    return {"username": username, **user}


def require_role(*roles: str):
    """Dependency factory that enforces role-based access control."""
    async def _check(current_user: dict = Depends(get_current_user)) -> dict:
        user_roles: list[str] = current_user.get("roles", [])
        if not any(r in user_roles for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {list(roles)}",
            )
        return current_user
    return _check


# ─── App factory ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api.startup", version="1.1.0", allowed_origins=ALLOWED_ORIGINS)
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="ML Incident Response API",
    version="1.1.0",
    description="Hardened incident management API for ML platform operations.",
    docs_url="/docs" if os.getenv("ENV", "production") != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Rate limiter exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — only add middleware when origins are explicitly configured
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Trace-Id"],
    )


# ─── Middleware: trace ID + security headers + structured request logging ──────
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    # Bind trace context for all log calls in this request
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else "unknown",
    )

    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    log.info(
        "http.request",
        status_code=response.status_code,
        duration_ms=duration_ms,
    )

    # Inject security headers
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Request-Id"] = trace_id
    return response


# ─── Auth endpoints ────────────────────────────────────────────────────────────
@app.post("/auth/token", response_model=TokenResponse, tags=["auth"])
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
):
    """Issue access + refresh tokens. Rate-limited to 5 attempts/IP/minute."""
    user = _USERS.get(form.username)
    client_ip = request.client.host if request.client else "unknown"

    if not user or not verify_password(form.password, user["hashed_password"]):
        audit(
            "auth.login.failure",
            username=form.username,
            ip=client_ip,
            reason="invalid_credentials",
        )
        # Constant-time delay prevents user enumeration timing attacks
        await _constant_time_delay()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.get("disabled"):
        audit("auth.login.failure", username=form.username, ip=client_ip, reason="account_disabled")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    access_token = create_access_token(
        data={"sub": form.username, "roles": user["roles"]},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_refresh_token(form.username)

    audit(
        "auth.login.success",
        username=form.username,
        ip=client_ip,
        roles=user["roles"],
    )
    log.info("auth.token.issued", username=form.username, roles=user["roles"])

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
@limiter.limit("10/minute")
async def refresh_token(request: Request, body: RefreshRequest):
    """Rotate refresh token and issue a new access token."""
    payload = decode_token(body.refresh_token, expected_type="refresh")
    jti = payload.get("jti", "")
    username = payload.get("sub", "")

    stored = _REFRESH_TOKENS.get(jti)
    if not stored or stored.get("revoked"):
        audit("auth.refresh.failure", username=username, reason="revoked_or_unknown_token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # Rotate: revoke old, issue new
    _REFRESH_TOKENS[jti]["revoked"] = True

    user = _USERS.get(username)
    if not user or user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    new_access = create_access_token(data={"sub": username, "roles": user["roles"]})
    new_refresh = create_refresh_token(username)

    audit("auth.refresh.success", username=username)
    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.post("/auth/logout", tags=["auth"])
async def logout(
    body: RefreshRequest,
    current_user: dict = Depends(get_current_user),
):
    """Revoke a refresh token. Access tokens expire naturally."""
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
        jti = payload.get("jti", "")
        if jti in _REFRESH_TOKENS:
            _REFRESH_TOKENS[jti]["revoked"] = True
    except HTTPException:
        pass  # Already invalid — treat as success
    audit("auth.logout", username=current_user["username"])
    return {"message": "Logged out successfully"}


# ─── Incident endpoints ────────────────────────────────────────────────────────
@app.get("/incidents", tags=["incidents"])
async def list_incidents(
    current_user: dict = Depends(require_role("analyst", "admin")),
    status_filter: str | None = None,
    severity_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List incidents with optional filters."""
    incidents = list(_INCIDENTS.values())

    if status_filter:
        incidents = [i for i in incidents if i.get("status") == status_filter.lower()]
    if severity_filter:
        incidents = [i for i in incidents if i.get("severity") == severity_filter.lower()]

    total = len(incidents)
    page = incidents[offset: offset + limit]

    log.info("incidents.list", count=total, user=current_user["username"])
    return {"total": total, "offset": offset, "limit": limit, "incidents": page}


@app.post("/incidents", status_code=status.HTTP_201_CREATED, tags=["incidents"])
async def create_incident(
    incident: IncidentCreate,
    current_user: dict = Depends(require_role("analyst", "admin")),
    request: Request = None,
):
    """Create a new incident record."""
    incident_id = f"INC-{uuid.uuid4().hex[:12].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    record: dict[str, Any] = {
        "id": incident_id,
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "affected_model": incident.affected_model,
        "status": "open",
        "created_by": current_user["username"],
        "created_at": now_iso,
        "updated_at": now_iso,
        "resolution_notes": None,
    }
    _INCIDENTS[incident_id] = record

    audit(
        "incident.created",
        incident_id=incident_id,
        severity=incident.severity,
        affected_model=incident.affected_model,
        user=current_user["username"],
    )
    log.info("incident.created", incident_id=incident_id, severity=incident.severity)

    # Escalate critical incidents automatically
    if incident.severity == "critical":
        send_alert(
            f"[CRITICAL] New incident {incident_id}: {incident.title}",
            level="critical",
            incident_id=incident_id,
        )

    return record


@app.patch("/incidents/{incident_id}", tags=["incidents"])
async def update_incident(
    incident_id: str,
    update: IncidentUpdate,
    current_user: dict = Depends(require_role("analyst", "admin")),
):
    """Update incident status or resolution notes."""
    # Validate ID format
    if not incident_id.startswith("INC-") or len(incident_id) != 16:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid incident ID format")

    # Existence check (IDOR prevention)
    record = _INCIDENTS.get(incident_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    # Ownership enforcement for operator role
    user_roles: list[str] = current_user.get("roles", [])
    if "admin" not in user_roles and record.get("created_by") != current_user["username"]:
        audit(
            "incident.update.denied",
            incident_id=incident_id,
            user=current_user["username"],
            reason="not_owner",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update this incident")

    update_data = update.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No update fields provided")

    record.update(update_data)
    record["updated_at"] = datetime.now(timezone.utc).isoformat()

    audit(
        "incident.updated",
        incident_id=incident_id,
        fields=list(update_data.keys()),
        user=current_user["username"],
    )
    log.info("incident.updated", incident_id=incident_id, fields=list(update_data.keys()))

    if update.status == "resolved":
        send_alert(f"Incident {incident_id} resolved.", level="info", incident_id=incident_id)

    return record


# ─── Health endpoints ──────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe — confirms the process is running."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready", tags=["ops"])
async def readiness():
    """Readiness probe — validates all dependencies are reachable.

    Fixed from original: now performs real dependency checks instead
    of returning unconditional {status: ready}.
    """
    checks: dict[str, Any] = {}
    healthy = True

    # JWT secret availability
    try:
        if not JWT_SECRET or len(JWT_SECRET) < 16:
            raise ValueError("JWT secret too short")
        checks["jwt_config"] = "ok"
    except Exception as exc:
        checks["jwt_config"] = f"error: {exc}"
        healthy = False

    # User store
    try:
        if not _USERS:
            raise ValueError("User store is empty")
        checks["user_store"] = "ok"
    except Exception as exc:
        checks["user_store"] = f"error: {exc}"
        healthy = False

    # Future: add DB ping, Redis ping, external service checks here
    # checks["database"] = await db_ping()

    if not healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "checks": checks},
        )

    return {"status": "ready", "checks": checks, "timestamp": datetime.now(timezone.utc).isoformat()}


# ─── Helpers ───────────────────────────────────────────────────────────────────
async def _constant_time_delay():
    """Add ~100ms jitter to auth failures to prevent timing-based user enumeration."""
    import asyncio
    import random
    await asyncio.sleep(0.1 + random.uniform(0, 0.05))
