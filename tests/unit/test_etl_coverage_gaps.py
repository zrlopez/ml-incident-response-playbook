"""test_etl_coverage_gaps.py - Targets uncovered lines in pipelines/etl_template.py.

Covers:
  - extract() S3 branch (lines 121-153)
  - extract() error propagation (lines 177-194)
  - transform() bad timestamp skip (lines 199-218)
  - transform() severity alias mapping (lines 236-250)
  - transform() unknown payload fields -> description (lines 292-295)
  - load() insert loop / batching (lines 345-373)
  - load() rollback / ETLLoadError path (lines 417-419)
  - run_pipeline() load/transform error propagation (lines 451-460)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipelines.etl_template import (
    ETLLoadError,
    extract,
    load,
    run_pipeline,
    transform,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _input_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "gap-id-001",
        "timestamp": "2026-05-26T00:00:00Z",
        "event_type": "model_degradation",
        "payload": {"metric": "accuracy", "value": 0.9},
    }
    base.update(overrides)
    return base


def _transformed_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "gap-id-001",
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


def _make_s3_client_mock(contents: list, body_json: Any) -> MagicMock:
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [{"Contents": contents}]
    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps(body_json).encode()
    mock_client.get_object.return_value = {"Body": mock_body}
    return mock_client


# ---------------------------------------------------------------------------
# TestExtractS3Branch
# ---------------------------------------------------------------------------

class TestExtractS3Branch:
    def test_s3_branch_called_when_bucket_set(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", "my-bucket"), \
             patch("pipelines.etl_template._extract_from_s3", return_value=[]) as mock_s3:
            result = extract()
        mock_s3.assert_called_once()
        assert result == []

    def test_s3_returns_rows(self) -> None:
        rows = [_input_row(), _input_row(id="gap-id-002")]
        with patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", "my-bucket"), \
             patch("pipelines.etl_template._extract_from_s3", return_value=rows):
            result = extract()
        assert len(result) == 2

    def test_extract_from_s3_reads_json_list(self) -> None:
        json_rows = [_input_row(), _input_row(id="s3-id-002")]
        mock_client = _make_s3_client_mock([{"Key": "incidents/batch1.json"}], json_rows)
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client
        with patch("pipelines.etl_template._S3_BUCKET", "my-bucket"), \
             patch("pipelines.etl_template._S3_PREFIX", "incidents/"), \
             patch.dict(sys.modules, {"boto3": mock_boto3}):
            from pipelines.etl_template import _extract_from_s3
            result = _extract_from_s3()
        assert len(result) == 2

    def test_extract_from_s3_skips_non_json_keys(self) -> None:
        mock_client = _make_s3_client_mock(
            [{"Key": "incidents/notes.txt"}, {"Key": "incidents/data.json"}],
            [_input_row()],
        )
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client
        with patch("pipelines.etl_template._S3_BUCKET", "my-bucket"), \
             patch("pipelines.etl_template._S3_PREFIX", "incidents/"), \
             patch.dict(sys.modules, {"boto3": mock_boto3}):
            from pipelines.etl_template import _extract_from_s3
            result = _extract_from_s3()
        assert mock_client.get_object.call_count == 1
        assert len(result) == 1

    def test_extract_from_s3_single_dict_payload(self) -> None:
        mock_client = _make_s3_client_mock([{"Key": "incidents/single.json"}], _input_row())
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client
        with patch("pipelines.etl_template._S3_BUCKET", "my-bucket"), \
             patch("pipelines.etl_template._S3_PREFIX", "incidents/"), \
             patch.dict(sys.modules, {"boto3": mock_boto3}):
            from pipelines.etl_template import _extract_from_s3
            result = _extract_from_s3()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestExtractErrorPropagation
# ---------------------------------------------------------------------------

class TestExtractErrorPropagation:
    def test_db_failure_raises_runtime_error(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "postgresql://fake"), \
             patch("pipelines.etl_template._extract_from_db", side_effect=Exception("conn refused")):  # noqa: E501
            with pytest.raises(RuntimeError, match="Extract failed"):
                extract()

    def test_s3_failure_raises_runtime_error(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", "my-bucket"), \
             patch("pipelines.etl_template._extract_from_s3", side_effect=Exception("S3 timeout")):
            with pytest.raises(RuntimeError, match="Extract failed"):
                extract()

    def test_synthetic_failure_raises_runtime_error(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "sqlite:///./test.db"), \
             patch("pipelines.etl_template._S3_BUCKET", ""), \
             patch("pipelines.etl_template._extract_synthetic", side_effect=Exception("rng broke")):  # noqa: E501
            with pytest.raises(RuntimeError, match="Extract failed"):
                extract()

    def test_error_message_contains_original_cause(self) -> None:
        with patch("pipelines.etl_template._DATABASE_URL", "postgresql://fake"), \
             patch("pipelines.etl_template._extract_from_db", side_effect=Exception("timeout")):
            with pytest.raises(RuntimeError, match="timeout"):
                extract()


# ---------------------------------------------------------------------------
# TestTransformBadTimestamp
# ---------------------------------------------------------------------------

class TestTransformBadTimestamp:
    def test_bad_timestamp_row_is_skipped(self) -> None:
        rows = [_input_row(timestamp="not-a-date")]
        result = transform(rows)
        assert result == []

    def test_bad_timestamp_does_not_stop_valid_rows(self) -> None:
        rows = [
            _input_row(id="bad", timestamp="garbage"),
            _input_row(id="good"),
        ]
        result = transform(rows)
        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_multiple_bad_timestamps_all_skipped(self) -> None:
        rows = [_input_row(timestamp="nope") for _ in range(3)]
        result = transform(rows)
        assert result == []


# ---------------------------------------------------------------------------
# TestTransformSeverityMapping
# ---------------------------------------------------------------------------

class TestTransformSeverityMapping:
    def _row_with_severity(self, sev: str) -> dict[str, Any]:
        return _input_row(payload={"severity": sev})

    def test_severity_critical_maps_to_sev1(self) -> None:
        result = transform([self._row_with_severity("critical")])
        assert result[0]["severity"] == "SEV-1"

    def test_severity_high_maps_to_sev2(self) -> None:
        result = transform([self._row_with_severity("high")])
        assert result[0]["severity"] == "SEV-2"

    def test_severity_medium_maps_to_sev3(self) -> None:
        result = transform([self._row_with_severity("medium")])
        assert result[0]["severity"] == "SEV-3"

    def test_severity_low_maps_to_sev4(self) -> None:
        result = transform([self._row_with_severity("low")])
        assert result[0]["severity"] == "SEV-4"

    def test_severity_uppercase_critical_maps(self) -> None:
        result = transform([self._row_with_severity("CRITICAL")])
        assert result[0]["severity"] == "SEV-1"

    def test_severity_already_sev_enum_passes_through(self) -> None:
        result = transform([self._row_with_severity("SEV-2")])
        assert result[0]["severity"] == "SEV-2"

    def test_default_severity_from_event_type(self) -> None:
        row = _input_row(event_type="model_degradation", payload={})
        result = transform([row])
        assert result[0]["severity"] == "SEV-1"

    def test_latency_spike_severity_is_valid_enum(self) -> None:
        row = _input_row(event_type="latency_spike", payload={})
        result = transform([row])
        assert result[0]["severity"] in {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}


# ---------------------------------------------------------------------------
# TestTransformUnknownPayloadFields
# ---------------------------------------------------------------------------

class TestTransformUnknownPayloadFields:
    def test_unknown_fields_end_up_in_description(self) -> None:
        row = _input_row(payload={"score": 0.99, "region": "us-east-1"})
        result = transform([row])
        assert result[0]["description"] is not None
        desc = result[0]["description"]
        assert "score" in desc or "region" in desc

    def test_known_fields_promoted_not_leftover(self) -> None:
        row = _input_row(payload={"title": "My Incident", "status": "open"})
        result = transform([row])
        assert result[0]["title"] == "My Incident"
        assert result[0]["status"] == "open"

    def test_empty_payload_gives_none_description(self) -> None:
        row = _input_row(payload={})
        result = transform([row])
        assert result[0]["description"] is None


# ---------------------------------------------------------------------------
# TestLoadInsertLoop
# ---------------------------------------------------------------------------

class TestLoadInsertLoop:
    def _make_engine(self) -> MagicMock:
        mock_conn = MagicMock()
        mock_conn.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
        return mock_engine

    def test_insert_loop_returns_row_count(self) -> None:
        rows = [_transformed_row(id=f"id-{i}") for i in range(5)]
        mock_engine = self._make_engine()
        with patch("pipelines.etl_template.sa") as mock_sa, \
             patch("pipelines.etl_template._check_run_id_exists", return_value=False), \
             patch("pipelines.etl_template._record_run_id"):
            mock_sa.create_engine.return_value = mock_engine
            count = load(rows)
        assert count == 5

    def test_insert_loop_batches_correctly(self) -> None:
        rows = [_transformed_row(id=f"id-{i}") for i in range(5)]
        mock_engine = self._make_engine()
        mock_conn = mock_engine.begin.return_value.__enter__.return_value
        with patch("pipelines.etl_template.sa") as mock_sa, \
             patch("pipelines.etl_template._BATCH_SIZE", 2), \
             patch("pipelines.etl_template._check_run_id_exists", return_value=False), \
             patch("pipelines.etl_template._record_run_id"):
            mock_sa.create_engine.return_value = mock_engine
            mock_sa.text.return_value = MagicMock()
            load(rows)
        # 5 rows / batch 2 = 3 execute calls (2+2+1)
        assert mock_conn.execute.call_count == 3

    def test_already_loaded_run_id_returns_zero(self) -> None:
        rows = [_transformed_row()]
        mock_engine = self._make_engine()
        with patch("pipelines.etl_template.sa") as mock_sa, \
             patch("pipelines.etl_template._check_run_id_exists", return_value=True):
            mock_sa.create_engine.return_value = mock_engine
            count = load(rows)
        assert count == 0


# ---------------------------------------------------------------------------
# TestLoadRollbackPath
# ---------------------------------------------------------------------------

class TestLoadRollbackPath:
    def _make_failing_engine(self) -> MagicMock:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("DB write error")
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
        return mock_engine

    def test_insert_failure_raises_etl_load_error(self) -> None:
        rows = [_transformed_row()]
        mock_engine = self._make_failing_engine()
        with patch("pipelines.etl_template.sa") as mock_sa, \
             patch("pipelines.etl_template._check_run_id_exists", return_value=False):
            mock_sa.create_engine.return_value = mock_engine
            mock_sa.text.return_value = MagicMock()
            with pytest.raises(ETLLoadError, match="Load failed"):
                load(rows)

    def test_load_error_message_contains_run_id(self) -> None:
        rows = [_transformed_row()]
        mock_engine = self._make_failing_engine()
        with patch("pipelines.etl_template.sa") as mock_sa, \
             patch("pipelines.etl_template._check_run_id_exists", return_value=False):
            mock_sa.create_engine.return_value = mock_engine
            mock_sa.text.return_value = MagicMock()
            with pytest.raises(ETLLoadError, match="run_id"):
                load(rows)

    def test_engine_disposed_on_rollback(self) -> None:
        rows = [_transformed_row()]
        mock_engine = self._make_failing_engine()
        with patch("pipelines.etl_template.sa") as mock_sa, \
             patch("pipelines.etl_template._check_run_id_exists", return_value=False):
            mock_sa.create_engine.return_value = mock_engine
            mock_sa.text.return_value = MagicMock()
            with pytest.raises(ETLLoadError):
                load(rows)
        mock_engine.dispose.assert_called()


# ---------------------------------------------------------------------------
# TestRunPipelineEdgeCases
# ---------------------------------------------------------------------------

class TestRunPipelineEdgeCases:
    def test_transform_failure_propagates(self) -> None:
        with patch("pipelines.etl_template.extract", return_value=[{}]), \
             patch("pipelines.etl_template.transform", side_effect=ValueError("bad schema")):
            with pytest.raises(ValueError, match="bad schema"):
                run_pipeline()

    def test_load_failure_propagates(self) -> None:
        with patch("pipelines.etl_template.extract", return_value=[{}]), \
             patch("pipelines.etl_template.transform", return_value=[_transformed_row()]), \
             patch("pipelines.etl_template.load", side_effect=ETLLoadError("insert fail")):
            with pytest.raises(ETLLoadError, match="insert fail"):
                run_pipeline()

    def test_zero_rows_extracted_returns_success(self) -> None:
        with patch("pipelines.etl_template.extract", return_value=[]), \
             patch("pipelines.etl_template.transform", return_value=[]), \
             patch("pipelines.etl_template.load", return_value=0):
            report = run_pipeline()
        assert report["status"] == "success"
        assert report["extracted"] == 0
        assert report["loaded"] == 0
