"""test_app.py — Unit tests for the top-level Gradio entry point (app.py).

Strategy
--------
The Gradio UI block (gr.Blocks) is not unit-testable and is excluded from
coverage by pragma comments in app.py.  The testable surface is:

  _run_inference()  — pure business logic wrapping model_registry.predict()
  _SEVERITY_MAP     — severity label → numeric mapping constant
  _RUNBOOK_HINTS    — is_anomalous bool → hint string constant

All model I/O is mocked; Gradio is never imported during these tests.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_mock(*, artifact_exists: bool = True, is_anomalous: bool = False) -> MagicMock:
    """Return a mock model_registry with health() and predict() pre-configured."""
    mock = MagicMock()
    mock.health.return_value = {"artifact_exists": artifact_exists}
    mock.predict.return_value = {
        "is_anomalous": is_anomalous,
        "anomaly_score": -0.1234 if is_anomalous else 0.0567,
        "confidence": 0.85,
    }
    return mock


def _default_inputs() -> dict[str, Any]:
    """Default valid inputs matching _run_inference() signature."""
    return dict(
        severity_label="SEV-3 (Medium)",
        alert_count=10,
        time_to_detect=15.0,
        affected_services=2,
        escalations=1,
        duplicate_ratio=0.1,
        blast_radius=10.0,
    )


# ---------------------------------------------------------------------------
# TestSeverityMap
# ---------------------------------------------------------------------------

class TestSeverityMap:
    """The _SEVERITY_MAP constant maps every label to the correct integer."""

    def test_sev1_maps_to_1(self) -> None:
        from app import _SEVERITY_MAP
        assert _SEVERITY_MAP["SEV-1 (Critical)"] == 1

    def test_sev2_maps_to_2(self) -> None:
        from app import _SEVERITY_MAP
        assert _SEVERITY_MAP["SEV-2 (High)"] == 2

    def test_sev3_maps_to_3(self) -> None:
        from app import _SEVERITY_MAP
        assert _SEVERITY_MAP["SEV-3 (Medium)"] == 3

    def test_sev4_maps_to_4(self) -> None:
        from app import _SEVERITY_MAP
        assert _SEVERITY_MAP["SEV-4 (Low)"] == 4

    def test_all_four_severities_present(self) -> None:
        from app import _SEVERITY_MAP
        assert len(_SEVERITY_MAP) == 4


# ---------------------------------------------------------------------------
# TestRunbookHints
# ---------------------------------------------------------------------------

class TestRunbookHints:
    """_RUNBOOK_HINTS returns the right guidance string per anomaly flag."""

    def test_anomalous_hint_contains_sev(self) -> None:
        from app import _RUNBOOK_HINTS
        assert "SEV" in _RUNBOOK_HINTS[True]

    def test_normal_hint_contains_normal(self) -> None:
        from app import _RUNBOOK_HINTS
        # The normal hint should indicate within-bounds status
        hint = _RUNBOOK_HINTS[False]
        assert any(word in hint.lower() for word in ("normal", "standard", "bounds"))

    def test_both_keys_present(self) -> None:
        from app import _RUNBOOK_HINTS
        assert True in _RUNBOOK_HINTS
        assert False in _RUNBOOK_HINTS


# ---------------------------------------------------------------------------
# TestRunInference — artifact missing branch
# ---------------------------------------------------------------------------

class TestRunInferenceArtifactMissing:
    """When model_registry.health() reports artifact_exists=False, _run_inference
    returns an error tuple without calling predict().
    """

    def _call(self, **kwargs: Any) -> tuple:
        inputs = {**_default_inputs(), **kwargs}
        mock_registry = _make_registry_mock(artifact_exists=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            return _run_inference(**inputs)

    def test_returns_four_tuple(self) -> None:
        result = self._call()
        assert len(result) == 4

    def test_first_element_is_error_message(self) -> None:
        verdict, *_ = self._call()
        assert "artifact" in verdict.lower() or "not found" in verdict.lower()

    def test_remaining_elements_are_placeholders(self) -> None:
        _, score, confidence, hint = self._call()
        assert score == "—"
        assert confidence == "—"
        assert hint == "—"

    def test_predict_never_called_when_artifact_missing(self) -> None:
        mock_registry = _make_registry_mock(artifact_exists=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            _run_inference(**_default_inputs())
        mock_registry.predict.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunInference — normal prediction branch
# ---------------------------------------------------------------------------

class TestRunInferenceNormal:
    """When artifact exists and model returns is_anomalous=False."""

    def _call(self, **overrides: Any) -> tuple:
        inputs = {**_default_inputs(), **overrides}
        mock_registry = _make_registry_mock(artifact_exists=True, is_anomalous=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            return _run_inference(**inputs)

    def test_verdict_is_normal(self) -> None:
        verdict, *_ = self._call()
        assert "NORMAL" in verdict

    def test_score_is_formatted_float(self) -> None:
        _, score, _, _ = self._call()
        # Should be parseable as a float
        float(score)

    def test_confidence_ends_with_percent(self) -> None:
        _, _, confidence, _ = self._call()
        assert confidence.endswith("%")

    def test_hint_references_normal_triage(self) -> None:
        _, _, _, hint = self._call()
        assert any(word in hint.lower() for word in ("normal", "standard", "bounds", "triage"))

    def test_predict_called_once(self) -> None:
        mock_registry = _make_registry_mock(artifact_exists=True, is_anomalous=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            _run_inference(**_default_inputs())
        mock_registry.predict.assert_called_once()

    def test_predict_receives_seven_features(self) -> None:
        mock_registry = _make_registry_mock(artifact_exists=True, is_anomalous=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            _run_inference(**_default_inputs())
        call_args = mock_registry.predict.call_args
        features = call_args[0][0]  # first positional arg
        assert len(features) == 7

    def test_all_features_are_float(self) -> None:
        mock_registry = _make_registry_mock(artifact_exists=True, is_anomalous=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            _run_inference(**_default_inputs())
        features = mock_registry.predict.call_args[0][0]
        assert all(isinstance(f, float) for f in features)


# ---------------------------------------------------------------------------
# TestRunInference — anomalous prediction branch
# ---------------------------------------------------------------------------

class TestRunInferenceAnomalous:
    """When artifact exists and model returns is_anomalous=True."""

    def _call(self, **overrides: Any) -> tuple:
        inputs = {**_default_inputs(), **overrides}
        mock_registry = _make_registry_mock(artifact_exists=True, is_anomalous=True)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            return _run_inference(**inputs)

    def test_verdict_is_anomalous(self) -> None:
        verdict, *_ = self._call()
        assert "ANOMALOUS" in verdict

    def test_hint_references_runbook(self) -> None:
        _, _, _, hint = self._call()
        assert any(word in hint.lower() for word in ("runbook", "sev", "anomal", "consult"))


# ---------------------------------------------------------------------------
# TestRunInference — severity label routing
# ---------------------------------------------------------------------------

class TestRunInferenceSeverityRouting:
    """Each severity label maps to the correct numeric feature value."""

    def _get_severity_feature(self, label: str) -> float:
        mock_registry = _make_registry_mock(artifact_exists=True)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            inputs = {**_default_inputs(), "severity_label": label}
            _run_inference(**inputs)
        return mock_registry.predict.call_args[0][0][0]  # first feature = severity

    def test_critical_sends_1(self) -> None:
        assert self._get_severity_feature("SEV-1 (Critical)") == 1.0

    def test_high_sends_2(self) -> None:
        assert self._get_severity_feature("SEV-2 (High)") == 2.0

    def test_medium_sends_3(self) -> None:
        assert self._get_severity_feature("SEV-3 (Medium)") == 3.0

    def test_low_sends_4(self) -> None:
        assert self._get_severity_feature("SEV-4 (Low)") == 4.0

    def test_unknown_label_falls_back_to_3(self) -> None:
        # Unknown label should default to 3 (get returns None → int(None) would fail;
        # the code uses .get(label, 3) so we expect 3.0)
        assert self._get_severity_feature("UNKNOWN") == 3.0
