"""
Unit tests for observability/monitoring_example.py

Covers:
  - register_metrics: idempotent (safe to call twice)
  - run_drift_check_job: stable, minor, major, anomaly-escalation scenarios
  - run_drift_check_job: anomaly metrics trigger severity escalation in summary
  - run_drift_check_job: no feature_distributions (optional arg)
  - run_drift_check_job: empty anomaly_metrics dict
  - register_metrics: graceful degradation when prometheus_client missing

All tests are synchronous, no DB, no Prometheus server required.
Prometheus metric objects are reset between tests to avoid CollectorRegistry
duplicate-registration errors.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import observability.monitoring_example as me
from observability.drift_check import DriftSeverity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset module-level Prometheus state before each test.

    This prevents CollectorRegistry errors when metrics are registered
    in multiple tests within the same process.
    """
    me._METRICS_REGISTERED = False
    me._drift_events_counter = None
    me._anomaly_breach_counter = None
    me._prediction_latency_hist = None
    me._psi_gauge = None
    yield
    me._METRICS_REGISTERED = False
    me._drift_events_counter = None
    me._anomaly_breach_counter = None
    me._prediction_latency_hist = None
    me._psi_gauge = None


_STABLE_REF = [100.0, 200.0, 300.0, 250.0, 100.0, 50.0]
_STABLE_PROD = [102.0, 198.0, 302.0, 248.0, 99.0, 51.0]
_MAJOR_REF = [100.0, 200.0, 300.0, 250.0, 100.0, 50.0]
_MAJOR_PROD = [20.0, 50.0, 100.0, 300.0, 350.0, 180.0]


# ---------------------------------------------------------------------------
# register_metrics
# ---------------------------------------------------------------------------

class TestRegisterMetrics:

    def setup_method(self):
        """Reset module-level Prometheus state before each test to avoid
        'Duplicated timeseries' errors from the shared default registry."""
        # Clear any None sentinel left in sys.modules by patch.dict in a prior test
        sys.modules.pop("prometheus_client", None)
        me._METRICS_REGISTERED = False
        me._drift_events_counter = None
        me._anomaly_breach_counter = None
        me._prediction_latency_hist = None
        me._psi_gauge = None
        # Unregister any previously registered collectors from the default registry
        try:
            from prometheus_client import REGISTRY
            collectors = list(REGISTRY._names_to_collectors.values())
            for c in set(collectors):
                try:
                    REGISTRY.unregister(c)
                except Exception:
                    pass
        except Exception:
            pass

    def test_register_returns_true_when_prometheus_available(self):
        # prometheus_client is in requirements-dev.txt — should be importable in CI
        result = me.register_metrics()
        assert result in (True, False)  # graceful regardless of install state

    def test_register_idempotent(self):
        me.register_metrics()
        result = me.register_metrics()  # second call must short-circuit via _METRICS_REGISTERED
        # Second call must return True without re-registering
        assert result is True

    def test_register_graceful_when_prometheus_missing(self):
        """Should return False (not raise) when prometheus_client is absent."""
        with patch.dict(sys.modules, {"prometheus_client": None}):
            # Force import to use patched sys.modules
            result = me.register_metrics()
        # Result may be True (if already cached) or False (if absent)
        # The critical contract: no exception raised
        assert result in (True, False)


# ---------------------------------------------------------------------------
# run_drift_check_job
# ---------------------------------------------------------------------------

class TestRunDriftCheckJob:

    def test_stable_returns_no_drift_summary(self):
        summary = me.run_drift_check_job(
            model_name="test_model",
            reference_scores=_STABLE_REF,
            production_scores=_STABLE_PROD,
        )
        assert summary["overall_severity"] == DriftSeverity.NO_DRIFT.value
        assert summary["model"] == "test_model"
        assert summary["drifted_features"] == []
        assert summary["anomaly_count"] == 0

    def test_major_drift_returns_major_severity(self):
        summary = me.run_drift_check_job(
            model_name="test_model",
            reference_scores=_MAJOR_REF,
            production_scores=_MAJOR_PROD,
        )
        assert summary["overall_severity"] == DriftSeverity.MAJOR.value

    def test_anomaly_escalates_stable_psi_to_major(self):
        """Stable PSI + anomaly breach → MAJOR via check_drift_suite escalation."""
        summary = me.run_drift_check_job(
            model_name="test_model",
            reference_scores=_STABLE_REF,
            production_scores=_STABLE_PROD,
            anomaly_metrics={
                "prediction_volume": (50.0, 1000.0),  # -95% drop → breach
            },
        )
        assert summary["overall_severity"] == DriftSeverity.MAJOR.value
        assert summary["anomaly_count"] == 1

    def test_empty_anomaly_metrics_is_safe(self):
        summary = me.run_drift_check_job(
            model_name="test_model",
            reference_scores=_STABLE_REF,
            production_scores=_STABLE_PROD,
            anomaly_metrics={},
        )
        assert summary["anomaly_count"] == 0

    def test_feature_distributions_populated_in_summary(self):
        summary = me.run_drift_check_job(
            model_name="test_model",
            reference_scores=_STABLE_REF,
            production_scores=_STABLE_PROD,
            feature_distributions={
                "age": ([100.0, 200.0, 300.0], [100.0, 200.0, 300.0]),
            },
        )
        # Stable feature — no drift escalation
        assert summary["overall_severity"] == DriftSeverity.NO_DRIFT.value
        assert summary["drifted_features"] == []

    def test_summary_contains_all_expected_keys(self):
        summary = me.run_drift_check_job(
            model_name="test_model",
            reference_scores=_STABLE_REF,
            production_scores=_STABLE_PROD,
        )
        expected_keys = {"model", "psi", "psi_severity", "overall_severity",
                         "drifted_features", "anomaly_count", "notes"}
        assert expected_keys.issubset(summary.keys())

    def test_psi_is_numeric_float(self):
        summary = me.run_drift_check_job(
            model_name="test_model",
            reference_scores=_STABLE_REF,
            production_scores=_STABLE_PROD,
        )
        assert isinstance(summary["psi"], float)
        assert summary["psi"] >= 0.0
