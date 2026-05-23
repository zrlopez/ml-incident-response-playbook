"""
monitoring/monitoring_example.py — Runnable drift monitoring example.

Demonstrates how drift_check utilities integrate into a scheduled
monitoring job. Run directly:

    python -m monitoring.monitoring_example

The synthetic data below represents a feature snapshot from a binary
classification model serving credit-risk predictions. Two features are
deliberately drifted to demonstrate breach detection and PSI thresholds.

In production this script would:
  1. Pull current-window feature stats from your feature store or data
     warehouse (e.g. BigQuery, Snowflake, or a dbt mart).
  2. Pull the training-window baseline stats from model metadata storage.
  3. Publish results to Prometheus via the scan_features() gauge export
     (prometheus_client must be installed and the pushgateway configured).
  4. Run as an Airflow task on a schedule matching your SLO cadence.
"""
from __future__ import annotations

import sys
from monitoring.drift_check import DriftScanError, psi_score, scan_features


def main() -> int:
    # ── Synthetic feature snapshot (current window vs training baseline) ───────
    # In production: replace with live feature store queries.
    current = {
        "prediction_confidence":  0.71,   # was 0.82 at training — notable drop
        "input_token_count":      148.0,  # was 140 — within noise
        "response_latency_p50":   0.38,   # was 0.31 — moderate increase
        "null_feature_rate":      0.063,  # was 0.040 — data quality concern
        "request_rate_per_min":   312.0,  # was 300 — within SLO
    }
    baseline = {
        "prediction_confidence":  0.82,
        "input_token_count":      140.0,
        "response_latency_p50":   0.31,
        "null_feature_rate":      0.040,
        "request_rate_per_min":   300.0,
    }

    print("\n=== Feature Drift Scan (threshold: 20%) ===")
    print(f"{'Feature':<30} {'Current':>10} {'Baseline':>10} {'Drift Ratio':>12} {'Status':>12}")
    print("-" * 78)

    exit_code = 0
    try:
        results = scan_features(
            current=current,
            baseline=baseline,
            threshold=0.20,
            raise_on_breach=True,
            export_prometheus=False,   # no Pushgateway in this example
        )
    except DriftScanError as exc:
        # DriftScanError is raised AFTER all features are evaluated.
        # The results dict is populated regardless; we re-run without raise
        # to get the full result set for display.
        results = scan_features(
            current=current,
            baseline=baseline,
            threshold=0.20,
            raise_on_breach=False,
            export_prometheus=False,
        )
        print(f"\n[DRIFT SCAN ERROR] {exc}\n")
        exit_code = 1

    for feat, r in results.items():
        if r.skipped:
            status = "SKIPPED"
            ratio_str = "n/a"
        elif r.drifting:
            status = "BREACH"
            ratio_str = f"{r.drift_ratio:.4f}"
        else:
            status = "ok"
            ratio_str = f"{r.drift_ratio:.4f}"
        print(f"{feat:<30} {r.current_mean:>10.4f} {r.baseline_mean:>10.4f} {ratio_str:>12} {status:>12}")

    # ── PSI example: credit score band distribution ───────────────────────────
    print("\n=== PSI: prediction_confidence_band ===")
    print("Bins: [<0.5, 0.5-0.6, 0.6-0.7, 0.7-0.8, 0.8-0.9, ≥0.9]")

    # Simulated count distributions across confidence bins
    # (what we'd get from a histogram query against the feature store)
    actual_counts   = [55, 90, 210, 380, 180, 85]   # current window
    expected_counts = [20, 60, 150, 420, 240, 110]   # training baseline

    psi = psi_score(
        actual_counts=actual_counts,
        expected_counts=expected_counts,
        label="prediction_confidence_band",
    )

    if psi < 0.10:
        severity = "STABLE   — no action required"
    elif psi < 0.25:
        severity = "MODERATE — investigate upstream data pipeline"
    else:
        severity = "SIGNIFICANT — consider model retraining"

    print(f"PSI = {psi:.4f}  [{severity}]")

    print("\nComplete. See monitoring/metrics.md for metric catalog.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
