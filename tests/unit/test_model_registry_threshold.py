"""
tests/unit/test_model_registry_threshold.py
============================================
Parameterized tests for the env-var-configurable anomaly threshold
introduced in ML-01 (docs/REMEDIATION_LOG.md Phase 12).

Covers:
  - Default threshold (0.0) is used when ANOMALY_THRESHOLD is not set
  - Negative threshold (e.g. -0.05) raises precision: borderline-negative
    scores that would have been flagged at 0.0 are now allowed through
  - Positive threshold (e.g. 0.05) raises recall: borderline-positive
    scores that would have been missed at 0.0 are now flagged
  - health() response includes `anomaly_threshold` field reflecting
    the currently active value
  - Invalid ANOMALY_THRESHOLD env value raises a clear ValueError at
    import/init time rather than silently defaulting

All tests use monkeypatch to set/unset ANOMALY_THRESHOLD so they are
completely isolated and do not affect other test sessions.

Note: These tests exercise the *threshold logic* in ModelRegistry, not
the model artifact itself. The registry is patched so that predict()
returns a raw decision-function score that we control, letting us verify
that the is_anomalous flag flips at exactly the right threshold.
"""
from __future__ import annotations

import importlib
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_registry(monkeypatch: pytest.MonkeyPatch, threshold_str: str | None):
    """Set/unset ANOMALY_THRESHOLD, reload the registry module, and return it."""
    if threshold_str is None:
        monkeypatch.delenv("ANOMALY_THRESHOLD", raising=False)
    else:
        monkeypatch.setenv("ANOMALY_THRESHOLD", threshold_str)

    import ml_models.incident_anomaly.registry as reg_module
    importlib.reload(reg_module)
    return reg_module


# ---------------------------------------------------------------------------
# Default threshold
# ---------------------------------------------------------------------------

class TestDefaultThreshold:
    def test_default_threshold_is_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ANOMALY_THRESHOLD is unset, the registry uses 0.0."""
        reg_module = _reload_registry(monkeypatch, None)
        assert reg_module._ANOMALY_THRESHOLD == 0.0

    def test_health_exposes_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ModelRegistry.health() must include anomaly_threshold key."""
        reg_module = _reload_registry(monkeypatch, None)
        registry = reg_module.ModelRegistry()
        # Patch module-level artifact path to avoid real filesystem dependency
        with patch.object(reg_module, "_MODEL_FILE", new=MagicMock()) as mock_path:
            mock_path.exists.return_value = False
            health = registry.health()
        assert "anomaly_threshold" in health, (
            "health() must expose anomaly_threshold so operators can verify "
            "the active value via GET /api/v1/inference/anomaly/health"
        )
        assert health["anomaly_threshold"] == 0.0


# ---------------------------------------------------------------------------
# Threshold behaviour: precision vs recall tradeoffs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "threshold, raw_score, expected_anomalous, description",
    [
        # Default threshold: score exactly at boundary is NOT anomalous
        ("0.0",  0.0,   False, "score == threshold: not anomalous (boundary is exclusive)"),
        # Default threshold: score below boundary IS anomalous
        ("0.0",  -0.01, True,  "score just below 0.0: anomalous at default threshold"),
        # Negative threshold: tightens the gate — borderline negative scores pass through
        ("-0.05", -0.03, False, "score=-0.03 above -0.05 threshold: NOT anomalous (higher precision)"),
        ("-0.05", -0.06, True,  "score=-0.06 below -0.05 threshold: anomalous"),
        # Positive threshold: loosens the gate — catches more borderline positives
        ("0.05",  0.03,  True,  "score=0.03 below 0.05 threshold: anomalous (higher recall)"),
        ("0.05",  0.06,  False, "score=0.06 above 0.05 threshold: NOT anomalous"),
    ],
)
def test_threshold_flips_is_anomalous(
    monkeypatch: pytest.MonkeyPatch,
    threshold: str,
    raw_score: float,
    expected_anomalous: bool,
    description: str,
) -> None:
    """Verify that is_anomalous flips at exactly the configured threshold.

    The invariant is: is_anomalous == (raw_score < threshold)
    This test drives that contract from the registry's public predict() output.
    """
    reg_module = _reload_registry(monkeypatch, threshold)
    registry = reg_module.ModelRegistry()

    # Build a mock sklearn model that returns our controlled raw score
    mock_model = MagicMock()
    mock_model.decision_function.return_value = [raw_score]
    mock_model.predict.return_value = [-1 if raw_score < float(threshold) else 1]

    with patch.object(registry, "_model", new=mock_model):
        result = registry.predict([2, 10, 30.0, 3, 1, 0.1, 20.0])

    assert result["is_anomalous"] == expected_anomalous, (
        f"Failed: {description}\n"
        f"  threshold={threshold}, raw_score={raw_score}, "
        f"  expected is_anomalous={expected_anomalous}, got {result['is_anomalous']}"
    )


# ---------------------------------------------------------------------------
# Health reflects active threshold after env override
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("threshold_str, expected_float", [
    ("0.0",   0.0),
    ("-0.05", -0.05),
    ("0.1",   0.1),
])
def test_health_reflects_env_threshold(
    monkeypatch: pytest.MonkeyPatch,
    threshold_str: str,
    expected_float: float,
) -> None:
    """health() anomaly_threshold must match the ANOMALY_THRESHOLD env var."""
    reg_module = _reload_registry(monkeypatch, threshold_str)
    registry = reg_module.ModelRegistry()
    with patch.object(reg_module, "_MODEL_FILE", new=MagicMock()) as mock_path:
        mock_path.exists.return_value = False
        health = registry.health()
    assert health["anomaly_threshold"] == pytest.approx(expected_float)


# ---------------------------------------------------------------------------
# Invalid threshold value
# ---------------------------------------------------------------------------

def test_invalid_threshold_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-numeric ANOMALY_THRESHOLD must raise ValueError at load time.

    Silent fallback to 0.0 would be dangerous: an operator who intends
    a tighter threshold (e.g. '-0.05') would get default sensitivity
    without any warning.  Fail loud instead.
    """
    monkeypatch.setenv("ANOMALY_THRESHOLD", "not-a-number")
    import ml_models.incident_anomaly.registry as reg_module
    with pytest.raises((ValueError, SystemExit)):
        importlib.reload(reg_module)
