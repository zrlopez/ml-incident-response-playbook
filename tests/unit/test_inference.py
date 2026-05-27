"""
tests/unit/test_inference.py
=============================
Unit tests for ML inference layer:
  - AnomalyRequest / AnomalyResponse schema validation
  - ModelRegistry.predict() contract
  - ModelRegistry.health() contract
  - Inference router (mocked registry)

No actual model artifact is required for the schema or mock tests.
The registry tests use a real artifact if present, else skip.

Attribution note:
    Model under test uses scikit-learn IsolationForest (BSD-3-Clause).
    See MODEL_CARD.md for full attribution.
"""
from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from ml_models.incident_anomaly.schema import AnomalyRequest, AnomalyResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_VALID_PAYLOAD: dict = {
    "severity_numeric": 1,
    "alert_count": 142,
    "time_to_detect_minutes": 4.7,
    "affected_services": 8,
    "on_call_escalations": 3,
    "duplicate_alert_ratio": 0.35,
    "blast_radius_pct": 62.0,
}

_ARTIFACT = (
    Path(__file__).parent.parent.parent
    / "ml_models" / "incident_anomaly" / "artifacts"
    / "isolation_forest_v1.joblib"
)


# ---------------------------------------------------------------------------
# Schema: AnomalyRequest
# ---------------------------------------------------------------------------
class TestAnomalyRequest:
    def test_valid_payload_parses(self) -> None:
        req = AnomalyRequest(**_VALID_PAYLOAD)
        assert req.severity_numeric == 1
        assert req.alert_count == 142

    def test_severity_below_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyRequest(**{**_VALID_PAYLOAD, "severity_numeric": 0})

    def test_severity_above_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyRequest(**{**_VALID_PAYLOAD, "severity_numeric": 6})

    def test_negative_alert_count_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyRequest(**{**_VALID_PAYLOAD, "alert_count": 0})

    def test_alert_count_exceeds_max_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyRequest(**{**_VALID_PAYLOAD, "alert_count": 501})

    def test_dup_ratio_above_1_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyRequest(**{**_VALID_PAYLOAD, "duplicate_alert_ratio": 1.001})

    def test_blast_radius_above_100_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyRequest(**{**_VALID_PAYLOAD, "blast_radius_pct": 100.1})

    def test_float_clamping_validator_runs(self) -> None:
        req = AnomalyRequest(**{**_VALID_PAYLOAD, "duplicate_alert_ratio": 0.123456789})
        assert len(str(req.duplicate_alert_ratio).split(".")[1]) <= 7


# ---------------------------------------------------------------------------
# Schema: AnomalyResponse
# ---------------------------------------------------------------------------
class TestAnomalyResponse:
    def test_valid_response_builds(self) -> None:
        resp = AnomalyResponse(
            anomaly_score=-0.312,
            is_anomalous=True,
            confidence=0.78,
            model_version="1.0.0",
            inference_latency_ms=1.4,
        )
        assert resp.is_anomalous is True
        assert resp.model_version == "1.0.0"

    def test_confidence_below_0_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyResponse(
                anomaly_score=0.1,
                is_anomalous=False,
                confidence=-0.01,
                model_version="1.0.0",
                inference_latency_ms=1.0,
            )

    def test_confidence_above_1_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalyResponse(
                anomaly_score=0.1,
                is_anomalous=False,
                confidence=1.001,
                model_version="1.0.0",
                inference_latency_ms=1.0,
            )


# ---------------------------------------------------------------------------
# ModelRegistry — with real artifact (skip if absent)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not _ARTIFACT.exists(),
    reason="Model artifact not present — run scripts/train_model.py first",
)
class TestModelRegistryWithArtifact:
    def test_predict_returns_required_keys(self) -> None:
        from ml_models.incident_anomaly.registry import ModelRegistry
        reg = ModelRegistry()
        result = reg.predict(list(_VALID_PAYLOAD.values()))
        assert "anomaly_score" in result
        assert "is_anomalous" in result
        assert "confidence" in result
        assert "inference_latency_ms" in result

    def test_predict_confidence_in_range(self) -> None:
        from ml_models.incident_anomaly.registry import ModelRegistry
        reg = ModelRegistry()
        result = reg.predict(list(_VALID_PAYLOAD.values()))
        assert 0.0 <= result["confidence"] <= 1.0

    def test_predict_latency_positive(self) -> None:
        from ml_models.incident_anomaly.registry import ModelRegistry
        reg = ModelRegistry()
        result = reg.predict(list(_VALID_PAYLOAD.values()))
        assert result["inference_latency_ms"] > 0.0

    def test_health_artifact_exists_true(self) -> None:
        from ml_models.incident_anomaly.registry import ModelRegistry
        reg = ModelRegistry()
        h = reg.health()
        assert h["artifact_exists"] is True
        assert h["model_version"] == "1.0.0"

    def test_anomalous_incident_scores_negative(self) -> None:
        """A clear SEV-1 anomaly should produce a negative anomaly score."""
        from ml_models.incident_anomaly.registry import ModelRegistry
        reg = ModelRegistry()
        # extreme anomaly: SEV-1, 490 alerts, 700 min TTD, 48 services, 9 escalations
        anomaly_features = [1, 490, 700.0, 48, 9, 0.9, 95.0]
        result = reg.predict(anomaly_features)
        assert result["is_anomalous"] is True, (
            f"Expected is_anomalous=True for extreme incident, got score={result['anomaly_score']}"
        )


# ---------------------------------------------------------------------------
# ModelRegistry — mocked (no artifact required)
# ---------------------------------------------------------------------------
class TestModelRegistryMocked:
    def test_health_artifact_absent_returns_false(self) -> None:
        from ml_models.incident_anomaly.registry import ModelRegistry
        reg = ModelRegistry()
        with patch.object(
            Path, "exists", return_value=False
        ):
            h = reg.health()
            assert h["artifact_exists"] is False

    def test_predict_calls_decision_function(self) -> None:
        import numpy as np
        from ml_models.incident_anomaly.registry import ModelRegistry

        mock_model = MagicMock()
        mock_model.decision_function.return_value = np.array([-0.4])

        reg = ModelRegistry()
        reg._model = mock_model  # inject mock

        result = reg.predict(list(_VALID_PAYLOAD.values()))
        mock_model.decision_function.assert_called_once()
        assert result["is_anomalous"] is True
        assert math.isfinite(result["confidence"])
