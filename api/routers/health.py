"""
api/routers/health.py
=====================
Kubernetes liveness and readiness probes.

R-GOD Step 7: Extracted from api/app.py.
R-C03 COMPLETE: _deps._denylist replaced with request.app.state.denylist.

  GET /health  — liveness probe (process alive)
  GET /ready   — readiness probe (JWT subsystem + Redis denylist + env vars)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.config import JWT_SECRET, JWT_ALGORITHM
from src.auth import jwt_rs256
from src.auth.tokens import create_access_token

router = APIRouter(tags=["ops"])


@router.get("/health", include_in_schema=False)
async def liveness() -> Dict[str, str]:
    """Kubernetes liveness probe — confirms process is alive."""
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/ready", include_in_schema=False)
async def readiness(request: Request) -> JSONResponse:
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

    # R-C03: Read denylist from app.state, not module globals.
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
        # R-S04: Denylist degradation does not hard-fail the readiness probe.
        checks["redis_denylist_degraded"] = "true"

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
