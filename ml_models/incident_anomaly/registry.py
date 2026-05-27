"""
ml_models/incident_anomaly/registry.py
=======================================
Model registry for the incident anomaly detector.

Responsibilities:
  - Load the serialized IsolationForest artifact from disk on first access
  - Expose version, health-check, and reload primitives
  - Thread-safe singleton via threading.Lock

Attribution:
    sklearn.ensemble.IsolationForest — BSD-3-Clause License
    Copyright (c) 2007-2025 The scikit-learn developers.
    See MODEL_CARD.md for full license text and BibTeX citation.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

_ARTIFACT_DIR = Path(__file__).parent / "artifacts"
_MODEL_FILE = _ARTIFACT_DIR / "isolation_forest_v1.joblib"
MODEL_VERSION = "1.0.0"

# Decision boundary: scores below this threshold are flagged as anomalous.
# IsolationForest.decision_function() returns positive scores for inliers and
# negative scores for outliers when contamination is set correctly.
_ANOMALY_THRESHOLD: float = 0.0


class ModelRegistry:
    """Thread-safe lazy loader for the incident anomaly model."""

    def __init__(self) -> None:
        self._model: IsolationForest | None = None
        self._lock = threading.Lock()
        self._loaded_at: float | None = None

    def load(self) -> None:
        """Explicitly load or reload the model artifact from disk."""
        with self._lock:
            self._model = joblib.load(_MODEL_FILE)
            self._loaded_at = time.time()

    def _ensure_loaded(self) -> IsolationForest:
        if self._model is None:
            self.load()
        assert self._model is not None  # noqa: S101 — internal guard
        return self._model

    def predict(self, features: list[float]) -> dict[str, Any]:
        """Run inference on a single feature vector.

        Args:
            features: Ordered list of 7 floats matching the training schema.
                      Order: [severity_numeric, alert_count,
                              time_to_detect_minutes, affected_services,
                              on_call_escalations, duplicate_alert_ratio,
                              blast_radius_pct]

        Returns:
            dict with keys: anomaly_score, is_anomalous, confidence.
        """
        model = self._ensure_loaded()
        x = np.array(features, dtype=np.float64).reshape(1, -1)

        t0 = time.perf_counter()
        score: float = float(model.decision_function(x)[0])
        latency_ms = (time.perf_counter() - t0) * 1_000

        is_anomalous = score < _ANOMALY_THRESHOLD

        # Normalize score to [0, 1] as a confidence proxy.
        # Clip to prevent extreme values from escaping the [0,1] range.
        raw_confidence = 1.0 / (1.0 + np.exp(score * 3))  # sigmoid-ish
        confidence = float(np.clip(raw_confidence, 0.0, 1.0))

        return {
            "anomaly_score": round(score, 6),
            "is_anomalous": is_anomalous,
            "confidence": round(confidence, 6),
            "inference_latency_ms": round(latency_ms, 3),
        }

    def health(self) -> dict[str, Any]:
        """Return a health summary for use by /ready endpoint."""
        loaded = self._model is not None
        return {
            "model_loaded": loaded,
            "model_version": MODEL_VERSION,
            "artifact_path": str(_MODEL_FILE),
            "artifact_exists": _MODEL_FILE.exists(),
            "loaded_at": self._loaded_at,
        }


# Module-level singleton — imported by the FastAPI router.
model_registry = ModelRegistry()
