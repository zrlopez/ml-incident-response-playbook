"""
Integration tests: IncidentRepository lifecycle enforcement.

These tests exercise the full repository layer against an in-memory SQLite
database (from conftest.sqlite_session fixture). They validate:

  1. Happy-path transitions pass through to the DB
  2. Blocked transitions raise InvalidTransitionError BEFORE any DB write
  3. The CLOSED terminal state cannot be escaped
  4. Idempotent (same-state) updates succeed without error
  5. resolved_at is set automatically on RESOLVED transition
  6. resolved_at is NOT overwritten on subsequent CLOSED transition
  7. Non-existent incident raises ValueError

These tests use the sqlite_session fixture, NOT postgres_session, so they
run without an external database. Full Postgres integration tests that
verify enum type behaviour are in test_repository_postgres.py and require
DATABASE_URL to be set.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from src.domain.incident_lifecycle import IncidentStatus, SeverityLevel
from src.incident_tracker import InvalidTransitionError


# ── Fixtures ─────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def open_incident(incident_repo):
    """Create a fresh OPEN incident and return it."""
    return await incident_repo.create(
        title="Test: model latency spike",
        severity=SeverityLevel.SEV2,
        category="latency",
        owner="oncall-ml",
        description="p99 latency exceeded 2000ms",
    )


# ── Happy-path transitions ──────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_open_to_investigating(incident_repo, open_incident):
    updated = await incident_repo.update_status(
        open_incident.id, IncidentStatus.INVESTIGATING
    )
    assert updated.status == IncidentStatus.INVESTIGATING
    assert updated.resolved_at is None


@pytest.mark.integration
async def test_open_to_mitigating_skips_investigation(incident_repo, open_incident):
    """SEV-1 fast-path: skip investigation, go straight to mitigation."""
    updated = await incident_repo.update_status(
        open_incident.id, IncidentStatus.MITIGATING
    )
    assert updated.status == IncidentStatus.MITIGATING


@pytest.mark.integration
async def test_full_happy_path(incident_repo, open_incident):
    """OPEN -> INVESTIGATING -> MITIGATING -> RESOLVED -> CLOSED."""
    inc = open_incident

    inc = await incident_repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    assert inc.status == IncidentStatus.INVESTIGATING

    inc = await incident_repo.update_status(inc.id, IncidentStatus.MITIGATING)
    assert inc.status == IncidentStatus.MITIGATING

    inc = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)
    assert inc.status == IncidentStatus.RESOLVED
    assert inc.resolved_at is not None

    inc = await incident_repo.update_status(inc.id, IncidentStatus.CLOSED)
    assert inc.status == IncidentStatus.CLOSED


@pytest.mark.integration
async def test_mitigation_can_revert_to_investigating(incident_repo, open_incident):
    """Re-investigation during an active mitigation must be allowed."""
    inc = await incident_repo.update_status(open_incident.id, IncidentStatus.MITIGATING)
    inc = await incident_repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    assert inc.status == IncidentStatus.INVESTIGATING


# ── resolved_at timestamp behaviour ───────────────────────────────────────────────

@pytest.mark.integration
async def test_resolved_at_set_automatically(incident_repo, open_incident):
    before = datetime.now(timezone.utc)
    inc = await incident_repo.update_status(open_incident.id, IncidentStatus.INVESTIGATING)
    inc = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)
    after = datetime.now(timezone.utc)

    assert inc.resolved_at is not None
    assert before <= inc.resolved_at <= after


@pytest.mark.integration
async def test_resolved_at_not_overwritten_on_close(incident_repo, open_incident):
    """resolved_at must remain stable through the RESOLVED -> CLOSED transition."""
    inc = await incident_repo.update_status(open_incident.id, IncidentStatus.INVESTIGATING)
    inc = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)
    resolved_at = inc.resolved_at

    inc = await incident_repo.update_status(inc.id, IncidentStatus.CLOSED)
    assert inc.resolved_at == resolved_at  # Unchanged


@pytest.mark.integration
async def test_resolved_at_accepts_explicit_timestamp(incident_repo, open_incident):
    explicit_ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    inc = await incident_repo.update_status(
        open_incident.id,
        IncidentStatus.INVESTIGATING,
    )
    inc = await incident_repo.update_status(
        inc.id,
        IncidentStatus.RESOLVED,
        resolved_at=explicit_ts,
    )
    assert inc.resolved_at == explicit_ts


# ── Blocked transition enforcement (CR-2) ──────────────────────────────────────────

@pytest.mark.integration
async def test_open_cannot_jump_to_resolved(incident_repo, open_incident):
    with pytest.raises(InvalidTransitionError) as exc_info:
        await incident_repo.update_status(open_incident.id, IncidentStatus.RESOLVED)
    assert "open" in str(exc_info.value)
    assert "resolved" in str(exc_info.value)


@pytest.mark.integration
async def test_resolved_cannot_reopen(incident_repo, open_incident):
    inc = await incident_repo.update_status(open_incident.id, IncidentStatus.INVESTIGATING)
    inc = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)

    with pytest.raises(InvalidTransitionError):
        await incident_repo.update_status(inc.id, IncidentStatus.OPEN)


@pytest.mark.integration
async def test_closed_is_terminal(incident_repo, open_incident):
    """No transition out of CLOSED is permitted."""
    inc = await incident_repo.update_status(open_incident.id, IncidentStatus.INVESTIGATING)
    inc = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)
    inc = await incident_repo.update_status(inc.id, IncidentStatus.CLOSED)

    for bad_target in [
        IncidentStatus.OPEN,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.MITIGATING,
        IncidentStatus.RESOLVED,
    ]:
        with pytest.raises(InvalidTransitionError, match="terminal"):
            await incident_repo.update_status(inc.id, bad_target)


@pytest.mark.integration
async def test_blocked_transition_does_not_write_to_db(
    incident_repo, sqlite_session, open_incident
):
    """
    The database record must be unchanged after a rejected transition.
    This confirms the guard runs BEFORE any mutation is applied.
    """
    original_status = open_incident.status

    with pytest.raises(InvalidTransitionError):
        await incident_repo.update_status(open_incident.id, IncidentStatus.RESOLVED)

    # Re-fetch from session to confirm no write occurred
    from src.incident_tracker import Incident
    from sqlalchemy import select
    result = await sqlite_session.execute(
        select(Incident).where(Incident.id == open_incident.id)
    )
    reloaded = result.scalar_one()
    assert reloaded.status == original_status


# ── Idempotent updates ───────────────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_idempotent_open_update_succeeds(incident_repo, open_incident):
    """Setting status to the same value must not raise."""
    updated = await incident_repo.update_status(open_incident.id, IncidentStatus.OPEN)
    assert updated.status == IncidentStatus.OPEN


# ── Non-existent incident ─────────────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_update_nonexistent_incident_raises_value_error(incident_repo):
    with pytest.raises(ValueError, match="not found"):
        await incident_repo.update_status(
            "00000000-0000-0000-0000-000000000000",
            IncidentStatus.INVESTIGATING,
        )
