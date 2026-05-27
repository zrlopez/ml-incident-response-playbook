"""
ml_models/incident_anomaly/schema.py
=====================================
Pydantic v2 request / response schemas for the incident anomaly inference
endpoint.  These are the API boundary contracts — keeping them separate from
the model implementation allows schema validation to be tested independently.

Attribution:
    Uses Pydantic v2 (MIT License — https://github.com/pydantic/pydantic).
    Model algorithm: scikit-learn IsolationForest (BSD-3-Clause).
    See MODEL_CARD.md at the repository root for full attribution and license.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AnomalyRequest(BaseModel):
    """Feature vector for a single incident observation.

    All fields correspond to the training feature schema documented in
    MODEL_CARD.md.  Ranges are enforced here so the model never receives
    out-of-distribution inputs silently.
    """

    severity_numeric: int = Field(
        ...,
        ge=1,
        le=5,
        description="Incident severity: 1=SEV-1 (critical) – 5=informational.",
    )
    alert_count: int = Field(
        ...,
        ge=1,
        le=500,
        description="Total alerts fired during the incident window.",
    )
    time_to_detect_minutes: float = Field(
        ...,
        gt=0.0,
        le=720.0,
        description="Minutes elapsed from first anomalous signal to detection.",
    )
    affected_services: int = Field(
        ...,
        ge=1,
        le=50,
        description="Count of distinct services impacted.",
    )
    on_call_escalations: int = Field(
        ...,
        ge=0,
        le=10,
        description="Number of on-call escalation pages generated.",
    )
    duplicate_alert_ratio: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of alerts that were duplicates of earlier alerts.",
    )
    blast_radius_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Estimated percentage of user-facing traffic impacted.",
    )

    @field_validator("duplicate_alert_ratio", "blast_radius_pct")
    @classmethod
    def _clamp_float(cls, v: float) -> float:
        """Guard against floating-point edge cases at exact boundaries."""
        return round(float(v), 6)

    model_config = {
        "json_schema_extra": {
            "example": {
                "severity_numeric": 1,
                "alert_count": 142,
                "time_to_detect_minutes": 4.7,
                "affected_services": 8,
                "on_call_escalations": 3,
                "duplicate_alert_ratio": 0.35,
                "blast_radius_pct": 62.0,
            }
        }
    }


class AnomalyResponse(BaseModel):
    """Inference result for a single incident observation."""

    anomaly_score: float = Field(
        ...,
        description=(
            "Raw Isolation Forest decision function score. "
            "Negative values indicate anomalous observations."
        ),
    )
    is_anomalous: bool = Field(
        ...,
        description="True when anomaly_score is below the configured threshold.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Normalized confidence in [0.0, 1.0]: distance from the decision "
            "boundary scaled to a probability-like value."
        ),
    )
    model_version: str = Field(
        ...,
        description="Semantic version of the loaded model artifact.",
    )
    inference_latency_ms: float = Field(
        ...,
        description="Wall-clock time for model.predict() in milliseconds.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "anomaly_score": -0.312,
                "is_anomalous": True,
                "confidence": 0.78,
                "model_version": "1.0.0",
                "inference_latency_ms": 1.4,
            }
        }
    }
