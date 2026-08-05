"""
observability/drift_pipeline.py
================================
End-to-end drift evaluation pipeline.

This module wires the drift_check utility functions into the actual incident
response system. It is the missing link that transforms drift_check.py from
a standalone helper into a live production signal.

Pipeline flow
-------------
  1. Query recent anomaly scores from the incidents DB (via async SQLAlchemy).
  2. Build score histograms for the reference (training baseline) window
     and the current production window.
  3. Call check_drift_suite() — PSI + per-feature JSD.
  4. If overall_severity >= MAJOR, emit a structured drift incident record
     by calling the incident service. This makes drift a first-class incident
     type, visible in dashboards and routed through the same runbooks as
     any other platform event.
  5. Log the full DriftSuiteResult as a structured JSON event so it can be
     consumed by Prometheus, Grafana, or any OTLP-compatible backend.

Integration
-----------
  Call ``run_drift_evaluation()`` from a scheduled job (cron, Celery beat,
  Prefect flow, etc.). For portfolio demonstration, it can also be triggered
  manually via the ``make drift-check`` Makefile target.

  Example scheduler wiring (pseudo-code)::

      # Airflow / Prefect / APScheduler
      schedule.every(6).hours.do(
          asyncio.run, run_drift_evaluation(db_session, n_production=500)
      )

Production hardening checklist
------------------------------
  [ ] Replace _REFERENCE_SCORES_STUB with scores logged at training time
      (store in DB or object storage, keyed by model version).
  [ ] Add feature histogram collection: log raw feature vectors at inference
      time and aggregate into histograms here.
  [ ] Wire into Prometheus: push DriftSuiteResult.overall_severity as a gauge.
  [ ] Add alerting rule: PagerDuty / Opsgenie on severity == MAJOR.
  [ ] Retrain trigger: POST to CI/CD pipeline webhook on MAJOR + anomaly_count > N.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from observability.drift_check import DriftSeverity, DriftSuiteResult, check_drift_suite
from src.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Reference baseline (stub — replace with DB/artifact lookup in production)
# ---------------------------------------------------------------------------

# In production, these reference scores are computed once at training time and
# stored alongside the model artifact (e.g., in model_metadata.json or a
# dedicated scores table). They represent the decision_function() distribution
# on the training/validation set, binned into N_BINS equal-width buckets.
#
# For this portfolio demonstration, we use a synthetic Gaussian baseline that
# approximates a well-behaved IsolationForest score distribution:
#   - Center near 0.05 (slightly positive — mostly normal traffic)
#   - Std ~0.15 (realistic spread for a trained model)
N_BINS: int = 20
_SCORE_MIN: float = -0.5
_SCORE_MAX: float = 0.5


def _gaussian_histogram(mean: float, std: float, n_bins: int) -> list[float]:
    """Generate a normalised Gaussian histogram for baseline reference."""
    bin_width = (_SCORE_MAX - _SCORE_MIN) / n_bins
    hist = []
    for i in range(n_bins):
        x = _SCORE_MIN + (i + 0.5) * bin_width
        hist.append(math.exp(-0.5 * ((x - mean) / std) ** 2))
    total = sum(hist)
    return [v / total * 1000 for v in hist]  # scale to pseudo-counts


_REFERENCE_SCORES_STUB: list[float] = _gaussian_histogram(mean=0.05, std=0.15, n_bins=N_BINS)


# ---------------------------------------------------------------------------
# Histogram builder
# ---------------------------------------------------------------------------


def _scores_to_histogram(scores: list[float], n_bins: int = N_BINS) -> list[float]:
    """Bin a list of raw anomaly scores into a fixed-width histogram.

    Args:
        scores:  Raw decision_function() scores from production inference.
        n_bins:  Number of equal-width bins. Must match the reference histogram.

    Returns:
        List of counts per bin (length == n_bins).
    """
    if not scores:
        return [0.0] * n_bins

    bin_width = (_SCORE_MAX - _SCORE_MIN) / n_bins
    hist = [0.0] * n_bins
    for s in scores:
        # Clamp out-of-range scores to edge bins
        idx = int((s - _SCORE_MIN) / bin_width)
        idx = max(0, min(n_bins - 1, idx))
        hist[idx] += 1.0
    return hist


# ---------------------------------------------------------------------------
# DB query helpers (async SQLAlchemy stubs)
# ---------------------------------------------------------------------------


async def _fetch_recent_anomaly_scores(
    db: "AsyncSession",
    n_recent: int = 500,
) -> list[float]:
    """Fetch the most recent N anomaly scores from the inference log table.

    In production this queries an `inference_logs` table with columns:
        id, incident_id, anomaly_score, is_anomalous, created_at, model_version

    For the portfolio demo, the query is shown as a comment since the
    `inference_logs` table is not yet part of the Alembic schema. Adding it
    is Phase-2 work tracked in docs/REMEDIATION_LOG.md.

    Returns:
        List of recent anomaly_score floats, newest-first.
    """
    # Production query (uncomment when inference_logs table exists):
    # from sqlalchemy import text
    # result = await db.execute(
    #     text(
    #         "SELECT anomaly_score FROM inference_logs "
    #         "ORDER BY created_at DESC LIMIT :n"
    #     ),
    #     {"n": n_recent},
    # )
    # return [row[0] for row in result.fetchall()]
    #
    # Stub: return synthetic production scores that simulate moderate drift
    # (shifted mean of -0.02, slightly wider std) for demonstration purposes.
    import random
    rng = random.Random(42)  # deterministic for tests
    return [
        max(_SCORE_MIN, min(_SCORE_MAX, rng.gauss(-0.02, 0.18)))
        for _ in range(n_recent)
    ]


async def _count_recent_anomalies(
    db: "AsyncSession",
    n_recent: int = 500,
) -> int:
    """Return count of is_anomalous=True in the most recent N inference records."""
    # Production query:
    # from sqlalchemy import text
    # result = await db.execute(
    #     text(
    #         "SELECT COUNT(*) FROM inference_logs "
    #         "WHERE is_anomalous = TRUE "
    #         "ORDER BY created_at DESC LIMIT :n"
    #     ),
    #     {"n": n_recent},
    # )
    # return result.scalar() or 0
    return 12  # stub: 12 anomalies in last 500 observations


# ---------------------------------------------------------------------------
# Main pipeline entry-point
# ---------------------------------------------------------------------------


async def run_drift_evaluation(
    db: "AsyncSession",
    n_production: int = 500,
    reference_histogram: list[float] | None = None,
) -> DriftSuiteResult:
    """Run the full drift evaluation pipeline and emit incidents on MAJOR drift.

    This is the primary scheduled entry-point. Call it every N hours from
    your task scheduler (Airflow, Celery beat, APScheduler, etc.).

    Args:
        db:                   Async SQLAlchemy session (injected by scheduler).
        n_production:         Number of recent inference records to evaluate.
        reference_histogram:  Optional override for the reference histogram.
                              Defaults to the training-time baseline stub.

    Returns:
        DriftSuiteResult — structured, immutable, JSON-serialisable.
    """
    log.info("drift_pipeline.started", n_production=n_production)

    # Step 1: Fetch recent scores from DB
    recent_scores = await _fetch_recent_anomaly_scores(db, n_recent=n_production)
    anomaly_count = await _count_recent_anomalies(db, n_recent=n_production)

    # Step 2: Build production histogram
    production_histogram = _scores_to_histogram(recent_scores)
    ref_histogram = reference_histogram or _REFERENCE_SCORES_STUB

    # Step 3: Run the full drift suite
    result = check_drift_suite(
        reference=ref_histogram,
        production=production_histogram,
        label="anomaly_score",
        anomaly_count=anomaly_count,
    )

    # Step 4: Log the full result as a structured event
    log.info(
        "drift_pipeline.complete",
        psi=result.psi_result.psi,
        psi_severity=result.psi_result.severity.value,
        overall_severity=result.overall_severity.value,
        drifted_features=result.drifted_features,
        anomaly_count=result.anomaly_count,
        notes=result.notes,
    )

    # Step 5: Emit a drift incident if severity is MAJOR
    if result.overall_severity == DriftSeverity.MAJOR:
        log.error(
            "drift_pipeline.major_drift_detected",
            psi=result.psi_result.psi,
            anomaly_count=result.anomaly_count,
            action="evaluate_model_retraining",
            runbook="runbooks/model_degradation.md",
        )
        # Production hook: create a drift incident via the incident service.
        # Uncomment when incident service is wired into the scheduler context:
        #
        # from src.services.incident_service import IncidentService
        # await IncidentService(db).create_incident(
        #     title=f"Model drift detected: PSI={result.psi_result.psi:.4f}",
        #     severity="SEV-2",
        #     description=str(result.notes),
        #     source="drift_pipeline",
        # )

    return result


# ---------------------------------------------------------------------------
# CLI entry-point for `make drift-check`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def _demo() -> None:
        """Demonstrate drift evaluation with stub data (no real DB required)."""
        # Use None for db — stubs will be invoked
        result = await run_drift_evaluation(db=None, n_production=500)  # type: ignore[arg-type]
        print(f"Drift severity: {result.overall_severity.value}")
        print(f"PSI: {result.psi_result.psi:.4f}")
        print(f"Notes: {result.notes}")

    asyncio.run(_demo())
