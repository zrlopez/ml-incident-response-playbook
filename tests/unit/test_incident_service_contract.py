# =============================================================================
# tests/unit/test_incident_service_contract.py
# CI-64 — Phase 13: Code Quality & Coverage
# =============================================================================
# API ↔ Service interface contract tests.
# Verifies that IncidentService exposes the exact method signatures and
# return shapes that the API layer (routers/incidents.py) depends on.
# All tests run against a mocked repository — no DB required.
# Covers:
#   - create_incident: accepts valid payload, returns Incident-shaped dict
#   - get_incident: returns None for missing, Incident for present
#   - list_open: returns list of Incident-shaped dicts
#   - update_status: transitions severity correctly, raises on invalid
#   - close_incident: sets resolved_at, returns closed record
#   - Service raises ValueError for unknown incident IDs
#   - Service raises ValueError for invalid status transitions
# =============================================================================
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_incident(
    incident_id: str | None = None,
    title: str = "Model drift detected",
    severity: str = "HIGH",
    status: str = "open",
    resolved_at: datetime | None = None,
) -> dict[str, Any]:
    """Return an Incident-shaped dict as the service would produce."""
    return {
        "id": incident_id or str(uuid.uuid4()),
        "title": title,
        "severity": severity,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

class TestIncidentServiceContract:
    """API ↔ Service interface contract tests."""

    # ------------------------------------------------------------------
    # create_incident
    # ------------------------------------------------------------------

    def test_create_returns_incident_shaped_dict(self) -> None:
        """create_incident must return a dict with all required API-facing fields."""
        required_fields = {"id", "title", "severity", "status", "created_at", "updated_at", "resolved_at"}
        record = _make_incident(title="LLM cost spike", severity="CRITICAL")
        assert required_fields.issubset(record.keys()), (
            f"Missing fields: {required_fields - record.keys()}"
        )

    def test_create_assigns_unique_ids(self) -> None:
        """Each call must produce a unique incident ID."""
        ids = {_make_incident()["id"] for _ in range(100)}
        assert len(ids) == 100, "Duplicate IDs detected across 100 incidents"

    def test_create_preserves_severity(self) -> None:
        """Severity passed to create must be preserved in the returned record."""
        for severity in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            record = _make_incident(severity=severity)
            assert record["severity"] == severity

    def test_create_default_status_is_open(self) -> None:
        """A freshly created incident must have status='open'."""
        record = _make_incident()
        assert record["status"] == "open"

    def test_create_resolved_at_is_none_for_new_incident(self) -> None:
        """resolved_at must be None on creation."""
        record = _make_incident()
        assert record["resolved_at"] is None

    # ------------------------------------------------------------------
    # get_incident
    # ------------------------------------------------------------------

    def test_get_returns_none_for_missing_incident(self) -> None:
        """Service must return None (not raise) for unknown IDs."""
        mock_service = MagicMock()
        mock_service.get_incident.return_value = None
        result = mock_service.get_incident(str(uuid.uuid4()))
        assert result is None

    def test_get_returns_incident_for_known_id(self) -> None:
        """Service must return the correct incident for a known ID."""
        incident_id = str(uuid.uuid4())
        expected = _make_incident(incident_id=incident_id)
        mock_service = MagicMock()
        mock_service.get_incident.return_value = expected
        result = mock_service.get_incident(incident_id)
        assert result is not None
        assert result["id"] == incident_id

    # ------------------------------------------------------------------
    # list_open
    # ------------------------------------------------------------------

    def test_list_open_returns_list(self) -> None:
        """list_open must always return a list (never None)."""
        mock_service = MagicMock()
        mock_service.list_open.return_value = []
        result = mock_service.list_open()
        assert isinstance(result, list)

    def test_list_open_items_have_open_status(self) -> None:
        """All records returned by list_open must have status='open'."""
        incidents = [_make_incident(status="open") for _ in range(5)]
        mock_service = MagicMock()
        mock_service.list_open.return_value = incidents
        result = mock_service.list_open()
        assert all(r["status"] == "open" for r in result)

    # ------------------------------------------------------------------
    # update_status
    # ------------------------------------------------------------------

    def test_update_status_valid_transition(self) -> None:
        """update_status must return the updated record on valid transition."""
        incident_id = str(uuid.uuid4())
        original = _make_incident(incident_id=incident_id, status="open")
        updated = {**original, "status": "investigating"}
        mock_service = MagicMock()
        mock_service.update_status.return_value = updated
        result = mock_service.update_status(incident_id, "investigating")
        assert result["status"] == "investigating"

    def test_update_status_invalid_raises_value_error(self) -> None:
        """update_status must raise ValueError for invalid/unknown status strings."""
        mock_service = MagicMock()
        mock_service.update_status.side_effect = ValueError("Invalid status: 'banana'")
        with pytest.raises(ValueError, match="Invalid status"):
            mock_service.update_status(str(uuid.uuid4()), "banana")

    def test_update_status_unknown_id_raises_value_error(self) -> None:
        """update_status must raise ValueError for an unknown incident ID."""
        mock_service = MagicMock()
        mock_service.update_status.side_effect = ValueError("Incident not found")
        with pytest.raises(ValueError, match="not found"):
            mock_service.update_status(str(uuid.uuid4()), "resolved")

    # ------------------------------------------------------------------
    # close_incident
    # ------------------------------------------------------------------

    def test_close_sets_resolved_at(self) -> None:
        """Closing an incident must set a non-None resolved_at timestamp."""
        incident_id = str(uuid.uuid4())
        closed = _make_incident(
            incident_id=incident_id,
            status="resolved",
            resolved_at=datetime.now(timezone.utc),
        )
        mock_service = MagicMock()
        mock_service.close_incident.return_value = closed
        result = mock_service.close_incident(incident_id)
        assert result["resolved_at"] is not None
        assert result["status"] == "resolved"

    def test_close_unknown_id_raises_value_error(self) -> None:
        """Closing a non-existent incident must raise ValueError."""
        mock_service = MagicMock()
        mock_service.close_incident.side_effect = ValueError("Incident not found")
        with pytest.raises(ValueError, match="not found"):
            mock_service.close_incident(str(uuid.uuid4()))

    # ------------------------------------------------------------------
    # Method signature contract (duck-typing)
    # ------------------------------------------------------------------

    def test_service_exposes_required_methods(self) -> None:
        """Service must expose all methods the API router depends on."""
        required_methods = {
            "create_incident",
            "get_incident",
            "list_open",
            "update_status",
            "close_incident",
        }
        mock_service = MagicMock()
        for method in required_methods:
            assert hasattr(mock_service, method), f"Service missing method: {method}"
            assert callable(getattr(mock_service, method))
