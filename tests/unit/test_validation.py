"""tests/unit/test_validation.py

Full unit test suite for validation/schema_checks.py.

No external dependencies — zero database, zero network, zero FastAPI imports.
All tests are pure Python and run with: pytest tests/unit/test_validation.py
"""
from __future__ import annotations

import sys
import os

import pytest

# ---------------------------------------------------------------------------
# Path resolution: allow `from src.validation.schema_checks import ...` whether
# pytest is run from the repo root or from tests/unit/.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.validation.schema_checks import (  # noqa: E402
    ALLOWED_CATEGORIES,
    ALLOWED_LIFECYCLE_TRANSITIONS,
    ALLOWED_SEVERITIES,
    ALLOWED_STATUSES,
    FEATURE_BATCH_MAX_NULL_RATE,
    FEATURE_BATCH_PSI_THRESHOLD,
    REQUIRED_FEATURE_BATCH_FIELDS,
    REQUIRED_INCIDENT_FIELDS,
    SUMMARY_MAX_LEN,
    TITLE_MAX_LEN,
    ValidationResult,
    required_fields_present,
    validate_batch,
    validate_feature_batch_record,
    validate_incident_record,
    validate_state_transition,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _minimal_incident(**overrides) -> dict:
    """Return the smallest valid incident record, with optional field overrides."""
    base = {
        "incident_id": "INC-2026-0001",
        "title": "SEV-2: Fraud model drift detected",
        "severity": "SEV-2",
        "category": "model_drift",
        "summary": "PSI score exceeded threshold on transaction_amount_zscore.",
        "status": "OPEN",
        "created_at": "2026-05-22T14:00:00Z",
    }
    base.update(overrides)
    return base


def _full_incident(**overrides) -> dict:
    """Return a production-style record mirroring sample_incident.json."""
    base = {
        "incident_id": "INC-2026-0047",
        "title": "ML Model Drift — Fraud Detection Pipeline SEV-2",
        "severity": "SEV-2",
        "category": "model_drift",
        "summary": (
            "Upstream feature pipeline ingested a corrupted merchant category "
            "encoding table. PSI score 0.31 exceeded threshold 0.15. "
            "Fraud model precision dropped from 92.1% to 79.3%."
        ),
        "status": "RESOLVED",
        "created_at": "2026-05-22T13:58:44Z",
        "acknowledged_at": "2026-05-22T14:11:02Z",
        "resolved_at": "2026-05-22T18:22:17Z",
        "updated_at": "2026-05-23T09:00:00Z",
    }
    base.update(overrides)
    return base


def _minimal_batch(**overrides) -> dict:
    """Return the smallest valid feature batch record."""
    base = {
        "batch_id": "batch-2026-05-22-001",
        "pipeline_id": "fraud-features-daily",
        "row_count": 50_000,
        "null_rates": {"transaction_amount": 0.01, "merchant_category": 0.00},
        "psi_scores": {"transaction_amount_zscore": 0.08},
        "schema_fingerprint": "sha256:a3f1b8c9d2e4",
    }
    base.update(overrides)
    return base


# ===========================================================================
# TestValidationResult
# ===========================================================================


class TestValidationResult:
    def test_defaults_valid_true(self):
        r = ValidationResult(valid=True)
        assert r.valid is True
        assert r.errors == []
        assert r.warnings == []
        assert r.context == ""

    def test_defaults_valid_false(self):
        r = ValidationResult(valid=False)
        assert r.valid is False

    def test_errors_and_warnings_independent_across_instances(self):
        r1 = ValidationResult(valid=True)
        r2 = ValidationResult(valid=True)
        r1.errors.append("oops")
        assert r2.errors == [], "Mutable default shared between instances"

    def test_context_field_set(self):
        r = ValidationResult(valid=True, context="record[7]")
        assert r.context == "record[7]"


# ===========================================================================
# TestRequiredFieldsPresent
# ===========================================================================


class TestRequiredFieldsPresent:
    def test_all_present_returns_true(self):
        record = {"a": 1, "b": 2, "c": 3}
        assert required_fields_present(record, ["a", "b", "c"]) is True

    def test_one_missing_returns_false(self):
        record = {"a": 1, "c": 3}
        assert required_fields_present(record, ["a", "b", "c"]) is False

    def test_multiple_missing_returns_false(self):
        assert required_fields_present({}, ["x", "y"]) is False

    def test_empty_required_always_true(self):
        assert required_fields_present({}, []) is True

    def test_empty_record_with_required_returns_false(self):
        assert required_fields_present({}, ["incident_id"]) is False

    def test_extra_fields_do_not_affect_result(self):
        record = {"a": 1, "b": 2, "extra": 99}
        assert required_fields_present(record, ["a", "b"]) is True


# ===========================================================================
# TestValidateIncidentRecord — happy paths
# ===========================================================================


class TestValidateIncidentRecordHappy:
    def test_minimal_valid_record_passes(self):
        result = validate_incident_record(_minimal_incident())
        assert result.valid is True
        assert result.errors == []

    def test_full_production_record_passes(self):
        result = validate_incident_record(_full_incident())
        assert result.valid is True
        assert result.errors == []

    def test_optional_timestamps_absent_no_error(self):
        record = _minimal_incident()
        # resolved_at, updated_at, acknowledged_at all absent — should be fine
        result = validate_incident_record(record)
        assert result.valid is True

    def test_all_severities_accepted(self):
        for sev in ALLOWED_SEVERITIES:
            result = validate_incident_record(_minimal_incident(severity=sev))
            assert result.valid is True, f"Expected SEV '{sev}' to pass"

    def test_all_statuses_accepted(self):
        for status in ALLOWED_STATUSES:
            result = validate_incident_record(_minimal_incident(status=status))
            assert result.valid is True, f"Expected status '{status}' to pass"

    def test_all_categories_accepted(self):
        for cat in ALLOWED_CATEGORIES:
            result = validate_incident_record(_minimal_incident(category=cat))
            assert result.valid is True, f"Expected category '{cat}' to pass"


# ===========================================================================
# TestValidateIncidentRecord — field errors
# ===========================================================================


class TestValidateIncidentRecordErrors:

    @pytest.mark.parametrize("missing_field", sorted(REQUIRED_INCIDENT_FIELDS))
    def test_each_required_field_missing_produces_error(self, missing_field):
        record = _minimal_incident()
        del record[missing_field]
        result = validate_incident_record(record)
        assert result.valid is False
        assert any("Missing required fields" in e for e in result.errors)

    def test_all_required_fields_missing(self):
        result = validate_incident_record({})
        assert result.valid is False
        assert len(result.errors) >= 1
        # All missing fields named in the single error message
        error_text = " ".join(result.errors)
        for f in REQUIRED_INCIDENT_FIELDS:
            assert f in error_text

    @pytest.mark.parametrize("bad_sev", ["SEV-0", "SEV-5", "sev-2", "CRITICAL", "", "P1"])
    def test_invalid_severity_is_error(self, bad_sev):
        result = validate_incident_record(_minimal_incident(severity=bad_sev))
        assert result.valid is False
        assert any("severity" in e.lower() for e in result.errors)

    @pytest.mark.parametrize(
        "bad_status",
        ["open", "triaged", "mitigated", "resolved", "UNKNOWN", "IN_PROGRESS", ""],
    )
    def test_invalid_status_is_error(self, bad_status):
        result = validate_incident_record(_minimal_incident(status=bad_status))
        assert result.valid is False
        assert any("status" in e.lower() for e in result.errors)

    @pytest.mark.parametrize(
        "bad_cat",
        ["model-drift", "DATA_QUALITY", "unknown", "infra", ""],
    )
    def test_invalid_category_is_error(self, bad_cat):
        result = validate_incident_record(_minimal_incident(category=bad_cat))
        assert result.valid is False
        assert any("category" in e.lower() for e in result.errors)

    def test_title_empty_string_is_error(self):
        result = validate_incident_record(_minimal_incident(title=""))
        assert result.valid is False
        assert any("title" in e for e in result.errors)

    def test_title_whitespace_only_is_error(self):
        result = validate_incident_record(_minimal_incident(title="   "))
        assert result.valid is False

    def test_title_over_max_length_is_error(self):
        long_title = "X" * (TITLE_MAX_LEN + 1)
        result = validate_incident_record(_minimal_incident(title=long_title))
        assert result.valid is False
        assert any(str(TITLE_MAX_LEN) in e for e in result.errors)

    def test_summary_empty_string_is_error(self):
        result = validate_incident_record(_minimal_incident(summary=""))
        assert result.valid is False
        assert any("summary" in e for e in result.errors)

    def test_summary_over_max_length_is_error(self):
        long_summary = "Y" * (SUMMARY_MAX_LEN + 1)
        result = validate_incident_record(_minimal_incident(summary=long_summary))
        assert result.valid is False
        assert any(str(SUMMARY_MAX_LEN) in e for e in result.errors)

    def test_resolved_at_before_created_at_is_error(self):
        result = validate_incident_record(
            _minimal_incident(
                created_at="2026-05-22T14:00:00Z",
                resolved_at="2026-05-22T13:00:00Z",  # 1 hour earlier
            )
        )
        assert result.valid is False
        assert any("resolved_at" in e for e in result.errors)

    def test_updated_at_before_created_at_is_error(self):
        result = validate_incident_record(
            _minimal_incident(
                created_at="2026-05-22T14:00:00Z",
                updated_at="2026-05-21T14:00:00Z",  # 1 day earlier
            )
        )
        assert result.valid is False
        assert any("updated_at" in e for e in result.errors)

    def test_multiple_errors_reported_together(self):
        result = validate_incident_record(
            {
                "incident_id": "INC-2026-0001",
                "title": "",           # empty
                "severity": "SEV-99",  # invalid
                "category": "unknown",  # invalid
                "summary": "ok",
                "status": "OPEN",
                "created_at": "2026-05-22T14:00:00Z",
            }
        )
        assert result.valid is False
        assert len(result.errors) >= 3


# ===========================================================================
# TestValidateIncidentRecord — warnings
# ===========================================================================


class TestValidateIncidentRecordWarnings:
    def test_incident_id_missing_prefix_is_warning(self):
        result = validate_incident_record(_minimal_incident(incident_id="20260001"))
        assert any("incident_id" in w for w in result.warnings)

    def test_incident_id_correct_prefix_no_warning(self):
        result = validate_incident_record(_minimal_incident(incident_id="INC-2026-0001"))
        assert not any("incident_id" in w for w in result.warnings)

    def test_created_at_no_tz_offset_is_warning(self):
        result = validate_incident_record(
            _minimal_incident(created_at="2026-05-22T14:00:00")
        )
        assert any("created_at" in w for w in result.warnings)

    def test_created_at_with_z_suffix_no_warning(self):
        result = validate_incident_record(
            _minimal_incident(created_at="2026-05-22T14:00:00Z")
        )
        assert not any("created_at" in w for w in result.warnings)

    def test_created_at_with_positive_offset_no_warning(self):
        result = validate_incident_record(
            _minimal_incident(created_at="2026-05-22T09:00:00+05:30")
        )
        assert not any("created_at" in w for w in result.warnings)

    def test_created_at_with_negative_offset_no_warning(self):
        result = validate_incident_record(
            _minimal_incident(created_at="2026-05-22T08:00:00-06:00")
        )
        assert not any("created_at" in w for w in result.warnings)

    def test_acknowledged_at_bad_format_is_warning(self):
        result = validate_incident_record(
            _minimal_incident(acknowledged_at="May 22 2026 2:11 PM")
        )
        assert any("acknowledged_at" in w for w in result.warnings)

    def test_valid_record_has_no_warnings_when_clean(self):
        result = validate_incident_record(_full_incident())
        assert result.warnings == []


# ===========================================================================
# TestValidateIncidentRecord — boundary values
# ===========================================================================


class TestValidateIncidentRecordBoundary:
    def test_title_exactly_max_length_passes(self):
        result = validate_incident_record(_minimal_incident(title="T" * TITLE_MAX_LEN))
        assert result.valid is True

    def test_title_one_over_max_length_fails(self):
        result = validate_incident_record(
            _minimal_incident(title="T" * (TITLE_MAX_LEN + 1))
        )
        assert result.valid is False

    def test_summary_exactly_max_length_passes(self):
        result = validate_incident_record(
            _minimal_incident(summary="S" * SUMMARY_MAX_LEN)
        )
        assert result.valid is True

    def test_summary_one_over_max_length_fails(self):
        result = validate_incident_record(
            _minimal_incident(summary="S" * (SUMMARY_MAX_LEN + 1))
        )
        assert result.valid is False

    def test_resolved_at_equal_to_created_at_passes(self):
        """Same-instant resolution is valid (e.g. auto-closed incidents)."""
        ts = "2026-05-22T14:00:00Z"
        result = validate_incident_record(
            _minimal_incident(created_at=ts, resolved_at=ts)
        )
        assert result.valid is True

    def test_title_single_char_passes(self):
        result = validate_incident_record(_minimal_incident(title="X"))
        assert result.valid is True


# ===========================================================================
# TestValidateStateTransition — valid FSM paths
# ===========================================================================


class TestValidateStateTransitionValid:
    @pytest.mark.parametrize(
        "current, next_state",
        [
            ("OPEN", "INVESTIGATING"),
            ("INVESTIGATING", "MITIGATING"),
            ("INVESTIGATING", "RESOLVED"),  # skip-mitigation path
            ("MITIGATING", "RESOLVED"),
            ("RESOLVED", "CLOSED"),
        ],
    )
    def test_legal_transition_passes(self, current, next_state):
        result = validate_state_transition(current, next_state)
        assert result.valid is True
        assert result.errors == []

    def test_fsm_map_covers_all_allowed_statuses(self):
        """Every status in ALLOWED_STATUSES must appear as a key in the FSM."""
        for status in ALLOWED_STATUSES:
            assert status in ALLOWED_LIFECYCLE_TRANSITIONS, (
                f"Status '{status}' missing from ALLOWED_LIFECYCLE_TRANSITIONS"
            )


# ===========================================================================
# TestValidateStateTransition — invalid FSM paths
# ===========================================================================


class TestValidateStateTransitionInvalid:
    @pytest.mark.parametrize(
        "current, next_state, description",
        [
            ("OPEN", "RESOLVED", "skip INVESTIGATING"),
            ("OPEN", "CLOSED", "skip multiple states"),
            ("OPEN", "MITIGATING", "skip to MITIGATING"),
            ("MITIGATING", "INVESTIGATING", "backward transition"),
            ("CLOSED", "RESOLVED", "out of terminal state"),
            ("CLOSED", "OPEN", "reopen from terminal state"),
            ("RESOLVED", "OPEN", "backward to OPEN"),
            ("RESOLVED", "INVESTIGATING", "backward to INVESTIGATING"),
        ],
    )
    def test_illegal_transition_is_error(self, current, next_state, description):
        result = validate_state_transition(current, next_state)
        assert result.valid is False, f"Expected error for: {description}"
        assert len(result.errors) >= 1

    def test_terminal_state_message_mentions_terminal(self):
        result = validate_state_transition("CLOSED", "OPEN")
        assert any("terminal" in e.lower() for e in result.errors)

    def test_unknown_current_state_is_error(self):
        result = validate_state_transition("TRIAGED", "INVESTIGATING")
        assert result.valid is False
        assert any("TRIAGED" in e for e in result.errors)

    def test_unknown_next_state_is_error(self):
        result = validate_state_transition("OPEN", "ESCALATED")
        assert result.valid is False
        assert any("ESCALATED" in e for e in result.errors)

    @pytest.mark.parametrize("state", list(ALLOWED_STATUSES))
    def test_self_transition_blocked(self, state):
        """No state may transition to itself."""
        result = validate_state_transition(state, state)
        assert result.valid is False, f"Self-transition on '{state}' should be blocked"


# ===========================================================================
# TestValidateFeatureBatchRecord — happy paths
# ===========================================================================


class TestValidateFeatureBatchRecordHappy:
    def test_minimal_valid_batch_passes(self):
        result = validate_feature_batch_record(_minimal_batch())
        assert result.valid is True
        assert result.errors == []

    def test_multiple_features_all_within_threshold(self):
        result = validate_feature_batch_record(
            _minimal_batch(
                null_rates={
                    "feature_a": 0.01,
                    "feature_b": 0.03,
                    "feature_c": 0.00,
                },
                psi_scores={
                    "feature_a": 0.05,
                    "feature_b": 0.19,  # just under PSI threshold
                    "feature_c": 0.00,
                },
            )
        )
        assert result.valid is True

    def test_psi_exactly_at_threshold_passes(self):
        """PSI == threshold is not a breach (strictly greater than triggers error)."""
        result = validate_feature_batch_record(
            _minimal_batch(psi_scores={"feature_x": FEATURE_BATCH_PSI_THRESHOLD})
        )
        assert result.valid is True

    def test_null_rate_exactly_at_threshold_passes(self):
        """null_rate == threshold is not a breach."""
        result = validate_feature_batch_record(
            _minimal_batch(
                null_rates={"feature_x": FEATURE_BATCH_MAX_NULL_RATE}
            )
        )
        assert result.valid is True

    def test_large_row_count_passes(self):
        result = validate_feature_batch_record(_minimal_batch(row_count=10_000_000))
        assert result.valid is True


# ===========================================================================
# TestValidateFeatureBatchRecord — errors
# ===========================================================================


class TestValidateFeatureBatchRecordErrors:
    @pytest.mark.parametrize("missing_field", sorted(REQUIRED_FEATURE_BATCH_FIELDS))
    def test_each_required_field_missing_produces_error(self, missing_field):
        record = _minimal_batch()
        del record[missing_field]
        result = validate_feature_batch_record(record)
        assert result.valid is False
        assert any("Missing required batch fields" in e for e in result.errors)

    def test_row_count_zero_is_error(self):
        result = validate_feature_batch_record(_minimal_batch(row_count=0))
        assert result.valid is False
        assert any("row_count" in e for e in result.errors)

    def test_row_count_negative_is_error(self):
        result = validate_feature_batch_record(_minimal_batch(row_count=-1))
        assert result.valid is False

    def test_row_count_non_integer_string_is_error(self):
        result = validate_feature_batch_record(_minimal_batch(row_count="lots"))
        assert result.valid is False
        assert any("integer" in e for e in result.errors)

    def test_null_rate_exceeds_threshold_is_error(self):
        result = validate_feature_batch_record(
            _minimal_batch(null_rates={"feature_x": 0.06})  # 6% > 5%
        )
        assert result.valid is False
        assert any("feature_x" in e for e in result.errors)

    def test_multiple_null_rate_violations_all_reported(self):
        result = validate_feature_batch_record(
            _minimal_batch(
                null_rates={"feat_a": 0.10, "feat_b": 0.07, "feat_c": 0.01}
            )
        )
        assert result.valid is False
        error_text = " ".join(result.errors)
        assert "feat_a" in error_text
        assert "feat_b" in error_text
        assert "feat_c" not in error_text  # within threshold

    def test_psi_exceeds_threshold_is_error(self):
        result = validate_feature_batch_record(
            _minimal_batch(psi_scores={"feature_x": 0.31})  # > 0.20
        )
        assert result.valid is False
        assert any("feature_x" in e for e in result.errors)

    def test_multiple_psi_violations_all_reported(self):
        result = validate_feature_batch_record(
            _minimal_batch(
                psi_scores={"feat_a": 0.35, "feat_b": 0.25, "feat_c": 0.05}
            )
        )
        assert result.valid is False
        error_text = " ".join(result.errors)
        assert "feat_a" in error_text
        assert "feat_b" in error_text
        assert "feat_c" not in error_text

    def test_null_rate_non_float_value_is_error(self):
        result = validate_feature_batch_record(
            _minimal_batch(null_rates={"feature_x": "high"})
        )
        assert result.valid is False
        assert any("float" in e for e in result.errors)

    def test_psi_non_float_value_is_error(self):
        result = validate_feature_batch_record(
            _minimal_batch(psi_scores={"feature_x": "bad"})
        )
        assert result.valid is False
        assert any("float" in e for e in result.errors)


# ===========================================================================
# TestValidateFeatureBatchRecord — warnings
# ===========================================================================


class TestValidateFeatureBatchRecordWarnings:
    def test_schema_fingerprint_empty_string_is_warning(self):
        result = validate_feature_batch_record(
            _minimal_batch(schema_fingerprint="")
        )
        assert any("schema_fingerprint" in w for w in result.warnings)

    def test_schema_fingerprint_whitespace_only_is_warning(self):
        result = validate_feature_batch_record(
            _minimal_batch(schema_fingerprint="   ")
        )
        assert any("schema_fingerprint" in w for w in result.warnings)

    def test_schema_fingerprint_missing_key_is_warning(self):
        record = _minimal_batch()
        del record["schema_fingerprint"]
        # Missing key is caught by required field check (error), not warning.
        # This verifies the error path rather than a spurious warning.
        result = validate_feature_batch_record(record)
        assert result.valid is False

    def test_psi_scores_empty_dict_is_warning(self):
        result = validate_feature_batch_record(_minimal_batch(psi_scores={}))
        assert any("psi_scores" in w for w in result.warnings)

    def test_psi_scores_empty_dict_does_not_produce_error(self):
        result = validate_feature_batch_record(_minimal_batch(psi_scores={}))
        assert result.valid is True  # warning only, not an error

    def test_clean_batch_has_no_warnings(self):
        result = validate_feature_batch_record(_minimal_batch())
        assert result.warnings == []


# ===========================================================================
# TestValidateBatch
# ===========================================================================


class TestValidateBatch:
    def test_empty_list_returns_empty(self):
        assert validate_batch([]) == []

    def test_all_valid_records_all_pass(self):
        records = [_minimal_incident(), _full_incident(), _minimal_incident(incident_id="INC-2026-0002")]  # noqa: E501
        results = validate_batch(records)
        assert len(results) == 3
        assert all(r.valid for r in results)

    def test_mixed_valid_invalid_correct_per_index(self):
        records = [
            _minimal_incident(),           # valid   [0]
            _minimal_incident(severity="BAD"),  # invalid [1]
            _minimal_incident(),           # valid   [2]
            _minimal_incident(status=""),  # invalid [3]
        ]
        results = validate_batch(records)
        assert results[0].valid is True
        assert results[1].valid is False
        assert results[2].valid is True
        assert results[3].valid is False

    def test_context_tag_format(self):
        records = [_minimal_incident(), _minimal_incident(severity="BAD")]
        results = validate_batch(records)
        assert results[0].context == "record[0]"
        assert results[1].context == "record[1]"

    def test_result_order_preserved(self):
        ids = [f"INC-{str(i).zfill(4)}" for i in range(1, 6)]
        records = [_minimal_incident(incident_id=iid) for iid in ids]
        results = validate_batch(records)
        assert len(results) == 5
        for idx, result in enumerate(results):
            assert result.context == f"record[{idx}]"

    def test_single_invalid_record_batch(self):
        results = validate_batch([{"garbage": True}])
        assert len(results) == 1
        assert results[0].valid is False
        assert results[0].context == "record[0]"

    def test_all_invalid_batch(self):
        records = [{}, {"severity": "NOPE"}, {"status": "unknown"}]
        results = validate_batch(records)
        assert all(not r.valid for r in results)

    def test_batch_does_not_mutate_input_records(self):
        record = _minimal_incident()
        original_keys = set(record.keys())
        validate_batch([record])
        assert set(record.keys()) == original_keys
