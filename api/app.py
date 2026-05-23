"""
ML Incident Response API  —  Production-hardened FastAPI application.

Security posture:
  - PyJWT (replaces python-jose; CVE-2024-33663 mitigated)
  - SlowAPI rate limiting on authentication endpoints
  - Per-request trace_id injected into every log event
  - Structured audit logging via observability.logging_config
  - RBAC enforcement on every protected route
  - Token revocation via in-memory denylist (Redis-backed in production)
  - Readiness probe with dependency health checks
  - Full CORS allowlist validation with sanitised empty-string guard
  - UUID-based incident IDs (replaces millisecond-timestamp collision risk)
  - Input validation via Pydantic v2 field validators
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

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

# ── Bootstrap structured logging immediately at import time ──────────────────
configure_logging()
log = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
JWT_SECRET_KEY: str = os.environ["JWT_SECRET_KEY"]          # Required — no default
JWT_ALGORITHM: str  = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int  = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS: int    = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS",   "7"))
APP_ENV: str = os.getenv("APP_ENV", "production")

_raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Security primitives ───────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# ── In-memory token denylist (swap for Redis in production) ───────────────────
# Structure: {jti: expiry_datetime}
_token_denylist: dict[str, datetime] = {}

def _denylist_add(jti: str, exp: datetime) -> None:
    _token_denylist[jti] = exp

def _denylist_contains(jti: str) -> bool:
    return jti in _token_denylist

def _denylist_purge_expired() -> None:
    now = datetime.now(tz=timezone.utc)
    expired = [k for k, v in _token_denylist.items() if v < now]
    for k in expired:
        del _token_denylist[k]

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="ML Incident Response API",
    version="1.0.0",
    description="Secure incident management API for ML operations.",
    docs_url="/docs" if APP_ENV != "production" else None,
    redoc_url="/redoc" if APP_ENV != "production" else None,
    openapi_url="/openapi.json" if APP_ENV != "production" else None,
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

# ── Security headers + request tracing middleware ─────────────────────────────
@app.middleware("http")
async def security_and_tracing_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        method=request.method,
        path=str(request.url.path),
        client_ip=request.client.host if request.client else "unknown",
    )
    import time
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    log.info("http_request", status_code=response.status_code, latency_ms=latency_ms)
    response.headers["X-Trace-Id"]            = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"]          = "no-store"
    structlog.contextvars.clear_contextvars()
    return response

# ── Synthetic user store (replace with DB in production) ──────────────────────
USERS_DB: dict[str, dict[str, Any]] = {
    "admin": {
        "username": "admin",
        "hashed_password": pwd_context.hash("changeme"),
        "role": "admin",
        "disabled": False,
    },
    "operator": {
        "username": "operator",
        "hashed_password": pwd_context.hash("operatorpass"),
        "role": "operator",
        "disabled": False,
    },
    "viewer": {
        "username": "viewer",
        "hashed_password": pwd_context.hash("viewerpass"),
        "role": "viewer",
        "disabled": False,
    },
}

# ── Pydantic models ───────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

class TokenData(BaseModel):
    username: str | None = None
    role: str | None = None
    jti: str | None = None

class IncidentCreate(BaseModel):
    title: str          = Field(..., min_length=5, max_length=200)
    description: str    = Field(..., min_length=10, max_length=4000)
    severity: str       = Field(...)
    affected_system: str= Field(..., min_length=2, max_length=100)
    tags: list[str]     = Field(default_factory=list, max_length=10)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"critical", "high", "medium", "low"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.lower()

class IncidentUpdate(BaseModel):
    status: str | None   = None
    resolution: str | None = None
    severity: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"open", "investigating", "resolved", "closed"}
        if v.lower() not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v.lower()

class RefreshRequest(BaseModel):
    refresh_token: str

# ── JWT helpers ───────────────────────────────────────────────────────────────
def _create_token(
    data: dict[str, Any],
    expires_delta: timedelta,
    token_type: str = "access",
) -> str:
    """Create a signed JWT with a unique jti claim."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        **data,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
        "type": token_type,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    delta = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return _create_token(data, delta, token_type="access")

def create_refresh_token(data: dict[str, Any]) -> str:
    return _create_token(data, timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS), token_type="refresh")

def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT, raising HTTP 401 on any failure."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "jti", "sub", "type"]},
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

# ── Auth helpers ──────────────────────────────────────────────────────────────
def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def get_user(username: str) -> dict[str, Any] | None:
    return USERS_DB.get(username)

def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    user = get_user(username)
    if not user or user.get("disabled") or not verify_password(password, user["hashed_password"]):
        return None
    return user

async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    jti = payload.get("jti", "")
    if _denylist_contains(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")
    username: str | None = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    user = get_user(username)
    if not user or user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    return user

def require_role(*roles: str):
    async def _checker(current_user: dict = Depends(get_current_user)):
        if current_user.get("role") not in roles:
            log.warning("authz_denied", user=current_user.get("username"), required_roles=roles)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return _checker

# ── In-memory incident store (replace with DB) ────────────────────────────────
incidents_db: dict[str, dict[str, Any]] = {}

# ── Routes ────────────────────────────────────────────────────────────────────
@app.post("/auth/token", response_model=Token, tags=["Authentication"])
@limiter.limit("5/minute")
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    """Issue access + refresh token pair.  Rate-limited to 5 attempts/minute per IP."""
    user = authenticate_user(form.username, form.password)
    if not user:
        log.warning("auth_failure", username=form.username, event="invalid_credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_data = {"sub": user["username"], "role": user["role"]}
    access_token  = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)
    log.info("auth_success", username=user["username"], role=user["role"], event="login",
             log_type="audit")
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@app.post("/auth/refresh", response_model=Token, tags=["Authentication"])
@limiter.limit("10/minute")
async def refresh(request: Request, body: RefreshRequest):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    jti = payload.get("jti", "")
    if _denylist_contains(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")
    username = payload.get("sub")
    user = get_user(username) if username else None
    if not user or user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    # Rotate: revoke old refresh token
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    _denylist_add(jti, exp)
    token_data = {"sub": user["username"], "role": user["role"]}
    log.info("token_refreshed", username=username, event="token_refresh", log_type="audit")
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@app.post("/auth/logout", tags=["Authentication"])
async def logout(request: Request, token: str = Depends(oauth2_scheme),
                 current_user: dict = Depends(get_current_user)):
    """Revoke the current access token immediately."""
    payload = decode_token(token)
    jti = payload.get("jti", "")
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    _denylist_add(jti, exp)
    _denylist_purge_expired()   # opportunistic cleanup
    log.info("auth_logout", username=current_user["username"], event="logout", log_type="audit")
    return {"detail": "Successfully logged out"}

@app.post("/incidents", status_code=status.HTTP_201_CREATED, tags=["Incidents"])
async def create_incident(
    incident: IncidentCreate,
    current_user: dict = Depends(require_role("operator", "admin")),
):
    """Create a new ML incident record."""
    incident_id = f"INC-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(tz=timezone.utc).isoformat()
    record: dict[str, Any] = {
        "id": incident_id,
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "affected_system": incident.affected_system,
        "tags": incident.tags,
        "status": "open",
        "created_by": current_user["username"],
        "created_at": now,
        "updated_at": now,
    }
    incidents_db[incident_id] = record
    log.info(
        "incident_created",
        incident_id=incident_id,
        severity=incident.severity,
        created_by=current_user["username"],
        log_type="audit",
    )
    return record

@app.get("/incidents", tags=["Incidents"])
async def list_incidents(
    severity: str | None = None,
    status_filter: str | None = None,
    current_user: dict = Depends(require_role("viewer", "operator", "admin")),
):
    """List all incidents, with optional severity/status filters."""
    results = list(incidents_db.values())
    if severity:
        results = [r for r in results if r["severity"] == severity.lower()]
    if status_filter:
        results = [r for r in results if r["status"] == status_filter.lower()]
    return {"incidents": results, "total": len(results)}

@app.patch("/incidents/{incident_id}", tags=["Incidents"])
async def update_incident(
    incident_id: str,
    update: IncidentUpdate,
    current_user: dict = Depends(require_role("operator", "admin")),
):
    """Update an existing incident.  Operators may only update incidents they created."""
    if not incident_id.startswith("INC-"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid incident ID format")
    record = incidents_db.get(incident_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    # Ownership check: operators can only update their own incidents
    if current_user["role"] == "operator" and record["created_by"] != current_user["username"]:
        log.warning("idor_attempt", incident_id=incident_id, user=current_user["username"], log_type="audit")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot modify incidents created by others")
    changed_fields: dict[str, Any] = {}
    if update.status is not None:
        changed_fields["status"] = update.status
    if update.resolution is not None:
        changed_fields["resolution"] = update.resolution
    if update.severity is not None:
        changed_fields["severity"] = update.severity
    record.update(changed_fields)
    record["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    incidents_db[incident_id] = record
    log.info(
        "incident_updated",
        incident_id=incident_id,
        changed_fields=list(changed_fields.keys()),
        updated_by=current_user["username"],
        log_type="audit",
    )
    return record

# ── Health / Readiness probes ─────────────────────────────────────────────────
@app.get("/health", tags=["Ops"])
async def health_check():
    """Liveness probe — returns 200 if the process is alive."""
    return {"status": "alive", "timestamp": datetime.now(tz=timezone.utc).isoformat()}

@app.get("/ready", tags=["Ops"])
async def readiness_check():
    """
    Readiness probe — validates all critical dependencies are reachable.
    In production, extend checks to include database, cache, and external APIs.
    Returns 503 if any dependency is unhealthy.
    """
    checks: dict[str, str] = {}
    all_ok = True

    # JWT secret validation
    try:
        test_payload = {"sub": "healthcheck", "role": "system"}
        test_token = create_access_token(test_payload, timedelta(seconds=5))
        decode_token(test_token)
        checks["jwt"] = "ok"
    except Exception as exc:  # noqa: BLE001
        log.error("readiness_check_failed", component="jwt", error=str(exc))
        checks["jwt"] = "error"
        all_ok = False

    # TODO(prod): Add DB connection check:
    # async with db_pool.acquire() as conn:
    #     await conn.fetchval("SELECT 1")
    checks["database"] = "not_configured"   # Replace with real check

    if not all_ok:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            content={"status": "degraded", "checks": checks})
    return {"status": "ready", "checks": checks, "timestamp": datetime.now(tz=timezone.utc).isoformat()}
