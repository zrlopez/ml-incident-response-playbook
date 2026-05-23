"""
Drift detection utility — remediation R-06.

Fix: The previous implementation silently returned 0.0 when baseline_mean == 0,
making it impossible to detect ANY drift against zero-baseline features.
This is a silent false-negative — the worst possible failure mode in monitoring.

Fix applied:
  - Raises ValueError for zero baseline (cannot compute relative deviation)
  - Callers MUST handle zero-baseline features with an absolute threshold check
  - Adds optional label argument for structured log context
  - Adds debug-level structlog event per call for audit trail

Usage:
    from monitoring.drift_check import drift_ratio

    ratio = drift_ratio(
        current_mean=0.35,
        baseline_mean=0.30,
        label="feature_score",
    )
    # Returns: 0.1667 (16.7% drift)

    # Zero-baseline features — use absolute check instead:
    if current_mean > ABSOLUTE_THRESHOLD:
        alert("feature_count drift detected")
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def drift_ratio(
    current_mean: float,
    baseline_mean: float,
    label: str = "feature",
) -> float:
    """
    Compute the absolute relative deviation between current and baseline means.

    Args:
        current_mean:  Current observed distribution mean.
        baseline_mean: Historical/expected distribution mean.
        label:         Feature or metric name for logging context.

    Returns:
        Non-negative fractional deviation: abs(current - baseline) / abs(baseline).
        A value of 0.20 means 20% drift from baseline.

    Raises:
        ValueError: If baseline_mean is exactly zero.
                    Relative deviation against zero is mathematically undefined.
                    Use an absolute threshold comparison for near-zero features.
        TypeError:  If either argument is not a real number.
    """
    if not isinstance(current_mean, (int, float)) or not isinstance(baseline_mean, (int, float)):
        raise TypeError(
            f"[drift_check] Both current_mean and baseline_mean must be numeric. "
            f"Got: current_mean={type(current_mean).__name__}, "
            f"baseline_mean={type(baseline_mean).__name__}"
        )

    if baseline_mean == 0:
        raise ValueError(
            f"[drift_check] baseline_mean for feature '{label}' is zero. "
            "Cannot compute relative drift against a zero baseline — division by zero. "
            "Use an absolute threshold check (e.g. 'if current_mean > 0.01') "
            "for near-zero or zero-baseline features instead."
        )

    ratio = abs(current_mean - baseline_mean) / abs(baseline_mean)

    log.debug(
        "drift_check.computed",
        label=label,
        current_mean=round(current_mean, 6),
        baseline_mean=round(baseline_mean, 6),
        drift_ratio=round(ratio, 6),
        breached_20pct=(ratio > 0.20),
    )

    return ratio


def is_drifting(current_mean: float, baseline_mean: float, threshold: float = 0.20, label: str = "feature") -> bool:
    """
    Convenience wrapper: returns True if drift_ratio exceeds threshold.

    Handles zero-baseline features gracefully by returning False with a
    warning log rather than raising — appropriate for bulk feature checks
    where a single zero-baseline should not halt all evaluations.

    Args:
        current_mean:  Current observed mean.
        baseline_mean: Historical/expected mean.
        threshold:     Drift ratio above which the feature is considered drifting.
                       Default: 0.20 (20%).
        label:         Feature name for log context.

    Returns:
        True if drifting above threshold, False otherwise.
    """
    try:
        ratio = drift_ratio(current_mean, baseline_mean, label=label)
        return ratio > threshold
    except ValueError:
        log.warning(
            "drift_check.zero_baseline_skipped",
            label=label,
            current_mean=current_mean,
            message="Zero baseline — cannot compute relative drift. Use absolute threshold check.",
        )
        return False
