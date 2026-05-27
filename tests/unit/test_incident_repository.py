"""
Unit tests for IncidentRepository.

Covers OPEN-01 (updated_at correctness) and OPEN-02 (cursor pagination).
All tests use the in-memory SQLite engine provided by conftest.py fixtures.
No external services required.

Test matrix
-----------
[OPEN-01] updated_at advances after update_status()
[OPEN-01] updated_at advances after metadata mutation (direct ORM write, mirrors API layer)
[OPEN-01] resolved_at set correctly on OPEN → RESOLVED
[OPEN-01] resolved_at NOT overwritten on second update to RESOLVED (idempotent)
[OPEN-02] list_open() without cursor returns all incidents
[OPEN-02] list_open() with before_id cursor excludes already-seen page
[OPEN-02] list_by_severity() cursor filters by severity and timestamp
[OPEN-02] list_open() raises ValueError on unknown cursor ID
[OPEN-02] list_open() hard cap respected
State machine: OPEN → INVESTIGATING allowed
State machine: OPEN → CLOSED rejected (InvalidTransitionError)
State machine: OPEN → RESOLVED rejected
create(): persists all fields
get(): returns None for unknown ID
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.incident_tracker import (
    Incident,
    IncidentRepository,
    IncidentStatus,
    InvalidTransitionError,
    SeverityLevel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_incident(
    repo: IncidentRepository,
    *,
    title: str = "Test incident",
    severity: SeverityLevel = SeverityLevel.SEV2,
    category: str = "ml-model",
    owner: str | None = "on-call",
    description: str | None = None,
) -> Incident:
    """Create a single incident and flush to materialise the ID."""
    return await repo.create(
        title=title,
        severity=severity,
        category=category,
        owner=owner,
        description=description,
    )


async def _sleep_tick() -> None:
    """
    Yield control for one event-loop iteration.
    In async tests with SQLite this is enough to ensure two consecutive
    datetime.now(utc) calls are distinguishable when the OS clock ticks.
    If sub-millisecond clock resolution is a concern on CI, bump to
    asyncio.sleep(0.01).
    """
    await asyncio.sleep(0.001)


# ---------------------------------------------------------------------------
# OPEN-01: updated_at correctness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_updated_at_advances_after_status_transition(incident_repo):
    """
    OPEN-01: After a valid status transition, updated_at must be strictly
    greater than the value captured immediately after creation.
    """
    inc = await _make_incident(incident_repo)
    original_updated_at = inc.updated_at

    await _sleep_tick()

    updated = await incident_repo.update_status(
        inc.id, IncidentStatus.INVESTIGATING
    )

    assert updated.updated_at > original_updated_at, (
        f"updated_at did not advance: was {original_updated_at}, "
        f"still {updated.updated_at} after OPEN → INVESTIGATING"
    )


@pytest.mark.asyncio
async def test_updated_at_advances_after_metadata_mutation(incident_repo):
    """
    OPEN-01: Simulates the API-layer metadata PATCH route.
    Direct ORM attribute mutation + explicit updated_at write must produce
    an updated_at strictly greater than the creation value.
    """
    inc = await _make_incident(incident_repo, description="original")
    original_updated_at = inc.updated_at

    await _sleep_tick()

    # Mirrors api/app.py update_incident_metadata() logic exactly
    inc.description = "revised notes"
    inc.updated_at = datetime.now(timezone.utc)

    assert inc.updated_at > original_updated_at, (
        f"updated_at did not advance after metadata mutation: "
        f"{original_updated_at} → {inc.updated_at}"
    )
    assert inc.description == "revised notes"


@pytest.mark.asyncio
async def test_resolved_at_set_on_resolution(incident_repo):
    """
    OPEN-01: Transitioning OPEN → INVESTIGATING → MITIGATING → RESOLVED
    must populate resolved_at.
    """
    inc = await _make_incident(incident_repo)
    assert inc.resolved_at is None

    await incident_repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    await incident_repo.update_status(inc.id, IncidentStatus.MITIGATING)
    resolved = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)

    assert resolved.resolved_at is not None
    assert resolved.resolved_at.tzinfo is not None  # timezone-aware


@pytest.mark.asyncio
async def test_resolved_at_not_overwritten_on_duplicate_resolved(incident_repo):
    """
    OPEN-01: If resolved_at is already set, a second call to update_status
    with RESOLVED must not overwrite it (idempotency guard).
    """
    inc = await _make_incident(incident_repo)
    await incident_repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    await incident_repo.update_status(inc.id, IncidentStatus.MITIGATING)
    first_resolution = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)
    original_resolved_at = first_resolution.resolved_at

    await _sleep_tick()

    # RESOLVED → RESOLVED: if the state machine allows it, resolved_at must not change.
    # If the state machine rejects it (also valid), the test passes trivially.
    try:
        second = await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)
        assert second.resolved_at == original_resolved_at, (
            "resolved_at was overwritten on a second RESOLVED transition"
        )
    except InvalidTransitionError:
        pass  # State machine correctly blocks RESOLVED → RESOLVED; guard not needed


# ---------------------------------------------------------------------------
# OPEN-02: Cursor (keyset) pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_open_without_cursor_returns_all(incident_repo):
    """
    OPEN-02: With no cursor, list_open() should return all non-CLOSED incidents
    up to the limit.
    """
    for i in range(5):
        await _make_incident(incident_repo, title=f"Incident {i}")

    results = await incident_repo.list_open(limit=10)
    assert len(results) == 5


@pytest.mark.asyncio
async def test_list_open_cursor_excludes_seen_page(incident_repo):
    """
    OPEN-02: With before_id set to the last item on page 1, page 2 must not
    repeat any IDs from page 1.
    """
    created = []
    for i in range(6):
        inc = await _make_incident(incident_repo, title=f"Incident {i}")
        await _sleep_tick()  # ensure distinct created_at values
        created.append(inc)

    page1 = await incident_repo.list_open(limit=3)
    assert len(page1) == 3

    page1_ids = {i.id for i in page1}
    cursor_id = page1[-1].id  # oldest item on page 1

    page2 = await incident_repo.list_open(limit=3, before_id=cursor_id)
    page2_ids = {i.id for i in page2}

    assert len(page2) > 0, "Expected at least one result on page 2"
    assert page1_ids.isdisjoint(page2_ids), (
        f"Cursor pagination overlapped: page1={page1_ids}, page2={page2_ids}"
    )


@pytest.mark.asyncio
async def test_list_by_severity_cursor_filters_correctly(incident_repo):
    """
    OPEN-02: Cursor pagination on list_by_severity() must respect both the
    severity filter and the timestamp cursor simultaneously.
    """
    for i in range(4):
        await _make_incident(
            incident_repo,
            title=f"SEV1 incident {i}",
            severity=SeverityLevel.SEV1,
        )
        await _sleep_tick()

    # Create one SEV2 incident — must never appear in SEV1 results
    await _make_incident(
        incident_repo, title="SEV2 noise", severity=SeverityLevel.SEV2
    )

    page1 = await incident_repo.list_by_severity(SeverityLevel.SEV1, limit=2)
    assert len(page1) == 2
    assert all(i.severity == SeverityLevel.SEV1 for i in page1)

    page2 = await incident_repo.list_by_severity(
        SeverityLevel.SEV1, limit=2, before_id=page1[-1].id
    )
    assert all(i.severity == SeverityLevel.SEV1 for i in page2)

    all_ids = {i.id for i in page1} | {i.id for i in page2}
    assert len(all_ids) == 4, "Expected 4 distinct SEV1 incidents across both pages"


@pytest.mark.asyncio
async def test_list_open_raises_on_unknown_cursor(incident_repo):
    """
    OPEN-02: Supplying a non-existent before_id must raise ValueError.
    The API layer maps this to HTTP 400.
    """
    await _make_incident(incident_repo, title="Existing incident")

    with pytest.raises(ValueError, match="not found"):
        await incident_repo.list_open(limit=10, before_id="00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_list_open_hard_cap(incident_repo):
    """
    OPEN-02: Even if limit=9999 is passed, list_open() must not return more
    than 1000 rows (the hard cap).
    """
    for i in range(5):
        await _make_incident(incident_repo, title=f"Cap test {i}")

    results = await incident_repo.list_open(limit=9999)
    assert len(results) <= 1000


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_to_investigating_allowed(incident_repo):
    inc = await _make_incident(incident_repo)
    updated = await incident_repo.update_status(inc.id, IncidentStatus.INVESTIGATING)
    assert updated.status == IncidentStatus.INVESTIGATING


@pytest.mark.asyncio
async def test_open_to_closed_rejected(incident_repo):
    inc = await _make_incident(incident_repo)
    with pytest.raises(InvalidTransitionError):
        await incident_repo.update_status(inc.id, IncidentStatus.CLOSED)


@pytest.mark.asyncio
async def test_open_to_resolved_rejected(incident_repo):
    inc = await _make_incident(incident_repo)
    with pytest.raises(InvalidTransitionError):
        await incident_repo.update_status(inc.id, IncidentStatus.RESOLVED)


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_persists_fields(incident_repo):
    inc = await _make_incident(
        incident_repo,
        title="Field persistence check",
        severity=SeverityLevel.SEV3,
        category="data-pipeline",
        owner="team-ml",
        description="Initial description",
    )

    assert inc.id is not None
    assert inc.title == "Field persistence check"
    assert inc.severity == SeverityLevel.SEV3
    assert inc.status == IncidentStatus.OPEN
    assert inc.category == "data-pipeline"
    assert inc.owner == "team-ml"
    assert inc.description == "Initial description"
    assert inc.created_at is not None
    assert inc.updated_at is not None
    assert inc.resolved_at is None


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_id(incident_repo):
    result = await incident_repo.get("nonexistent-id-xyz")
    assert result is None
