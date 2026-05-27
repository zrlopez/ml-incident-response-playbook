"""
observability/monitoring_example.py — Reference monitoring integration
======================================================================
Demonstrates the full observability stack for the ML Incident Response API:

  1. Structured logging   (structlog JSON)
  2. OpenTelemetry traces (otel_setup.configure_otel)
  3. Prometheus metrics   (prometheus_client counters + histograms)
  4. Drift detection      (drift_check.check_drift_suite)
  5. Anomaly detection    (anomaly_detection.check_multiple)

This module is intentionally runnable as a standalone script via
``python -m observability.monitoring_example`` and is importable for
unit tests without side effects until ``run_example()`` is called.

Prometheus metrics registered here:
  ml_drift_events_total{severity, model}       — Counter
  ml_anomaly_threshold_breaches_total{label}   — Counter
  ml_prediction_latency_seconds{model}         — Histogram
  ml_psi_score{model}                          — Gauge

Usage::

    # In application lifespan startup, after configure_otel():
    from observability.monitoring_example import register_metrics
    register_metrics()

    # In a background drift-check job:
    from observability.monitoring_example import run_drift_check_job
    await run_drift_check_job(
        model_name="risk_scorer_v3",
        reference_scores=reference_window,
        production_scores=current_window,
    )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from prometheus_client import Counter, Gauge

import structlog

from observability.anomaly_detection import check_multiple
from observability.drift_check import DriftSeverity, check_drift_suite

log = structlog.get_logger(__name__)
_stdlib_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metric objects (module-level singletons)
# These are lazily initialised by register_metrics() so that importing this
# module in unit tests does not require prometheus_client to be installed or
# a Prometheus registry to be running.
# ---------------------------------------------------------------------------

_METRICS_REGISTERED: bool = False
_drift_events_counter: Optional["Counter"] = None
_anomaly_breach_counter: Optional["Counter"] = None
_prediction_latency_hist: Optional[object] = None
_psi_gauge: Optional["Gauge"] = None


def register_metrics() -> bool:  # noqa: C901  (complexity is inherent to conditional imports)
    """Register Prometheus metrics.  Safe to call multiple times (idempotent).

    Returns:
        True if metrics were registered, False if prometheus_client is absent
        (non-fatal — the rest of the monitoring stack continues to work).
    """
    global _METRICS_REGISTERED, _drift_events_counter
    global _anomaly_breach_counter, _prediction_latency_hist, _psi_gauge

    if _METRICS_REGISTERED:
        return True

    try:
        from prometheus_client import Counter, Gauge, Histogram
    except ImportError:
        _stdlib_log.warning(
            "monitoring_example: prometheus_client not installed — "
            "Prometheus metrics disabled.  "
            "Add prometheus-client to requirements.txt to enable."
        )
        return False

    _drift_events_counter = Counter(
        "ml_drift_events_total",
        "Total number of drift events detected by the drift check suite",
        ["severity", "model"],
    )
    _anomaly_breach_counter = Counter(
        "ml_anomaly_threshold_breaches_total",
        "Total number of anomaly threshold breaches detected",
        ["label"],
    )
    _prediction_latency_hist = Histogram(
        "ml_prediction_latency_seconds",
        "Model prediction latency in seconds",
        ["model"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    )
    _psi_gauge = Gauge(
        "ml_psi_score",
        "Most recent Population Stability Index score for a model",
        ["model"],
    )
    _METRICS_REGISTERED = True
    log.info("monitoring.prometheus_metrics_registered")
    return True


# ---------------------------------------------------------------------------
# Core job
# ---------------------------------------------------------------------------


def run_drift_check_job(
    model_name: str,
    reference_scores: list[float],
    production_scores: list[float],
    feature_distributions: Optional[dict[str, tuple[list[float], list[float]]]] = None,
    anomaly_metrics: Optional[dict[str, tuple[float, float]]] = None,
) -> dict[str, object]:
    """Run a complete model health check and record Prometheus metrics.

    This function is the recommended integration point between the drift/anomaly
    detection subsystem and the incident response pipeline.  It is:
      - Synchronous (can be called from a background task, APScheduler job, etc.)
      - Side-effect-free with respect to the database (no DB writes)
      - Observable: every check emits structured log events and Prometheus counters

    Args:
        model_name:             Identifier used in Prometheus labels + log context.
        reference_scores:       Histogram of reference/training score distribution.
        production_scores:      Histogram of current production score distribution.
        feature_distributions:  Optional per-feature (ref, prod) histogram pairs.
        anomaly_metrics:        Optional dict of metric label -> (current, baseline)
                                pairs, passed directly to anomaly_detection.check_multiple.

    Returns:
        Serialisable summary dict (PSI, severity, drifted features, anomaly count).
    """
    log.info("monitoring.drift_check_job_start", model=model_name)

    # --- 1. Anomaly check (used to escalate drift severity) ---
    anomaly_count = 0
    if anomaly_metrics:
        anomaly_results = check_multiple(anomaly_metrics)
        breached = [k for k, v in anomaly_results.items() if v.breached]
        anomaly_count = len(breached)
        if _METRICS_REGISTERED and _anomaly_breach_counter is not None:
            for label in breached:
                _anomaly_breach_counter.labels(label=label).inc()  # type: ignore[union-attr]

    # --- 2. Drift suite ---
    suite = check_drift_suite(
        reference=reference_scores,
        production=production_scores,
        feature_distributions=feature_distributions,
        label=f"{model_name}_score",
        anomaly_count=anomaly_count,
    )

    # --- 3. Prometheus instrumentation ---
    if _METRICS_REGISTERED:
        if _psi_gauge is not None:
            _psi_gauge.labels(model=model_name).set(suite.psi_result.psi)  # type: ignore[union-attr]  # noqa: E501
        if _drift_events_counter is not None and suite.psi_result.severity != DriftSeverity.NO_DRIFT:  # noqa: E501
            _drift_events_counter.labels(  # type: ignore[union-attr]
                severity=suite.overall_severity.value,
                model=model_name,
            ).inc()

    # --- 4. Incident escalation log ---
    if suite.overall_severity == DriftSeverity.MAJOR:
        log.error(
            "monitoring.drift_incident_escalation",
            model=model_name,
            psi=suite.psi_result.psi,
            drifted_features=suite.drifted_features,
            anomaly_count=anomaly_count,
            action="open_incident",
        )
    elif suite.overall_severity == DriftSeverity.MINOR:
        log.warning(
            "monitoring.drift_minor_alert",
            model=model_name,
            psi=suite.psi_result.psi,
            drifted_features=suite.drifted_features,
        )

    summary: dict[str, object] = {
        "model": model_name,
        "psi": suite.psi_result.psi,
        "psi_severity": suite.psi_result.severity.value,
        "overall_severity": suite.overall_severity.value,
        "drifted_features": suite.drifted_features,
        "anomaly_count": anomaly_count,
        "notes": suite.notes,
    }
    log.info("monitoring.drift_check_job_complete", **summary)
    return summary


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------


def run_example() -> None:
    """Standalone demonstration of the full monitoring stack.

    Exercises:
      - PSI check: stable distribution (no drift)
      - PSI check: shifted distribution (minor drift)
      - PSI check: heavily shifted (major drift)
      - Feature-level JSD drift
      - Anomaly detection co-occurrence → severity escalation
    """
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    register_metrics()

    print("\n=== Scenario 1: Stable distribution ===\n")
    result = run_drift_check_job(
        model_name="risk_scorer_v3",
        reference_scores=[100, 200, 300, 250, 100, 50],
        production_scores=[105, 195, 305, 245, 98, 52],  # ~2% deviation
    )
    print(f"  Severity: {result['overall_severity']}  PSI: {result['psi']}")

    print("\n=== Scenario 2: Minor drift ===\n")
    result = run_drift_check_job(
        model_name="risk_scorer_v3",
        reference_scores=[100, 200, 300, 250, 100, 50],
        production_scores=[80, 160, 280, 270, 130, 80],  # ~12% PSI
    )
    print(f"  Severity: {result['overall_severity']}  PSI: {result['psi']}")

    print("\n=== Scenario 3: Major drift + feature shift ===\n")
    result = run_drift_check_job(
        model_name="risk_scorer_v3",
        reference_scores=[100, 200, 300, 250, 100, 50],
        production_scores=[20, 50, 100, 300, 350, 180],  # heavily shifted
        feature_distributions={
            "age": ([100, 200, 300, 200, 100], [50, 80, 150, 300, 420]),
            "income": ([200, 300, 300, 150, 50], [210, 295, 310, 145, 40]),
        },
    )
    print(f"  Severity: {result['overall_severity']}  PSI: {result['psi']}")
    print(f"  Drifted features: {result['drifted_features']}")

    print("\n=== Scenario 4: Anomaly co-occurrence → severity escalation ===\n")
    result = run_drift_check_job(
        model_name="risk_scorer_v3",
        reference_scores=[100, 200, 300, 250, 100, 50],
        production_scores=[95, 205, 295, 255, 102, 48],  # PSI < 0.10 (stable)
        anomaly_metrics={
            "prediction_volume": (150.0, 1000.0),   # -85% → breach
            "error_rate": (0.18, 0.05),              # +260% → breach
        },
    )
    print(f"  Severity: {result['overall_severity']}  (escalated from no_drift due to anomalies)")
    print(f"  Notes: {result['notes']}")


if __name__ == "__main__":
    run_example()
