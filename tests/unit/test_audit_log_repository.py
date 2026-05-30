"""
tests/unit/test_audit_log_repository.py
========================================
Unit tests for AuditLogRepository — Phase 8 (OPEN-06).

All tests use an in-process SQLite database (aiosqlite) via the shared
async session fixture pattern.  No network, no Postgres required.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.incident_tracker import Base, Incident, IncidentStatus, SeverityLevel
from src.models.audit_log import AuditEventType
from src.repositories.audit_log_repository import AuditLogRepository

# ---------------------------------------------------------------------------
# Shared async SQLite engine + session
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(scope="function")
async def engine():
    """Fresh in-memory SQLite DB per test function — avoids scope mismatch."""
    # Import Phase 8 models so their tables are registered on Base.metadata
    import src.models.audit_log  # noqa: F401
    import src.models.model_version  # noqa: F401

    _engine = create_async_engine(TEST_DB_URL, echo=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    await _engine.dispose()

@pytest.fixture()
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()  # isolate each test

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_incident(session: AsyncSession) -> Incident:
    """Insert a minimal Incident row so FK constraints are satisfied."""
    inc = Incident(
        id=str(uuid.uuid4()),
        title="Test incident",
        severity=SeverityLevel.SEV2,
        status=IncidentStatus.OPEN,
        category="test",
    )
    session.add(inc)
    await session.flush()
    return inc

# ---------------------------------------------------------------------------
# log_event
# ---------------------------------------------------------------------------

class TestLogEvent:
    async def test_log_event_creates_row(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)

        row = await repo.log_event(
            incident_id=inc.id,
            event_type=AuditEventType.CREATED,
            actor="alice",
            new_value="open",
        )

        assert row.id is not None
        assert row.incident_id == inc.id
        assert row.event_type == AuditEventType.CREATED
        assert row.actor == "alice"
        assert row.new_value == "open"
        assert row.old_value is None

    async def test_log_event_occurred_at_defaults_to_utcnow(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)
        before = datetime.now(timezone.utc)

        row = await repo.log_event(
            incident_id=inc.id,
            event_type=AuditEventType.STATUS_TRANSITION,
            actor="bob",
            old_value="open",
            new_value="investigating",
        )
        after = datetime.now(timezone.utc)

        assert before <= row.occurred_at.replace(tzinfo=timezone.utc) <= after

    async def test_log_event_accepts_explicit_occurred_at(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        row = await repo.log_event(
            incident_id=inc.id,
            event_type=AuditEventType.METADATA_UPDATE,
            actor="carol",
            occurred_at=ts,
        )

        assert row.occurred_at == ts

    async def test_multiple_events_same_incident(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)

        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.CREATED, actor="a")
        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.STATUS_TRANSITION, actor="b", old_value="open", new_value="investigating")
        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.STATUS_TRANSITION, actor="c", old_value="investigating", new_value="resolved")

        events = await repo.get_events_for_incident(inc.id)
        assert len(events) == 3

# ---------------------------------------------------------------------------
# get_events_for_incident
# ---------------------------------------------------------------------------

class TestGetEventsForIncident:
    async def test_returns_newest_first(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)

        ts1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.CREATED, actor="x", occurred_at=ts1)
        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.STATUS_TRANSITION, actor="y", occurred_at=ts2)

        events = await repo.get_events_for_incident(inc.id)
        assert events[0].occurred_at >= events[1].occurred_at

    async def test_returns_empty_for_unknown_incident(self, session):
        repo = AuditLogRepository(session)
        events = await repo.get_events_for_incident(str(uuid.uuid4()))
        assert events == []

    async def test_limit_respected(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)

        for _ in range(5):
            await repo.log_event(incident_id=inc.id, event_type=AuditEventType.METADATA_UPDATE, actor="z")

        events = await repo.get_events_for_incident(inc.id, limit=3)
        assert len(events) == 3

# ---------------------------------------------------------------------------
# get_status_transitions
# ---------------------------------------------------------------------------

class TestGetStatusTransitions:
    async def test_filters_to_status_transitions_only(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)

        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.CREATED, actor="a")
        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.STATUS_TRANSITION, actor="b", old_value="open", new_value="investigating")
        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.METADATA_UPDATE, actor="c")
        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.STATUS_TRANSITION, actor="d", old_value="investigating", new_value="resolved")

        transitions = await repo.get_status_transitions(inc.id)
        assert len(transitions) == 2
        assert all(t.event_type == AuditEventType.STATUS_TRANSITION for t in transitions)

    async def test_returns_oldest_first_for_timeline(self, session):
        inc = await _make_incident(session)
        repo = AuditLogRepository(session)

        ts1 = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)

        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.STATUS_TRANSITION, actor="a", occurred_at=ts2)
        await repo.log_event(incident_id=inc.id, event_type=AuditEventType.STATUS_TRANSITION, actor="b", occurred_at=ts1)

        transitions = await repo.get_status_transitions(inc.id)
        assert transitions[0].occurred_at <= transitions[1].occurred_at
