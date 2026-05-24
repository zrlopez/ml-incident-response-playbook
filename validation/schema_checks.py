"""Schema checks for incident payloads and downstream data contracts.

This module provides lightweight validation helpers that are easy to reuse in
API request handling, ETL jobs, and test suites. The helpers focus on explicit
field presence, type expectations, and domain-specific rules that matter for an
incident response workflow.

The goal is not to replace full schema tooling such as Pydantic, Marshmallow,
or Great Expectations. Instead, this file supplies a small, dependency-light
validation layer that can run in unit tests and batch validation jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

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
ALLOWED_STATUSES = {"open", "triaged", "mitigated", "resolved", "closed"}


@dataclass
class ValidationResult:
    """Container for schema validation outcomes.

    Attributes:
        valid: Whether all checks passed.
        errors: List of human-readable error messages.
        warnings: Non-fatal issues that should still be surfaced.
    """

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def required_fields_present(record: Mapping[str, Any], required: Iterable[str]) -> bool:
    """Return True when all required keys exist in a mapping.

    This helper is intentionally small because many callers only need a boolean
    gate before deeper validation. The full validator below returns structured
    error messages for richer workflows.
    """
    return all(field in record for field in required)


def validate_incident_record(record: Mapping[str, Any]) -> ValidationResult:
    """Validate a single incident record against the operational contract.

    The function checks:
        - Presence of required top-level fields.
        - Severity and status membership in approved value sets.
        - Basic string shape for identifiers and timestamps.

    Args:
        record: Mapping representing one incident payload.

    Returns:
        ValidationResult containing all discovered errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    missing = sorted(field for field in REQUIRED_INCIDENT_FIELDS if field not in record)
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")

    severity = record.get("severity")
    if severity is not None and severity not in ALLOWED_SEVERITIES:
        errors.append(f"Unsupported severity: {severity}")

    status = record.get("status")
    if status is not None and status not in ALLOWED_STATUSES:
        errors.append(f"Unsupported status: {status}")

    incident_id = record.get("incident_id")
    if incident_id is not None and not str(incident_id).startswith("INC-"):
        warnings.append("incident_id does not use the expected INC- prefix")

    created_at = record.get("created_at")
    if created_at is not None and "T" not in str(created_at):
        warnings.append("created_at should use an ISO 8601 timestamp")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


__all__ = [
    "ALLOWED_SEVERITIES",
    "ALLOWED_STATUSES",
    "REQUIRED_INCIDENT_FIELDS",
    "ValidationResult",
    "required_fields_present",
    "validate_incident_record",
]
