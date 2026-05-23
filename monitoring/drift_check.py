"""
monitoring/drift_check.py — ML feature drift detection utilities.

Provides three complementary approaches to drift detection:

1. drift_ratio()  — Relative deviation for a single scalar mean.
   Use when you have pre-aggregated mean values and want a fast,
   interpretable signal. Raises on zero baseline.

2. psi_score()  — Population Stability Index across binned distributions.
   Standard ML monitoring statistic. Captures distributional shift that
   a simple mean comparison misses (e.g., distribution shape changes while
   mean stays constant). Thresholds: < 0.10 stable, 0.10–0.25 moderate,
   > 0.25 significant drift requiring investigation.

3. scan_features()  — Batch drift check across a feature dict.
   Returns a DriftResult per feature, optionally exports to Prometheus,
   raises DriftScanError if any feature exceeds the threshold.
   Designed for use in scheduled monitoring jobs.

All functions emit structured structlog events for audit trail and
downstream log aggregation (Loki, CloudWatch Logs, etc.).

Usage examples:

    # Single mean comparison
    from monitoring.drift_check import drift_ratio, is_drifting
    ratio = drift_ratio(current_mean=0.35, baseline_mean=0.30, label="score")

    # Distributional shift via PSI
    from monitoring.drift_check import psi_score
    psi = psi_score(
        actual_counts=[45, 120, 200, 130, 55],
        expected_counts=[50, 110, 210, 120, 60],
        label="credit_score_band",
    )

    # Batch scan
    from monitoring.drift_check import scan_features
    results = scan_features(
        current={"score": 0.35, "count": 142.0},
        baseline={"score": 0.30, "count": 138.0},
        threshold=0.20,
    )
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import structlog

log = structlog.get_logger(__name__)


# ── Exceptions ───────────────────────────────────────────────────────────────────

class DriftScanError(Exception):
    """
    Raised by scan_features() when one or more features exceed the drift
    threshold. The exception message contains a summary of all breaching
    features so callers can log or alert without iterating results.
    """


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class DriftResult:
    """
    Outcome of a single-feature drift evaluation.

    Attributes:
        label:          Feature or metric name.
        current_mean:   Observed mean in the current window.
        baseline_mean:  Expected mean from the training or reference window.
        drift_ratio:    Relative deviation (None if baseline is zero).
        drifting:       True if drift_ratio exceeds the configured threshold.
        skipped:        True if evaluation could not be completed
                        (e.g. zero baseline, non-numeric input).
        skip_reason:    Human-readable explanation if skipped=True.
    """
    label: str
    current_mean: float
    baseline_mean: float
    drift_ratio: float | None = None
    drifting: bool = False
    skipped: bool = False
    skip_reason: str = ""


# ── Core: relative mean deviation ──────────────────────────────────────────────

def drift_ratio(
    current_mean: float,
    baseline_mean: float,
    label: str = "feature",
) -> float:
    """
    Compute the absolute relative deviation between current and baseline means.

    Returns:
        Non-negative fractional deviation: abs(current - baseline) / abs(baseline).
        0.20 means 20% drift.

    Raises:
        ValueError: baseline_mean is zero (division undefined).
        TypeError:  Either argument is not numeric.
    """
    if not isinstance(current_mean, (int, float)) or not isinstance(baseline_mean, (int, float)):
        raise TypeError(
            f"[drift_ratio] Both arguments must be numeric. "
            f"Got current_mean={type(current_mean).__name__}, "
            f"baseline_mean={type(baseline_mean).__name__}"
        )
    if baseline_mean == 0:
        raise ValueError(
            f"[drift_ratio] baseline_mean for '{label}' is zero. "
            "Relative drift against zero is undefined. "
            "Use an absolute threshold check for near-zero baselines, "
            "or use psi_score() for distributional comparisons."
        )
    ratio = abs(current_mean - baseline_mean) / abs(baseline_mean)
    log.debug(
        "drift_ratio.computed",
        label=label,
        current_mean=round(current_mean, 6),
        baseline_mean=round(baseline_mean, 6),
        drift_ratio=round(ratio, 6),
        breached_20pct=(ratio > 0.20),
    )
    return ratio


def is_drifting(
    current_mean: float,
    baseline_mean: float,
    threshold: float = 0.20,
    label: str = "feature",
) -> bool:
    """
    Convenience wrapper: True if drift_ratio > threshold.
    Handles zero-baseline gracefully (returns False with warning log).
    """
    try:
        return drift_ratio(current_mean, baseline_mean, label=label) > threshold
    except ValueError:
        log.warning(
            "drift_ratio.zero_baseline_skipped",
            label=label,
            current_mean=current_mean,
            message="Zero baseline — use absolute threshold check instead.",
        )
        return False


# ── PSI: Population Stability Index ───────────────────────────────────────────

def psi_score(
    actual_counts: Sequence[int | float],
    expected_counts: Sequence[int | float],
    label: str = "feature",
    epsilon: float = 1e-6,
) -> float:
    """
    Compute the Population Stability Index (PSI) for a binned feature.

    PSI measures how much a distribution has shifted relative to a reference.
    It is a standard statistic in model monitoring, originating in credit
    risk model validation and widely adopted in ML platform monitoring.

    Formula (per bin i, summed over all bins):
        PSI = sum( (A_i - E_i) * ln(A_i / E_i) )
    where A_i = actual proportion in bin i, E_i = expected proportion in bin i.

    Interpretation:
        PSI < 0.10  : No significant distributional shift.
        0.10 – 0.25 : Moderate shift — investigate upstream data pipeline.
        PSI > 0.25  : Significant shift — model likely needs retraining.

    Args:
        actual_counts:   Observed counts per bin in the current window.
                         Must have the same length as expected_counts.
        expected_counts: Reference/training distribution counts per bin.
        label:           Feature name for structured logging.
        epsilon:         Small constant added to zero proportions to avoid
                         log(0). Default 1e-6 is negligible for typical
                         count magnitudes (>100 total).

    Returns:
        Non-negative PSI value.

    Raises:
        ValueError: Sequences have different lengths, or all counts are zero.
        TypeError:  Non-numeric values in either sequence.
    """
    if len(actual_counts) != len(expected_counts):
        raise ValueError(
            f"[psi_score] actual_counts and expected_counts must have the same "
            f"length. Got {len(actual_counts)} vs {len(expected_counts)}."
        )
    if len(actual_counts) == 0:
        raise ValueError("[psi_score] Count sequences must not be empty.")

    for seq_name, seq in (("actual_counts", actual_counts), ("expected_counts", expected_counts)):
        for i, v in enumerate(seq):
            if not isinstance(v, (int, float)):
                raise TypeError(
                    f"[psi_score] {seq_name}[{i}] must be numeric, got {type(v).__name__}."
                )

    total_actual = sum(actual_counts)
    total_expected = sum(expected_counts)

    if total_actual == 0 or total_expected == 0:
        raise ValueError(
            f"[psi_score] All counts are zero for feature '{label}'. "
            "Cannot compute proportions from an empty distribution."
        )

    psi = 0.0
    for a_count, e_count in zip(actual_counts, expected_counts):
        # Convert to proportions; add epsilon to prevent log(0)
        a_prop = max(a_count / total_actual, epsilon)
        e_prop = max(e_count / total_expected, epsilon)
        psi += (a_prop - e_prop) * math.log(a_prop / e_prop)

    psi = round(psi, 6)

    severity = (
        "stable" if psi < 0.10
        else "moderate_shift" if psi < 0.25
        else "significant_shift"
    )

    log.info(
        "psi_score.computed",
        label=label,
        psi=psi,
        severity=severity,
        n_bins=len(actual_counts),
        total_actual=total_actual,
        total_expected=total_expected,
    )

    return psi


# ── Batch scanner ────────────────────────────────────────────────────────────────

def scan_features(
    current: dict[str, float],
    baseline: dict[str, float],
    threshold: float = 0.20,
    raise_on_breach: bool = True,
    export_prometheus: bool = True,
) -> dict[str, DriftResult]:
    """
    Run drift_ratio() across all features in a dict and return structured results.

    Designed for scheduled monitoring jobs. Evaluates every feature present
    in both current and baseline, skips gracefully on zero-baseline or
    non-numeric values, and optionally exports each ratio to a Prometheus
    gauge (ml_feature_drift_ratio{feature_name=...}) if prometheus_client
    is installed.

    Args:
        current:           Dict mapping feature name → current window mean.
        baseline:          Dict mapping feature name → reference window mean.
        threshold:         Drift ratio above which a feature is considered
                           breaching. Default: 0.20 (20%).
        raise_on_breach:   If True, raises DriftScanError after evaluating all
                           features when any breach is detected. All features
                           are evaluated regardless — the error is raised at
                           the end so the caller gets the full result dict.
        export_prometheus: If True, set the ml_feature_drift_ratio gauge for
                           each evaluated feature. Silently skips if
                           prometheus_client is not installed.

    Returns:
        Dict mapping feature name → DriftResult.

    Raises:
        DriftScanError: One or more features exceeded threshold
                        (only when raise_on_breach=True).
    """
    # Attempt Prometheus gauge setup — no hard dependency
    _gauge = None
    if export_prometheus:
        try:
            from prometheus_client import Gauge
            _gauge = Gauge(
                "ml_feature_drift_ratio",
                "Relative drift ratio per feature (current vs baseline mean)",
                ["feature_name"],
            )
        except ImportError:
            log.debug("scan_features.prometheus_unavailable",
                      message="prometheus_client not installed; skipping metric export.")
        except Exception:
            # Gauge already registered (e.g. test re-runs) — fetch existing
            try:
                from prometheus_client import REGISTRY
                _gauge = REGISTRY._names_to_collectors.get("ml_feature_drift_ratio")
            except Exception:
                pass

    all_features = set(current) & set(baseline)
    results: dict[str, DriftResult] = {}
    breaching: list[str] = []

    for feat in sorted(all_features):
        c_val = current[feat]
        b_val = baseline[feat]
        result = DriftResult(label=feat, current_mean=c_val, baseline_mean=b_val)

        try:
            ratio = drift_ratio(c_val, b_val, label=feat)
            result.drift_ratio = ratio
            result.drifting = ratio > threshold

            if _gauge is not None:
                try:
                    _gauge.labels(feature_name=feat).set(ratio)
                except Exception:
                    pass

            if result.drifting:
                breaching.append(feat)
                log.warning(
                    "scan_features.breach",
                    feature=feat,
                    drift_ratio=round(ratio, 4),
                    threshold=threshold,
                )

        except (ValueError, TypeError) as exc:
            result.skipped = True
            result.skip_reason = str(exc)
            log.warning(
                "scan_features.skipped",
                feature=feat,
                reason=result.skip_reason,
            )

        results[feat] = result

    log.info(
        "scan_features.complete",
        total=len(results),
        breaching=len(breaching),
        skipped=sum(1 for r in results.values() if r.skipped),
        threshold=threshold,
    )

    if raise_on_breach and breaching:
        raise DriftScanError(
            f"{len(breaching)} feature(s) exceeded drift threshold {threshold}: "
            + ", ".join(f"{f} ({results[f].drift_ratio:.4f})" for f in breaching)
        )

    return results
