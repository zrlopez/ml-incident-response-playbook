"""
Phase-8 test coverage: IncidentResponse and IncidentListResponse schemas.

Verifies that the Pydantic v2 models serialise correctly from to_dict()
payloads and ORM-like dicts, and that missing required fields raise
ValidationError rather than silently producing partial objects.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.schemas.incident import IncidentListResponse, IncidentResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _full_dict(
    incident_id: str = "abc-123",
    title: str = "Test incident",
    severity: str = "SEV-2",
    status: str = "open",
    category: str = "infra",
    owner: str = "alice",
    description: str = "Something broke",
) -> dict:
    return {
        "id": incident_id,
        "title": title,
        "severity": severity,
        "status": status,
        "category": category,
        "owner": owner,
        "description": description,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "resolved_at": None,
    }


# ---------------------------------------------------------------------------
# IncidentResponse
# ---------------------------------------------------------------------------

class TestIncidentResponseFromDict:
    def test_round_trips_full_to_dict_payload(self):
        d = _full_dict()
        r = IncidentResponse.model_validate(d)
        assert r.id == "abc-123"
        assert r.title == "Test incident"
        assert r.severity == "SEV-2"
        assert r.status == "open"
        assert r.category == "infra"
        assert r.owner == "alice"
        assert r.description == "Something broke"
        assert r.resolved_at is None

    def test_created_at_is_datetime(self):
        r = IncidentResponse.model_validate(_full_dict())
        assert isinstance(r.created_at, datetime)
        assert isinstance(r.updated_at, datetime)


class TestIncidentResponseOptionalFields:
    def test_owner_may_be_none(self):
        d = _full_dict()
        d["owner"] = None
        r = IncidentResponse.model_validate(d)
        assert r.owner is None

    def test_description_may_be_none(self):
        d = _full_dict()
        d["description"] = None
        r = IncidentResponse.model_validate(d)
        assert r.description is None

    def test_resolved_at_may_be_none(self):
        r = IncidentResponse.model_validate(_full_dict())
        assert r.resolved_at is None

    def test_resolved_at_parses_isoformat(self):
        d = _full_dict()
        d["resolved_at"] = NOW.isoformat()
        r = IncidentResponse.model_validate(d)
        assert isinstance(r.resolved_at, datetime)


class TestIncidentResponseValidationError:
    def test_missing_id_raises_validation_error(self):
        d = _full_dict()
        del d["id"]
        with pytest.raises(ValidationError):
            IncidentResponse.model_validate(d)

    def test_missing_title_raises_validation_error(self):
        d = _full_dict()
        del d["title"]
        with pytest.raises(ValidationError):
            IncidentResponse.model_validate(d)

    def test_missing_created_at_raises_validation_error(self):
        d = _full_dict()
        del d["created_at"]
        with pytest.raises(ValidationError):
            IncidentResponse.model_validate(d)


# ---------------------------------------------------------------------------
# IncidentListResponse
# ---------------------------------------------------------------------------

class TestIncidentListResponse:
    def test_populated_page(self):
        items = [IncidentResponse.model_validate(_full_dict(incident_id=f"id-{n}")) for n in range(3)]  # noqa: E501
        resp = IncidentListResponse(incidents=items, next_cursor="id-2", count=3)
        assert resp.count == 3
        assert resp.next_cursor == "id-2"
        assert len(resp.incidents) == 3

    def test_last_page_has_null_cursor(self):
        items = [IncidentResponse.model_validate(_full_dict())]
        resp = IncidentListResponse(incidents=items, next_cursor=None, count=1)
        assert resp.next_cursor is None

    def test_empty_page(self):
        resp = IncidentListResponse(incidents=[], next_cursor=None, count=0)
        assert resp.incidents == []
        assert resp.count == 0
