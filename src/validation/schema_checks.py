"""Schema checks for incident payloads and downstream data contracts.

This module provides lightweight validation helpers that are easy to reuse in
API request handling, ETL jobs, and test suites. The helpers focus on explicit
field presence, type expectations, and domain-specific rules that matter for an
incident response workflow.

The goal is not to replace full schema tooling such as Pydantic, Marshmallow,
or Great Expectations. Instead, this file supplies a small, dependency-light
validation layer that can run in unit tests and batch validation jobs.

Public API
----------
- validate_incident_record(record)  -> ValidationResult
- validate_state_transition(current, next_state) -> ValidationResult
- validate_feature_batch_record(record) -> ValidationResult
- validate_batch(records) -> list[ValidationResult]
- required_fields_present(record, required) -> bool
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_INCIDENT_FIELDS = {
    "incident_id",
    "title",
    "severity",
    "category",
    "summary",
    "status",
    "created_at",
}

ALLOWED_SEVERITIES = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}

ALLOWED_STATUSES = {"OPEN", "INVESTIGATING", "MITIGATING", "RESOLVED", "CLOSED"}

ALLOWED_CATEGORIES = {
    "api",
    "data_quality",
    "model_drift",
    "cost_spike",
    "pipeline_failure",
    "security",
}

# Lifecycle finite-state machine: maps each state to the set of states it may
# legally transition into. Any transition not listed here is invalid.
ALLOWED_LIFECYCLE_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"INVESTIGATING"},
    "INVESTIGATING": {"MITIGATING", "RESOLVED"},
    "MITIGATING": {"RESOLVED"},
    "RESOLVED": {"CLOSED"},
    "CLOSED": set(),  # terminal — no outbound transitions
}

# incident_id must match INC- followed by at least 4 digits (e.g. INC-0047 or INC-2026-0047)
_INCIDENT_ID_RE = re.compile(r"^INC-[0-9]{4,}")

# Minimal ISO 8601 UTC/offset check: ends with Z or +HH:MM / -HH:MM
_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$"
)

TITLE_MAX_LEN = 200
SUMMARY_MAX_LEN = 2_000

# Feature batch thresholds (align with Great Expectations suite)
FEATURE_BATCH_MIN_ROW_COUNT = 1
FEATURE_BATCH_MAX_NULL_RATE = 0.05  # 5 %
FEATURE_BATCH_PSI_THRESHOLD = 0.20

REQUIRED_FEATURE_BATCH_FIELDS = {
    "batch_id",
    "pipeline_id",
    "row_count",
    "null_rates",
    "psi_scores",
    "schema_fingerprint",
}


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Container for schema validation outcomes.

    Attributes:
        valid: True when no errors were recorded.
        errors: Fatal issues that must block ingestion or persist.
        warnings: Non-fatal issues that should be logged and monitored.
        context: Optional label (e.g. batch index) for traceability.
    """

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def required_fields_present(record: Mapping[str, Any], required: Iterable[str]) -> bool:
    """Return True when all required keys exist in a mapping."""
    return all(f in record for f in required)


def _is_iso8601(value: Any) -> bool:
    """Return True when value is a string matching minimal ISO 8601 UTC format."""
    return bool(_ISO8601_RE.match(str(value))) if value is not None else False


# ---------------------------------------------------------------------------
# Incident record validator
# ---------------------------------------------------------------------------


def validate_incident_record(record: Mapping[str, Any]) -> ValidationResult:
    """Validate a single incident record against the operational contract.

    Checks performed
    ----------------
    Errors (block ingest):
        - Required fields present
        - severity in ALLOWED_SEVERITIES
        - status in ALLOWED_STATUSES
        - category in ALLOWED_CATEGORIES
        - title length <= TITLE_MAX_LEN
        - summary length <= SUMMARY_MAX_LEN
        - resolved_at >= created_at (if both present)
        - updated_at >= created_at (if both present)

    Warnings (log and monitor):
        - incident_id does not match INC-[0-9]{4,} pattern
        - Any timestamp field fails ISO 8601 format check

    Args:
        record: Mapping representing one incident payload.

    Returns:
        ValidationResult with all discovered errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- Required fields ---
    missing = sorted(f for f in REQUIRED_INCIDENT_FIELDS if f not in record)
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")

    # --- Enum checks ---
    severity = record.get("severity")
    if severity is not None and severity not in ALLOWED_SEVERITIES:
        errors.append(
            f"Unsupported severity '{severity}'. Allowed: {sorted(ALLOWED_SEVERITIES)}"
        )

    status = record.get("status")
    if status is not None and status not in ALLOWED_STATUSES:
        errors.append(
            f"Unsupported status '{status}'. Allowed: {sorted(ALLOWED_STATUSES)}"
        )

    category = record.get("category")
    if category is not None and category not in ALLOWED_CATEGORIES:
        errors.append(
            f"Unsupported category '{category}'. Allowed: {sorted(ALLOWED_CATEGORIES)}"
        )

    # --- String length guards ---
    title = record.get("title", "")
    if isinstance(title, str):
        if not title.strip():
            errors.append("title must not be empty")
        elif len(title) > TITLE_MAX_LEN:
            errors.append(f"title exceeds {TITLE_MAX_LEN} characters ({len(title)} found)")

    summary = record.get("summary", "")
    if isinstance(summary, str):
        if not summary.strip():
            errors.append("summary must not be empty")
        elif len(summary) > SUMMARY_MAX_LEN:
            errors.append(
                f"summary exceeds {SUMMARY_MAX_LEN} characters ({len(summary)} found)"
            )

    # --- incident_id format ---
    incident_id = record.get("incident_id")
    if incident_id is not None and not _INCIDENT_ID_RE.match(str(incident_id)):
        warnings.append(
            f"incident_id '{incident_id}' does not match expected pattern INC-[0-9]{{4,}}"
        )

    # --- Timestamp format checks ---
    ts_fields = ["created_at", "resolved_at", "updated_at", "acknowledged_at"]
    for ts_field in ts_fields:
        ts_val = record.get(ts_field)
        if ts_val is not None and not _is_iso8601(ts_val):
            warnings.append(
                f"'{ts_field}' value '{ts_val}' should be ISO 8601 with UTC offset (e.g. 2026-05-22T14:00:00Z)"  # noqa: E501
            )

    # --- Temporal ordering checks ---
    created_at = record.get("created_at", "")
    resolved_at = record.get("resolved_at")
    if resolved_at and created_at and str(resolved_at) < str(created_at):
        errors.append(
            f"resolved_at '{resolved_at}' cannot precede created_at '{created_at}'"
        )

    updated_at = record.get("updated_at")
    if updated_at and created_at and str(updated_at) < str(created_at):
        errors.append(
            f"updated_at '{updated_at}' cannot precede created_at '{created_at}'"
        )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# State machine transition validator
# ---------------------------------------------------------------------------


def validate_state_transition(current_state: str, next_state: str) -> ValidationResult:
    """Validate a proposed incident lifecycle state transition.

    Uses ALLOWED_LIFECYCLE_TRANSITIONS to enforce the FSM:
        OPEN -> INVESTIGATING -> MITIGATING -> RESOLVED -> CLOSED

    INVESTIGATING may also transition directly to RESOLVED to support
    incidents resolved before a mitigation phase is entered.

    Args:
        current_state: The incident's current status string.
        next_state: The proposed new status string.

    Returns:
        ValidationResult. errors is non-empty if the transition is illegal.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if current_state not in ALLOWED_LIFECYCLE_TRANSITIONS:
        errors.append(
            f"Unknown current state '{current_state}'. "
            f"Allowed states: {sorted(ALLOWED_LIFECYCLE_TRANSITIONS)}"
        )
        return ValidationResult(valid=False, errors=errors)

    if next_state not in ALLOWED_STATUSES:
        errors.append(
            f"Unknown target state '{next_state}'. Allowed: {sorted(ALLOWED_STATUSES)}"
        )
        return ValidationResult(valid=False, errors=errors)

    allowed_next = ALLOWED_LIFECYCLE_TRANSITIONS[current_state]
    if next_state not in allowed_next:
        if not allowed_next:
            errors.append(
                f"State '{current_state}' is terminal — no further transitions are allowed."
            )
        else:
            errors.append(
                f"Transition '{current_state}' -> '{next_state}' is not allowed. "
                f"Valid next states from '{current_state}': {sorted(allowed_next)}"
            )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# Feature batch validator
# ---------------------------------------------------------------------------


def validate_feature_batch_record(record: Mapping[str, Any]) -> ValidationResult:
    """Validate a feature batch metadata record before pipeline ingestion.

    Checks performed
    ----------------
    Errors:
        - Required fields present
        - row_count >= FEATURE_BATCH_MIN_ROW_COUNT
        - Any null_rate value exceeds FEATURE_BATCH_MAX_NULL_RATE (5%)
        - Any PSI score exceeds FEATURE_BATCH_PSI_THRESHOLD (0.20)

    Warnings:
        - schema_fingerprint is missing or empty
        - psi_scores dict is empty (drift cannot be computed)

    These rules align with the Great Expectations suite
    `daily_feature_validation` defined in validation_rules.md Section 2.

    Args:
        record: Mapping representing one feature batch metadata payload.

    Returns:
        ValidationResult with all discovered errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    missing = sorted(f for f in REQUIRED_FEATURE_BATCH_FIELDS if f not in record)
    if missing:
        errors.append(f"Missing required batch fields: {', '.join(missing)}")

    row_count = record.get("row_count")
    if row_count is not None:
        try:
            if int(row_count) < FEATURE_BATCH_MIN_ROW_COUNT:
                errors.append(
                    f"row_count {row_count} is below minimum {FEATURE_BATCH_MIN_ROW_COUNT}"
                )
        except (TypeError, ValueError):
            errors.append(f"row_count must be an integer, got '{row_count}'")

    null_rates = record.get("null_rates", {})
    if isinstance(null_rates, dict):
        for col, rate in null_rates.items():
            try:
                if float(rate) > FEATURE_BATCH_MAX_NULL_RATE:
                    errors.append(
                        f"Null rate for '{col}' is {rate:.1%}, exceeds threshold "
                        f"{FEATURE_BATCH_MAX_NULL_RATE:.0%}"
                    )
            except (TypeError, ValueError):
                errors.append(f"null_rates['{col}'] must be a float, got '{rate}'")

    psi_scores = record.get("psi_scores", {})
    if isinstance(psi_scores, dict):
        if not psi_scores:
            warnings.append(
                "psi_scores dict is empty — drift cannot be computed for this batch"
            )
        for feat, psi in psi_scores.items():
            try:
                if float(psi) > FEATURE_BATCH_PSI_THRESHOLD:
                    errors.append(
                        f"PSI score for feature '{feat}' is {psi} (threshold: "
                        f"{FEATURE_BATCH_PSI_THRESHOLD}). Open SEV-2 drift incident."
                    )
            except (TypeError, ValueError):
                errors.append(f"psi_scores['{feat}'] must be a float, got '{psi}'")

    schema_fp = record.get("schema_fingerprint")
    if not schema_fp or not str(schema_fp).strip():
        warnings.append(
            "schema_fingerprint is missing or empty — schema drift cannot be detected"
        )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


def validate_batch(
    records: Sequence[Mapping[str, Any]],
) -> list[ValidationResult]:
    """Run validate_incident_record across a sequence of records.

    Each ValidationResult is tagged with a context string (e.g. "record[3]")
    so failures can be traced back to their position in the batch without
    needing the caller to add indexing logic.

    Args:
        records: Sequence of incident payload mappings.

    Returns:
        List of ValidationResult, one per input record, in order.
    """
    results: list[ValidationResult] = []
    for idx, record in enumerate(records):
        result = validate_incident_record(record)
        result.context = f"record[{idx}]"
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ALLOWED_CATEGORIES",
    "ALLOWED_LIFECYCLE_TRANSITIONS",
    "ALLOWED_SEVERITIES",
    "ALLOWED_STATUSES",
    "FEATURE_BATCH_MAX_NULL_RATE",
    "FEATURE_BATCH_MIN_ROW_COUNT",
    "FEATURE_BATCH_PSI_THRESHOLD",
    "REQUIRED_FEATURE_BATCH_FIELDS",
    "REQUIRED_INCIDENT_FIELDS",
    "SUMMARY_MAX_LEN",
    "TITLE_MAX_LEN",
    "ValidationResult",
    "required_fields_present",
    "validate_batch",
    "validate_feature_batch_record",
    "validate_incident_record",
    "validate_state_transition",
]
