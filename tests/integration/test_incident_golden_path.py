"""
Integration tests: IncidentService golden-path lifecycle.

These tests exercise the full IncidentService -> IncidentRepository -> SQLite
stack using the sqlite_session fixture from conftest.py. They validate the
service boundary -- that IncidentService correctly delegates to and returns
values from IncidentRepository -- covering all four lifecycle scenarios:

  1. Full happy-path: OPEN -> INVESTIGATING -> MITIGATING -> RESOLVED -> CLOSED
  2. SEV-1 fast-path: OPEN -> direct MITIGATING (skip INVESTIGATING)
  3. Governed rejection at service boundary: InvalidTransitionError, DB unchanged
  4. resolved_at stability: set on RESOLVED, not overwritten on CLOSED

These tests use the sqlite_session fixture and do NOT require Postgres.
They belong in tests/integration/ because they exercise the assembled
service layer, not isolated units.

Fixtures sourced from tests/conftest.py:
  sqlite_session  -- function-scoped in-memory SQLite AsyncSession
  (incident_repo is NOT used here; IncidentService is constructed inline
   with _repo swapped for the fixture-backed IncidentRepository)
"""
import pytest
import pytest_asyncio

from src.domain.incident_lifecycle import IncidentStatus, SeverityLevel
from src.incident_tracker import InvalidTransitionError
from src.services.incident_service import IncidentService


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def service(sqlite_session):
    """
    IncidentService wired to the in-memory SQLite session.
    _repo is swapped for the fixture-backed IncidentRepository so the full
    service -> repo -> DB path is exercised without a real Postgres instance.
    Pattern mirrors test_incident_service.py sentinel/_UNSET approach:
    construct via __new__, assign _repo directly.
    """
    from src.incident_tracker import IncidentRepository
    from src.repositories.audit_log_repository import AuditLogRepository
    svc = IncidentService.__new__(IncidentService)
    svc._repo = IncidentRepository(sqlite_session)
    svc._audit = AuditLogRepository(sqlite_session)
    svc._session = sqlite_session
    return svc


@pytest_asyncio.fixture
async def open_incident(service):
    """Create a fresh OPEN incident via the service layer and return it."""
    return await service.open_incident(
        title="SEV-2: feature store latency spike",
        severity=SeverityLevel.SEV2,
        category="latency",
        opened_by="oncall-ml",
        description="p99 exceeded 2000ms on feature retrieval path",
    )


# ── Test 1: Full happy-path ────────────────────────────────────────────────────

@pytest.mark.integration
async def test_full_lifecycle_open_to_closed(service, open_incident):
    """
    Full path: open_incident() -> INVESTIGATING -> MITIGATING -> RESOLVED -> CLOSED.
    Asserts service return values and final DB state at every step.
    """
    inc = open_incident
    assert inc.status == IncidentStatus.OPEN
    assert inc.resolved_at is None

    inc = await service.transition_status(
        incident_id=str(inc.id),
        new_status=IncidentStatus.INVESTIGATING,
        transitioned_by="oncall-ml",
    )
    assert inc.status == IncidentStatus.INVESTIGATING
    assert inc.resolved_at is None

    inc = await service.transition_status(
        incident_id=str(inc.id),
        new_status=IncidentStatus.MITIGATING,
        transitioned_by="oncall-ml",
    )
    assert inc.status == IncidentStatus.MITIGATING
    assert inc.resolved_at is None

    inc = await service.transition_status(
        incident_id=str(inc.id),
        new_status=IncidentStatus.RESOLVED,
        transitioned_by="oncall-ml",
    )
    assert inc.status == IncidentStatus.RESOLVED
    assert inc.resolved_at is not None

    inc = await service.transition_status(
        incident_id=str(inc.id),
        new_status=IncidentStatus.CLOSED,
        transitioned_by="oncall-ml",
    )
    assert inc.status == IncidentStatus.CLOSED

    # Verify final DB state via get_incident round-trip
    fetched = await service.get_incident(str(inc.id))
    assert fetched is not None
    assert fetched.status == IncidentStatus.CLOSED
    assert fetched.resolved_at is not None


# ── Test 2: SEV-1 fast-path ───────────────────────────────────────────────────

@pytest.mark.integration
async def test_sev1_fast_path_open_to_mitigating(service, open_incident):
    """
    SEV-1 fast-path: OPEN -> direct MITIGATING, skipping INVESTIGATING.
    resolved_at must be None mid-path (resolution has not occurred).
    """
    inc = await service.transition_status(
        incident_id=str(open_incident.id),
        new_status=IncidentStatus.MITIGATING,
        transitioned_by="oncall-sev1",
    )
    assert inc.status == IncidentStatus.MITIGATING
    assert inc.resolved_at is None


# ── Test 3: Governed rejection at service boundary ───────────────────────────

@pytest.mark.integration
async def test_invalid_transition_raises_and_db_unchanged(
    service, open_incident, sqlite_session
):
    """
    Bad transition (OPEN -> RESOLVED) raises InvalidTransitionError at the
    service boundary. DB record must be unchanged after the rejection --
    confirms the domain guard runs before any mutation is applied.
    """
    original_status = open_incident.status

    with pytest.raises(InvalidTransitionError):
        await service.transition_status(
            incident_id=str(open_incident.id),
            new_status=IncidentStatus.RESOLVED,
            transitioned_by="oncall-ml",
        )

    # Re-fetch directly from the session to confirm no write occurred
    from src.incident_tracker import Incident
    from sqlalchemy import select
    result = await sqlite_session.execute(
        select(Incident).where(Incident.id == open_incident.id)
    )
    reloaded = result.scalar_one()
    assert reloaded.status == original_status


# ── Test 4: resolved_at stability through RESOLVED -> CLOSED ──────────────────

@pytest.mark.integration
async def test_resolved_at_stable_through_close(service, open_incident):
    """
    resolved_at is set on RESOLVED and must NOT be overwritten on CLOSED.
    Asserted through the service layer, not the repo directly, to confirm
    the service boundary does not inadvertently reset the timestamp.
    """
    inc = await service.transition_status(
        incident_id=str(open_incident.id),
        new_status=IncidentStatus.INVESTIGATING,
        transitioned_by="oncall-ml",
    )
    inc = await service.transition_status(
        incident_id=str(inc.id),
        new_status=IncidentStatus.RESOLVED,
        transitioned_by="oncall-ml",
    )
    resolved_at_snapshot = inc.resolved_at
    assert resolved_at_snapshot is not None

    inc = await service.transition_status(
        incident_id=str(inc.id),
        new_status=IncidentStatus.CLOSED,
        transitioned_by="oncall-ml",
    )
    assert inc.resolved_at == resolved_at_snapshot
