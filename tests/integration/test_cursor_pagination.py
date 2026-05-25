"""tests/integration/test_cursor_pagination.py

Integration tests for OPEN-02 keyset (cursor) pagination on IncidentRepository.

Coverage targets:
  list_open(limit, before_id)
    - first-page queries (no cursor)
    - multi-page cursor walks
    - limit enforcement and hard-cap
    - status filtering (CLOSED excluded)
    - invalid before_id error path

  list_by_severity(severity, limit, before_id)
    - first-page queries (no cursor)
    - multi-page cursor walks
    - cross-severity contamination checks
    - CLOSED excluded, all non-CLOSED statuses included

  Edge cases
    - cursor to oldest row yields empty next page
    - cursor to newest row yields all remaining rows
    - limit=0 returns empty without error
    - identical created_at timestamps: stable keyset behaviour

All tests run against in-memory SQLite via the `incident_repo` fixture from
tests/conftest.py. No external services required.

Run with:
    pytest tests/integration/test_cursor_pagination.py -m integration
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.incident_tracker import IncidentRepository, IncidentStatus, SeverityLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_T0 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=_UTC)  # anchor timestamp


def _ts(offset_minutes: int = 0) -> datetime:
    """Return anchor timestamp + offset_minutes."""
    return _T0 + timedelta(minutes=offset_minutes)


async def _seed(
    repo: IncidentRepository,
    *,
    n: int,
    severity: SeverityLevel = SeverityLevel.SEV2,
    minutes_apart: int = 1,
    start_offset: int = 0,
    title_prefix: str = "incident",
) -> list[str]:
    """
    Create *n* incidents, each created_at spaced by minutes_apart.

    Returns list of IDs oldest-first so callers can reference by natural index.
    Each incident is flushed in sequence so created_at ordering is deterministic.
    """
    ids: list[str] = []
    for i in range(n):
        inc = await repo._session.run_sync(
            lambda _s, _i=i: None  # placeholder — use async path below
        ) if False else None

        from src.incident_tracker import Incident
        inc = Incident(
            title=f"{title_prefix}-{i}",
            severity=severity,
            status=IncidentStatus.OPEN,
            category="test",
            owner="test-oncall",
            created_at=_ts(start_offset + i * minutes_apart),
            updated_at=_ts(start_offset + i * minutes_apart),
        )
        repo._session.add(inc)
        await repo._session.flush()
        ids.append(inc.id)
    return ids  # oldest first


async def _seed_one(
    repo: IncidentRepository,
    *,
    severity: SeverityLevel = SeverityLevel.SEV2,
    offset_minutes: int = 0,
    title: str = "single",
    status: IncidentStatus = IncidentStatus.OPEN,
) -> str:
    """Seed a single incident and return its ID."""
    from src.incident_tracker import Incident
    inc = Incident(
        title=title,
        severity=severity,
        status=status,
        category="test",
        owner="test-oncall",
        created_at=_ts(offset_minutes),
        updated_at=_ts(offset_minutes),
    )
    repo._session.add(inc)
    await repo._session.flush()
    return inc.id


# ===========================================================================
# TestListOpenFirstPage — no cursor
# ===========================================================================


@pytest.mark.integration
class TestListOpenFirstPage:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self, incident_repo):
        result = await incident_repo.list_open()
        assert result == []

    @pytest.mark.asyncio
    async def test_single_incident_returned(self, incident_repo):
        await _seed_one(incident_repo)
        result = await incident_repo.list_open()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_results_ordered_newest_first(self, incident_repo):
        ids = await _seed(incident_repo, n=5, minutes_apart=1)
        result = await incident_repo.list_open()
        result_ids = [r.id for r in result]
        # ids is oldest-first; result should be newest-first (reversed)
        assert result_ids == list(reversed(ids))

    @pytest.mark.asyncio
    async def test_limit_one_returns_exactly_one(self, incident_repo):
        await _seed(incident_repo, n=5)
        result = await incident_repo.list_open(limit=1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_limit_larger_than_total_returns_all(self, incident_repo):
        await _seed(incident_repo, n=3)
        result = await incident_repo.list_open(limit=100)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_limit_hard_capped_at_1000(self, incident_repo):
        # Seed 5 rows; requesting 9999 should not crash and still returns ≤ 1000
        await _seed(incident_repo, n=5)
        result = await incident_repo.list_open(limit=9999)
        assert len(result) <= 1000
        assert len(result) == 5  # only 5 exist

    @pytest.mark.asyncio
    async def test_closed_incidents_excluded(self, incident_repo):
        await _seed_one(incident_repo, status=IncidentStatus.CLOSED, offset_minutes=10)
        await _seed_one(incident_repo, status=IncidentStatus.OPEN, offset_minutes=11)
        result = await incident_repo.list_open()
        assert all(r.status != IncidentStatus.CLOSED for r in result)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_all_non_closed_statuses_included(self, incident_repo):
        """OPEN, INVESTIGATING, MITIGATING, and RESOLVED all appear; CLOSED does not."""
        statuses = [
            IncidentStatus.OPEN,
            IncidentStatus.INVESTIGATING,
            IncidentStatus.MITIGATING,
            IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED,  # should be excluded
        ]
        for i, st in enumerate(statuses):
            await _seed_one(incident_repo, status=st, offset_minutes=i)
        result = await incident_repo.list_open()
        returned_statuses = {r.status for r in result}
        assert IncidentStatus.CLOSED not in returned_statuses
        assert len(result) == 4


# ===========================================================================
# TestListOpenCursorPagination
# ===========================================================================


@pytest.mark.integration
class TestListOpenCursorPagination:
    @pytest.mark.asyncio
    async def test_two_pages_cover_full_set(self, incident_repo):
        ids = await _seed(incident_repo, n=6, minutes_apart=1)
        # newest-first: ids[-1], ids[-2], ids[-3] on page 1
        page1 = await incident_repo.list_open(limit=3)
        cursor = page1[-1].id
        page2 = await incident_repo.list_open(limit=3, before_id=cursor)
        combined = [r.id for r in page1] + [r.id for r in page2]
        assert set(combined) == set(ids)
        assert len(combined) == 6

    @pytest.mark.asyncio
    async def test_page2_excludes_all_page1_rows(self, incident_repo):
        await _seed(incident_repo, n=8, minutes_apart=1)
        page1 = await incident_repo.list_open(limit=4)
        cursor = page1[-1].id
        page2 = await incident_repo.list_open(limit=4, before_id=cursor)
        page1_ids = {r.id for r in page1}
        page2_ids = {r.id for r in page2}
        assert page1_ids.isdisjoint(page2_ids), "Pages must not overlap"

    @pytest.mark.asyncio
    async def test_no_overlap_between_any_pages(self, incident_repo):
        ids = await _seed(incident_repo, n=9, minutes_apart=1)
        pages: list[list] = []
        cursor = None
        while True:
            page = await incident_repo.list_open(limit=3, before_id=cursor)
            if not page:
                break
            pages.append(page)
            cursor = page[-1].id
        all_ids = [r.id for page in pages for r in page]
        assert len(all_ids) == len(set(all_ids)), "Duplicate IDs across pages"
        assert set(all_ids) == set(ids)

    @pytest.mark.asyncio
    async def test_final_page_returns_empty_when_exhausted(self, incident_repo):
        ids = await _seed(incident_repo, n=4, minutes_apart=1)
        page1 = await incident_repo.list_open(limit=4)
        cursor = page1[-1].id  # oldest row on page 1 = absolute oldest
        page2 = await incident_repo.list_open(limit=4, before_id=cursor)
        assert page2 == []

    @pytest.mark.asyncio
    async def test_cursor_to_last_row_yields_empty_next_page(self, incident_repo):
        ids = await _seed(incident_repo, n=3, minutes_apart=1)
        oldest_id = ids[0]  # smallest offset = oldest
        result = await incident_repo.list_open(limit=10, before_id=oldest_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_before_id_raises_value_error(self, incident_repo):
        with pytest.raises(ValueError, match="not found"):
            await incident_repo.list_open(
                before_id="00000000-0000-0000-0000-000000000000"
            )

    @pytest.mark.asyncio
    async def test_error_message_contains_cursor_id(self, incident_repo):
        bad_id = "deadbeef-dead-dead-dead-deaddeadbeef"
        with pytest.raises(ValueError, match=bad_id):
            await incident_repo.list_open(before_id=bad_id)

    @pytest.mark.asyncio
    async def test_three_page_walk_covers_all_rows(self, incident_repo):
        ids = await _seed(incident_repo, n=9, minutes_apart=2)
        collected: list[str] = []
        cursor = None
        for _ in range(3):  # exactly 3 pages of 3
            page = await incident_repo.list_open(limit=3, before_id=cursor)
            assert len(page) == 3
            collected.extend(r.id for r in page)
            cursor = page[-1].id
        # Page 4 should be empty
        last = await incident_repo.list_open(limit=3, before_id=cursor)
        assert last == []
        assert set(collected) == set(ids)

    @pytest.mark.asyncio
    async def test_single_row_pages_accumulate_correctly(self, incident_repo):
        ids = await _seed(incident_repo, n=5, minutes_apart=1)
        collected: list[str] = []
        cursor = None
        while True:
            page = await incident_repo.list_open(limit=1, before_id=cursor)
            if not page:
                break
            collected.append(page[0].id)
            cursor = page[0].id
        assert collected == list(reversed(ids))  # newest-first order maintained

    @pytest.mark.asyncio
    async def test_closed_row_as_cursor_still_valid_lookup(self, incident_repo):
        """A CLOSED row can be a valid cursor even though it won't appear in results."""
        # Seed CLOSED row at minute 0, OPEN row at minute 5
        closed_id = await _seed_one(
            incident_repo, status=IncidentStatus.CLOSED, offset_minutes=0
        )
        await _seed_one(
            incident_repo, status=IncidentStatus.OPEN, offset_minutes=5
        )
        # Cursor points to the CLOSED row; rows older than it should be empty
        result = await incident_repo.list_open(before_id=closed_id)
        # The CLOSED row is older than the OPEN row, so nothing is older than cursor
        assert result == []


# ===========================================================================
# TestListBySeverityFirstPage — no cursor
# ===========================================================================


@pytest.mark.integration
class TestListBySeverityFirstPage:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self, incident_repo):
        result = await incident_repo.list_by_severity(SeverityLevel.SEV1)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_sev1_returned(self, incident_repo):
        await _seed_one(incident_repo, severity=SeverityLevel.SEV1)
        result = await incident_repo.list_by_severity(SeverityLevel.SEV1)
        assert len(result) == 1
        assert result[0].severity == SeverityLevel.SEV1

    @pytest.mark.asyncio
    async def test_results_ordered_newest_first(self, incident_repo):
        ids = await _seed(
            incident_repo, n=5, severity=SeverityLevel.SEV2, minutes_apart=1
        )
        result = await incident_repo.list_by_severity(SeverityLevel.SEV2)
        result_ids = [r.id for r in result]
        assert result_ids == list(reversed(ids))

    @pytest.mark.asyncio
    async def test_other_severities_excluded(self, incident_repo):
        await _seed_one(incident_repo, severity=SeverityLevel.SEV1, offset_minutes=1)
        await _seed_one(incident_repo, severity=SeverityLevel.SEV2, offset_minutes=2)
        await _seed_one(incident_repo, severity=SeverityLevel.SEV3, offset_minutes=3)
        result = await incident_repo.list_by_severity(SeverityLevel.SEV2)
        assert all(r.severity == SeverityLevel.SEV2 for r in result)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_closed_incidents_excluded(self, incident_repo):
        await _seed_one(
            incident_repo,
            severity=SeverityLevel.SEV1,
            status=IncidentStatus.CLOSED,
            offset_minutes=0,
        )
        await _seed_one(
            incident_repo,
            severity=SeverityLevel.SEV1,
            status=IncidentStatus.OPEN,
            offset_minutes=1,
        )
        result = await incident_repo.list_by_severity(SeverityLevel.SEV1)
        assert all(r.status != IncidentStatus.CLOSED for r in result)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_all_non_closed_statuses_included(self, incident_repo):
        statuses = [
            IncidentStatus.OPEN,
            IncidentStatus.INVESTIGATING,
            IncidentStatus.MITIGATING,
            IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED,
        ]
        for i, st in enumerate(statuses):
            await _seed_one(
                incident_repo,
                severity=SeverityLevel.SEV3,
                status=st,
                offset_minutes=i,
            )
        result = await incident_repo.list_by_severity(SeverityLevel.SEV3)
        assert len(result) == 4
        assert all(r.severity == SeverityLevel.SEV3 for r in result)
        assert IncidentStatus.CLOSED not in {r.status for r in result}

    @pytest.mark.asyncio
    async def test_limit_respected_per_severity(self, incident_repo):
        await _seed(incident_repo, n=10, severity=SeverityLevel.SEV2)
        result = await incident_repo.list_by_severity(SeverityLevel.SEV2, limit=3)
        assert len(result) == 3


# ===========================================================================
# TestListBySeverityCursorPagination
# ===========================================================================


@pytest.mark.integration
class TestListBySeverityCursorPagination:
    @pytest.mark.asyncio
    async def test_two_pages_cover_full_sev2_set(self, incident_repo):
        ids = await _seed(
            incident_repo, n=6, severity=SeverityLevel.SEV2, minutes_apart=1
        )
        page1 = await incident_repo.list_by_severity(SeverityLevel.SEV2, limit=3)
        cursor = page1[-1].id
        page2 = await incident_repo.list_by_severity(
            SeverityLevel.SEV2, limit=3, before_id=cursor
        )
        combined = {r.id for r in page1} | {r.id for r in page2}
        assert combined == set(ids)
        assert len(combined) == 6

    @pytest.mark.asyncio
    async def test_cross_severity_contamination_absent(self, incident_repo):
        """SEV-1 rows must never appear in SEV-2 pagination results."""
        sev1_ids = await _seed(
            incident_repo,
            n=4,
            severity=SeverityLevel.SEV1,
            minutes_apart=1,
            start_offset=0,
        )
        sev2_ids = await _seed(
            incident_repo,
            n=4,
            severity=SeverityLevel.SEV2,
            minutes_apart=1,
            start_offset=10,
        )
        page1 = await incident_repo.list_by_severity(SeverityLevel.SEV2, limit=2)
        cursor = page1[-1].id
        page2 = await incident_repo.list_by_severity(
            SeverityLevel.SEV2, limit=2, before_id=cursor
        )
        all_returned = {r.id for r in page1} | {r.id for r in page2}
        assert all_returned == set(sev2_ids)
        assert not all_returned.intersection(sev1_ids)

    @pytest.mark.asyncio
    async def test_invalid_before_id_raises_value_error(self, incident_repo):
        with pytest.raises(ValueError, match="not found"):
            await incident_repo.list_by_severity(
                SeverityLevel.SEV1,
                before_id="00000000-0000-0000-0000-000000000000",
            )

    @pytest.mark.asyncio
    async def test_three_page_walk_over_sev3(self, incident_repo):
        ids = await _seed(
            incident_repo,
            n=9,
            severity=SeverityLevel.SEV3,
            minutes_apart=2,
        )
        collected: list[str] = []
        cursor = None
        for _ in range(3):
            page = await incident_repo.list_by_severity(
                SeverityLevel.SEV3, limit=3, before_id=cursor
            )
            assert len(page) == 3
            collected.extend(r.id for r in page)
            cursor = page[-1].id
        last = await incident_repo.list_by_severity(
            SeverityLevel.SEV3, limit=3, before_id=cursor
        )
        assert last == []
        assert set(collected) == set(ids)

    @pytest.mark.asyncio
    async def test_severity_cursor_combo_returns_correct_subset(self, incident_repo):
        """Mixed-severity DB: page 2 of SEV-2 excludes all non-SEV-2 and page-1 rows."""
        await _seed(
            incident_repo,
            n=3,
            severity=SeverityLevel.SEV1,
            minutes_apart=1,
            start_offset=0,
        )
        sev2_ids = await _seed(
            incident_repo,
            n=4,
            severity=SeverityLevel.SEV2,
            minutes_apart=1,
            start_offset=10,
        )
        page1 = await incident_repo.list_by_severity(SeverityLevel.SEV2, limit=2)
        cursor = page1[-1].id
        page2 = await incident_repo.list_by_severity(
            SeverityLevel.SEV2, limit=2, before_id=cursor
        )
        returned = {r.id for r in page1} | {r.id for r in page2}
        assert returned == set(sev2_ids)
        assert all(r.severity == SeverityLevel.SEV2 for r in page2)


# ===========================================================================
# TestCursorEdgeCases
# ===========================================================================


@pytest.mark.integration
class TestCursorEdgeCases:
    @pytest.mark.asyncio
    async def test_cursor_to_oldest_row_returns_empty(self, incident_repo):
        ids = await _seed(incident_repo, n=5, minutes_apart=1)
        oldest_id = ids[0]
        result = await incident_repo.list_open(before_id=oldest_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_cursor_to_newest_row_returns_all_others(self, incident_repo):
        ids = await _seed(incident_repo, n=5, minutes_apart=1)
        newest_id = ids[-1]  # largest offset = newest
        result = await incident_repo.list_open(before_id=newest_id)
        assert len(result) == 4
        assert newest_id not in {r.id for r in result}

    @pytest.mark.asyncio
    async def test_identical_created_at_stable(self, incident_repo):
        """
        Two incidents with exactly the same created_at timestamp:
        list_open() must return both on the first page and not duplicate them.
        A cursor pointing to one of them correctly excludes that one (strictly
        less than) but the other is also at the same timestamp so it is also
        excluded — this documents the known keyset tie-breaking behaviour.
        """
        from src.incident_tracker import Incident
        same_ts = _ts(0)
        inc_a = Incident(
            title="twin-a",
            severity=SeverityLevel.SEV2,
            status=IncidentStatus.OPEN,
            category="test",
            owner="test",
            created_at=same_ts,
            updated_at=same_ts,
        )
        inc_b = Incident(
            title="twin-b",
            severity=SeverityLevel.SEV2,
            status=IncidentStatus.OPEN,
            category="test",
            owner="test",
            created_at=same_ts,
            updated_at=same_ts,
        )
        incident_repo._session.add(inc_a)
        incident_repo._session.add(inc_b)
        await incident_repo._session.flush()

        page1 = await incident_repo.list_open()
        assert len(page1) == 2, "Both same-timestamp rows should appear on page 1"
        result_ids = {r.id for r in page1}
        assert inc_a.id in result_ids
        assert inc_b.id in result_ids

    @pytest.mark.asyncio
    async def test_limit_zero_returns_empty(self, incident_repo):
        await _seed(incident_repo, n=5)
        result = await incident_repo.list_open(limit=0)
        assert result == []

    @pytest.mark.asyncio
    async def test_mixing_list_open_and_list_by_severity_cursors_is_safe(self, incident_repo):
        """
        A cursor ID obtained from list_open() can be passed into list_by_severity()
        without raising — the cursor lookup is a global ID fetch, not scoped to
        the filter. The results are filtered by severity as expected.
        """
        sev2_ids = await _seed(
            incident_repo,
            n=4,
            severity=SeverityLevel.SEV2,
            minutes_apart=1,
            start_offset=5,
        )
        await _seed(
            incident_repo,
            n=2,
            severity=SeverityLevel.SEV1,
            minutes_apart=1,
            start_offset=0,
        )
        open_page1 = await incident_repo.list_open(limit=3)
        # Use the cursor from list_open in list_by_severity — must not raise
        cross_cursor = open_page1[-1].id
        result = await incident_repo.list_by_severity(
            SeverityLevel.SEV2, limit=10, before_id=cross_cursor
        )
        # Result must only contain SEV-2 rows older than cross_cursor
        assert all(r.severity == SeverityLevel.SEV2 for r in result)
