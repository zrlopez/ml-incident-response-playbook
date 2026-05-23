"""anomaly_detection.py — Hardened anomaly detection utilities (remediation initiative)

Fixes applied:
  - ZeroDivisionError guard
  - Bidirectional spike detection (up AND down)
  - Type safety via dataclasses + Pydantic-style validation
  - Structured logging via structlog
  - Unit-testable design (no side effects in core functions)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ThresholdResult:
    """Immutable result of a threshold check."""
    breached: bool
    direction: Optional[str]      # "high" | "low" | None
    current: float
    baseline: float
    pct_deviation: float
    message: str


def simple_threshold(
    current: float,
    baseline: float,
    pct: float = 0.20,
    check_low: bool = True,
    label: str = "metric",
) -> ThresholdResult:
    """Evaluate whether *current* deviates from *baseline* by more than *pct*.

    Fixes from audit:
      - Raises ValueError if baseline == 0 (was silent ZeroDivisionError risk)
      - Detects both upper (spike) AND lower (drop/silence) breaches
      - Returns structured ThresholdResult instead of bare bool
      - Emits structured log event on breach

    Args:
        current:    The current observed metric value.
        baseline:   The expected / historical baseline value.
        pct:        Fractional deviation threshold (default 20%).
        check_low:  If True, also alert on downward drop (data starvation, model silence).
        label:      Human-readable metric name for log context.

    Returns:
        ThresholdResult with breach status, direction, and diagnostic info.

    Raises:
        ValueError: If baseline is zero (cannot compute relative deviation).
        ValueError: If pct is not in (0, 1].
    """
    if baseline == 0:
        raise ValueError(
            f"[anomaly_detection] baseline must be non-zero for '{label}'. "
            "Cannot compute relative deviation against zero."
        )
    if not (0 < pct <= 1):
        raise ValueError(f"[anomaly_detection] pct must be in (0, 1]; got {pct}")

    pct_deviation = (current - baseline) / baseline  # Signed deviation

    high_breach = current > baseline * (1 + pct)
    low_breach = check_low and (current < baseline * (1 - pct))

    if high_breach:
        direction = "high"
        message = (
            f"{label} spiked {pct_deviation:.1%} above baseline "
            f"({current:.4g} vs {baseline:.4g}, threshold +{pct:.0%})"
        )
        log.warning(
            "anomaly.threshold_breach",
            label=label, direction=direction,
            current=current, baseline=baseline,
            pct_deviation=round(pct_deviation, 4),
        )
    elif low_breach:
        direction = "low"
        message = (
            f"{label} dropped {abs(pct_deviation):.1%} below baseline "
            f"({current:.4g} vs {baseline:.4g}, threshold -{pct:.0%})"
        )
        log.warning(
            "anomaly.threshold_breach",
            label=label, direction=direction,
            current=current, baseline=baseline,
            pct_deviation=round(pct_deviation, 4),
        )
    else:
        direction = None
        message = f"{label} within normal range ({current:.4g} vs baseline {baseline:.4g})"
        log.debug(
            "anomaly.within_range",
            label=label, current=current, baseline=baseline,
            pct_deviation=round(pct_deviation, 4),
        )

    return ThresholdResult(
        breached=high_breach or low_breach,
        direction=direction,
        current=current,
        baseline=baseline,
        pct_deviation=round(pct_deviation, 4),
        message=message,
    )


def check_multiple(
    metrics: dict[str, tuple[float, float]],
    pct: float = 0.20,
    check_low: bool = True,
) -> dict[str, ThresholdResult]:
    """Run threshold checks against multiple metrics at once.

    Args:
        metrics: Mapping of label -> (current, baseline) tuples.
        pct:     Shared deviation threshold.
        check_low: Whether to check downward deviations.

    Returns:
        Mapping of label -> ThresholdResult for every metric.
    """
    results: dict[str, ThresholdResult] = {}
    for label, (current, baseline) in metrics.items():
        results[label] = simple_threshold(
            current=current, baseline=baseline,
            pct=pct, check_low=check_low, label=label,
        )
    breached = [k for k, v in results.items() if v.breached]
    if breached:
        log.warning("anomaly.multi_check_summary", total_checked=len(metrics), breached=breached)
    else:
        log.info("anomaly.multi_check_summary", total_checked=len(metrics), breached=[])
    return results
