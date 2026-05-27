"""test_etl_validation.py — Unit tests for pipelines/etl_template.py.

All tests are I/O-free: SQLAlchemy, S3, and structlog I/O are mocked.
Duplicate anomaly-detection and API-model tests have been removed;
those concerns live in test_anomaly_detection.py and test_incident_schema.py.
"""
from __future__ import annotations


from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipelines.etl_template import (
    ETLSchemaError,
    REQUIRED_FIELDS,
    VALID_EVENT_TYPES,
    _compute_run_id,
    _hash_pii,
    _infer_event_type,
    _parse_timestamp,
    _validate_row,
    load,
    run_pipeline,
    transform,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _input_row(**overrides: Any) -> dict[str, Any]:
    """Minimal valid ETL *input* row (pre-transform shape)."""
    base: dict[str, Any] = {
        "id": "test-id-001",
        "timestamp": "2026-05-26T00:00:00Z",
        "event_type": "model_degradation",
        "payload": {"metric": "accuracy", "value": 0.82},
    }
    base.update(overrides)
    return base


def _transformed_row(**overrides: Any) -> dict[str, Any]:
    """Valid post-transform row matching the DB INSERT schema."""
    base: dict[str, Any] = {
        "id": "test-id-001",
        "title": "Model Degradation Event",
        "severity": "SEV-1",
        "status": "open",
        "category": "model_degradation",
        "owner": None,
        "description": None,
        "created_at": datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestValidateRow  (12 tests)
# ---------------------------------------------------------------------------

class TestValidateRow:
    def test_valid_row_passes(self) -> None:
        _validate_row(_input_row(), 0)  # must not raise

    def test_missing_id_raises(self) -> None:
        row = _input_row()
        del row["id"]
        with pytest.raises(ETLSchemaError, match="missing required fields"):
            _validate_row(row, 0)

    def test_missing_timestamp_raises(self) -> None:
        row = _input_row()
        del row["timestamp"]
        with pytest.raises(ETLSchemaError, match="missing required fields"):
            _validate_row(row, 0)

    def test_missing_event_type_raises(self) -> None:
        row = _input_row()
        del row["event_type"]
        with pytest.raises(ETLSchemaError, match="missing required fields"):
            _validate_row(row, 0)

    def test_missing_payload_raises(self) -> None:
        row = _input_row()
        del row["payload"]
        with pytest.raises(ETLSchemaError, match="missing required fields"):
            _validate_row(row, 0)

    def test_invalid_event_type_raises(self) -> None:
        with pytest.raises(ETLSchemaError, match="invalid event_type"):
            _validate_row(_input_row(event_type="unknown_event"), 0)

    def test_payload_not_dict_raises(self) -> None:
        with pytest.raises(ETLSchemaError, match="payload must be a dict"):
            _validate_row(_input_row(payload="not-a-dict"), 0)

    def test_payload_list_raises(self) -> None:
        with pytest.raises(ETLSchemaError, match="payload must be a dict"):
            _validate_row(_input_row(payload=[1, 2, 3]), 0)

    def test_all_valid_event_types_pass(self) -> None:
        for et in VALID_EVENT_TYPES:
            _validate_row(_input_row(event_type=et), 0)

    def test_required_fields_constant(self) -> None:
        assert REQUIRED_FIELDS == frozenset({"id", "timestamp", "event_type", "payload"})

    def test_valid_event_types_constant(self) -> None:
        expected = {"model_degradation", "pipeline_failure", "data_quality", "latency_spike", "cost_spike"}  # noqa: E501
        assert VALID_EVENT_TYPES == frozenset(expected)

    def test_error_message_contains_field_name(self) -> None:
        row = _input_row()
        del row["payload"]
        with pytest.raises(ETLSchemaError, match="payload"):
            _validate_row(row, 0)


# ---------------------------------------------------------------------------
# TestParseTimestamp  (7 tests)
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    def test_iso_z_string(self) -> None:
        dt = _parse_timestamp("2026-05-26T12:00:00Z")
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_iso_offset_string(self) -> None:
        dt = _parse_timestamp("2026-05-26T12:00:00+05:30")
        assert dt.tzinfo is not None

    def test_epoch_int(self) -> None:
        dt = _parse_timestamp(0)
        assert dt == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_epoch_float(self) -> None:
        dt = _parse_timestamp(1_000_000.5)
        assert dt.tzinfo == timezone.utc

    def test_aware_datetime_passthrough(self) -> None:
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert _parse_timestamp(aware) is aware

    def test_naive_datetime_gets_utc(self) -> None:
        naive = datetime(2026, 1, 1)
        result = _parse_timestamp(naive)
        assert result.tzinfo == timezone.utc

    def test_bad_string_raises(self) -> None:
        with pytest.raises(ETLSchemaError, match="Cannot parse timestamp"):
            _parse_timestamp("not-a-date")


# ---------------------------------------------------------------------------
# TestComputeRunId  (2 tests)
# ---------------------------------------------------------------------------

class TestComputeRunId:
    def test_deterministic(self) -> None:
        rows = [_input_row()]
        assert _compute_run_id(rows) == _compute_run_id(rows)

    def test_content_sensitive(self) -> None:
        r1 = [_input_row(id="a")]
        r2 = [_input_row(id="b")]
        assert _compute_run_id(r1) != _compute_run_id(r2)


# ---------------------------------------------------------------------------
# TestHashPii  (3 tests)
# ---------------------------------------------------------------------------

class TestHashPii:
    def test_returns_hex_string(self) -> None:
        result = _hash_pii("alice")
        assert len(result) == 64
        int(result, 16)  # must be valid hex

    def test_idempotent(self) -> None:
        assert _hash_pii("bob") == _hash_pii("bob")

    def test_different_inputs_differ(self) -> None:
        assert _hash_pii("alice") != _hash_pii("bob")


# ---------------------------------------------------------------------------
# TestInferEventType  (6 tests)
# ---------------------------------------------------------------------------

class TestInferEventType:
    def test_model_category(self) -> None:
        assert _infer_event_type("model_accuracy") == "model_degradation"

    def test_pipeline_category(self) -> None:
        assert _infer_event_type("pipeline_stage") == "pipeline_failure"

    def test_data_category(self) -> None:
        assert _infer_event_type("data_ingestion") == "data_quality"

    def test_latency_category(self) -> None:
        assert _infer_event_type("latency_p99") == "latency_spike"

    def test_cost_category(self) -> None:
        assert _infer_event_type("cost_overrun") == "cost_spike"

    def test_unknown_falls_back_to_data_quality(self) -> None:
        assert _infer_event_type("completely_unknown") == "data_quality"


# ---------------------------------------------------------------------------
# TestTransform  (7 tests)
# ---------------------------------------------------------------------------

class TestTransform:
    def test_valid_rows_pass_through(self) -> None:
        rows = [_input_row(), _input_row(id="row2")]
        result = transform(rows)
        assert len(result) == 2

    def test_invalid_rows_skipped_silently(self) -> None:
        rows = [_input_row(), {"id": "bad"}, _input_row(id="row3")]
        result = transform(rows)
        assert len(result) == 2

    def test_empty_input_returns_empty(self) -> None:
        assert transform([]) == []

    def test_all_invalid_returns_empty(self) -> None:
        assert transform([{"garbage": True}]) == []

    def test_output_has_db_columns(self) -> None:
        result = transform([_input_row()])
        assert len(result) == 1
        row = result[0]
        for col in ("id", "title", "severity", "status", "category", "created_at", "updated_at"):
            assert col in row, f"Missing column: {col}"

    def test_severity_mapped_from_event_type(self) -> None:
        result = transform([_input_row(event_type="model_degradation")])
        assert result[0]["severity"] == "SEV-1"

    def test_pii_field_hashed(self) -> None:
        row = _input_row(payload={"owner": "alice", "metric": "acc"})
        # Default _PII_HASH_FIELDS = ["owner"]
        result = transform([row])
        # owner is popped from payload and hashed; verify it doesn't survive as plaintext
        desc = result[0].get("description") or ""
        assert "alice" not in desc


# ---------------------------------------------------------------------------
# TestLoad  (3 tests)
# ---------------------------------------------------------------------------

class TestLoad:
    def test_load_empty_returns_zero(self) -> None:
        assert load([]) == 0

    @patch("pipelines.etl_template.sa")
    def test_load_valid_rows_returns_count(self, mock_sa: MagicMock) -> None:
        """load() with valid post-transform rows returns inserted count."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
        mock_sa.create_engine.return_value = mock_engine
        # Patch _check_run_id_exists so it returns False (not already loaded)
        with patch("pipelines.etl_template._check_run_id_exists", return_value=False), \
             patch("pipelines.etl_template._record_run_id"):
            rows = [_transformed_row(), _transformed_row(id="test-id-002")]
            count = load(rows)
        assert count == 2

    def test_load_empty_list_skips_engine(self) -> None:
        """load([]) must not touch SQLAlchemy at all."""
        with patch("pipelines.etl_template.sa") as mock_sa:
            load([])
            mock_sa.create_engine.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunPipeline  (3 tests)
# ---------------------------------------------------------------------------

class TestRunPipeline:
    def _mock_pipeline(self) -> tuple[list, list, int]:
        raw = [_input_row()]
        transformed = [_transformed_row()]
        loaded = 1
        return raw, transformed, loaded

    def test_pipeline_returns_success_report(self) -> None:
        raw, transformed, loaded = self._mock_pipeline()
        with patch("pipelines.etl_template.extract", return_value=raw), \
             patch("pipelines.etl_template.transform", return_value=transformed), \
             patch("pipelines.etl_template.load", return_value=loaded):
            report = run_pipeline()
        assert report["status"] == "success"
        assert report["extracted"] == 1
        assert report["transformed"] == 1
        assert report["loaded"] == 1
        assert report["elapsed_s"] >= 0

    def test_pipeline_report_has_all_keys(self) -> None:
        raw, transformed, loaded = self._mock_pipeline()
        with patch("pipelines.etl_template.extract", return_value=raw), \
             patch("pipelines.etl_template.transform", return_value=transformed), \
             patch("pipelines.etl_template.load", return_value=loaded):
            report = run_pipeline()
        for key in ("status", "extracted", "transformed", "loaded", "elapsed_s"):
            assert key in report

    def test_pipeline_propagates_extract_error(self) -> None:
        with patch("pipelines.etl_template.extract", side_effect=RuntimeError("DB down")):
            with pytest.raises(RuntimeError, match="DB down"):
                run_pipeline()
