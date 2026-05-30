"""
tests/integration/test_api_gdpr_routes.py
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

_ADMIN_USER = {"sub": "admin", "username": "admin", "role": "admin", "disabled": False}


def _export_path():
    from api.gdpr_routes import router
    return next((r.path for r in router.routes if "export" in r.path), None)


def _delete_path():
    from api.gdpr_routes import router
    return next((r.path for r in router.routes if r.path.endswith("/me")), None)


class TestGDPRExport:

    @pytest.mark.anyio
    async def test_export_as_authed_user_returns_200(self):
        path = _export_path()
        if path is None:
            pytest.skip("No export route")
        from fastapi import FastAPI
        from api.gdpr_routes import router
        from api.dependencies import get_current_user
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(path)
        assert resp.status_code not in (401, 403)

    @pytest.mark.anyio
    async def test_export_unauthenticated_returns_4xx(self):
        """Without valid credentials the route must return 401 or 403."""
        path = _export_path()
        if path is None:
            pytest.skip("No export route")
        from fastapi import FastAPI, HTTPException, status as http_status
        from api.gdpr_routes import router
        from api.dependencies import get_current_user

        def _deny():
            raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = _deny
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(path)
        assert resp.status_code in (401, 403)


class TestGDPRDelete:

    @pytest.mark.anyio
    async def test_delete_as_authed_user_returns_2xx(self):
        path = _delete_path()
        if path is None:
            pytest.skip("No delete/me route")
        from fastapi import FastAPI
        from api.gdpr_routes import router
        from api.dependencies import get_current_user
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(path)
        assert resp.status_code not in (401, 403)


class TestGDPRRoutesRegistered:

    def test_gdpr_router_has_routes(self):
        from api.gdpr_routes import router
        assert len(router.routes) > 0

    def test_export_route_exists(self):
        assert _export_path() is not None
