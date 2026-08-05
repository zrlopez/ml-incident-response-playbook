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

Remediation changelog:
  SEC-03  Fixed TOCTOU race in _ensure_loaded(): lock is now acquired
          before the None-check, not after. This is correct double-checked
          locking — one lock acquisition, one re-check inside the lock.
  SEC-04  Added SHA-256 hash verification of the artifact file before
          joblib.load(). Reads expected hash from a sidecar
          .sha256 manifest file (artifacts/isolation_forest_v1.joblib.sha256)
          if present; falls back to _EXPECTED_SHA256 constant; skips with
          WARNING if constant is the zero-sentinel (backward compat until
          MLOPS-01 training pipeline ships the manifest).
  SEC-05  health() no longer exposes the absolute artifact_path filesystem
          path. Replaced with artifact_file (basename) and artifact_version.
  ML-04   Added explicit return type annotations throughout; replaced
          bare Any-typed dict returns with TypedDict; numpy array ops
          annotated so mypy can verify; ignore_errors override removed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, TypedDict

import joblib
import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import IsolationForest

from src.logger import get_logger

log = get_logger(__name__)

_ARTIFACT_DIR = Path(__file__).parent / "artifacts"
_MODEL_FILE = _ARTIFACT_DIR / "isolation_forest_v1.joblib"
_MANIFEST_FILE = _ARTIFACT_DIR / "isolation_forest_v1.joblib.sha256"
MODEL_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Configurable anomaly threshold
# ---------------------------------------------------------------------------
# The IsolationForest decision_function returns scores in roughly [-0.5, 0.5].
# Negative scores = more anomalous. The default of 0.0 classifies anything
# below the mean path length as anomalous, which yields ~contamination-rate
# positives on the training distribution.
#
# Override via env var ANOMALY_THRESHOLD (float) to tune precision/recall
# tradeoff without retraining. Example:
#   ANOMALY_THRESHOLD=-0.05  → fewer false positives (higher precision)
#   ANOMALY_THRESHOLD=0.05   → fewer false negatives (higher recall)
#
# Calibration guidance: plot score histogram on a representative sample,
# then choose threshold at the desired operating point on the PR curve.
_ANOMALY_THRESHOLD: float = float(os.environ.get("ANOMALY_THRESHOLD", "0.0"))

_EXPECTED_SHA256: str = "0" * 64  # sentinel — replace with real digest


class PredictResult(TypedDict):
    anomaly_score: float
    is_anomalous: bool
    confidence: float
    inference_latency_ms: float


class HealthResult(TypedDict):
    model_loaded: bool
    model_version: str
    artifact_file: str
    artifact_exists: bool
    loaded_at: float | None
    anomaly_threshold: float  # active threshold; configurable via ANOMALY_THRESHOLD env var


def _load_expected_hash() -> str | None:
    """Return the expected SHA-256 hex string, or None if unverifiable."""
    if _MANIFEST_FILE.exists():
        raw = _MANIFEST_FILE.read_text().strip().split()[0]
        if len(raw) == 64:
            return raw
        log.warning("registry.manifest_malformed", extra={"path": str(_MANIFEST_FILE)})

    if _EXPECTED_SHA256 == "0" * 64:
        log.warning(
            "registry.hash_check_skipped",
            extra={
                "reason": "_EXPECTED_SHA256 is zero-sentinel; pin a real digest or ship a .sha256 manifest",
                "artifact": _MODEL_FILE.name,
            },
        )
        return None
    return _EXPECTED_SHA256


def _verify_artifact_hash(path: Path) -> None:
    """Compute SHA-256 of *path* and compare against the expected digest.

    Raises:
        RuntimeError: if the digest does not match the expected value.
    """
    expected = _load_expected_hash()
    if expected is None:
        return

    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            sha.update(chunk)
    actual = sha.hexdigest()

    if actual != expected:
        raise RuntimeError(
            f"Artifact hash mismatch for {path.name}. "
            f"Expected {expected!r}, got {actual!r}. "
            "The model file may have been tampered with or corrupted. "
            "Re-run the training pipeline to regenerate a verified artifact."
        )
    log.info(
        "registry.artifact_hash_verified",
        extra={"artifact": path.name, "sha256": actual[:16] + "..."},
    )


class ModelRegistry:
    """Thread-safe lazy loader for the incident anomaly model."""

    def __init__(self) -> None:
        self._model: IsolationForest | None = None
        self._lock = threading.Lock()
        self._loaded_at: float | None = None

    def load(self) -> None:
        """Explicitly load or reload the model artifact from disk.

        Acquires the lock for the full duration of hash verification +
        deserialization to prevent concurrent loads (SEC-03).
        """
        with self._lock:
            _verify_artifact_hash(_MODEL_FILE)
            self._model = joblib.load(_MODEL_FILE)
            self._loaded_at = time.time()

    def _ensure_loaded(self) -> IsolationForest:
        # SEC-03: Correct double-checked locking.
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                _verify_artifact_hash(_MODEL_FILE)
                self._model = joblib.load(_MODEL_FILE)
                self._loaded_at = time.time()
        assert self._model is not None  # noqa: S101
        return self._model

    def predict(self, features: list[float]) -> PredictResult:
        """Run inference on a single feature vector.

        Args:
            features: Ordered list of 7 floats matching the training schema.
                      Order: [severity_numeric, alert_count,
                              time_to_detect_minutes, affected_services,
                              on_call_escalations, duplicate_alert_ratio,
                              blast_radius_pct]

        Returns:
            PredictResult TypedDict with anomaly_score, is_anomalous,
            confidence, and inference_latency_ms.
        """
        model = self._ensure_loaded()
        x: NDArray[np.float64] = np.array(features, dtype=np.float64).reshape(1, -1)

        t0 = time.perf_counter()
        score: float = float(model.decision_function(x)[0])
        latency_ms: float = (time.perf_counter() - t0) * 1_000

        is_anomalous: bool = score < _ANOMALY_THRESHOLD
        raw_confidence: np.floating[Any] = 1.0 / (1.0 + np.exp(score * 3))
        confidence: float = float(np.clip(raw_confidence, 0.0, 1.0))

        return PredictResult(
            anomaly_score=round(score, 6),
            is_anomalous=is_anomalous,
            confidence=round(confidence, 6),
            inference_latency_ms=round(latency_ms, 3),
        )

    def health(self) -> HealthResult:
        """Return a health summary for use by the /ready endpoint.

        SEC-05: Does NOT expose the absolute filesystem path of the artifact.
        Callers receive only the filename and version — no container internals.
        """
        loaded: bool = self._model is not None
        return HealthResult(
            model_loaded=loaded,
            model_version=MODEL_VERSION,
            artifact_file=_MODEL_FILE.name,
            artifact_exists=_MODEL_FILE.exists(),
            loaded_at=self._loaded_at,
            anomaly_threshold=_ANOMALY_THRESHOLD,
        )


# Module-level singleton — imported by the FastAPI router.
model_registry = ModelRegistry()
