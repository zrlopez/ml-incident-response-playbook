"""test_validation.py — Real unit tests for ETL, anomaly detection, and API models."""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# ETL Tests
# ---------------------------------------------------------------------------
from pipelines.etl_template import (
    ETLSchemaError,
    ETLLoadError,
    _validate_row,
    transform,
    load,
    run_pipeline,
)


class TestValidateRow:
    def _valid_row(self, **overrides) -> dict:
        base = {
            "id": "abc123",
            "timestamp": "2026-05-22T00:00:00Z",
            "event_type": "model_degradation",
            "payload": {"metric": "accuracy", "value": 0.82},
        }
        base.update(overrides)
        return base

    def test_valid_row_passes(self):
        _validate_row(self._valid_row(), 0)  # Should not raise

    def test_missing_required_field_raises(self):
        row = self._valid_row()
        del row["id"]
        with pytest.raises(ETLSchemaError, match="missing required fields"):
            _validate_row(row, 0)

    def test_invalid_event_type_raises(self):
        with pytest.raises(ETLSchemaError, match="invalid event_type"):
            _validate_row(self._valid_row(event_type="unknown_event"), 0)

    def test_payload_must_be_dict(self):
        with pytest.raises(ETLSchemaError, match="payload must be a dict"):
            _validate_row(self._valid_row(payload="not a dict"), 0)

    def test_all_valid_event_types_pass(self):
        valid_types = ["model_degradation", "pipeline_failure", "data_quality", "latency_spike", "cost_spike"]
        for et in valid_types:
            _validate_row(self._valid_row(event_type=et), 0)


class TestTransform:
    def _row(self, **overrides) -> dict:
        base = {
            "id": "row1",
            "timestamp": "2026-05-22T01:00:00Z",
            "event_type": "pipeline_failure",
            "payload": {"stage": "extract"},
        }
        base.update(overrides)
        return base

    def test_valid_rows_pass_through(self):
        rows = [self._row(), self._row(id="row2")]
        result = transform(rows)
        assert len(result) == 2

    def test_invalid_rows_skipped(self):
        rows = [
            self._row(),
            {"id": "bad"},  # Missing fields
            self._row(id="row3"),
        ]
        result = transform(rows)
        assert len(result) == 2  # Bad row silently skipped

    def test_empty_input_returns_empty(self):
        assert transform([]) == []

    def test_all_invalid_returns_empty(self):
        result = transform([{"garbage": True}])
        assert result == []


class TestLoad:
    def _row(self) -> dict:
        return {
            "id": "r1",
            "timestamp": "2026-05-22T02:00:00Z",
            "event_type": "data_quality",
            "payload": {"check": "nulls"},
        }

    def test_load_empty_returns_zero(self):
        assert load([]) == 0

    def test_load_valid_rows_returns_count(self):
        rows = [self._row(), self._row()]
        count = load(rows)
        assert count == 2


class TestRunPipeline:
    def test_pipeline_returns_report(self):
        report = run_pipeline()
        assert report["status"] == "success"
        assert "extracted" in report
        assert "transformed" in report
        assert "loaded" in report
        assert report["elapsed_s"] >= 0


# ---------------------------------------------------------------------------
# Anomaly Detection Tests
# ---------------------------------------------------------------------------
from observability.anomaly_detection import simple_threshold, check_multiple, ThresholdResult


class TestSimpleThreshold:
    def test_no_breach_within_range(self):
        result = simple_threshold(current=1.0, baseline=1.0)
        assert result.breached is False
        assert result.direction is None

    def test_high_breach_detected(self):
        result = simple_threshold(current=1.25, baseline=1.0, pct=0.20)
        assert result.breached is True
        assert result.direction == "high"

    def test_low_breach_detected(self):
        result = simple_threshold(current=0.75, baseline=1.0, pct=0.20, check_low=True)
        assert result.breached is True
        assert result.direction == "low"

    def test_low_breach_ignored_when_disabled(self):
        result = simple_threshold(current=0.75, baseline=1.0, pct=0.20, check_low=False)
        assert result.breached is False

    def test_zero_baseline_raises(self):
        with pytest.raises(ValueError, match="baseline must be non-zero"):
            simple_threshold(current=1.0, baseline=0.0)

    def test_invalid_pct_raises(self):
        with pytest.raises(ValueError, match="pct must be in"):
            simple_threshold(current=1.0, baseline=1.0, pct=0.0)

    def test_exactly_at_threshold_not_breached(self):
        # boundary: current == baseline * (1 + pct) is NOT > threshold, no breach
        result = simple_threshold(current=1.20, baseline=1.0, pct=0.20)
        assert result.breached is False

    def test_result_is_immutable(self):
        result = simple_threshold(1.0, 1.0)
        with pytest.raises(Exception):  # frozen dataclass
            result.breached = True  # type: ignore

    def test_label_in_message(self):
        result = simple_threshold(current=2.0, baseline=1.0, label="error_rate")
        assert "error_rate" in result.message

    def test_pct_deviation_sign(self):
        result = simple_threshold(current=1.5, baseline=1.0, pct=0.30)
        assert result.pct_deviation > 0

        result_low = simple_threshold(current=0.5, baseline=1.0, pct=0.30, check_low=True)
        assert result_low.pct_deviation < 0


class TestCheckMultiple:
    def test_all_within_range(self):
        metrics = {
            "latency": (100.0, 100.0),
            "error_rate": (0.02, 0.02),
        }
        results = check_multiple(metrics)
        assert all(not r.breached for r in results.values())

    def test_one_breach_detected(self):
        metrics = {
            "latency": (200.0, 100.0),  # +100%
            "error_rate": (0.02, 0.02),
        }
        results = check_multiple(metrics)
        assert results["latency"].breached is True
        assert results["error_rate"].breached is False

    def test_empty_metrics_returns_empty(self):
        assert check_multiple({}) == {}

    def test_returns_threshold_result_instances(self):
        metrics = {"cpu": (0.5, 1.0)}
        results = check_multiple(metrics)
        assert isinstance(results["cpu"], ThresholdResult)


# ---------------------------------------------------------------------------
# API Model Validation Tests
# ---------------------------------------------------------------------------
import sys
import os
# Allow importing api module without running the server
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydantic import ValidationError


class TestIncidentCreate:
    def _base(self) -> dict:
        return {
            "title": "Model accuracy degraded",
            "severity": "SEV-2",
            "description": "Accuracy dropped from 0.91 to 0.74 in production.",
            "affected_system": "recommendation-engine",
        }

    def test_valid_incident_passes(self):
        # Import lazily to avoid JWT_SECRET_KEY requirement at test time
        import importlib
        os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
        from api.app import IncidentCreate
        inc = IncidentCreate(**self._base())
        assert inc.severity == "SEV-2"

    def test_invalid_severity_raises(self):
        os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
        from api.app import IncidentCreate
        data = self._base()
        data["severity"] = "SEV-99"
        with pytest.raises(ValidationError, match="severity"):
            IncidentCreate(**data)

    def test_title_too_short_raises(self):
        os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
        from api.app import IncidentCreate
        data = self._base()
        data["title"] = "ab"
        with pytest.raises(ValidationError):
            IncidentCreate(**data)
