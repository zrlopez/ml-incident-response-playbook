"""test_app.py — Unit tests for the top-level Gradio entry point (app.py).

Strategy
--------
Gradio is only in requirements-demo.txt, not requirements-dev.txt.  Importing
app.py executes ``import gradio as gr`` at module load, which would raise
ModuleNotFoundError in the standard test environment before any patch can apply.

The fix is to inject a lightweight MagicMock stub for ``gradio`` (and the
artifact bootstrap / model-registry import side-effects) into sys.modules
BEFORE app is first imported.  This is done once at module level in the
``_bootstrap_app_module()`` helper, which is called at import time of this
test file.  Because Python caches modules in sys.modules, subsequent
``from app import ...`` calls inside test methods reuse the already-patched
module without re-executing the module body.

Testable surface (Gradio UI block intentionally excluded):
  _run_inference()  — pure business logic wrapping model_registry.predict()
  _SEVERITY_MAP     — severity label → numeric mapping constant
  _RUNBOOK_HINTS    — is_anomalous bool → hint string constant
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Gradio stub — must run before any ``import app`` or ``from app import ...``
# ---------------------------------------------------------------------------

def _make_gradio_stub() -> types.ModuleType:
    """Build a minimal fake ``gradio`` module that satisfies app.py's usage.

    app.py uses:
      gr.Blocks(title=...)  as a context manager
      gr.Markdown(...)
      gr.Row() / gr.Column()  as context managers
      gr.Dropdown(...) / gr.Slider(...) / gr.Button(...) / gr.Textbox(...)
      submit_btn.click(...)
    All of these just need to be callable and support ``with`` blocks.
    A MagicMock handles all of that automatically.
    """
    stub = types.ModuleType("gradio")
    magic = MagicMock()
    stub.__dict__.update({
        "Blocks": magic.Blocks,
        "Markdown": magic.Markdown,
        "Row": magic.Row,
        "Column": magic.Column,
        "Dropdown": magic.Dropdown,
        "Slider": magic.Slider,
        "Button": magic.Button,
        "Textbox": magic.Textbox,
    })
    return stub


def _bootstrap_app_module() -> None:
    """Inject stubs and import app exactly once, making the patched module
    available for all test methods without re-executing the module body."""
    if "gradio" not in sys.modules:
        sys.modules["gradio"] = _make_gradio_stub()  # type: ignore[assignment]

    _artifact_patch = patch(
        "pathlib.Path.exists",
        return_value=True,
    )

    mock_registry = MagicMock()
    mock_registry.health.return_value = {"artifact_exists": True}
    mock_registry.predict.return_value = {
        "is_anomalous": False,
        "anomaly_score": 0.05,
        "confidence": 0.9,
    }
    registry_module = types.ModuleType("ml_models.incident_anomaly.registry")
    registry_module.model_registry = mock_registry  # type: ignore[attr-defined]
    registry_module.MODEL_VERSION = "v1-test"  # type: ignore[attr-defined]

    if "ml_models.incident_anomaly.registry" not in sys.modules:
        sys.modules["ml_models.incident_anomaly.registry"] = registry_module

    with _artifact_patch:
        if "app" not in sys.modules:
            import app  # noqa: F401


_bootstrap_app_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_mock(
    *, artifact_exists: bool = True, is_anomalous: bool = False
) -> MagicMock:
    mock = MagicMock()
    mock.health.return_value = {"artifact_exists": artifact_exists}
    mock.predict.return_value = {
        "is_anomalous": is_anomalous,
        "anomaly_score": -0.1234 if is_anomalous else 0.0567,
        "confidence": 0.85,
    }
    return mock


def _default_inputs() -> dict[str, Any]:
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
    def test_anomalous_hint_contains_sev(self) -> None:
        from app import _RUNBOOK_HINTS
        assert "SEV" in _RUNBOOK_HINTS[True]

    def test_normal_hint_contains_normal(self) -> None:
        from app import _RUNBOOK_HINTS
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
    def _call(self, **kwargs: Any) -> tuple:
        inputs = {**_default_inputs(), **kwargs}
        mock_registry = _make_registry_mock(artifact_exists=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            return _run_inference(**inputs)

    def test_returns_four_tuple(self) -> None:
        assert len(self._call()) == 4

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
        assert len(mock_registry.predict.call_args[0][0]) == 7

    def test_all_features_are_float(self) -> None:
        mock_registry = _make_registry_mock(artifact_exists=True, is_anomalous=False)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            _run_inference(**_default_inputs())
        assert all(isinstance(f, float) for f in mock_registry.predict.call_args[0][0])


# ---------------------------------------------------------------------------
# TestRunInference — anomalous prediction branch
# ---------------------------------------------------------------------------

class TestRunInferenceAnomalous:
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
    def _get_severity_feature(self, label: str) -> float:
        mock_registry = _make_registry_mock(artifact_exists=True)
        with patch("app.model_registry", mock_registry):
            from app import _run_inference
            _run_inference(**{**_default_inputs(), "severity_label": label})
        return mock_registry.predict.call_args[0][0][0]

    def test_critical_sends_1(self) -> None:
        assert self._get_severity_feature("SEV-1 (Critical)") == 1.0

    def test_high_sends_2(self) -> None:
        assert self._get_severity_feature("SEV-2 (High)") == 2.0

    def test_medium_sends_3(self) -> None:
        assert self._get_severity_feature("SEV-3 (Medium)") == 3.0

    def test_low_sends_4(self) -> None:
        assert self._get_severity_feature("SEV-4 (Low)") == 4.0

    def test_unknown_label_falls_back_to_3(self) -> None:
        assert self._get_severity_feature("UNKNOWN") == 3.0
