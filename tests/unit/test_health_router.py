"""
tests/unit/test_health_router.py
=================================
Unit tests for api/routers/health.py — liveness and readiness probes.

Covers missing lines (CI-67):
  78-80   — DB engine.connect() + SELECT 1 success path
  85-92   — DB engine error path; engine=None path
  106-108 — Redis denylist.ping() success
  122-126 — ML model registry: artifact present, artifact missing, exception

All external dependencies (engine, denylist, model_registry) are patched
so these tests run without Postgres, Redis, or real ML artifacts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.health import router


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_engine(ok: bool = True) -> MagicMock:
    """Return a fake async SQLAlchemy engine."""
    conn = AsyncMock()
    if not ok:
        conn.execute.side_effect = RuntimeError("db unreachable")
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = cm
    return engine


def _mock_denylist(ok: bool = True, raises: bool = False) -> AsyncMock:
    denylist = AsyncMock()
    if raises:
        denylist.ping.side_effect = ConnectionError("redis down")
    return denylist


def _mock_registry(artifact_exists: bool = True, raises: bool = False) -> MagicMock:
    registry = MagicMock()
    if raises:
        registry.health.side_effect = RuntimeError("registry unavailable")
    else:
        registry.health.return_value = {
            "artifact_exists": artifact_exists,
            "model_version": "1.0.0",
        }
    return registry


# ---------------------------------------------------------------------------
# Liveness probe
# ---------------------------------------------------------------------------

def test_liveness(app: FastAPI) -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "alive"
    assert "timestamp" in data


# ---------------------------------------------------------------------------
# Readiness probe — all checks pass
# ---------------------------------------------------------------------------

def test_readiness_all_ok(app: FastAPI) -> None:
    """200 when engine, denylist, ML registry, and JWT all pass."""
    engine = _mock_engine(ok=True)
    denylist = _mock_denylist(ok=True)
    registry = _mock_registry(artifact_exists=True)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        client.app.state.engine = engine
        client.app.state.denylist = denylist
        resp = client.get("/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis_denylist"] == "ok"
    assert "ok" in body["checks"]["ml_anomaly_model"]


# ---------------------------------------------------------------------------
# Readiness probe — DB paths (lines 78-80, 85-92)
# ---------------------------------------------------------------------------

def test_readiness_db_error(app: FastAPI) -> None:
    """503 when engine.connect() raises."""
    engine = _mock_engine(ok=False)
    denylist = _mock_denylist(ok=True)
    registry = _mock_registry(artifact_exists=True)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        client.app.state.engine = engine
        client.app.state.denylist = denylist
        resp = client.get("/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert "error" in body["checks"]["database"]


def test_readiness_db_not_initialised(app: FastAPI) -> None:
    """503 when engine is not on app.state."""
    denylist = _mock_denylist(ok=True)
    registry = _mock_registry(artifact_exists=True)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        # engine deliberately absent from state
        client.app.state.denylist = denylist
        resp = client.get("/ready")

    body = resp.json()
    assert body["checks"]["database"] == "not_initialised"
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Readiness probe — Redis paths (lines 106-108)
# ---------------------------------------------------------------------------

def test_readiness_redis_ok(app: FastAPI) -> None:
    engine = _mock_engine(ok=True)
    denylist = _mock_denylist(ok=True)
    registry = _mock_registry(artifact_exists=True)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        client.app.state.engine = engine
        client.app.state.denylist = denylist
        resp = client.get("/ready")

    assert resp.json()["checks"]["redis_denylist"] == "ok"


def test_readiness_redis_ping_raises(app: FastAPI) -> None:
    """Redis ping exception — degraded key set but does not flip all_ok per source."""
    engine = _mock_engine(ok=True)
    denylist = _mock_denylist(raises=True)
    registry = _mock_registry(artifact_exists=True)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        client.app.state.engine = engine
        client.app.state.denylist = denylist
        resp = client.get("/ready")

    body = resp.json()
    assert "error" in body["checks"]["redis_denylist"]


def test_readiness_redis_not_initialised(app: FastAPI) -> None:
    """503 when denylist is not on app.state."""
    engine = _mock_engine(ok=True)
    registry = _mock_registry(artifact_exists=True)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        client.app.state.engine = engine
        # denylist deliberately absent
        resp = client.get("/ready")

    body = resp.json()
    assert body["checks"]["redis_denylist"] == "not_initialised"
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Readiness probe — ML registry paths (lines 122-126)
# ---------------------------------------------------------------------------

def test_readiness_ml_artifact_missing(app: FastAPI) -> None:
    """503 when ML registry reports artifact_exists=False."""
    engine = _mock_engine(ok=True)
    denylist = _mock_denylist(ok=True)
    registry = _mock_registry(artifact_exists=False)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        client.app.state.engine = engine
        client.app.state.denylist = denylist
        resp = client.get("/ready")

    assert resp.status_code == 503
    assert "error" in resp.json()["checks"]["ml_anomaly_model"]


def test_readiness_ml_registry_exception(app: FastAPI) -> None:
    """503 when model_registry.health() raises."""
    engine = _mock_engine(ok=True)
    denylist = _mock_denylist(ok=True)
    registry = _mock_registry(raises=True)

    with (
        patch("api.routers.health.model_registry", registry),
        TestClient(app) as client,
    ):
        client.app.state.engine = engine
        client.app.state.denylist = denylist
        resp = client.get("/ready")

    assert resp.status_code == 503
    assert "error" in resp.json()["checks"]["ml_anomaly_model"]
