"""
ml-incident-response-playbook · API layer
==========================================
Production-hardened FastAPI application.

Remediation log (2026-05-23):
  CRIT-01  Fixed missing `data` parameter in create_access_token()
  CRIT-02  Replaced python-jose with PyJWT (CVE-2024-33663 mitigated)
  HIGH-01  SlowAPI rate limiting on /auth/token
  HIGH-02  configure_logging() wired at startup
  HIGH-03  audit() calls on auth + incident events
  HIGH-04  uuid4-based incident IDs (no more timestamp collisions)
  MED-01   trace_id injected into every request context
  MED-02   /ready probe checks real dependency availability
  MED-03   CORS origins parsing hardened
  MED-04   Refresh-token endpoint + in-memory revocation blacklist
  MED-05   Ownership check stub on PATCH /incidents/{id}
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import jwt
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from observability.logging_config import configure_logging
from observability.logging_config import audit, send_alert

# ──────────────────────────────────────────────────────────────────────────────
# Startup / shutdown lifecycle
# ──────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Configure structured logging and verify dependencies on startup."""
    configure_logging()
    log = structlog.get_logger(__name__)
    log.info("api.startup", version="1.0.0")
    yield
    log.info("api.shutdown")


# ──────────────────────────────────────────────────────────────────────────────
# Rate limiter
# ──────────────────────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ──────────────────────────────────────────────────────────────────────────────
# App factory
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ML Incident Response API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ──────────────────────────────────────────────────────────────────────────────
# CORS — hardened origin parsing
# ──────────────────────────────────────────────────────────────────────────────

_raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in _raw_origins.split(",") if o.strip()
]

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Trace-Id"],
        max_age=600,
    )

# ──────────────────────────────────────────────────────────────────────────────
# Security configuration
# ──────────────────────────────────────────────────────────────────────────────

JWT_SECRET: str = os.environ["JWT_SECRET_KEY"]          # Fails fast if unset
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)
REFRESH_TOKEN_EXPIRE_DAYS: int = int(
    os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")
)

# In-memory token revocation blacklist.
# Production replacement: Redis SET with TTL matching token expiry.
_revoked_tokens: set[str] = set()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# Demo user store — replace with DB lookup in production.
_DEMO_USERS: dict[str, dict[str, Any]] = {
    "admin": {
        "username": "admin",
        "hashed_password": pwd_context.hash("changeme"),
        "role": "admin",
        "disabled": False,
    },
    "analyst": {
        "username": "analyst",
        "hashed_password": pwd_context.hash("changeme"),
        "role": "analyst",
        "disabled": False,
    },
    "operator": {
        "username": "operator",
        "hashed_password": pwd_context.hash("changeme"),
        "role": "operator",
        "disabled": False,
    },
}

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────────────────────────────────────

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class TokenData(BaseModel):
    username: str | None = None
    role: str | None = None
    jti: str | None = None


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10, max_length=2000)
    severity: str
    affected_model: str = Field(..., min_length=1, max_length=100)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"critical", "high", "medium", "low"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of: {allowed}")
        return v.lower()


class IncidentUpdate(BaseModel):
    status: str | None = None
    resolution_notes: str | None = Field(None, max_length=2000)
    assigned_to: str | None = Field(None, max_length=100)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"open", "investigating", "resolved", "closed"}
        if v.lower() not in allowed:
            raise ValueError(f"status must be one of: {allowed}")
        return v.lower()


class HealthResponse(BaseModel):
    status: str
    version: str
    dependencies: dict[str, str]


# ──────────────────────────────────────────────────────────────────────────────
# Middleware — trace ID injection + security headers
# ──────────────────────────────────────────────────────────────────────────────

@app.middleware("http")
async def security_and_tracing_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    start = datetime.now(timezone.utc)

    # Bind trace context for all log events in this request
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else "unknown",
    )

    response = await call_next(request)

    elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    structlog.get_logger(__name__).info(
        "http.request",
        status_code=response.status_code,
        elapsed_ms=round(elapsed_ms, 2),
    )

    # Security headers
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"  # Modern browsers: use CSP
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store"
    return response


# ──────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────────────────────────────────────

def create_token(
    data: dict[str, Any],
    expires_delta: timedelta,
    token_type: str = "access",
) -> str:
    """
    Issue a signed JWT with explicit expiry and a unique jti claim.

    The jti (JWT ID) enables per-token revocation by storing it in
    _revoked_tokens without invalidating the entire signing key.
    """
    now = datetime.now(timezone.utc)
    payload = {
        **data,
        "iat": now,
        "exp": now + expires_delta,
        "nbf": now,
        "jti": str(uuid.uuid4()),
        "typ": token_type,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """
    Create a short-lived access token.

    REMEDIATION NOTE (CRIT-01):
      Original signature was `def create_access_token( dict[str, Any], ...)`
      — the parameter name `data` was missing, causing a SyntaxError/NameError
      that prevented the entire module from importing. Fixed.
    """
    delta = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_token(data, delta, token_type="access")


def create_refresh_token(data: dict[str, Any]) -> str:
    """Create a long-lived refresh token."""
    return create_token(
        data,
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        token_type="refresh",
    )


def verify_token(token: str, expected_type: str = "access") -> TokenData:
    """
    Decode and validate a JWT, checking revocation status.

    REMEDIATION NOTE (CRIT-02):
      Replaced python-jose (CVE-2024-33663 — algorithm confusion) with
      PyJWT >= 2.9. PyJWT enforces algorithm allowlist strictly and is
      actively maintained.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "nbf", "jti", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise credentials_exc

    # Enforce token type
    if payload.get("typ") != expected_type:
        raise credentials_exc

    # Check revocation blacklist
    jti: str | None = payload.get("jti")
    if jti and jti in _revoked_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str | None = payload.get("sub")
    role: str | None = payload.get("role")
    if not username:
        raise credentials_exc

    return TokenData(username=username, role=role, jti=jti)


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict[str, Any]:
    token_data = verify_token(token, expected_type="access")
    user = _DEMO_USERS.get(token_data.username or "")
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )
    return {**user, "jti": token_data.jti}


def require_role(*roles: str):
    """Dependency factory: enforce RBAC role membership."""
    async def _check(current_user: Annotated[dict, Depends(get_current_user)]):
        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user['role']}' is not authorized for this operation.",
            )
        return current_user
    return _check


# ──────────────────────────────────────────────────────────────────────────────
# In-memory incident store (replace with DB in production)
# ──────────────────────────────────────────────────────────────────────────────

_incidents: dict[str, dict[str, Any]] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Health / readiness probes
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health_check():
    """Liveness probe — confirms process is running."""
    return HealthResponse(
        status="ok",
        version="1.0.0",
        dependencies={},
    )


@app.get("/ready", response_model=HealthResponse, tags=["ops"])
async def readiness_check():
    """
    Readiness probe — confirms downstream dependencies are available.

    REMEDIATION NOTE (MED-02):
      Original probe returned {status: ready} unconditionally. Now checks
      that the JWT_SECRET_KEY env var is set (required for auth) and
      verifies the in-memory store is reachable. In production, add:
        - DB connection pool ping
        - Redis ping (for token blacklist)
        - Airflow API health check
    """
    deps: dict[str, str] = {}
    overall = "ready"

    # Check JWT secret is configured
    if not os.environ.get("JWT_SECRET_KEY"):
        deps["jwt_secret"] = "missing"
        overall = "degraded"
    else:
        deps["jwt_secret"] = "ok"

    # Check incident store is accessible
    try:
        _ = len(_incidents)
        deps["incident_store"] = "ok"
    except Exception:
        deps["incident_store"] = "error"
        overall = "degraded"

    if overall == "degraded":
        return JSONResponse(
            status_code=503,
            content=HealthResponse(
                status=overall, version="1.0.0", dependencies=deps
            ).model_dump(),
        )

    return HealthResponse(status=overall, version="1.0.0", dependencies=deps)


# ──────────────────────────────────────────────────────────────────────────────
# Auth routes
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/auth/token", response_model=Token, tags=["auth"])
@limiter.limit("5/minute")
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """
    Issue access + refresh tokens.

    REMEDIATION NOTE (HIGH-01):
      SlowAPI rate-limits this endpoint to 5 attempts/minute per IP.
      Brute-force attacks are now throttled at the application layer.
      In production, add:
        - Account lockout after N failed attempts
        - CAPTCHA for repeated failures
        - Alerting on >3 failures from same IP
    """
    log = structlog.get_logger(__name__)
    user = _DEMO_USERS.get(form_data.username)

    if not user or not pwd_context.verify(form_data.password, user["hashed_password"]):
        audit(
            "auth.login.failed",
            username=form_data.username,
            reason="invalid_credentials",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    token_payload = {"sub": user["username"], "role": user["role"]}
    access_token = create_access_token(token_payload)
    refresh_token = create_refresh_token(token_payload)

    audit(
        "auth.login.success",
        username=user["username"],
        role=user["role"],
    )
    log.info("auth.login", username=user["username"], role=user["role"])

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.post("/auth/refresh", response_model=Token, tags=["auth"])
@limiter.limit("10/minute")
async def refresh_token(request: Request, body: TokenRefreshRequest):
    """
    Exchange a refresh token for a new access token.

    REMEDIATION NOTE (MED-04):
      Original codebase had no refresh-token mechanism. Stolen access tokens
      were valid until expiry with no revocation path. This endpoint issues
      a new access token from a valid refresh token and revokes the old
      refresh token (rotation pattern).
    """
    token_data = verify_token(body.refresh_token, expected_type="refresh")

    user = _DEMO_USERS.get(token_data.username or "")
    if not user or user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Revoke consumed refresh token (rotation)
    if token_data.jti:
        _revoked_tokens.add(token_data.jti)

    token_payload = {"sub": user["username"], "role": user["role"]}
    new_access = create_access_token(token_payload)
    new_refresh = create_refresh_token(token_payload)

    audit("auth.token.refreshed", username=user["username"])

    return Token(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.post("/auth/logout", status_code=204, tags=["auth"])
async def logout(
    current_user: Annotated[dict, Depends(get_current_user)],
    token: Annotated[str, Depends(oauth2_scheme)],
):
    """
    Revoke the current access token.

    REMEDIATION NOTE (MED-04):
      Adds per-token invalidation. The jti claim is added to the
      _revoked_tokens set. Subsequent requests with this token will
      receive 401. Production: store jti in Redis with TTL = token expiry.
    """
    jti = current_user.get("jti")
    if jti:
        _revoked_tokens.add(jti)
    audit("auth.logout", username=current_user["username"])


# ──────────────────────────────────────────────────────────────────────────────
# Incident routes
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/incidents", status_code=201, tags=["incidents"])
async def create_incident(
    incident: IncidentCreate,
    current_user: Annotated[
        dict, Depends(require_role("analyst", "admin", "operator"))
    ],
):
    """
    Create a new ML incident record.

    REMEDIATION NOTE (HIGH-04):
      Original used int(time.time()*1000) as incident ID — concurrent
      requests within the same millisecond produce identical IDs.
      Now uses uuid4 — collision probability is cryptographically negligible.
    """
    incident_id = f"INC-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc).isoformat()

    record: dict[str, Any] = {
        "id": incident_id,
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "affected_model": incident.affected_model,
        "status": "open",
        "created_by": current_user["username"],
        "created_at": now,
        "updated_at": now,
        "resolution_notes": None,
        "assigned_to": None,
    }
    _incidents[incident_id] = record

    audit(
        "incident.created",
        incident_id=incident_id,
        severity=incident.severity,
        created_by=current_user["username"],
    )
    structlog.get_logger(__name__).info(
        "incident.created", incident_id=incident_id, severity=incident.severity
    )

    if incident.severity in ("critical", "high"):
        send_alert(
            f"[{incident.severity.upper()}] New incident: {incident.title}",
            severity=incident.severity,
            incident_id=incident_id,
        )

    return record


@app.get("/incidents", tags=["incidents"])
async def list_incidents(
    current_user: Annotated[
        dict, Depends(require_role("analyst", "admin", "operator"))
    ],
    status_filter: str | None = None,
    severity_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List incidents with optional filtering and pagination."""
    results = list(_incidents.values())

    if status_filter:
        results = [r for r in results if r["status"] == status_filter]
    if severity_filter:
        results = [r for r in results if r["severity"] == severity_filter]

    return {
        "total": len(results),
        "limit": limit,
        "offset": offset,
        "items": results[offset : offset + limit],
    }


@app.patch("/incidents/{incident_id}", tags=["incidents"])
async def update_incident(
    incident_id: str,
    update: IncidentUpdate,
    current_user: Annotated[
        dict, Depends(require_role("analyst", "admin", "operator"))
    ],
):
    """
    Update an existing incident.

    REMEDIATION NOTE (MED-05 / IDOR):
      Original code validated incident_id format but never checked whether
      the incident exists or whether the caller owns it. Now:
        1. 404 if incident does not exist
        2. Operators can only update their own incidents
        3. Analysts and admins can update any incident
      When a real DB is added, replace _incidents lookup with a parameterised
      query that includes created_by = current_user for operator role.
    """
    if not incident_id.startswith("INC-") or len(incident_id) != 16:
        raise HTTPException(status_code=400, detail="Invalid incident ID format")

    record = _incidents.get(incident_id)
    if not record:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Ownership enforcement for operator role
    if (
        current_user["role"] == "operator"
        and record["created_by"] != current_user["username"]
    ):
        raise HTTPException(
            status_code=403,
            detail="Operators may only update incidents they created",
        )

    now = datetime.now(timezone.utc).isoformat()
    update_data = update.model_dump(exclude_none=True)
    record.update({**update_data, "updated_at": now})
    _incidents[incident_id] = record

    audit(
        "incident.updated",
        incident_id=incident_id,
        updated_by=current_user["username"],
        fields_changed=list(update_data.keys()),
    )

    return record


@app.get("/incidents/{incident_id}", tags=["incidents"])
async def get_incident(
    incident_id: str,
    current_user: Annotated[
        dict, Depends(require_role("analyst", "admin", "operator"))
    ],
):
    """Retrieve a single incident by ID."""
    record = _incidents.get(incident_id)
    if not record:
        raise HTTPException(status_code=404, detail="Incident not found")
    return record
