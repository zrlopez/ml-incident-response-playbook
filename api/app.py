"""
api/app.py
==========
ML Incident Response API — application factory.

R-GOD: God-file decomposition complete.  This module is now a thin factory
shell.  All logic has been extracted to dedicated modules:

  api/config.py            — env, algorithm guard, limiter, oauth2_scheme
  api/stub_users.py        — dev/test _USERS store and env guard
  api/schemas.py           — request/response Pydantic models
  api/dependencies.py      — auth deps, get_current_user, require_role
  api/lifespan.py          — startup / shutdown wiring
  api/middleware.py        — all middleware incl. trace_and_security_headers
  src/auth/tokens.py       — JWT sign/verify helpers
  api/routers/health.py    — GET /health, GET /ready
  api/routers/auth.py      — POST /auth/token, /auth/refresh, /auth/logout
  api/routers/incidents.py — POST/GET/PATCH /incidents/*

Unlocks:
  R-C03  shared-state race   — _denylist/_user_repo isolated in dependencies.py
  R-C04  _build_engine() DI  — token helpers importable without engine init
  R-CI02 deployment target   — clean `api.app:app` for Gunicorn/Uvicorn
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.config import ALLOWED_ORIGINS, ENVIRONMENT, limiter
from api.lifespan import lifespan
from api.middleware import (
    MaxBodySizeMiddleware,
    SecurityHeadersMiddleware,
    RequestTimeoutMiddleware,
    trace_and_security_headers,
)
from api.gdpr_routes import router as gdpr_router
from api.routers.health import router as health_router
from api.routers.auth import router as auth_router
from api.routers.incidents import router as incidents_router
from api.routers.inference import router as inference_router
from src.incident_tracker import InvalidTransitionError

app = FastAPI(
    title="ML Incident Response API",
    version="2.4.0",
    description=(
        "Production-hardened ML incident management API with JWT auth, "
        "RBAC, cursor pagination, structured audit logging, and "
        "IsolationForest anomaly detection inference layer."
    ),
    lifespan=lifespan,
    docs_url="/docs" if ENVIRONMENT != "production" else None,
    redoc_url=None,
    redirect_slashes=False,
)

# ── Rate limiter ───────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # noqa: E501


# ── Exception handlers ────────────────────────────────────────────────────────
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


# ── Middleware ────────────────────────────────────────────────────────────────
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
async def _trace_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    return await trace_and_security_headers(request, call_next)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(gdpr_router)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(incidents_router)
app.include_router(inference_router)
