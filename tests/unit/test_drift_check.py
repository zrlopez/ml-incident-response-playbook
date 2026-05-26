"""
Unit tests for observability/drift_check.py

Covers:
  - compute_psi: no_drift / minor_drift / major_drift severity
  - compute_psi: ValueError on mismatched bin counts, empty histograms
  - compute_psi: equal distributions → PSI = 0
  - compute_feature_drift: stable / drifted JSD results
  - check_drift_suite: PSI severity propagation
  - check_drift_suite: severity escalation via anomaly_count
  - check_drift_suite: feature drift escalates no_drift → minor
  - check_drift_suite: stable features do not escalate severity
  - DriftSuiteResult fields: drifted_features, notes populated correctly

All tests are synchronous, zero-dependency on DB, network, or Prometheus.
"""
from __future__ import annotations

import math

import pytest

from observability.drift_check import (
    DriftSeverity,
    DriftSuiteResult,
    FeatureDriftResult,
    PsiResult,
    check_drift_suite,
    compute_feature_drift,
    compute_psi,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A perfectly stable distribution: PSI will be ~0
_STABLE_REF = [100.0, 200.0, 300.0, 250.0, 100.0, 50.0]
_STABLE_PROD = [102.0, 198.0, 302.0, 248.0, 99.0, 51.0]  # ~1% deviation

# Minor drift: PSI in [0.10, 0.20)
_MINOR_REF = [100.0, 200.0, 300.0, 250.0, 100.0, 50.0]
_MINOR_PROD = [75.0, 155.0, 280.0, 270.0, 135.0, 85.0]  # ~12% PSI

# Major drift: PSI >= 0.20
_MAJOR_REF = [100.0, 200.0, 300.0, 250.0, 100.0, 50.0]
_MAJOR_PROD = [20.0, 50.0, 100.0, 300.0, 350.0, 180.0]  # heavily shifted


# ---------------------------------------------------------------------------
# compute_psi
# ---------------------------------------------------------------------------

class TestComputePsi:

    def test_stable_distribution_is_no_drift(self):
        result = compute_psi(_STABLE_REF, _STABLE_PROD)
        assert isinstance(result, PsiResult)
        assert result.severity == DriftSeverity.NO_DRIFT
        assert result.psi < 0.10
        assert result.n_bins == 6
        assert "stable" in result.message.lower()

    def test_minor_drift_severity(self):
        result = compute_psi(_MINOR_REF, _MINOR_PROD)
        assert result.severity == DriftSeverity.MINOR
        assert 0.10 <= result.psi < 0.20
        assert "minor" in result.message.lower()

    def test_major_drift_severity(self):
        result = compute_psi(_MAJOR_REF, _MAJOR_PROD)
        assert result.severity == DriftSeverity.MAJOR
        assert result.psi >= 0.20
        assert "major" in result.message.lower()

    def test_equal_distributions_produce_zero_psi(self):
        hist = [50.0, 100.0, 150.0, 100.0, 50.0]
        result = compute_psi(hist, hist)
        # PSI is not exactly 0 due to epsilon, but must be negligible
        assert result.psi < 0.001
        assert result.severity == DriftSeverity.NO_DRIFT

    def test_mismatched_bins_raise_value_error(self):
        with pytest.raises(ValueError, match="same number of bins"):
            compute_psi([10.0, 20.0, 30.0], [10.0, 20.0])

    def test_empty_histogram_raises_value_error(self):
        with pytest.raises(ValueError, match="must not be empty"):
            compute_psi([], [])

    def test_all_zero_reference_raises_value_error(self):
        with pytest.raises(ValueError, match="positive"):
            compute_psi([0.0, 0.0, 0.0], [10.0, 20.0, 30.0])

    def test_all_zero_production_raises_value_error(self):
        with pytest.raises(ValueError, match="positive"):
            compute_psi([10.0, 20.0, 30.0], [0.0, 0.0, 0.0])

    def test_psi_result_is_immutable(self):
        result = compute_psi(_STABLE_REF, _STABLE_PROD)
        with pytest.raises((AttributeError, TypeError)):
            result.psi = 99.0  # type: ignore[misc]

    def test_psi_label_appears_in_message(self):
        result = compute_psi(_STABLE_REF, _STABLE_PROD, label="risk_scorer_v3")
        assert "risk_scorer_v3" in result.message


# ---------------------------------------------------------------------------
# compute_feature_drift
# ---------------------------------------------------------------------------

class TestComputeFeatureDrift:

    def test_stable_feature_not_flagged(self):
        ref = [100.0, 200.0, 300.0, 200.0, 100.0]
        prod = [98.0, 202.0, 299.0, 201.0, 100.0]  # negligible shift
        result = compute_feature_drift("age", ref, prod)
        assert isinstance(result, FeatureDriftResult)
        assert result.drifted is False
        assert result.kl_divergence < 0.10
        assert result.feature == "age"
        assert "stable" in result.message.lower()

    def test_drifted_feature_is_flagged(self):
        ref = [300.0, 200.0, 100.0, 50.0, 10.0]
        prod = [10.0, 50.0, 100.0, 200.0, 300.0]  # reversed
        result = compute_feature_drift("income", ref, prod)
        assert result.drifted is True
        assert result.kl_divergence >= 0.10
        assert "shift" in result.message.lower()

    def test_equal_feature_distributions_not_drifted(self):
        hist = [100.0, 200.0, 300.0, 200.0, 100.0]
        result = compute_feature_drift("credit_score", hist, hist)
        assert result.drifted is False
        assert result.kl_divergence < 0.001

    def test_feature_result_is_immutable(self):
        result = compute_feature_drift("age", [1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        with pytest.raises((AttributeError, TypeError)):
            result.drifted = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# check_drift_suite
# ---------------------------------------------------------------------------

class TestCheckDriftSuite:

    def test_stable_suite_returns_no_drift(self):
        result = check_drift_suite(_STABLE_REF, _STABLE_PROD)
        assert isinstance(result, DriftSuiteResult)
        assert result.overall_severity == DriftSeverity.NO_DRIFT
        assert result.drifted_features == []
        assert result.anomaly_count == 0

    def test_major_psi_returns_major_severity(self):
        result = check_drift_suite(_MAJOR_REF, _MAJOR_PROD)
        assert result.overall_severity == DriftSeverity.MAJOR

    def test_anomaly_count_escalates_to_major(self):
        """Stable PSI + anomaly_count > 0 should escalate to MAJOR."""
        result = check_drift_suite(
            _STABLE_REF, _STABLE_PROD, anomaly_count=2
        )
        assert result.overall_severity == DriftSeverity.MAJOR
        assert result.anomaly_count == 2
        assert any("anomaly" in n.lower() for n in result.notes)

    def test_feature_drift_escalates_no_drift_to_minor(self):
        """Stable PSI + drifted feature should escalate no_drift -> minor."""
        drifted_ref = [300.0, 200.0, 100.0, 50.0, 10.0]
        drifted_prod = [10.0, 50.0, 100.0, 200.0, 300.0]
        result = check_drift_suite(
            _STABLE_REF,
            _STABLE_PROD,
            feature_distributions={"income": (drifted_ref, drifted_prod)},
        )
        assert result.overall_severity == DriftSeverity.MINOR
        assert "income" in result.drifted_features
        assert any("minor" in n.lower() for n in result.notes)

    def test_stable_features_do_not_change_severity(self):
        stable_feat = [100.0, 200.0, 150.0, 100.0, 50.0]
        result = check_drift_suite(
            _STABLE_REF,
            _STABLE_PROD,
            feature_distributions={"age": (stable_feat, stable_feat)},
        )
        assert result.overall_severity == DriftSeverity.NO_DRIFT
        assert result.drifted_features == []

    def test_feature_results_dict_populated(self):
        result = check_drift_suite(
            _MINOR_REF,
            _MINOR_PROD,
            feature_distributions={
                "age": ([100.0, 200.0, 300.0], [100.0, 200.0, 300.0]),
            },
        )
        assert "age" in result.feature_results
        assert isinstance(result.feature_results["age"], FeatureDriftResult)

    def test_empty_feature_distributions_ok(self):
        result = check_drift_suite(
            _STABLE_REF, _STABLE_PROD, feature_distributions={}
        )
        assert result.feature_results == {}

    def test_notes_empty_when_no_escalation(self):
        result = check_drift_suite(_STABLE_REF, _STABLE_PROD)
        assert result.notes == []

    def test_suite_result_is_immutable(self):
        result = check_drift_suite(_STABLE_REF, _STABLE_PROD)
        with pytest.raises((AttributeError, TypeError)):
            result.overall_severity = DriftSeverity.MAJOR  # type: ignore[misc]
