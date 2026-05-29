"""
app.py  —  Hugging Face Space entry point (Gradio demo)
=========================================================
Purpose
-------
This file exists **solely** to satisfy the Hugging Face Spaces SDK
convention, which requires a top-level ``app.py`` that launches a Gradio
(or Streamlit) interface.  It is **not** the production application.

Production API
--------------
The production FastAPI application lives at ``api/app.py`` and is
deployed via Gunicorn/Uvicorn with full JWT authentication, RBAC,
rate-limiting, and middleware.  That entry point is intentionally
inaccessible from this demo surface to avoid exposing auth internals.

What this demo does
-------------------
Calls ``ml_models.incident_anomaly.registry.model_registry.predict()``
directly — the same inference layer used by the production
``POST /api/v1/inference/anomaly`` route — but without the HTTP/auth
wrapper.  Inputs map 1-to-1 to the 7-feature AnomalyRequest schema
defined in ``ml_models/incident_anomaly/schema.py``.

This approach intentionally bypasses auth because:
  1. HF Spaces is a read-only, stateless showcase environment.
  2. The model artifact itself contains no PII or proprietary data.
  3. The demo surface exposes no write operations, tokens, or secrets.

See Also
--------
- ``api/app.py``            — production FastAPI factory
- ``api/routers/inference.py`` — authenticated inference route
- ``ml_models/incident_anomaly/registry.py`` — model registry
- ``DEMO.md``               — architecture decision record for this split
- ``MODEL_CARD.md``         — model provenance, limitations, and license
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import gradio as gr

from ml_models.incident_anomaly.registry import MODEL_VERSION, model_registry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_RUNBOOK_DIR = Path("runbooks")

_SEVERITY_MAP: dict[str, int] = {
    "SEV-1 (Critical)": 1,
    "SEV-2 (High)": 2,
    "SEV-3 (Medium)": 3,
    "SEV-4 (Low)": 4,
}

_RUNBOOK_HINTS: dict[bool, str] = {
    True: "⚠️  Anomalous pattern detected — consult the SEV-1/SEV-2 runbook.",
    False: "✅  Pattern within normal bounds — standard triage applies.",
}


# ---------------------------------------------------------------------------
# Inference wrapper
# ---------------------------------------------------------------------------
def _run_inference(
    severity_label: str,
    alert_count: int,
    time_to_detect: float,
    affected_services: int,
    escalations: int,
    duplicate_ratio: float,
    blast_radius: float,
) -> tuple[str, str, str, str]:
    """Invoke the IsolationForest registry and return formatted outputs."""
    health = model_registry.health()
    if not health["artifact_exists"]:
        msg = (
            "Model artifact not found. "
            "Run `python scripts/train_model.py` to generate it."
        )
        return msg, "—", "—", "—"

    severity_numeric = _SEVERITY_MAP.get(severity_label, 3)
    features: list[float] = [
        float(severity_numeric),
        float(alert_count),
        float(time_to_detect),
        float(affected_services),
        float(escalations),
        float(duplicate_ratio),
        float(blast_radius),
    ]

    result = model_registry.predict(features)

    verdict = "🔴 ANOMALOUS" if result["is_anomalous"] else "🟢 NORMAL"
    score = f"{result['anomaly_score']:.4f}"
    confidence = f"{result['confidence'] * 100:.1f}%"
    hint = _RUNBOOK_HINTS[result["is_anomalous"]]

    return verdict, score, confidence, hint


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------
_DESCRIPTION = textwrap.dedent("""
    ## ML Incident Response — Anomaly Detector Demo

    Enter incident telemetry below to score it against the
    **IsolationForest** model trained on historical ML system incidents.

    > **Note:** This is a read-only showcase of the inference layer.
    > The production API (`api/app.py`) requires JWT authentication
    > and is not exposed here.

    Model version: **{version}** · [Source](https://github.com/zrlopez/ml-incident-response-playbook)
""").format(version=MODEL_VERSION)

with gr.Blocks(title="ML Incident Anomaly Detector") as demo:
    gr.Markdown(_DESCRIPTION)

    with gr.Row():
        with gr.Column():
            severity = gr.Dropdown(
                choices=list(_SEVERITY_MAP.keys()),
                value="SEV-3 (Medium)",
                label="Severity Level",
            )
            alert_count = gr.Slider(
                minimum=1, maximum=500, value=10, step=1,
                label="Alert Count",
            )
            time_to_detect = gr.Slider(
                minimum=0.0, maximum=1440.0, value=15.0, step=0.5,
                label="Time to Detect (minutes)",
            )
            affected_services = gr.Slider(
                minimum=1, maximum=50, value=2, step=1,
                label="Affected Services",
            )

        with gr.Column():
            escalations = gr.Slider(
                minimum=0, maximum=20, value=1, step=1,
                label="On-Call Escalations",
            )
            duplicate_ratio = gr.Slider(
                minimum=0.0, maximum=1.0, value=0.1, step=0.01,
                label="Duplicate Alert Ratio",
            )
            blast_radius = gr.Slider(
                minimum=0.0, maximum=100.0, value=10.0, step=0.5,
                label="Blast Radius (%)",
            )

    submit_btn = gr.Button("Run Inference", variant="primary")

    with gr.Row():
        verdict_out = gr.Textbox(label="Verdict", interactive=False)
        score_out = gr.Textbox(label="Anomaly Score", interactive=False)
        confidence_out = gr.Textbox(label="Confidence", interactive=False)

    hint_out = gr.Textbox(label="Runbook Guidance", interactive=False)

    submit_btn.click(
        fn=_run_inference,
        inputs=[
            severity, alert_count, time_to_detect,
            affected_services, escalations, duplicate_ratio, blast_radius,
        ],
        outputs=[verdict_out, score_out, confidence_out, hint_out],
    )

if __name__ == "__main__":
    demo.launch()
