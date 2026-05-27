"""
Incident domain policy: lifecycle state machine and severity definitions.

Remediation: CR-2 (P0) - 2026-05-23

This module is the single authoritative source for:
  1. IncidentStatus and SeverityLevel enumerations (imported by ORM + API layers)
  2. The incident lifecycle state machine (ALLOWED_STATUS_TRANSITIONS)
  3. validate_status_transition() - the enforcement function called before
     any DB write that changes incident status.

Design principles:
  - Zero external dependencies (no SQLAlchemy, no FastAPI, no structlog)
  - Fully unit-testable in < 1 ms per test case
  - Immutable TransitionDecision result (dataclass frozen=True)
  - Human-readable rejection messages for API 409 response bodies

Lifecycle policy:
  OPEN -> INVESTIGATING | MITIGATING | CLOSED
  INVESTIGATING -> MITIGATING | RESOLVED | CLOSED
  MITIGATING -> INVESTIGATING | RESOLVED | CLOSED
  RESOLVED -> CLOSED
  CLOSED -> (terminal)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# -- Domain enumerations -------------------------------------------------------

class IncidentStatus(str, Enum):
    """Lifecycle states for an incident record."""
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class SeverityLevel(str, Enum):
    """Incident severity tiers aligned with governance.md SEV-1..SEV-4 SLAs."""
    SEV1 = "SEV-1"
    SEV2 = "SEV-2"
    SEV3 = "SEV-3"
    SEV4 = "SEV-4"


# -- State machine definition --------------------------------------------------
# Policy: OPEN cannot jump directly to CLOSED or RESOLVED.
# An incident must be acknowledged (INVESTIGATING) before it can be resolved
# or closed — this enforces a minimum audit trail.
#
# Mermaid:
#   OPEN --> INVESTIGATING
#   OPEN --> MITIGATING
#   INVESTIGATING --> MITIGATING
#   INVESTIGATING --> RESOLVED
#   INVESTIGATING --> CLOSED
#   MITIGATING --> INVESTIGATING
#   MITIGATING --> RESOLVED
#   MITIGATING --> CLOSED
#   RESOLVED --> CLOSED

ALLOWED_STATUS_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.OPEN: frozenset({
        IncidentStatus.INVESTIGATING,
        IncidentStatus.MITIGATING,
        # CLOSED intentionally absent: incidents must be investigated before closing.
    }),
    IncidentStatus.INVESTIGATING: frozenset({
        IncidentStatus.MITIGATING,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    }),
    IncidentStatus.MITIGATING: frozenset({
        IncidentStatus.INVESTIGATING,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    }),
    IncidentStatus.RESOLVED: frozenset({
        IncidentStatus.CLOSED,
    }),
    IncidentStatus.CLOSED: frozenset(),  # Terminal - no outbound transitions
}


# -- Result type ---------------------------------------------------------------

@dataclass(frozen=True)
class TransitionDecision:
    """
    Immutable result of a lifecycle transition validation.
    frozen=True prevents accidental mutation and enables use as dict keys.
    """
    allowed: bool
    current: IncidentStatus
    requested: IncidentStatus
    reason: str


# -- Policy enforcement --------------------------------------------------------

def validate_status_transition(
    current: IncidentStatus,
    requested: IncidentStatus,
) -> TransitionDecision:
    """
    Determine whether transitioning from current to requested is permitted.

    Idempotent transitions (same -> same) are always allowed.

    Returns TransitionDecision with allowed=True if valid, or allowed=False
    with a human-readable reason suitable for HTTP 409 response bodies.
    """
    if current == requested:
        return TransitionDecision(
            allowed=True,
            current=current,
            requested=requested,
            reason="idempotent: no state change",
        )

    allowed_targets = ALLOWED_STATUS_TRANSITIONS.get(current, frozenset())

    if requested in allowed_targets:
        return TransitionDecision(
            allowed=True,
            current=current,
            requested=requested,
            reason=f"transition {current.value} -> {requested.value} is permitted",
        )

    valid = sorted(s.value for s in allowed_targets)
    valid_display = ", ".join(valid) if valid else "none (terminal state)"

    return TransitionDecision(
        allowed=False,
        current=current,
        requested=requested,
        reason=(
            f"invalid incident state transition: {current.value} -> {requested.value}. "
            f"Valid targets from '{current.value}': [{valid_display}]."
        ),
    )
