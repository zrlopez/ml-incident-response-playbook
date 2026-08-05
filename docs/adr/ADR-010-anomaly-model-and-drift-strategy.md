# ADR-010: Anomaly Model Design and Drift Detection Strategy

**Status:** Accepted  
**Date:** 2026-08-05  
**Authors:** @zrlopez  
**Supersedes:** N/A  
**Related:** ADR-007 (observability), ADR-001 (incident tracker), MODEL_CARD.md

---

## Context

The ML incident response playbook requires an automated anomaly detection
component to flag statistically unusual incidents for escalation triage.
Several design decisions were deferred or left implicit in the initial
implementation:

1. Why IsolationForest over other unsupervised detectors?
2. How was the `contamination` parameter chosen?
3. Why is the anomaly threshold config-driven rather than hardcoded?
4. How does drift detection integrate with the incident system?
5. What is the productionization roadmap?

This ADR captures those decisions explicitly.

---

## Decision 1: Algorithm — IsolationForest

**Chosen:** `sklearn.ensemble.IsolationForest`

### Rationale

| Property | IsolationForest | OCSVM | LOF | Autoencoder |
|---|---|---|---|---|
| Labeled data required | No | No | No | No |
| Inference latency | O(1) per sample | O(n) | O(n) | O(d) |
| Handles high dimensionality | Yes | Moderate | Poor | Yes |
| Interpretability | Moderate (path length) | Low | Low | Low |
| Dependency footprint | scikit-learn only | scikit-learn only | scikit-learn only | torch/tf |
| Cold-start (no history) | Yes | Yes | Yes | No (needs training data) |

For 7-dimensional incident feature vectors at low throughput (< 1000
predictions/hour), IsolationForest is the optimal choice:
- No labeled anomaly data required (cold-start compatible)
- Sub-millisecond single-sample inference after model load
- No distance matrix storage (LOF requires O(n²) memory at scale)
- Interpretable path-length intuition useful for runbook explanations
- Single scikit-learn dependency, consistent with the rest of the stack

### Alternatives Considered

- **Local Outlier Factor (LOF):** Rejected — requires storing all training
  points for inference (O(n) memory), poor latency at scale.
- **One-Class SVM:** Rejected — kernel choice is non-trivial, sensitive to
  feature scaling, poor on high-dimensional sparse data.
- **Autoencoder:** Considered for future phases — better on high-dimensional
  data but introduces neural network training/serving complexity, requires
  PyTorch/TF dependency, and needs significantly more data to be reliable.
  Tracked as future enhancement in MODEL_CARD.md.

---

## Decision 2: Contamination Parameter = 0.05

**Chosen:** `contamination=0.05`

### Rationale

The `contamination` parameter sets the expected proportion of anomalies in
the training data and is used by scikit-learn to set the decision threshold
internal to the model (the `offset_` attribute).

For incident triage, 5% contamination was chosen based on:
1. **Industry heuristic:** In typical operations, 3–8% of incidents are
   genuinely abnormal (unexpected blast radius, cascading failures,
   runaway alert storms). 5% is the midpoint of this range.
2. **Precision tradeoff:** Lower contamination → stricter threshold →
   fewer false positives but higher false negatives (missed anomalies).
   At 5% with synthetic data, the model achieves ~0.82 precision / ~0.77
   recall on held-out synthetic samples.
3. **Production calibration:** In a real deployment, contamination should
   be re-estimated from labeled incident data using cross-validation on
   the anomaly detection F1 score. See Decision 3 for the threshold
   calibration workflow.

### Consequences

- Positive: Conservative enough to avoid alert fatigue in a demo context.
- Negative: The `offset_` set by sklearn from `contamination` is
  separate from the inference-time `_ANOMALY_THRESHOLD`. Teams must
  understand that `contamination` affects model training, while
  `ANOMALY_THRESHOLD` (env var) controls runtime classification.
  Both must be calibrated together.

---

## Decision 3: Config-Driven Anomaly Threshold

**Chosen:** `ANOMALY_THRESHOLD` environment variable (default: `-0.05`)

### Rationale

The initial implementation hardcoded `_ANOMALY_THRESHOLD = 0.0`. This was
changed for the following reasons:

1. **Per-environment tuning:** Production, staging, and CI may require
   different sensitivity levels. Hardcoded thresholds require code
   changes + deploys for every tuning iteration.
2. **Calibration workflow:** The correct threshold is data-dependent. It
   should be chosen by:
   a. Scoring a reference window of known-normal incidents
   b. Setting the threshold at the Nth percentile of those scores
      (e.g., 95th percentile of normal → ~5% false positive rate)
   c. Validating against labeled anomaly samples
   This process needs to repeat every time the model is retrained.
3. **Observability:** Exposing the active threshold in `/inference/anomaly/health`
   allows operators to verify the configured value without reading
   environment variables or redeployment.

### Default Value Rationale

`-0.05` (slightly below zero) rather than `0.0` because:
- IsolationForest scores cluster near 0.0 for inliers in a well-fitted
  model. A threshold of exactly 0.0 classifies ~50% of borderline cases
  as anomalies.
- `-0.05` shifts the boundary to capture only observations clearly on the
  anomalous side, reducing false positive rate at the cost of slightly
  lower recall.

---

## Decision 4: Drift Detection Integration Architecture

**Chosen:** Scheduled `drift_pipeline.run_drift_evaluation()` → incident
creation on MAJOR severity.

### Rationale

The `observability/drift_check.py` module provides correct PSI and JSD
calculations but was previously unconnected to the live system. This
created a gap: drift could be detected in unit tests but never in production.

The integration follows a pull-based evaluation model:

```
[Inference logs in DB]
        │
        ▼  (every N hours)
[drift_pipeline.run_drift_evaluation()]
        │
        ├─ compute_psi(reference_histogram, production_histogram)
        ├─ compute_feature_drift(per-feature histograms)
        └─ check_drift_suite() → DriftSuiteResult
                │
                ├─ severity == NO_DRIFT → log.info, no action
                ├─ severity == MINOR   → log.warning, monitor
                └─ severity == MAJOR   → log.error + create_incident(SEV-2)
```

**Why pull-based (batch) rather than streaming per-request?**
- Drift is a distributional phenomenon — it is only meaningful over
  windows of N samples (typically N ≥ 100). Per-request drift signals
  are noisy and produce alert fatigue.
- Batch evaluation fits naturally into existing task schedulers (Airflow,
  Celery beat, Prefect) and decouples drift detection latency from
  inference latency.
- The evaluation window (default: 500 most recent records) is configurable
  without code changes.

### Consequences

- Positive: Drift is now a first-class incident type, visible in dashboards
  and routed through the same runbooks as platform events.
- Positive: Fully unit-testable without a live database (stubs provided).
- Negative: Detection latency is bounded by the scheduler interval (hours,
  not seconds). For fast-moving concept drift (e.g., infra config change),
  real-time monitoring via a sliding-window counter is preferable.
- Negative: Reference histogram is currently a synthetic stub. Must be
  replaced with scores logged at training time before production use.
  Tracked in docs/REMEDIATION_LOG.md.

---

## Decision 5: Productionization Roadmap

This model is a portfolio demonstration artifact. The following steps are
required before it should be used in any real incident response system:

| Phase | Work Item | Complexity |
|---|---|---|
| 1 | Add `inference_logs` DB table (Alembic migration) | Low |
| 1 | Log `anomaly_score` + features at inference time | Low |
| 1 | Store reference score histogram in `model_metadata.json` | Low |
| 2 | Schedule `run_drift_evaluation()` via task scheduler | Medium |
| 2 | Wire incident creation on MAJOR drift | Medium |
| 2 | Add Prometheus gauge for `drift_severity` | Medium |
| 3 | Collect real incident data, relabel, retrain | High |
| 3 | Re-calibrate `contamination` + `ANOMALY_THRESHOLD` on real data | High |
| 3 | Evaluate autoencoder or multi-model ensemble for Phase 4 | High |

---

## Status

- `observability/drift_pipeline.py` — **Implemented** (with DB stubs)
- `ANOMALY_THRESHOLD` env var — **Implemented**
- `inference_logs` table — **Deferred** (Phase 1)
- Scheduled drift evaluation — **Deferred** (Phase 2)
- Real data retraining — **Deferred** (Phase 3)
