"""
tests/unit/test_anomaly_schema_properties.py
=============================================
Property-based tests for ml_models/incident_anomaly/schema.py  (TEST-03)

Uses Hypothesis to verify that AnomalyRequest:
  1. Accepts every valid (in-range) combination of all 7 features.
  2. Rejects any input with at least one out-of-range field.
  3. Always rounds duplicate_alert_ratio and blast_radius_pct to <= 6 dp.
  4. Always produces a dict of exactly 7 values via .model_dump().

No model artifact is required — these are pure schema/validation tests.

Attribution:
    Hypothesis (MPL-2.0) — https://hypothesis.readthedocs.io
    Pydantic v2 (MIT)    — https://docs.pydantic.dev
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from ml_models.incident_anomaly.schema import AnomalyRequest

# ---------------------------------------------------------------------------
# Reusable strategies — mirror field constraints in schema.py exactly
# ---------------------------------------------------------------------------

_valid_request = st.fixed_dictionaries({
    "severity_numeric":      st.integers(min_value=1, max_value=5),
    "alert_count":           st.integers(min_value=1, max_value=500),
    "time_to_detect_minutes": st.floats(min_value=0.001, max_value=720.0,
                                         allow_nan=False, allow_infinity=False),
    "affected_services":     st.integers(min_value=1, max_value=50),
    "on_call_escalations":   st.integers(min_value=0, max_value=10),
    "duplicate_alert_ratio": st.floats(min_value=0.0, max_value=1.0,
                                        allow_nan=False, allow_infinity=False),
    "blast_radius_pct":      st.floats(min_value=0.0, max_value=100.0,
                                        allow_nan=False, allow_infinity=False),
})

_SETTINGS = settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
_SETTINGS_200 = settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
_SETTINGS_150 = settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])


# ---------------------------------------------------------------------------
# PROPERTY 1: Every valid input is accepted
# ---------------------------------------------------------------------------

@given(payload=_valid_request)
@_SETTINGS
def test_valid_inputs_always_parse(payload: dict) -> None:
    """AnomalyRequest must accept every combination within documented bounds."""
    req = AnomalyRequest(**payload)  # must not raise
    assert req.severity_numeric == payload["severity_numeric"]
    assert req.alert_count == payload["alert_count"]


# ---------------------------------------------------------------------------
# PROPERTY 2: Rejected when severity_numeric is out of range
# ---------------------------------------------------------------------------

@given(
    payload=_valid_request,
    bad_severity=st.one_of(
        st.integers(max_value=0),
        st.integers(min_value=6),
    ),
)
@_SETTINGS_200
def test_invalid_severity_always_rejected(payload: dict, bad_severity: int) -> None:
    with pytest.raises(ValidationError):
        AnomalyRequest(**{**payload, "severity_numeric": bad_severity})


# ---------------------------------------------------------------------------
# PROPERTY 3: Rejected when alert_count is out of range
# ---------------------------------------------------------------------------

@given(
    payload=_valid_request,
    bad_count=st.one_of(
        st.integers(max_value=0),
        st.integers(min_value=501),
    ),
)
@_SETTINGS_200
def test_invalid_alert_count_always_rejected(payload: dict, bad_count: int) -> None:
    with pytest.raises(ValidationError):
        AnomalyRequest(**{**payload, "alert_count": bad_count})


# ---------------------------------------------------------------------------
# PROPERTY 4: Rejected when duplicate_alert_ratio > 1.0
# ---------------------------------------------------------------------------

@given(
    payload=_valid_request,
    bad_ratio=st.floats(
        min_value=1.0001, max_value=1e6,
        allow_nan=False, allow_infinity=False,
    ),
)
@_SETTINGS_200
def test_dup_ratio_above_1_always_rejected(payload: dict, bad_ratio: float) -> None:
    with pytest.raises(ValidationError):
        AnomalyRequest(**{**payload, "duplicate_alert_ratio": bad_ratio})


# ---------------------------------------------------------------------------
# PROPERTY 5: Rejected when blast_radius_pct > 100.0
# ---------------------------------------------------------------------------

@given(
    payload=_valid_request,
    bad_blast=st.floats(
        min_value=100.0001, max_value=1e6,
        allow_nan=False, allow_infinity=False,
    ),
)
@_SETTINGS_200
def test_blast_radius_above_100_always_rejected(payload: dict, bad_blast: float) -> None:
    with pytest.raises(ValidationError):
        AnomalyRequest(**{**payload, "blast_radius_pct": bad_blast})


# ---------------------------------------------------------------------------
# PROPERTY 6: Clamped floats always have <= 6 decimal places
# ---------------------------------------------------------------------------

@given(payload=_valid_request)
@_SETTINGS
def test_float_fields_clamped_to_6dp(payload: dict) -> None:
    """_clamp_float validator must round to at most 6 decimal places."""
    req = AnomalyRequest(**payload)
    for field in ("duplicate_alert_ratio", "blast_radius_pct"):
        val = getattr(req, field)
        parts = f"{val}".rstrip("0").split(".")
        dp = len(parts[1]) if len(parts) == 2 else 0
        assert dp <= 6, f"{field}={val!r} has {dp} decimal places (> 6)"


# ---------------------------------------------------------------------------
# PROPERTY 7: model_dump() always produces exactly 7 entries
# ---------------------------------------------------------------------------

@given(payload=_valid_request)
@_SETTINGS
def test_model_dump_always_7_fields(payload: dict) -> None:
    """Schema must never silently drop or add fields across the dump boundary."""
    req = AnomalyRequest(**payload)
    dumped = req.model_dump()
    assert len(dumped) == 7, f"Expected 7 fields, got {len(dumped)}: {list(dumped)}"


# ---------------------------------------------------------------------------
# PROPERTY 8: Rejected when time_to_detect_minutes <= 0
# ---------------------------------------------------------------------------

@given(
    payload=_valid_request,
    bad_ttd=st.one_of(
        st.just(0.0),
        st.floats(max_value=-0.001, allow_nan=False, allow_infinity=False),
    ),
)
@_SETTINGS_150
def test_ttd_zero_or_negative_always_rejected(payload: dict, bad_ttd: float) -> None:
    with pytest.raises(ValidationError):
        AnomalyRequest(**{**payload, "time_to_detect_minutes": bad_ttd})
