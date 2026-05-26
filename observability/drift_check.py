"""
observability/drift_check.py — Production-grade ML drift detection
====================================================================
Implements:
  - Population Stability Index (PSI) for distribution-level drift
  - Symmetric KL-divergence approximation for per-feature drift
  - A unified ``check_drift_suite()`` entry-point for the incident pipeline

Design principles (consistent with anomaly_detection.py):
  - Pure functions with no I/O side effects — fully unit-testable
  - Immutable result dataclasses
  - Structured logging via structlog
  - Raises on invalid inputs (fail-fast, no silent NaN propagation)

PSI Severity thresholds (industry standard):
  PSI < 0.10  → no_drift    (stable distribution)
  PSI < 0.20  → minor_drift (monitor, no action required)
  PSI >= 0.20 → major_drift (re-training trigger)

Usage::

    from observability.drift_check import check_drift_suite, DriftSeverity

    result = check_drift_suite(
        reference=reference_scores,
        production=production_scores,
        feature_distributions={
            "age": (ref_age_hist, prod_age_hist),
            "income": (ref_income_hist, prod_income_hist),
        },
    )
    if result.psi_result.severity == DriftSeverity.MAJOR:
        trigger_retraining_incident(result)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Small epsilon prevents log(0) and division-by-zero in PSI / KL calculations.
_EPSILON: float = 1e-8

# Industry-standard PSI thresholds.
_PSI_MINOR_THRESHOLD: float = 0.10
_PSI_MAJOR_THRESHOLD: float = 0.20

# KL-divergence threshold above which a single feature is flagged as drifted.
_KL_DRIFT_THRESHOLD: float = 0.10


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------


class DriftSeverity(str, Enum):
    """Ordinal severity levels for drift events."""
    NO_DRIFT = "no_drift"
    MINOR = "minor_drift"
    MAJOR = "major_drift"


@dataclass(frozen=True)
class PsiResult:
    """Immutable result of a Population Stability Index check."""
    psi: float
    severity: DriftSeverity
    n_bins: int
    message: str


@dataclass(frozen=True)
class FeatureDriftResult:
    """KL-divergence drift result for a single feature."""
    feature: str
    kl_divergence: float
    drifted: bool
    message: str


@dataclass(frozen=True)
class DriftSuiteResult:
    """Aggregated result from a full drift check suite run."""
    psi_result: PsiResult
    feature_results: dict[str, FeatureDriftResult]
    drifted_features: list[str]
    overall_severity: DriftSeverity
    anomaly_count: int = 0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _normalize_histogram(hist: list[float]) -> list[float]:
    """Normalize a histogram to a probability distribution.

    Args:
        hist: Raw bucket counts or frequencies (all values must be >= 0).

    Returns:
        Normalized probability values that sum to ~1.0.

    Raises:
        ValueError: If hist is empty or all-zero.
    """
    if not hist:
        raise ValueError("drift_check: histogram must not be empty")
    total = sum(hist)
    if total <= 0:
        raise ValueError(
            f"drift_check: histogram sum must be positive, got {total}. "
            "Cannot normalize an all-zero distribution."
        )
    return [v / total for v in hist]


def compute_psi(
    reference: list[float],
    production: list[float],
    label: str = "score",
) -> PsiResult:
    """Compute the Population Stability Index between two distributions.

    PSI = sum_i ( (P_i - Q_i) * ln(P_i / Q_i) )

    where P is the reference (training) distribution and Q is the
    production (inference) distribution, bucketed identically.

    Args:
        reference:  Reference histogram bucket counts (one per bin).
        production: Production histogram bucket counts (same bin layout).
        label:      Metric label used in log events.

    Returns:
        PsiResult with PSI value, severity, bin count, and a message.

    Raises:
        ValueError: If the two histograms have different lengths or are empty.
    """
    if len(reference) != len(production):
        raise ValueError(
            f"drift_check: reference and production histograms must have the same "
            f"number of bins; got {len(reference)} vs {len(production)}."
        )
    if len(reference) == 0:
        raise ValueError("drift_check: histograms must not be empty.")

    ref_p = _normalize_histogram(reference)
    prod_p = _normalize_histogram(production)

    psi = sum(
        (p - q) * math.log((p + _EPSILON) / (q + _EPSILON))
        for p, q in zip(ref_p, prod_p)
    )
    psi = round(psi, 6)

    if psi < _PSI_MINOR_THRESHOLD:
        severity = DriftSeverity.NO_DRIFT
        message = (
            f"{label}: PSI={psi:.4f} — distribution stable "
            f"(threshold <{_PSI_MINOR_THRESHOLD})"
        )
        log.info("drift.psi_check", label=label, psi=psi, severity=severity.value)
    elif psi < _PSI_MAJOR_THRESHOLD:
        severity = DriftSeverity.MINOR
        message = (
            f"{label}: PSI={psi:.4f} — minor drift detected "
            f"(threshold {_PSI_MINOR_THRESHOLD}–{_PSI_MAJOR_THRESHOLD}). Monitor."
        )
        log.warning("drift.psi_minor", label=label, psi=psi, severity=severity.value)
    else:
        severity = DriftSeverity.MAJOR
        message = (
            f"{label}: PSI={psi:.4f} — MAJOR drift detected "
            f"(threshold >={_PSI_MAJOR_THRESHOLD}). Consider retraining."
        )
        log.error(
            "drift.psi_major",
            label=label,
            psi=psi,
            severity=severity.value,
            action_required="evaluate_retraining",
        )

    return PsiResult(
        psi=psi,
        severity=severity,
        n_bins=len(reference),
        message=message,
    )


def compute_feature_drift(
    feature: str,
    reference_hist: list[float],
    production_hist: list[float],
) -> FeatureDriftResult:
    """Compute symmetric KL-divergence between two feature distributions.

    Uses the M-projection (Jensen-Shannon midpoint) for numerical symmetry:
        KL_sym = 0.5 * KL(P || M) + 0.5 * KL(Q || M)  where M = 0.5*(P+Q)

    This is equivalent to Jensen-Shannon divergence (JSD), bounded in [0, ln2].
    Values above ``_KL_DRIFT_THRESHOLD`` (default 0.10) indicate meaningful
    distributional shift.

    Args:
        feature:         Feature name for log context.
        reference_hist:  Reference histogram bucket counts.
        production_hist: Production histogram bucket counts.

    Returns:
        FeatureDriftResult with KL value, drift flag, and message.
    """
    ref_p = _normalize_histogram(reference_hist)
    prod_p = _normalize_histogram(production_hist)

    # Compute Jensen-Shannon divergence (symmetric, bounded)
    m = [(p + q) / 2 for p, q in zip(ref_p, prod_p)]

    def _kl(p_dist: list[float], q_dist: list[float]) -> float:
        return sum(
            pi * math.log((pi + _EPSILON) / (qi + _EPSILON))
            for pi, qi in zip(p_dist, q_dist)
        )

    jsd = round(0.5 * _kl(ref_p, m) + 0.5 * _kl(prod_p, m), 6)
    drifted = jsd >= _KL_DRIFT_THRESHOLD

    if drifted:
        message = (
            f"feature '{feature}': JSD={jsd:.4f} >= threshold {_KL_DRIFT_THRESHOLD} — "
            "distributional shift detected."
        )
        log.warning(
            "drift.feature_shift",
            feature=feature,
            jsd=jsd,
            threshold=_KL_DRIFT_THRESHOLD,
        )
    else:
        message = (
            f"feature '{feature}': JSD={jsd:.4f} < threshold {_KL_DRIFT_THRESHOLD} — "
            "stable."
        )
        log.debug("drift.feature_stable", feature=feature, jsd=jsd)

    return FeatureDriftResult(
        feature=feature,
        kl_divergence=jsd,
        drifted=drifted,
        message=message,
    )


def check_drift_suite(
    reference: list[float],
    production: list[float],
    feature_distributions: Optional[dict[str, tuple[list[float], list[float]]]] = None,
    label: str = "model_score",
    anomaly_count: int = 0,
) -> DriftSuiteResult:
    """Run the full drift check suite: PSI + per-feature JSD + anomaly gate.

    This is the primary entry-point for the incident response pipeline.
    A single call evaluates the complete model health picture and returns
    a structured, immutable result safe to serialize to JSON.

    Args:
        reference:             Reference (training/baseline) score distribution.
        production:            Current production score distribution.
        feature_distributions: Optional mapping of feature name ->
                               (reference_hist, production_hist).
        label:                 Label for the primary PSI check log events.
        anomaly_count:         Number of anomaly alerts fired in the same
                               window (from anomaly_detection.check_multiple).
                               Incorporated into the overall severity.

    Returns:
        DriftSuiteResult aggregating all checks.
    """
    psi_result = compute_psi(reference, production, label=label)

    feature_results: dict[str, FeatureDriftResult] = {}
    if feature_distributions:
        for feat, (ref_hist, prod_hist) in feature_distributions.items():
            feature_results[feat] = compute_feature_drift(feat, ref_hist, prod_hist)

    drifted_features = [k for k, v in feature_results.items() if v.drifted]

    # Overall severity escalates if anomaly_count > 0 or features are drifted
    overall_severity = psi_result.severity
    notes: list[str] = []

    if drifted_features:
        notes.append(f"Feature drift detected: {drifted_features}")
        if overall_severity == DriftSeverity.NO_DRIFT:
            overall_severity = DriftSeverity.MINOR
            notes.append("Severity escalated from no_drift → minor_drift (feature JSD)")

    if anomaly_count > 0:
        notes.append(f"{anomaly_count} anomaly breach(es) in observation window")
        if overall_severity != DriftSeverity.MAJOR:
            overall_severity = DriftSeverity.MAJOR
            notes.append("Severity escalated to major_drift (anomaly co-occurrence)")

    log.info(
        "drift.suite_complete",
        label=label,
        psi=psi_result.psi,
        psi_severity=psi_result.severity.value,
        drifted_features=drifted_features,
        anomaly_count=anomaly_count,
        overall_severity=overall_severity.value,
    )

    return DriftSuiteResult(
        psi_result=psi_result,
        feature_results=feature_results,
        drifted_features=drifted_features,
        overall_severity=overall_severity,
        anomaly_count=anomaly_count,
        notes=notes,
    )
