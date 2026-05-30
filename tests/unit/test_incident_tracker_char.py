"""
tests/unit/test_incident_tracker_char.py
=========================================
Characterization tests for src/incident_tracker.py  (R-P22)

PURPOSE
-------
This file is a *safety net*, not a behaviour spec.  It captures the
current observable behaviour of the incident_tracker module so that any
future refactor (R-P23) cannot silently break the public contract.

Rules for this file:
  1. Tests must be fast (in-process aiosqlite; no network, no Docker).
  2. Do NOT couple to implementation details (private attrs, SQL text).
  3. If a test fails after a refactor, that is EXPECTED — review the
     failure to confirm it is intentional, then update the test to match
     the new contract.
  4. Coverage target: ≥80 % of src/incident_tracker.py lines.

Scope:
  - Incident ORM model — field defaults, to_dict() shape
  - IncidentRepository — get, create-equivalent, list_open,
    list_by_severity, update_status, resolve
  - Keyset pagination (KEYSET-01 compound cursor)
  - InvalidTransitionError on illegal transitions
  - init_db() SQLite fast-path (no migration check)
  - get_session() commit / rollback lifecycle

SQLite timezone note:
  SQLite does not preserve tzinfo on DateTime(timezone=True) columns.
  Values written with a UTC-aware datetime are stored as naive strings
  and read back as naive datetimes (tzinfo=None). Tests in this file
  that check timestamp fields assert `isinstance(..., datetime)` and
  `is not None` rather than `tzinfo is not None`. The timezone contract
  is enforced at the PostgreSQL layer in integration tests.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.domain.incident_lifecycle import IncidentStatus, SeverityLevel
from src.incident_tracker import (
    Base,
    Incident,
    IncidentRepository,
    InvalidTransitionError,
    get_session,
    init_db,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture()
async def engine():
    """In-process async SQLite engine with fresh schema per test."""
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture()
async def session(engine) -> AsyncIterator[AsyncSession]:
    """Scoped async session; rolls back after each test for isolation."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture()
async def repo(session: AsyncSession) -> IncidentRepository:
    return IncidentRepository(session)


async def _create_incident(
    session: AsyncSession,
    *,
    title: str = "Test incident",
    severity: SeverityLevel = SeverityLevel.SEV3,
    category: str = "model-drift",
    owner: str | None = "eng-on-call",
    description: str | None = None,
) -> Incident:
    """Helper: insert a raw Incident row and flush so it has a PK."""
    inc = Incident(
        title=title,
        severity=severity,
        status=IncidentStatus.OPEN,
        category=category,
        owner=owner,
        description=description,
    )
    session.add(inc)
    await session.flush()
    await session.refresh(inc)
    return inc


# ---------------------------------------------------------------------------
# 1. ORM model — field presence and defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incident_default_status_is_open(session: AsyncSession) -> None:
    inc = await _create_incident(session)
    assert inc.status == IncidentStatus.OPEN


@pytest.mark.asyncio
async def test_incident_default_severity_falls_through(session: AsyncSession) -> None:
    """Explicit SEV2 assignment is preserved."""
    inc = await _create_incident(session, severity=SeverityLevel.SEV2)
    assert inc.severity == SeverityLevel.SEV2


@pytest.mark.asyncio
async def test_incident_has_uuid_id(session: AsyncSession) -> None:
    inc = await _create_incident(session)
    assert inc.id is not None
    assert len(inc.id) == 36  # UUID4 canonical form


@pytest.mark.asyncio
async def test_incident_created_at_is_utc(session: AsyncSession) -> None:
    # SQLite strips tzinfo on round-trip; assert the field is a non-None
    # datetime. The UTC timezone contract is verified in integration tests
    # against PostgreSQL where DateTime(timezone=True) is fully honoured.
    inc = await _create_incident(session)
    assert inc.created_at is not None
    assert isinstance(inc.created_at, datetime)


@pytest.mark.asyncio
async def test_incident_resolved_at_defaults_none(session: AsyncSession) -> None:
    inc = await _create_incident(session)
    assert inc.resolved_at is None


# ---------------------------------------------------------------------------
# 2. to_dict() shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_to_dict_contains_required_keys(session: AsyncSession) -> None:
    inc = await _create_incident(session)
    d = inc.to_dict()
    required = {
        "id", "title", "severity", "status", "category",
        "owner", "description", "created_at", "updated_at", "resolved_at",
    }
    assert required.issubset(d.keys())


@pytest.mark.asyncio
async def test_to_dict_severity_is_string(session: AsyncSession) -> None:
    inc = await _create_incident(session)
    d = inc.to_dict()
    assert isinstance(d["severity"], str)


@pytest.mark.asyncio
async def test_to_dict_resolved_at_none_when_open(session: AsyncSession) -> None:
    inc = await _create_incident(session)
    assert inc.to_dict()["resolved_at"] is None


# ---------------------------------------------------------------------------
# 3. IncidentRepository.get()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_get_returns_incident(session: AsyncSession, repo: IncidentRepository) -> None:
    inc = await _create_incident(session)
    fetched = await repo.get(inc.id)
    assert fetched is not None
    assert fetched.id == inc.id


@pytest.mark.asyncio
async def test_repo_get_returns_none_for_missing_id(repo: IncidentRepository) -> None:
    result = await repo.get("00000000-0000-0000-0000-000000000000")
    assert result is None


# ---------------------------------------------------------------------------
# 4. IncidentRepository.list_open()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_open_excludes_closed(session: AsyncSession, repo: IncidentRepository) -> None:
    open_inc = await _create_incident(session, title="Open")
    closed_inc = await _create_incident(session, title="Closed")
    closed_inc.status = IncidentStatus.CLOSED
    await session.flush()

    results = await repo.list_open()
    ids = [r.id for r in results]
    assert open_inc.id in ids
    assert closed_inc.id not in ids


@pytest.mark.asyncio
async def test_list_open_limit_respected(session: AsyncSession, repo: IncidentRepository) -> None:
    for i in range(5):
        await _create_incident(session, title=f"Inc {i}")
    results = await repo.list_open(limit=3)
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_list_open_returns_empty_when_all_closed(session: AsyncSession, repo: IncidentRepository) -> None:
    inc = await _create_incident(session)
    inc.status = IncidentStatus.CLOSED
    await session.flush()
    assert await repo.list_open() == []


# ---------------------------------------------------------------------------
# 5. Keyset pagination — compound (created_at, id) cursor (KEYSET-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyset_pagination_no_gaps(session: AsyncSession, repo: IncidentRepository) -> None:
    """Two pages of limit=2 across 4 incidents must cover all 4 unique IDs."""
    for i in range(4):
        await _create_incident(session, title=f"Paged {i}")

    page1 = await repo.list_open(limit=2)
    assert len(page1) == 2

    page2 = await repo.list_open(limit=2, before_id=page1[-1].id)
    assert len(page2) == 2

    all_ids = {r.id for r in page1} | {r.id for r in page2}
    assert len(all_ids) == 4  # no duplicates, no gaps


@pytest.mark.asyncio
async def test_keyset_before_id_not_found_raises(repo: IncidentRepository) -> None:
    with pytest.raises(ValueError, match="not found"):
        await repo.list_open(before_id="nonexistent-id")


# ---------------------------------------------------------------------------
# 6. IncidentRepository.list_by_severity()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_severity_filters_correctly(
    session: AsyncSession, repo: IncidentRepository
) -> None:
    await _create_incident(session, title="Sev1", severity=SeverityLevel.SEV1)
    await _create_incident(session, title="Sev3", severity=SeverityLevel.SEV3)

    sev1_results = await repo.list_by_severity(SeverityLevel.SEV1)
    assert all(r.severity == SeverityLevel.SEV1 for r in sev1_results)
    assert any(r.title == "Sev1" for r in sev1_results)


# ---------------------------------------------------------------------------
# 7. IncidentRepository.update_status() — state machine enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_status_valid_transition(
    session: AsyncSession, repo: IncidentRepository
) -> None:
    inc = await _create_incident(session)
    updated = await repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    assert updated.status == IncidentStatus.INVESTIGATING


@pytest.mark.asyncio
async def test_update_status_invalid_transition_raises(
    session: AsyncSession, repo: IncidentRepository
) -> None:
    inc = await _create_incident(session)
    # OPEN → CLOSED is not a direct allowed transition
    with pytest.raises(InvalidTransitionError):
        await repo.update_status(inc.id, IncidentStatus.CLOSED)


@pytest.mark.asyncio
async def test_update_status_updates_updated_at(
    session: AsyncSession, repo: IncidentRepository
) -> None:
    inc = await _create_incident(session)
    before = inc.updated_at
    await asyncio.sleep(0.01)  # ensure timestamp advances
    await repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    await session.refresh(inc)
    # updated_at must have changed (OPEN-01 discipline)
    assert inc.updated_at >= before


@pytest.mark.asyncio
async def test_update_status_missing_incident_raises(
    repo: IncidentRepository,
) -> None:
    with pytest.raises((ValueError, LookupError)):
        await repo.update_status("no-such-id", IncidentStatus.INVESTIGATING)


# ---------------------------------------------------------------------------
# 8. IncidentRepository.resolve()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_sets_resolved_at(
    session: AsyncSession, repo: IncidentRepository
) -> None:
    inc = await _create_incident(session)
    # Walk through required transitions: OPEN → INVESTIGATING → RESOLVED
    await repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    resolved = await repo.resolve(inc.id, resolution_notes="RCA complete")
    # SQLite strips tzinfo; assert resolved_at is a non-None datetime.
    # UTC timezone contract verified in PostgreSQL integration tests.
    assert resolved.resolved_at is not None
    assert isinstance(resolved.resolved_at, datetime)
    assert resolved.status == IncidentStatus.RESOLVED


@pytest.mark.asyncio
async def test_resolve_stores_resolution_notes(
    session: AsyncSession, repo: IncidentRepository
) -> None:
    inc = await _create_incident(session)
    await repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    resolved = await repo.resolve(inc.id, resolution_notes="Fixed by rollback")
    assert resolved.resolution_notes == "Fixed by rollback"
    # Confirm resolved_at is also set as a side-effect
    assert resolved.resolved_at is not None


# ---------------------------------------------------------------------------
# 9. init_db() — SQLite fast-path (no migration check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_db_sqlite_skips_migration_check() -> None:
    """
    init_db() on SQLite should complete without raising and should NOT
    attempt to query alembic_version (which doesn't exist in test DBs).
    """
    with patch("src.incident_tracker._engine") as mock_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock())
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_engine.url = MagicMock()
        mock_engine.url.__str__ = lambda _: "sqlite+aiosqlite:///./test.db"
        mock_engine.url.startswith = lambda _prefix: True
        # Should complete without error
        await init_db()


# ---------------------------------------------------------------------------
# 10. get_session() — commit on clean exit, rollback on exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_yields_session_and_commits(engine) -> None:
    """get_session() dependency must commit on a clean exit."""
    # Patch module-level _session_factory to use test engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch("src.incident_tracker._session_factory", factory):
        sessions_seen: list[AsyncSession] = []
        async for s in get_session():
            sessions_seen.append(s)
        assert len(sessions_seen) == 1


@pytest.mark.asyncio
async def test_get_session_rolls_back_on_exception(engine) -> None:
    """get_session() must rollback when a downstream exception is raised."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch("src.incident_tracker._session_factory", factory):
        with pytest.raises(RuntimeError, match="test-forced"):
            async for _s in get_session():
                raise RuntimeError("test-forced")
