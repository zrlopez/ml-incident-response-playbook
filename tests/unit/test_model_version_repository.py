"""
tests/unit/test_model_version_repository.py  — Phase 9

Verifies ModelVersionRepository against an in-memory SQLite database.
Uses the same function-scoped engine pattern as test_audit_log_repository.py
to avoid pytest-asyncio fixture scope conflicts.

Coverage targets
----------------
  DB-T01  upsert() creates a new row
  DB-T02  upsert() updates an existing row on conflict
  DB-T03  get() returns None for missing version
  DB-T04  get_active() returns the ACTIVE row
  DB-T05  deactivate_all() demotes all ACTIVE rows
  DB-T06  set_status() with ACTIVE writes activated_at
  DB-T07  list_all() returns rows newest-first
  DB-T08  metrics round-trip through JSON
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.incident_tracker import Base
from src.models.model_version import ModelVersion, ModelVersionStatus  # noqa: F401
from src.repositories.model_version_repository import ModelVersionRepository


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
async def repo(session):
    return ModelVersionRepository(session)


# ---------------------------------------------------------------------------
# DB-T01 / DB-T02 — upsert
# ---------------------------------------------------------------------------

async def test_upsert_creates_row(repo):
    row = await repo.upsert(
        version="1.0.0",
        status=ModelVersionStatus.INACTIVE,
        artifact_file="model_v1.joblib",
    )
    assert row.version == "1.0.0"
    assert row.status == ModelVersionStatus.INACTIVE


async def test_upsert_updates_existing_row(repo):
    await repo.upsert(
        version="1.0.0",
        status=ModelVersionStatus.INACTIVE,
        artifact_file="model_v1.joblib",
    )
    updated = await repo.upsert(
        version="1.0.0",
        status=ModelVersionStatus.ACTIVE,
        artifact_file="model_v1.joblib",
    )
    assert updated.status == ModelVersionStatus.ACTIVE


# ---------------------------------------------------------------------------
# DB-T03 — get missing
# ---------------------------------------------------------------------------

async def test_get_returns_none_for_missing(repo):
    assert await repo.get("99.99.99") is None


# ---------------------------------------------------------------------------
# DB-T04 — get_active
# ---------------------------------------------------------------------------

async def test_get_active_returns_active_row(repo):
    await repo.upsert(
        version="2.0.0",
        status=ModelVersionStatus.ACTIVE,
        artifact_file="model_v2.joblib",
    )
    active = await repo.get_active()
    assert active is not None
    assert active.version == "2.0.0"


async def test_get_active_returns_none_when_no_active(repo):
    await repo.upsert(
        version="1.0.0",
        status=ModelVersionStatus.INACTIVE,
        artifact_file="model_v1.joblib",
    )
    assert await repo.get_active() is None


# ---------------------------------------------------------------------------
# DB-T05 — deactivate_all
# ---------------------------------------------------------------------------

async def test_deactivate_all_demotes_active_rows(repo):
    await repo.upsert(version="1.0.0", status=ModelVersionStatus.ACTIVE, artifact_file="v1.joblib")
    await repo.upsert(version="2.0.0", status=ModelVersionStatus.ACTIVE, artifact_file="v2.joblib")
    await repo.deactivate_all()
    assert await repo.get_active() is None
    row = await repo.get("1.0.0")
    assert row.status == ModelVersionStatus.INACTIVE


# ---------------------------------------------------------------------------
# DB-T06 — set_status with ACTIVE sets activated_at
# ---------------------------------------------------------------------------

async def test_set_status_active_writes_activated_at(repo):
    await repo.upsert(version="1.0.0", status=ModelVersionStatus.INACTIVE, artifact_file="v1.joblib")
    row = await repo.set_status("1.0.0", ModelVersionStatus.ACTIVE)
    assert row is not None
    assert row.activated_at is not None


# ---------------------------------------------------------------------------
# DB-T07 — list_all newest-first
# ---------------------------------------------------------------------------

async def test_list_all_newest_first(repo):
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    await repo.upsert(version="1.0.0", status=ModelVersionStatus.INACTIVE,
                      artifact_file="v1.joblib", registered_at=t1)
    await repo.upsert(version="2.0.0", status=ModelVersionStatus.INACTIVE,
                      artifact_file="v2.joblib", registered_at=t2)
    rows = await repo.list_all()
    assert rows[0].version == "2.0.0"
    assert rows[1].version == "1.0.0"


# ---------------------------------------------------------------------------
# DB-T08 — metrics round-trip
# ---------------------------------------------------------------------------

async def test_metrics_round_trip(repo):
    metrics = {"precision": 0.91, "recall": 0.88}
    await repo.upsert(
        version="1.0.0",
        status=ModelVersionStatus.INACTIVE,
        artifact_file="v1.joblib",
        metrics=metrics,
    )
    row = await repo.get("1.0.0")
    assert row is not None
    assert row.metrics_json is not None
    assert json.loads(row.metrics_json) == metrics
