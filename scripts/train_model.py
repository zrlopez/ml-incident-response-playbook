"""
scripts/train_model.py
======================
Reproducible training script for the incident anomaly detector.

This script:
  1. Generates a synthetic incident feature dataset (no real data used)
  2. Trains an IsolationForest on the normal-class samples
  3. Validates qualitatively on injected anomaly samples
  4. Serializes the fitted model to ml_models/incident_anomaly/artifacts/

Usage:
    python scripts/train_model.py [--seed SEED] [--samples N] [--verbose]

Attribution:
    Algorithm: sklearn.ensemble.IsolationForest
    License:   BSD-3-Clause
    Copyright: (c) 2007-2025 The scikit-learn developers.

    Original paper:
        Liu, F.T., Ting, K.M., Zhou, Z.H. (2008). Isolation Forest.
        In: Proceedings of the 8th IEEE International Conference on
        Data Mining (ICDM 2008), pp. 413-422.
        DOI: 10.1109/ICDM.2008.17

    Full license and BibTeX citation: see MODEL_CARD.md at repo root.

Notes:
    - All data is synthetically generated; no real incidents are used.
    - Set PYTHONHASHSEED for fully deterministic runs.
    - Model artifact is committed to the repository under
      ml_models/incident_anomaly/artifacts/.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature definitions — must match ml_models/incident_anomaly/schema.py
# ---------------------------------------------------------------------------
FEATURE_NAMES = [
    "severity_numeric",       # int  1–5
    "alert_count",            # int  1–500
    "time_to_detect_minutes", # float 0–720
    "affected_services",      # int  1–50
    "on_call_escalations",    # int  0–10
    "duplicate_alert_ratio",  # float 0–1
    "blast_radius_pct",       # float 0–100
]

ARTIFACT_DIR = Path(__file__).parent.parent / "ml_models" / "incident_anomaly" / "artifacts"
ARTIFACT_FILE = ARTIFACT_DIR / "isolation_forest_v1.joblib"
METADATA_FILE = ARTIFACT_DIR / "model_metadata.json"

MODEL_VERSION = "1.0.0"


def _generate_normal(rng: np.random.Generator, n: int) -> np.ndarray:
    """Generate synthetic normal incident feature vectors.

    Distributions are chosen to approximate realistic incident telemetry:
    - Most incidents are low-severity with few alerts.
    - Time-to-detect clusters around 5-60 minutes for healthy monitoring.
    - Blast radius and escalations are mostly low.
    """
    severity = rng.integers(2, 6, size=n)          # SEV-2 to SEV-5 mostly normal
    alerts = rng.integers(1, 80, size=n)
    ttd = rng.exponential(scale=20.0, size=n).clip(0.5, 300.0)
    services = rng.integers(1, 10, size=n)
    escalations = rng.integers(0, 3, size=n)
    dup_ratio = rng.beta(2, 5, size=n)             # skewed low — most alerts unique
    blast = rng.beta(1.5, 5, size=n) * 100.0       # skewed low blast radius
    return np.column_stack([severity, alerts, ttd, services, escalations, dup_ratio, blast])


def _generate_anomalies(rng: np.random.Generator, n: int) -> np.ndarray:
    """Generate synthetic anomalous incident feature vectors.

    Anomalies are characterized by combinations of:
    - SEV-1 severity
    - Very high alert counts
    - Very slow time-to-detect (monitoring gap)
    - High blast radius
    - Many escalations
    """
    severity = rng.integers(1, 2, size=n)           # SEV-1
    alerts = rng.integers(150, 501, size=n)
    ttd = rng.uniform(200.0, 720.0, size=n)
    services = rng.integers(15, 51, size=n)
    escalations = rng.integers(5, 11, size=n)
    dup_ratio = rng.beta(5, 2, size=n)              # skewed high — alert storm
    blast = rng.uniform(60.0, 100.0, size=n)
    return np.column_stack([severity, alerts, ttd, services, escalations, dup_ratio, blast])


def train(seed: int = 42, n_samples: int = 2000, verbose: bool = False) -> None:
    """Train, validate, and serialize the IsolationForest model."""
    rng = np.random.default_rng(seed)
    log.info("Generating synthetic dataset (seed=%d, n=%d)", seed, n_samples)

    n_anomalies = int(n_samples * 0.1)   # 10% held-out anomalies for validation
    n_normal = n_samples - n_anomalies

    X_normal = _generate_normal(rng, n_normal)
    X_anomalies = _generate_anomalies(rng, n_anomalies)

    # Train ONLY on normal samples — unsupervised, no labels needed.
    log.info("Training IsolationForest on %d normal samples ...", n_normal)
    model = IsolationForest(
        n_estimators=200,
        contamination=0.05,       # expected 5% anomaly rate in deployment
        max_features=1.0,
        bootstrap=False,
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(X_normal)
    log.info("Training complete.")

    # Qualitative validation: score both populations.
    X_val = np.vstack([X_normal[:200], X_anomalies])
    y_true = np.array([1] * 200 + [-1] * n_anomalies)   # 1=normal, -1=anomaly
    y_pred = model.predict(X_val)

    if verbose:
        log.info("Validation report (normal=1, anomaly=-1):\n%s",
                 classification_report(y_true, y_pred, target_names=["normal", "anomaly"]))

    # Persist artifact.
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, ARTIFACT_FILE)
    log.info("Model saved to %s", ARTIFACT_FILE)

    metadata = {
        "model_version": MODEL_VERSION,
        "algorithm": "IsolationForest",
        "library": "scikit-learn",
        "library_license": "BSD-3-Clause",
        "n_estimators": 200,
        "contamination": 0.05,
        "random_state": seed,
        "n_train_samples": n_normal,
        "feature_names": FEATURE_NAMES,
        "artifact_file": str(ARTIFACT_FILE.name),
        "attribution": (
            "scikit-learn: Pedregosa et al., JMLR 12, pp. 2825-2830, 2011. "
            "BSD-3-Clause. See MODEL_CARD.md."
        ),
    }
    METADATA_FILE.write_text(json.dumps(metadata, indent=2))
    log.info("Metadata saved to %s", METADATA_FILE)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train incident anomaly detector.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--samples", type=int, default=2000, help="Training samples (default: 2000)")
    parser.add_argument("--verbose", action="store_true", help="Print validation report")
    args = parser.parse_args()
    train(seed=args.seed, n_samples=args.samples, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())  # type: ignore[func-returns-value]
