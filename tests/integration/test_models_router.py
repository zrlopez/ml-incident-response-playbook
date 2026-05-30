"""
tests/integration/test_models_router.py  — Phase 9

Integration tests for api/routers/models.py against in-memory SQLite.

Coverage targets
----------------
  RT-01  GET  /api/v1/models           — 200, returns bootstrapped version
  RT-02  GET  /api/v1/models/active    — 200, returns active version
  RT-03  GET  /api/v1/models/{version} — 200, returns specific version
  RT-04  GET  /api/v1/models/{version} — 404 for unknown version
  RT-05  POST /api/v1/models           — 201, registers new version
  RT-06  POST /api/v1/models           — 409 for duplicate version
  RT-07  POST /api/v1/models/{v}/activate   — 404 for unknown version
  RT-08  POST /api/v1/models/{v}/quarantine — 200, quarantines active version
  RT-09  GET  /api/v1/models/active after quarantine — 404

Fixture design
--------------
Overrides both get_session (src.platform.database) and get_current_user
(api.dependencies) so no Postgres, Redis, or JWT is needed. The model
registry service's _bootstrap() also calls model_registry.health(), which
is patched module-wide via monkeypatch to avoid loading the real joblib model.
"""
from __future__ import annotations

# pylint: disable=redefined-outer-name
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def models_client(sqlite_engine):
    """
    FastAPI test client wired to in-memory SQLite.

    Overrides:
      - src.platform.database.get_session -> SQLite session
      - api.dependencies.get_current_user  -> stub admin user
    """
    import os
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-minimum-32-chars-xxxxxxxxxxxx")

    from api.app import app
    from api.dependencies import get_current_user
    from src.platform.database import get_session

    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_current_user():
        return {"sub": "test-admin", "username": "test-admin", "role": "admin", "disabled": False}

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = override_get_current_user

    mock_registry = MagicMock()
    mock_registry.health.return_value = {
        "model_loaded": True,
        "artifact_file": "isolation_forest_v1.joblib",
        "loaded_at": 1_700_000_000.0,
    }

    with patch("src.services.model_registry_service.model_registry", mock_registry), \
         patch("src.services.model_registry_service.MODEL_VERSION", "1.0.0"):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://testserver",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# RT-01 — list versions
# ---------------------------------------------------------------------------

async def test_list_models_returns_bootstrapped_version(models_client):
    resp = await models_client.get("/api/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    versions = [v["version"] for v in data["versions"]]
    assert "1.0.0" in versions


# ---------------------------------------------------------------------------
# RT-02 — get active
# ---------------------------------------------------------------------------

async def test_get_active_model_returns_active(models_client):
    resp = await models_client.get("/api/v1/models/active")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


# ---------------------------------------------------------------------------
# RT-03 / RT-04 — get specific version
# ---------------------------------------------------------------------------

async def test_get_specific_version_200(models_client):
    resp = await models_client.get("/api/v1/models/1.0.0")
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.0.0"


async def test_get_specific_version_404(models_client):
    resp = await models_client.get("/api/v1/models/99.99.99")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RT-05 / RT-06 — register new version
# ---------------------------------------------------------------------------

async def test_register_new_version_201(models_client):
    resp = await models_client.post(
        "/api/v1/models",
        json={"version": "2.0.0", "artifact_file": "model_v2.joblib"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["version"] == "2.0.0"
    assert data["status"] == "inactive"


async def test_register_duplicate_version_409(models_client):
    resp = await models_client.post(
        "/api/v1/models",
        json={"version": "1.0.0", "artifact_file": "model_v1.joblib"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# RT-07 — activate unknown version 404
# ---------------------------------------------------------------------------

async def test_activate_unknown_version_404(models_client):
    resp = await models_client.post("/api/v1/models/99.99.99/activate")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RT-08 / RT-09 — quarantine
# ---------------------------------------------------------------------------

async def test_quarantine_active_version_200(models_client):
    resp = await models_client.post("/api/v1/models/1.0.0/quarantine")
    assert resp.status_code == 200
    assert resp.json()["status"] == "quarantined"


async def test_get_active_after_quarantine_returns_404(models_client):
    await models_client.post("/api/v1/models/1.0.0/quarantine")
    resp = await models_client.get("/api/v1/models/active")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RT-10 — register then retrieve by specific version (cross-request DB read)
# ---------------------------------------------------------------------------

async def test_registered_version_retrievable_by_version_endpoint(models_client):
    """Version registered via POST must be visible to GET /{version}."""
    await models_client.post(
        "/api/v1/models",
        json={"version": "3.0.0", "artifact_file": "model_v3.joblib"},
    )
    resp = await models_client.get("/api/v1/models/3.0.0")
    assert resp.status_code == 200
    assert resp.json()["version"] == "3.0.0"
