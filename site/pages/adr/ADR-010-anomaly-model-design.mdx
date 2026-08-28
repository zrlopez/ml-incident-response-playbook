# ADR-010 — Incident Anomaly Model Design

**Status:** Accepted  
**Date:** 2026-08-05  
**Author:** zrlopez  
**Deciders:** zrlopez  
**Supersedes:** —  
**Related:** ADR-007 (observability), MODEL_CARD.md, `runbooks/model_degradation.md`

---

## Context

The incident response API needs an ML signal to flag statistically anomalous
incidents for escalation triage. The constraints are:

- **No labeled data available at launch** — this is a portfolio/demo system
  with no real incident history.
- **Cold-start requirement** — the detector must produce meaningful scores
  on day one, before any production data accumulates.
- **Inference latency SLO** — p95 < 10 ms per request (single-sample,
  synchronous, CPU-only).
- **Operational simplicity** — the model must be serializable to a single
  joblib artifact, versioned by SHA-256, and loadable by a thread-safe
  registry without an external model store.

---

## Decision

Use **scikit-learn `IsolationForest`** trained on synthetically generated
incident feature vectors.

---

## Algorithm Rationale

| Property | IsolationForest | Alt: One-Class SVM | Alt: Autoencoder |
|---|---|---|---|
| Labeled data required | No | No | No |
| Scales to high-dim | Yes | Poor (RBF kernel) | Yes |
| Inference latency | < 1 ms (tree traversal) | ~5–50 ms | ~5–20 ms (GPU optional) |
| Calibrated probabilities | No (needs isotonic/Platt) | No | Reconstruction error only |
| Production artifact size | < 5 MB | < 1 MB | > 50 MB (even small nets) |
| Cold-start suitability | ✅ Excellent | ✅ Good | ❌ Needs tuning corpus |
| Portfolio legibility | ✅ Well-understood paper | Moderate | High complexity |

IsolationForest wins on latency, cold-start suitability, and simplicity given
the portfolio constraints.

---

## Contamination Parameter

`contamination=0.05` (5% expected anomaly rate) was chosen based on:

1. **Industry baseline** — typical major incident rate in healthy production
   systems is 1–10% of all alert events. 5% is a conservative midpoint.
2. **Effect on threshold** — `contamination` controls what fraction of the
   training set is used to set the decision boundary. At 5%, the model flags
   roughly the top 5% most anomalous training samples.
3. **Tuning guidance for real data** — once production incident logs are
   available, plot the score histogram and choose `contamination` at the
   desired operating point on the precision-recall curve. Then retrain.

> **Limitation:** On synthetic data, the 5% value is arbitrary. Real
> contamination selection must be driven by business cost of false positives
> (alert fatigue) vs. false negatives (missed escalations).

---

## Anomaly Threshold

The decision function threshold defaults to `0.0` (sign of score) which
corresponds to the `contamination`-derived boundary. This is configurable
via the `ANOMALY_THRESHOLD` environment variable without retraining:

```
ANOMALY_THRESHOLD=-0.05   # higher precision, fewer FPs
ANOMALY_THRESHOLD=0.05    # higher recall, fewer FNs
```

The active threshold is exposed in the `/api/v1/inference/anomaly/health`
response so operators can verify configuration in any environment.

---

## Confidence Score

The raw `decision_function` output is passed through a logistic sigmoid:

```
confidence = 1 / (1 + exp(score * 3))
```

This maps the score to [0, 1] where values near 1.0 indicate high anomaly
confidence. The `* 3` sharpens the sigmoid around the decision boundary.

> **Known limitation:** This is not a calibrated probability. It is a
> monotonic transformation for UX convenience. Do not treat it as P(anomaly).
> Platt scaling or isotonic regression on labeled holdout data would yield
> calibrated probabilities if that property is needed.

---

## Artifact Integrity

Every training run writes a `isolation_forest_v1.joblib.sha256` sidecar file.
The `ModelRegistry` verifies the SHA-256 digest before `joblib.load()` to
detect tampering or corruption (SEC-04). This replaces the previous zero-
sentinel approach: after any `python scripts/train_model.py` run, the manifest
is auto-generated and immediately active.

---

## Drift Detection Integration

The `observability/drift_check.py` module provides:

- **PSI** — Population Stability Index on the anomaly score distribution
  (reference = training-time score histogram).
- **KL divergence** — per-feature histogram comparison for upstream data drift.

For a production deployment, the intended wiring is:

```
Inference requests → score logged to DB
    ↓  (batch job, e.g. nightly Airflow/Prefect task)
Score histogram built from last N=1000 predictions
    ↓
check_drift_suite(reference=training_scores, production=recent_scores)
    ↓
PSI >= 0.20  →  create Incident (SEV-2 "model_drift") via incident API
PSI >= 0.10  →  log warning + emit metric drift.psi{severity="minor"}
```

> **Current state:** Drift helpers are implemented and unit-tested.
> The batch wiring (score persistence → histogram build → drift check loop)
> is documented in `runbooks/model_degradation.md` and `pipelines/`
> but not yet implemented in this repo (tracked: MLOPS-02).

---

## Productionization Gaps (honest accounting)

This model is intentionally demo-grade. Gaps to close before real production use:

| Gap | Impact | Mitigation path |
|---|---|---|
| Synthetic training data | Score distribution may not match real incidents | Retrain on real incident exports (Jira/PagerDuty) |
| No PR curve analysis | Unknown precision/recall at chosen threshold | Add `scripts/evaluate_model.py` with sklearn PR curve output |
| Single fixed threshold | Can't adapt to concept drift without redeploy | ANOMALY_THRESHOLD env var + shadow scoring |
| No calibrated probabilities | Confidence is not interpretable as P(anomaly) | Add isotonic regression calibration layer |
| No multi-model lifecycle | Only v1.0.0 artifact tracked | Extend ModelRegistry to support named versions + canary routing |
| Drift wiring incomplete | PSI check not connected to incident creation | Implement MLOPS-02 batch drift evaluation job |

---

## Consequences

**Positive:**
- Sub-millisecond inference latency satisfies the SLO with margin.
- Single-artifact + SHA-256 manifest keeps deployment simple and auditable.
- Configurable threshold enables ops to tune precision/recall without retraining.
- Clean separation: model logic in `ml_models/`, observability in `observability/`, API in `api/`.

**Negative:**
- Synthetic training data limits ecological validity.
- No multi-model versioning or canary routing in current registry.
- Confidence score is not calibrated — consumers must not treat it as a probability.

---

## References

- Liu, F.T., Ting, K.M., Zhou, Z.H. (2008). *Isolation Forest*. ICDM 2008. DOI: 10.1109/ICDM.2008.17
- scikit-learn IsolationForest docs: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
- MODEL_CARD.md — full feature schema, performance metrics, intended use
- `runbooks/model_degradation.md` — operational runbook for drift/degradation incidents
- `observability/drift_check.py` — PSI + KL drift detection helpers
