"""
Phase-8 test coverage: IncidentService unit tests.

All tests use a mock IncidentRepository so no real DB session is required.
The service layer must not perform domain validation itself — it only
translates and delegates. These tests verify the delegation contract.

Cycle 4 additions (2026-05-26):
  R-T01  TestTransitionAuditLog   — transition_status() emits audit log
  R-T02  TestUpdateMetadata       — update_metadata() full contract
  R-T03  TestListOpenUUIDGuard    — before_id UUID validation (R-S05)
  R-T05  TestKeyStoreSizeFloor    — generate() 2048-bit minimum (R-A02)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
    inc.resolution_notes = None
    inc.updated_at = None
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
    mock_session.flush = AsyncMock()
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
    # expose session on repo so update_metadata can reach _session
    repo._session = mock_session
    return service, repo


# ---------------------------------------------------------------------------
# open_incident
# ---------------------------------------------------------------------------

class TestIncidentServiceOpenIncident:
    def test_open_incident_delegates_to_repo(self):
        expected = _make_incident()
        service, repo = _service_with_mock_repo(create_return=expected)

        result = asyncio.run(
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
        asyncio.run(
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
        asyncio.run(
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
        result = asyncio.run(
            service.get_incident("abc")
        )
        repo.get.assert_awaited_once_with("abc")
        assert result is inc

    def test_returns_none_when_not_found(self):
        service, repo = _service_with_mock_repo(get_return=None)
        result = asyncio.run(
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
        result = asyncio.run(
            service.list_open(limit=3, before_id="00000000-0000-0000-0000-000000000001")
        )
        repo.list_open.assert_awaited_once_with(limit=3, before_id="00000000-0000-0000-0000-000000000001")  # noqa: E501
        assert result == incidents

    def test_list_open_empty_page_is_valid(self):
        service, repo = _service_with_mock_repo(list_open_return=[])
        result = asyncio.run(
            service.list_open(limit=50)
        )
        assert result == []

    def test_list_open_propagates_bad_cursor_value_error(self):
        service, _ = _service_with_mock_repo(
            list_open_side_effect=ValueError("Cursor 'bad-id' not found.")
        )
        with pytest.raises(ValueError, match="bad-id"):
            asyncio.run(
                service.list_open(limit=10, before_id="bad-id")
            )


# ---------------------------------------------------------------------------
# transition_status
# ---------------------------------------------------------------------------

class TestIncidentServiceTransition:
    def test_transition_delegates_to_repo(self):
        inc = _make_incident(status=IncidentStatus.INVESTIGATING)
        service, repo = _service_with_mock_repo(update_status_return=inc)
        result = asyncio.run(
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
        err = InvalidTransitionError("CLOSED \u2192 OPEN is not allowed")
        service, _ = _service_with_mock_repo(update_status_side_effect=err)
        with pytest.raises(InvalidTransitionError, match="CLOSED"):
            asyncio.run(
                service.transition_status(
                    incident_id="abc",
                    new_status=IncidentStatus.OPEN,
                    transitioned_by="admin",
                )
            )


# ---------------------------------------------------------------------------
# R-T01 — transition_status() emits structured audit log  (Cycle 4)
# ---------------------------------------------------------------------------

class TestTransitionAuditLog:
    """R-T01: transition_status() must emit 'incident.status_transitioned' audit event."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_audit_log_emitted_on_successful_transition(self):
        inc = _make_incident(status=IncidentStatus.INVESTIGATING)
        service, repo = _service_with_mock_repo(update_status_return=inc)

        mock_log = MagicMock()
        with patch("src.services.incident_service.log", mock_log):
            self._run(
                service.transition_status(
                    incident_id="inc-audit-01",
                    new_status=IncidentStatus.INVESTIGATING,
                    transitioned_by="ops-bob",
                )
            )

        mock_log.info.assert_called_once()
        call_args = mock_log.info.call_args
        # positional event name
        assert call_args.args[0] == "incident.status_transitioned"
        kwargs = call_args.kwargs
        assert kwargs["log_type"] == "audit"
        assert kwargs["incident_id"] == "inc-audit-01"
        assert kwargs["new_status"] == IncidentStatus.INVESTIGATING.value
        assert kwargs["transitioned_by"] == "ops-bob"

    def test_audit_log_not_emitted_on_failed_transition(self):
        """If repo raises, the audit log must NOT fire."""
        err = InvalidTransitionError("bad transition")
        service, _ = _service_with_mock_repo(update_status_side_effect=err)

        mock_log = MagicMock()
        with patch("src.services.incident_service.log", mock_log):
            with pytest.raises(InvalidTransitionError):
                self._run(
                    service.transition_status(
                        incident_id="inc-fail",
                        new_status=IncidentStatus.OPEN,
                        transitioned_by="admin",
                    )
                )

        mock_log.info.assert_not_called()


# ---------------------------------------------------------------------------
# R-T02 — update_metadata() full contract  (Cycle 4)
# ---------------------------------------------------------------------------

class TestUpdateMetadata:
    """R-T02: update_metadata() — severity coercion, notes, no-op, error paths, audit log."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_severity_coercion_applied_to_record(self):
        inc = _make_incident()
        service, repo = _service_with_mock_repo(get_return=inc)
        self._run(
            service.update_metadata(
                incident_id="inc-001",
                severity="SEV-1",
                updated_by="admin",
            )
        )
        assert inc.severity == SeverityLevel.SEV1

    def test_resolution_notes_written_to_record(self):
        inc = _make_incident()
        service, repo = _service_with_mock_repo(get_return=inc)
        self._run(
            service.update_metadata(
                incident_id="inc-001",
                resolution_notes="Root cause: upstream timeout.",
                updated_by="alice",
            )
        )
        assert inc.resolution_notes == "Root cause: upstream timeout."

    def test_no_op_returns_record_without_flush(self):
        inc = _make_incident()
        service, repo = _service_with_mock_repo(get_return=inc)
        result = self._run(
            service.update_metadata(
                incident_id="inc-001",
                updated_by="alice",
            )
        )
        assert result is inc
        repo._session.flush.assert_not_awaited()

    def test_missing_incident_raises_value_error(self):
        service, repo = _service_with_mock_repo(get_return=None)
        with pytest.raises(ValueError, match="not found"):
            self._run(
                service.update_metadata(
                    incident_id="ghost-id",
                    severity="SEV-2",
                    updated_by="admin",
                )
            )

    def test_invalid_severity_raises_value_error(self):
        inc = _make_incident()
        service, repo = _service_with_mock_repo(get_return=inc)
        with pytest.raises(ValueError, match="Invalid severity"):
            self._run(
                service.update_metadata(
                    incident_id="inc-001",
                    severity="BANANA",
                    updated_by="admin",
                )
            )

    def test_audit_log_emitted_with_changes(self):
        inc = _make_incident()
        service, repo = _service_with_mock_repo(get_return=inc)
        mock_log = MagicMock()
        with patch("src.services.incident_service.log", mock_log):
            self._run(
                service.update_metadata(
                    incident_id="inc-001",
                    severity="SEV-3",
                    updated_by="ops-alice",
                )
            )
        mock_log.info.assert_called_once()
        call_args = mock_log.info.call_args
        assert call_args.args[0] == "incident.metadata_updated"
        kwargs = call_args.kwargs
        assert kwargs["log_type"] == "audit"
        assert kwargs["incident_id"] == "inc-001"
        assert "severity" in kwargs["changes"]
        assert kwargs["updated_by"] == "ops-alice"

    def test_flush_called_when_changes_present(self):
        inc = _make_incident()
        service, repo = _service_with_mock_repo(get_return=inc)
        self._run(
            service.update_metadata(
                incident_id="inc-001",
                resolution_notes="Fixed.",
                updated_by="alice",
            )
        )
        repo._session.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# R-T03 — list_open() UUID guard (R-S05)  (Cycle 4)
# ---------------------------------------------------------------------------

class TestListOpenUUIDGuard:
    """R-T03: malformed before_id must raise ValueError before repo is called."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_malformed_uuid_raises_before_repo_called(self):
        service, repo = _service_with_mock_repo(list_open_return=[])
        with pytest.raises(ValueError, match="RFC 4122"):
            self._run(service.list_open(limit=10, before_id="not-a-uuid"))
        repo.list_open.assert_not_awaited()

    def test_empty_string_cursor_treated_as_none(self):
        """before_id=None bypasses validation entirely."""
        service, repo = _service_with_mock_repo(list_open_return=[])
        self._run(service.list_open(limit=10, before_id=None))
        repo.list_open.assert_awaited_once_with(limit=10, before_id=None)

    def test_valid_uuid_passes_through_to_repo(self):
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        service, repo = _service_with_mock_repo(list_open_return=[])
        self._run(service.list_open(limit=5, before_id=valid_uuid))
        repo.list_open.assert_awaited_once_with(limit=5, before_id=valid_uuid)

    def test_short_alphanumeric_id_raises(self):
        service, repo = _service_with_mock_repo(list_open_return=[])
        with pytest.raises(ValueError):
            self._run(service.list_open(limit=10, before_id="abc123"))
        repo.list_open.assert_not_awaited()

    def test_sql_injection_attempt_raises(self):
        service, repo = _service_with_mock_repo(list_open_return=[])
        with pytest.raises(ValueError):
            self._run(
                service.list_open(
                    limit=10,
                    before_id="'; DROP TABLE incidents; --",
                )
            )
        repo.list_open.assert_not_awaited()


# ---------------------------------------------------------------------------
# R-T05 — RS256KeyStore.generate() 2048-bit minimum (R-A02)  (Cycle 4)
# ---------------------------------------------------------------------------

class TestKeyStoreSizeFloor:
    """R-T05: generate() must reject key_size < 2048; accept 2048 and 4096."""

    def test_sub_2048_raises_value_error(self):
        from src.auth.key_store import RS256KeyStore
        with pytest.raises(ValueError, match="minimum"):
            RS256KeyStore.generate(key_size=1024)

    def test_1_bit_raises_value_error(self):
        from src.auth.key_store import RS256KeyStore
        with pytest.raises(ValueError, match="minimum"):
            RS256KeyStore.generate(key_size=1)

    def test_2047_raises_value_error(self):
        from src.auth.key_store import RS256KeyStore
        with pytest.raises(ValueError, match="minimum"):
            RS256KeyStore.generate(key_size=2047)

    def test_2048_succeeds_and_sign_verify_roundtrip(self):
        from src.auth.key_store import RS256KeyStore
        store = RS256KeyStore.generate(key_size=2048)
        assert store is not None
        assert store.key_id
        token, jti, ttl = store.sign_token({"sub": "cycle4-test"})
        payload = store.verify_token(token)
        assert payload["sub"] == "cycle4-test"
        assert payload["jti"] == jti

    def test_4096_succeeds(self):
        from src.auth.key_store import RS256KeyStore
        store = RS256KeyStore.generate(key_size=4096)
        assert store is not None
        token, _, _ = store.sign_token({"sub": "test"})
        payload = store.verify_token(token)
        assert payload["sub"] == "test"
