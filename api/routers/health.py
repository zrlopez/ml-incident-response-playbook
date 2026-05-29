"""
api/routers/health.py
=====================
Liveness and readiness probes.

R-GOD Step 7: Extracted from api/app.py.
R-C03 COMPLETE: _deps._denylist replaced with request.app.state.denylist.
ML-02 COMPLETE: /ready now includes anomaly model registry health gate.

Remediation changelog:
  MYPY-01: Replaced direct JWT_SECRET (SecretStr) import with
           get_jwt_secret() at the jwt.decode call site.

Probe contract:
  GET /health  — liveness   (process alive; never touches DB/Redis)
  GET /ready   — readiness  (JWT subsystem + DB ping + Redis denylist
                             + env vars + ML model registry)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.config import get_jwt_secret, JWT_ALGORITHM
from src.auth import jwt_rs256
from src.auth.tokens import create_access_token
from ml_models.incident_anomaly.registry import model_registry

router = APIRouter(tags=["ops"])


# ── Liveness probe ─────────────────────────────────────────────────────────────────────────

@router.get("/health", include_in_schema=False)
async def liveness() -> Dict[str, str]:
    """
    Liveness probe — confirms the process is alive.

    Must NEVER touch the database, Redis, or any external dependency.
    """
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Readiness probe ────────────────────────────────────────────────────────────────────

@router.get("/ready", include_in_schema=False)
async def readiness(request: Request) -> JSONResponse:
    """
    Readiness probe — checks all critical dependencies.

    Returns 200 when all checks pass; 503 when any check fails.

    Checks performed:
      1. JWT subsystem (sign + verify round-trip)
      2. Database connectivity (SELECT 1 via app.state.engine)
      3. Redis denylist (ping via app.state.denylist)
      4. Required environment variables
      5. ML anomaly model registry (artifact present)
    """
    checks: Dict[str, Any] = {}
    all_ok = True

    # ── 1. JWT subsystem ───────────────────────────────────────────────────────────────────────
    try:
        _test_payload = {"sub": "__healthcheck__", "role": "_probe"}
        test_token, test_jti, _ = create_access_token(_test_payload, timedelta(seconds=5))
        if jwt_rs256.rs256_available():
            jwt_rs256.verify_token(test_token)
        else:
            jwt.decode(test_token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        checks["jwt_subsystem"] = "ok"
        checks["jwt_algorithm"] = "RS256" if jwt_rs256.rs256_available() else "HS256"
    except Exception as exc:
        checks["jwt_subsystem"] = f"error: {exc}"
        all_ok = False

    # ── 2. Database connectivity ───────────────────────────────────────────────────────────
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        try:
            from sqlalchemy import text as sa_text
            async with engine.connect() as conn:
                await conn.execute(sa_text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            all_ok = False
    else:
        checks["database"] = "not_initialised"
        all_ok = False

    # ── 3. Redis denylist ────────────────────────────────────────────────────────────────────
    denylist = getattr(request.app.state, "denylist", None)
    try:
        if denylist is not None:
            await denylist.ping()
            checks["redis_denylist"] = "ok"
        else:
            checks["redis_denylist"] = "not_initialised"
            all_ok = False
    except Exception as exc:
        checks["redis_denylist"] = f"error: {exc}"
        checks["redis_denylist_degraded"] = "true"

    # ── 4. Required environment variables ──────────────────────────────────────────────
    for var in ["JWT_SECRET_KEY"]:
        checks[f"env_{var}"] = "ok" if os.getenv(var) else "missing"
        if not os.getenv(var):
            all_ok = False

    # ── 5. ML anomaly model registry ────────────────────────────────────────────────────
    try:
        ml_health = model_registry.health()
        if ml_health["artifact_exists"]:
            checks["ml_anomaly_model"] = f"ok (v{ml_health['model_version']})"
        else:
            checks["ml_anomaly_model"] = "error: artifact not found"
            all_ok = False
    except Exception as exc:
        checks["ml_anomaly_model"] = f"error: {exc}"
        all_ok = False

    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
