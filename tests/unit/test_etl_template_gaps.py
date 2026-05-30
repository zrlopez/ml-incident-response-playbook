"""test_etl_template_gaps.py — Targeted gap-filling tests for pipelines/etl_template.py.

This file adds coverage for branches NOT already exercised by
test_etl_validation.py.  Specifically:

  transform():
    - severity alias mapping (critical/high/medium/low → SEV enum)
    - bad timestamp inside a row causes that row to be skipped
    - payload DB-column promotion (title, status, category, description)
    - leftover payload fields serialised into description
    - id auto-generated when missing from row
    - None category in _infer_event_type falls back to data_quality

  extract():
    - synthetic fallback (CI path, no DB/S3 env vars needed)
    - extract() wraps exceptions as RuntimeError

  _check_run_id_exists() / _record_run_id():
    - both silently handle DB exceptions (table-not-found path)

  load():
    - already-loaded batch returns 0 (idempotency guard)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipelines.etl_template import (
    ETLLoadError,
    _check_run_id_exists,
    _infer_event_type,
    _record_run_id,
    extract,
    load,
    transform,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "gap-test-id",
        "timestamp": "2026-05-26T00:00:00Z",
        "event_type": "model_degradation",
        "payload": {},
    }
    base.update(overrides)
    return base


def _transformed_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "gap-test-id",
        "title": "Model Degradation Event",
        "severity": "SEV-1",
        "status": "open",
        "category": "model_degradation",
        "owner": None,
        "description": None,
        "created_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestTransformSeverityAliases
# ---------------------------------------------------------------------------

class TestTransformSeverityAliases:
    """Inbound 'critical/high/medium/low' severity strings are normalised to SEV enums."""

    def _severity_for(self, inbound: str) -> str:
        row = _row(payload={"severity": inbound})
        result = transform([row])
        assert len(result) == 1
        return result[0]["severity"]

    def test_critical_aliased_to_sev1(self) -> None:
        assert self._severity_for("critical") == "SEV-1"

    def test_high_aliased_to_sev2(self) -> None:
        assert self._severity_for("high") == "SEV-2"

    def test_medium_aliased_to_sev3(self) -> None:
        assert self._severity_for("medium") == "SEV-3"

    def test_low_aliased_to_sev4(self) -> None:
        assert self._severity_for("low") == "SEV-4"

    def test_unknown_severity_passthrough(self) -> None:
        # Unknown aliases are passed through as-is (not mapped)
        result = self._severity_for("CRITICAL_PLUS")
        assert result == "CRITICAL_PLUS"  # not in alias map, returned unchanged

    def test_case_insensitive_alias(self) -> None:
        assert self._severity_for("CRITICAL") == "SEV-1"
        assert self._severity_for("High") == "SEV-2"


# ---------------------------------------------------------------------------
# TestTransformBadTimestampSkipsRow
# ---------------------------------------------------------------------------

class TestTransformBadTimestampSkipsRow:
    """A row with an unparseable timestamp is skipped without crashing."""

    def test_bad_timestamp_row_skipped(self) -> None:
        good = _row(id="good")
        bad = _row(id="bad", timestamp="NOT-A-DATE")
        result = transform([good, bad])
        ids = [r["id"] for r in result]
        assert "good" in ids
        assert "bad" not in ids

    def test_all_bad_timestamps_returns_empty(self) -> None:
        rows = [_row(timestamp="nope"), _row(timestamp="also-nope")]
        assert transform(rows) == []


# ---------------------------------------------------------------------------
# TestTransformPayloadPromotion
# ---------------------------------------------------------------------------

class TestTransformPayloadPromotion:
    """DB-column keys inside payload are promoted to top-level columns."""

    def test_title_promoted(self) -> None:
        row = _row(payload={"title": "My Incident"})
        result = transform([row])
        assert result[0]["title"] == "My Incident"

    def test_status_promoted(self) -> None:
        row = _row(payload={"status": "resolved"})
        result = transform([row])
        assert result[0]["status"] == "resolved"

    def test_category_promoted(self) -> None:
        row = _row(payload={"category": "ml_ops"})
        result = transform([row])
        assert result[0]["category"] == "ml_ops"

    def test_leftover_payload_in_description(self) -> None:
        """Unknown payload keys end up JSON-serialised in description."""
        row = _row(payload={"custom_key": "custom_value"})
        result = transform([row])
        desc = result[0]["description"]
        assert desc is not None
        parsed = json.loads(desc)
        assert "custom_key" in parsed

    def test_empty_payload_description_is_none(self) -> None:
        """An empty payload produces description=None (no leftover keys)."""
        row = _row(payload={})
        result = transform([row])
        assert result[0]["description"] is None

    def test_id_autogenerated_when_missing(self) -> None:
        """Missing 'id' field in input row is auto-populated with a UUID."""
        row = _row()
        del row["id"]
        # Re-add the required fields minus id; _validate_row requires id so
        # this row will fail validation — instead test the uuid path via a
        # row that has id=None or id="" explicitly.
        row["id"] = ""
        result = transform([row])
        assert len(result) == 1
        assert result[0]["id"] != ""  # should be auto-generated UUID


# ---------------------------------------------------------------------------
# TestInferEventTypeNoneCategory
# ---------------------------------------------------------------------------

class TestInferEventTypeNoneCategory:
    """_infer_event_type(None) falls back to data_quality."""

    def test_none_returns_data_quality(self) -> None:
        assert _infer_event_type(None) == "data_quality"

    def test_empty_string_returns_data_quality(self) -> None:
        assert _infer_event_type("") == "data_quality"


# ---------------------------------------------------------------------------
# TestExtractSynthetic
# ---------------------------------------------------------------------------

class TestExtractSynthetic:
    """extract() uses synthetic seed when DATABASE_URL is sqlite and no S3 bucket."""

    def test_extract_returns_list(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", ""):
            rows = extract()
        assert isinstance(rows, list)

    def test_extract_returns_nonempty_rows(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", ""):
            rows = extract()
        assert len(rows) > 0

    def test_extracted_rows_have_required_fields(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", ""):
            rows = extract()
        from pipelines.etl_template import REQUIRED_FIELDS
        for row in rows:
            assert REQUIRED_FIELDS.issubset(row.keys()), f"Row missing fields: {row}"

    def test_extract_wraps_errors_as_runtime_error(self) -> None:
        with patch("pipelines.etl_template._extract_synthetic", side_effect=ValueError("boom")), \
             patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", ""):
            with pytest.raises(RuntimeError, match="Extract failed"):
                extract()


# ---------------------------------------------------------------------------
# TestCheckRunIdExists
# ---------------------------------------------------------------------------

class TestCheckRunIdExists:
    """_check_run_id_exists returns False gracefully when etl_runs table absent."""

    def test_returns_false_when_table_missing(self) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("table not found")
        assert _check_run_id_exists(mock_conn, "some-run-id") is False

    def test_returns_true_when_row_found(self) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (1,)
        assert _check_run_id_exists(mock_conn, "existing-run-id") is True

    def test_returns_false_when_row_not_found(self) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        assert _check_run_id_exists(mock_conn, "missing-run-id") is False


# ---------------------------------------------------------------------------
# TestRecordRunId
# ---------------------------------------------------------------------------

class TestRecordRunId:
    """_record_run_id silently swallows exceptions (etl_runs table may not exist)."""

    def test_silently_ignores_db_exception(self) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("table missing")
        _record_run_id(mock_conn, "run-id", 10)  # must not raise

    def test_calls_execute_when_table_present(self) -> None:
        mock_conn = MagicMock()
        _record_run_id(mock_conn, "run-id", 5)
        mock_conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# TestLoadIdempotency
# ---------------------------------------------------------------------------

class TestLoadIdempotency:
    """load() skips the batch and returns 0 when run_id already recorded."""

    @patch("pipelines.etl_template.sa")
    def test_already_loaded_returns_zero(self, mock_sa: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
        mock_sa.create_engine.return_value = mock_engine

        with patch("pipelines.etl_template._check_run_id_exists", return_value=True):
            result = load([_transformed_row()])

        assert result == 0

    @patch("pipelines.etl_template.sa")
    def test_db_insert_failure_raises_etl_load_error(self, mock_sa: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("DB exploded")
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
        mock_sa.create_engine.return_value = mock_engine

        with patch("pipelines.etl_template._check_run_id_exists", return_value=False):
            with pytest.raises(ETLLoadError, match="Load failed"):
                load([_transformed_row()])
