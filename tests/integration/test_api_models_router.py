"""
tests/integration/test_api_models_router.py

_record_to_schema() uses dict key access (record["version"] etc.).
activate_version_async returns a tuple (record_dict, previous_version_str).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient, ASGITransport

_ADMIN_USER  = {"sub": "admin",   "username": "admin",   "role": "admin",   "disabled": False}
_ANALYST_USER = {"sub": "analyst", "username": "analyst", "role": "analyst", "disabled": False}
_NOW = datetime.now(timezone.utc)


def _make_record(status: str = "inactive") -> dict:
    return {
        "version": "v1.0.0",
        "status": status,
        "artifact_file": "model_v1.pkl",
        "artifact_exists": True,
        "registered_at": _NOW,
        "activated_at": _NOW,
        "sha256_verified": True,
        "metrics": {"accuracy": 0.95},
    }


def _make_svc():
    r = _make_record("inactive")
    q = _make_record("quarantined")
    svc = MagicMock()
    svc.list_versions_async    = AsyncMock(return_value=[r])
    svc.get_version_async      = AsyncMock(return_value=r)
    svc.get_active_async       = AsyncMock(return_value=r)
    svc.register_version_async = AsyncMock(return_value=r)
    # activate returns (new_record, previous_version_str)
    svc.activate_version_async  = AsyncMock(return_value=(r, "v0.9.0"))
    svc.quarantine_version_async = AsyncMock(return_value=q)
    return svc


def _make_app(user: dict, svc=None, allow_admin: bool = True):
    from fastapi import FastAPI
    from api.routers.models import router as models_router, get_model_service
    from api.dependencies import get_current_user, require_role

    mock_svc = svc or _make_svc()
    app = FastAPI()
    app.include_router(models_router)
    app.dependency_overrides[get_current_user]  = lambda: user
    app.dependency_overrides[get_model_service] = lambda: mock_svc

    if allow_admin:
        app.dependency_overrides[require_role] = lambda role="admin": (lambda: user)
    else:
        def _reject(role: str = "admin"):
            def _inner():
                raise HTTPException(status_code=403, detail="Forbidden")
            return _inner
        app.dependency_overrides[require_role] = _reject

    return app, mock_svc


# ---------------------------------------------------------------------------
class TestModelsListRoute:

    @pytest.mark.anyio
    async def test_list_returns_200(self):
        app, _ = _make_app(_ANALYST_USER)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/models")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_list_unauthenticated_returns_4xx(self):
        from fastapi import FastAPI
        from api.routers.models import router as models_router
        bare = FastAPI()
        bare.include_router(models_router)
        async with AsyncClient(transport=ASGITransport(app=bare), base_url="http://test") as c:
            resp = await c.get("/api/v1/models")
        assert resp.status_code in (401, 403, 422)


class TestModelsGetActive:

    @pytest.mark.anyio
    async def test_get_active_returns_200(self):
        app, _ = _make_app(_ANALYST_USER)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/models/active")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_get_active_none_returns_404(self):
        svc = _make_svc()
        svc.get_active_async = AsyncMock(return_value=None)
        app, _ = _make_app(_ANALYST_USER, svc=svc)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/models/active")
        assert resp.status_code == 404


class TestModelsRegister:

    @pytest.mark.anyio
    async def test_register_as_admin_returns_201(self):
        app, _ = _make_app(_ADMIN_USER)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/models",
                json={"version": "v2.0.0", "artifact_file": "model_v2.pkl"},
            )
        assert resp.status_code in (200, 201)

    @pytest.mark.anyio
    async def test_register_as_analyst_forbidden(self):
        app, _ = _make_app(_ANALYST_USER, allow_admin=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/models",
                json={"version": "v2.0.0", "artifact_file": "model_v2.pkl"},
            )
        assert resp.status_code == 403


class TestModelsActivateQuarantine:

    @pytest.mark.anyio
    async def test_activate_as_admin_returns_200(self):
        app, _ = _make_app(_ADMIN_USER)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/models/v1.0.0/activate")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_activate_missing_version_returns_404(self):
        svc = _make_svc()
        svc.activate_version_async = AsyncMock(side_effect=KeyError("v1.0.0"))
        app, _ = _make_app(_ADMIN_USER, svc=svc)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/models/v1.0.0/activate")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_quarantine_as_admin_returns_200(self):
        app, _ = _make_app(_ADMIN_USER)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/models/v1.0.0/quarantine")
        assert resp.status_code == 200
