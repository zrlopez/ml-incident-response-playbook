# ML Models

The ML Incident Response platform ships one production ML model: the
**Incident Anomaly Detector**, a scikit-learn `IsolationForest` trained
on historical incident telemetry. This page documents the model's
purpose, feature schema, inference contract, operational controls, and
attribution.

---

## Model Overview

| Property | Value |
|---|---|
| **Algorithm** | `sklearn.ensemble.IsolationForest` |
| **Version** | `1.0.0` |
| **Artifact** | `ml_models/incident_anomaly/artifacts/isolation_forest_v1.joblib` |
| **License** | BSD-3-Clause (scikit-learn) — see `MODEL_CARD.md` for BibTeX citation |
| **Decision threshold** | `score < 0.0` — scores below zero are flagged as anomalous |
| **Inference endpoint** | `POST /api/v1/inference/anomaly` |
| **Health endpoint** | `GET /api/v1/inference/anomaly/health` |

---

## Feature Schema

The model expects exactly **7 features** in the order listed below.
All ranges are enforced at the API boundary by the `AnomalyRequest`
Pydantic schema before the feature vector reaches the model.

| # | Field | Type | Range | Description |
|---|---|---|---|---|
| 1 | `severity_numeric` | `int` | 1–5 | Incident severity (1 = SEV-1 critical, 5 = informational) |
| 2 | `alert_count` | `int` | 1–500 | Total alerts fired during the incident window |
| 3 | `time_to_detect_minutes` | `float` | 0.0–720.0 | Minutes from first anomalous signal to detection |
| 4 | `affected_services` | `int` | 1–50 | Count of distinct services impacted |
| 5 | `on_call_escalations` | `int` | 0–10 | Number of on-call escalation pages generated |
| 6 | `duplicate_alert_ratio` | `float` | 0.0–1.0 | Fraction of alerts that were duplicates |
| 7 | `blast_radius_pct` | `float` | 0.0–100.0 | Estimated percentage of user-facing traffic impacted |

---

## Inference Contract

### Request

```json
POST /api/v1/inference/anomaly
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "severity_numeric": 1,
  "alert_count": 142,
  "time_to_detect_minutes": 4.7,
  "affected_services": 8,
  "on_call_escalations": 3,
  "duplicate_alert_ratio": 0.35,
  "blast_radius_pct": 62.0
}
```

### Response

```json
{
  "anomaly_score": -0.312,
  "is_anomalous": true,
  "confidence": 0.78,
  "model_version": "1.0.0",
  "inference_latency_ms": 1.4
}
```

| Field | Type | Description |
|---|---|---|
| `anomaly_score` | `float` | Raw `decision_function()` score. Negative = anomalous |
| `is_anomalous` | `bool` | `true` when `anomaly_score < 0.0` |
| `confidence` | `float` [0, 1] | Sigmoid-scaled distance from the decision boundary |
| `model_version` | `str` | Semantic version of the loaded artifact (`MODEL_VERSION` constant) |
| `inference_latency_ms` | `float` | Wall-clock time for `decision_function()` in milliseconds |

### Error responses

| Status | Condition |
|---|---|
| `422 Unprocessable Entity` | Feature vector fails Pydantic range validation |
| `503 Service Unavailable` | Artifact file missing (`artifact_exists: false`) or inference exception |
| `401 Unauthorized` | Missing or invalid Bearer JWT |

---

## Registry & Security Controls

The model is loaded and managed by `ml_models/incident_anomaly/registry.py`
via a thread-safe `ModelRegistry` singleton.

### SEC-03 — TOCTOU fix (double-checked locking)

`_ensure_loaded()` acquires the lock **before** the `None` check, not after.
This eliminates the race window where two concurrent requests could both
observe `self._model is None` and both trigger a `joblib.load()` simultaneously.

### SEC-04 — Artifact hash verification

Before every `joblib.load()`, `_verify_artifact_hash()` computes the SHA-256
digest of the `.joblib` file and compares it against a pinned expected value.
The expected hash is read from a sidecar manifest
(`isolation_forest_v1.joblib.sha256`) if present, or falls back to the
`_EXPECTED_SHA256` constant. If the hashes do not match, a `RuntimeError`
is raised and the model is **not** loaded. If no hash is pinned (zero-sentinel),
a `WARNING` is logged and load proceeds (backward-compatibility mode until
MLOPS-01 ships the manifest).

### SEC-05 — No path exposure in health()

`health()` returns only `artifact_file` (basename) and `artifact_version`.
The absolute container path is never surfaced to API clients or logs.

---

## Drift Monitoring

The model's input distribution is continuously monitored by
`observability/drift_check.py` using two complementary methods:

| Method | Metric | Threshold |
|---|---|---|
| **PSI** (Population Stability Index) | `ml_psi_score` | warning > 0.10 / critical ≥ 0.20 |
| **Drift ratio** (relative mean deviation) | `ml_feature_drift_ratio` | configurable per feature |

See `docs/metrics.md` for full alert threshold documentation and
`observability/alert_rules.yml` for the Prometheus rule definitions.

---

## Training & Artifact Generation

The artifact is not committed to source control. To generate it locally:

```bash
python scripts/train_model.py
```

This produces `ml_models/incident_anomaly/artifacts/isolation_forest_v1.joblib`
and (when MLOPS-01 lands) a companion `.sha256` manifest. The artifact
directory is covered by `.gitignore` to prevent accidental model commits.

The MLOPS-01 training pipeline will automate artifact generation, hash
manifest creation, and registry promotion as part of the CI/CD release flow.

---

## Attribution

```
scikit-learn — BSD-3-Clause License
Copyright (c) 2007-2025 The scikit-learn developers.

Pedregosa et al., Scikit-learn: Machine Learning in Python,
JMLR 12, pp. 2825-2830, 2011.
```

Full license text and BibTeX citation: see `MODEL_CARD.md` at the
repository root.
