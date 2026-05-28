"""
tests/unit/test_model_registry_service_db.py  — Phase 9

Verifies ModelRegistryService.create_db_backed() and its async mutation
methods against an in-memory SQLite database.

Coverage targets
----------------
  SVC-DB-01  create_db_backed() bootstraps version into DB
  SVC-DB-02  register_version_async() persists to DB
  SVC-DB-03  activate_version_async() persists ACTIVE status to DB
  SVC-DB-04  quarantine_version_async() persists QUARANTINED status to DB
  SVC-DB-05  list_versions_async() reads from DB
  SVC-DB-06  get_active_async() returns active row from DB
  SVC-DB-07  get_active_async() returns None after quarantine of active version
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.incident_tracker import Base
from src.models.model_version import ModelVersionStatus  # noqa: F401
from src.services.model_registry_service import ModelRegistryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_registry(*, model_loaded: bool = True) -> MagicMock:
    mock = MagicMock()
    mock.health.return_value = {
        "model_loaded": model_loaded,
        "artifact_file": "isolation_forest_v1.joblib",
        "loaded_at": 1_700_000_000.0,
    }
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_service(session):
    with patch(
        "src.services.model_registry_service.MODEL_VERSION", "1.0.0"
    ):
        svc = await ModelRegistryService.create_db_backed(
            session, mock_registry=_mock_registry(model_loaded=True)
        )
    return svc


@pytest_asyncio.fixture()
async def db_service_with_artifact(db_service, monkeypatch):
    import src.services.model_registry_service as svc_mod
    monkeypatch.setattr(
        svc_mod.ModelVersionRecord, "artifact_exists", lambda self: True
    )
    return db_service


# ---------------------------------------------------------------------------
# SVC-DB-01 — bootstrap
# ---------------------------------------------------------------------------

async def test_bootstrap_persists_version_to_db(db_service):
    rows = await db_service.list_versions_async()
    assert len(rows) == 1
    assert rows[0]["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# SVC-DB-02 — register_version_async
# ---------------------------------------------------------------------------

async def test_register_version_async_persists(db_service):
    await db_service.register_version_async(
        version="2.0.0",
        artifact_file="model_v2.joblib",
        metrics={"precision": 0.95},
    )
    rows = await db_service.list_versions_async()
    versions = [r["version"] for r in rows]
    assert "2.0.0" in versions


async def test_register_duplicate_still_raises(db_service):
    with pytest.raises(ValueError, match="already registered"):
        await db_service.register_version_async(
            version="1.0.0",
            artifact_file="model_v1.joblib",
        )


# ---------------------------------------------------------------------------
# SVC-DB-03 — activate_version_async
# ---------------------------------------------------------------------------

async def test_activate_version_async_persists_active_status(
    db_service_with_artifact,
):
    svc = db_service_with_artifact
    record, _ = await svc.activate_version_async("1.0.0")
    assert record["status"] == "active"
    active = await svc.get_active_async()
    assert active is not None
    assert active["version"] == "1.0.0"
    assert active["status"] == "active"


async def test_activate_version_async_demotes_previous(
    db_service_with_artifact,
):
    svc = db_service_with_artifact
    await svc.register_version_async(version="2.0.0", artifact_file="v2.joblib")
    await svc.activate_version_async("2.0.0")

    rows = await svc.list_versions_async()
    status_map = {r["version"]: r["status"] for r in rows}
    assert status_map["2.0.0"] == "active"
    assert status_map["1.0.0"] == "inactive"


# ---------------------------------------------------------------------------
# SVC-DB-04 — quarantine_version_async
# ---------------------------------------------------------------------------

async def test_quarantine_version_async_persists(
    db_service_with_artifact,
):
    svc = db_service_with_artifact
    await svc.quarantine_version_async("1.0.0")
    rows = await svc.list_versions_async()
    assert rows[0]["status"] == "quarantined"


# ---------------------------------------------------------------------------
# SVC-DB-06 / SVC-DB-07 — get_active_async edge cases
# ---------------------------------------------------------------------------

async def test_get_active_async_none_when_unloaded(session):
    with patch("src.services.model_registry_service.MODEL_VERSION", "1.0.0"):
        svc = await ModelRegistryService.create_db_backed(
            session, mock_registry=_mock_registry(model_loaded=False)
        )
    active = await svc.get_active_async()
    assert active is None


async def test_get_active_async_none_after_quarantine(
    db_service_with_artifact,
):
    svc = db_service_with_artifact
    await svc.activate_version_async("1.0.0")
    await svc.quarantine_version_async("1.0.0")
    assert await svc.get_active_async() is None
