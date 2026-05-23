from __future__ import annotations


def drift_ratio(current_mean: float, baseline_mean: float) -> float:
    if baseline_mean == 0:
        return 0.0
    return abs(current_mean - baseline_mean) / abs(baseline_mean)
