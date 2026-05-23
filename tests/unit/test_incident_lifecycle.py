"""
Unit tests: incident lifecycle state machine (src.domain.incident_lifecycle).

Coverage targets:
  - All ALLOWED_STATUS_TRANSITIONS entries (allowed paths)
  - All blocked cross-state paths (rejected transitions)
  - Idempotent same-state transitions
  - CLOSED terminal state (no outbound transitions)
  - TransitionDecision immutability (frozen dataclass)
  - Rejection reason string content

These tests are zero-dependency: no DB, no Redis, no FastAPI client.
Each test completes in < 1 ms.
"""
import pytest
from src.domain.incident_lifecycle import (
    ALLOWED_STATUS_TRANSITIONS,
    IncidentStatus,
    SeverityLevel,
    TransitionDecision,
    validate_status_transition,
)


# ---------------------------------------------------------------------------
# Parametrize every ALLOWED transition
# ---------------------------------------------------------------------------

_ALLOWED_PAIRS = [
    (src, tgt)
    for src, targets in ALLOWED_STATUS_TRANSITIONS.items()
    for tgt in targets
]


@pytest.mark.parametrize("current,requested", _ALLOWED_PAIRS)
def test_allowed_transitions_pass(current: IncidentStatus, requested: IncidentStatus):
    decision = validate_status_transition(current, requested)
    assert decision.allowed is True
    assert decision.current == current
    assert decision.requested == requested


# ---------------------------------------------------------------------------
# Parametrize known-blocked transitions
# ---------------------------------------------------------------------------

_BLOCKED_PAIRS = [
    (IncidentStatus.CLOSED,      IncidentStatus.OPEN),
    (IncidentStatus.CLOSED,      IncidentStatus.INVESTIGATING),
    (IncidentStatus.CLOSED,      IncidentStatus.MITIGATING),
    (IncidentStatus.CLOSED,      IncidentStatus.RESOLVED),
    (IncidentStatus.RESOLVED,    IncidentStatus.OPEN),
    (IncidentStatus.RESOLVED,    IncidentStatus.INVESTIGATING),
    (IncidentStatus.RESOLVED,    IncidentStatus.MITIGATING),
    (IncidentStatus.OPEN,        IncidentStatus.RESOLVED),   # Must go through INVESTIGATING/MITIGATING
]


@pytest.mark.parametrize("current,requested", _BLOCKED_PAIRS)
def test_blocked_transitions_fail(current: IncidentStatus, requested: IncidentStatus):
    decision = validate_status_transition(current, requested)
    assert decision.allowed is False
    assert decision.current == current
    assert decision.requested == requested
    # Rejection reason must reference both states for ops clarity
    assert current.value in decision.reason
    assert requested.value in decision.reason


# ---------------------------------------------------------------------------
# Idempotent (same-state) transitions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", list(IncidentStatus))
def test_idempotent_transitions_always_allowed(state: IncidentStatus):
    decision = validate_status_transition(state, state)
    assert decision.allowed is True
    assert "idempotent" in decision.reason


# ---------------------------------------------------------------------------
# CLOSED is terminal
# ---------------------------------------------------------------------------

def test_closed_has_no_outbound_transitions():
    assert ALLOWED_STATUS_TRANSITIONS[IncidentStatus.CLOSED] == frozenset()


@pytest.mark.parametrize("target", list(IncidentStatus))
def test_closed_rejects_all_non_idempotent_targets(target: IncidentStatus):
    if target == IncidentStatus.CLOSED:
        pytest.skip("idempotent path tested separately")
    decision = validate_status_transition(IncidentStatus.CLOSED, target)
    assert decision.allowed is False
    assert "terminal state" in decision.reason


# ---------------------------------------------------------------------------
# TransitionDecision is immutable
# ---------------------------------------------------------------------------

def test_transition_decision_is_frozen():
    decision = validate_status_transition(IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)
    with pytest.raises((AttributeError, TypeError)):
        decision.allowed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RESOLVED -> CLOSED: the only valid postmortem close path
# ---------------------------------------------------------------------------

def test_resolved_to_closed_is_allowed():
    decision = validate_status_transition(IncidentStatus.RESOLVED, IncidentStatus.CLOSED)
    assert decision.allowed is True


def test_resolved_cannot_reopen():
    for bad_target in [IncidentStatus.OPEN, IncidentStatus.INVESTIGATING, IncidentStatus.MITIGATING]:
        decision = validate_status_transition(IncidentStatus.RESOLVED, bad_target)
        assert decision.allowed is False, f"Expected RESOLVED -> {bad_target} to be blocked"


# ---------------------------------------------------------------------------
# SeverityLevel enum completeness
# ---------------------------------------------------------------------------

def test_all_severity_levels_defined():
    levels = {s.value for s in SeverityLevel}
    assert levels == {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}


def test_severity_levels_are_string_comparable():
    assert SeverityLevel.SEV1 == "SEV-1"
    assert SeverityLevel.SEV2 == "SEV-2"
