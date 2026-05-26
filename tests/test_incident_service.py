"""
Phase-8 test coverage: IncidentService unit tests.

All tests use a mock IncidentRepository so no real DB session is required.
The service layer must not perform domain validation itself — it only
translates and delegates. These tests verify the delegation contract.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.incident_lifecycle import IncidentStatus, SeverityLevel
from src.incident_tracker import InvalidTransitionError
from src.services.incident_service import IncidentService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNSET = object()  # sentinel so callers can explicitly pass None as a return value


def _make_incident(
    incident_id: str = "inc-001",
    title: str = "Latency spike",
    severity: SeverityLevel = SeverityLevel.SEV2,
    status: IncidentStatus = IncidentStatus.OPEN,
    category: str = "performance",
    owner: str = "alice",
    description: str | None = None,
):
    """Return a lightweight MagicMock that mimics an Incident ORM record."""
    inc = MagicMock()
    inc.id = incident_id
    inc.title = title
    inc.severity = severity
    inc.status = status
    inc.category = category
    inc.owner = owner
    inc.description = description
    return inc


def _service_with_mock_repo(
    *,
    create_return=_UNSET,
    get_return=_UNSET,
    list_open_return=_UNSET,
    update_status_return=_UNSET,
    update_status_side_effect=_UNSET,
    list_open_side_effect=_UNSET,
):
    """Build an IncidentService wired to a patched IncidentRepository."""
    mock_session = MagicMock()
    service = IncidentService(mock_session)
    repo = AsyncMock()
    if create_return is not _UNSET:
        repo.create.return_value = create_return
    if get_return is not _UNSET:
        repo.get.return_value = get_return
    if list_open_return is not _UNSET:
        repo.list_open.return_value = list_open_return
    if update_status_return is not _UNSET:
        repo.update_status.return_value = update_status_return
    if update_status_side_effect is not _UNSET:
        repo.update_status.side_effect = update_status_side_effect
    if list_open_side_effect is not _UNSET:
        repo.list_open.side_effect = list_open_side_effect
    service._repo = repo
    return service, repo


# ---------------------------------------------------------------------------
# open_incident
# ---------------------------------------------------------------------------

class TestIncidentServiceOpenIncident:
    def test_open_incident_delegates_to_repo(self):
        expected = _make_incident()
        service, repo = _service_with_mock_repo(create_return=expected)

        result = asyncio.get_event_loop().run_until_complete(
            service.open_incident(
                title="Latency spike",
                severity=SeverityLevel.SEV2,
                category="performance",
                opened_by="alice",
                description="p99 > 2s for 5 minutes",
            )
        )

        repo.create.assert_awaited_once()
        call_kwargs = repo.create.call_args.kwargs
        assert call_kwargs["title"] == "Latency spike"
        assert call_kwargs["severity"] == SeverityLevel.SEV2
        assert call_kwargs["category"] == "performance"
        assert result is expected

    def test_open_incident_owner_defaults_to_opened_by(self):
        service, repo = _service_with_mock_repo(create_return=_make_incident())
        asyncio.get_event_loop().run_until_complete(
            service.open_incident(
                title="Auth outage",
                severity=SeverityLevel.SEV1,
                category="auth",
                opened_by="bob",
            )
        )
        kwargs = repo.create.call_args.kwargs
        assert kwargs["owner"] == "bob"

    def test_open_incident_explicit_owner_overrides_default(self):
        service, repo = _service_with_mock_repo(create_return=_make_incident())
        asyncio.get_event_loop().run_until_complete(
            service.open_incident(
                title="Auth outage",
                severity=SeverityLevel.SEV1,
                category="auth",
                opened_by="bob",
                owner="oncall-engineer",
            )
        )
        kwargs = repo.create.call_args.kwargs
        assert kwargs["owner"] == "oncall-engineer"


# ---------------------------------------------------------------------------
# get_incident
# ---------------------------------------------------------------------------

class TestIncidentServiceGetIncident:
    def test_returns_record_when_found(self):
        inc = _make_incident(incident_id="abc")
        service, repo = _service_with_mock_repo(get_return=inc)
        result = asyncio.get_event_loop().run_until_complete(
            service.get_incident("abc")
        )
        repo.get.assert_awaited_once_with("abc")
        assert result is inc

    def test_returns_none_when_not_found(self):
        service, repo = _service_with_mock_repo(get_return=None)
        result = asyncio.get_event_loop().run_until_complete(
            service.get_incident("missing")
        )
        assert result is None


# ---------------------------------------------------------------------------
# list_open
# ---------------------------------------------------------------------------

class TestIncidentServiceListOpen:
    def test_list_open_passes_limit_and_cursor(self):
        incidents = [_make_incident(incident_id=f"i{n}") for n in range(3)]
        service, repo = _service_with_mock_repo(list_open_return=incidents)
        result = asyncio.get_event_loop().run_until_complete(
            service.list_open(limit=3, before_id="cursor-id")
        )
        repo.list_open.assert_awaited_once_with(limit=3, before_id="cursor-id")
        assert result == incidents

    def test_list_open_empty_page_is_valid(self):
        service, repo = _service_with_mock_repo(list_open_return=[])
        result = asyncio.get_event_loop().run_until_complete(
            service.list_open(limit=50)
        )
        assert result == []

    def test_list_open_propagates_bad_cursor_value_error(self):
        service, _ = _service_with_mock_repo(
            list_open_side_effect=ValueError("Cursor 'bad-id' not found.")
        )
        with pytest.raises(ValueError, match="bad-id"):
            asyncio.get_event_loop().run_until_complete(
                service.list_open(limit=10, before_id="bad-id")
            )


# ---------------------------------------------------------------------------
# transition_status
# ---------------------------------------------------------------------------

class TestIncidentServiceTransition:
    def test_transition_delegates_to_repo(self):
        inc = _make_incident(status=IncidentStatus.INVESTIGATING)
        service, repo = _service_with_mock_repo(update_status_return=inc)
        result = asyncio.get_event_loop().run_until_complete(
            service.transition_status(
                incident_id="abc",
                new_status=IncidentStatus.INVESTIGATING,
                transitioned_by="operator",
            )
        )
        repo.update_status.assert_awaited_once_with(
            incident_id="abc",
            new_status=IncidentStatus.INVESTIGATING,
        )
        assert result is inc

    def test_invalid_transition_propagates_unchanged(self):
        # Error message derived from validate_status_transition() in
        # src/domain/incident_lifecycle.py — match pattern reflects the real
        # domain reason string, not a hand-written stub.
        err = InvalidTransitionError(
            "invalid incident state transition: closed -> open. "
            "Valid targets from 'closed': [none (terminal state)]."
        )
        service, _ = _service_with_mock_repo(update_status_side_effect=err)
        with pytest.raises(
            InvalidTransitionError,
            match="invalid incident state transition: closed",
        ):
            asyncio.get_event_loop().run_until_complete(
                service.transition_status(
                    incident_id="abc",
                    new_status=IncidentStatus.OPEN,
                    transitioned_by="admin",
                )
            )
