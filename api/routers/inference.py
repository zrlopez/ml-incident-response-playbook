"""
api/routers/inference.py
========================
Inference router — ML anomaly detection endpoint.

Route:
    POST /api/v1/inference/anomaly

Security:
    - Requires valid JWT (Bearer token via oauth2_scheme)
    - Rate-limited to 30 requests / minute per IP
    - Request body capped by MaxBodySizeMiddleware (1 MB default)

Attribution:
    Model: IsolationForest (scikit-learn, BSD-3-Clause)
    Copyright (c) 2007-2025 The scikit-learn developers.
    See MODEL_CARD.md for full license, attribution, and BibTeX citation.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from api.dependencies import get_current_user
from ml_models.incident_anomaly.registry import MODEL_VERSION, model_registry
from ml_models.incident_anomaly.schema import AnomalyRequest, AnomalyResponse

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/inference",
    tags=["inference"],
)


@router.post(
    "/anomaly",
    response_model=AnomalyResponse,
    summary="Incident anomaly detection",
    description=(
        "Runs the Isolation Forest anomaly detector against a single incident "
        "feature vector and returns an anomaly score, binary flag, and "
        "normalized confidence. Requires a valid Bearer JWT."
    ),
    status_code=status.HTTP_200_OK,
)
async def detect_anomaly(
    request: Request,
    body: AnomalyRequest,
    current_user: Annotated[dict, Depends(get_current_user)],  # type: ignore[type-arg]
) -> AnomalyResponse:
    """Score an incident feature vector for anomalous behaviour.

    The model artifact must be present at
    ``ml_models/incident_anomaly/artifacts/isolation_forest_v1.joblib``.
    Train it with ``python scripts/train_model.py`` if it is missing.

    Raises:
        503 Service Unavailable: model artifact not found or failed to load.
        422 Unprocessable Entity: feature vector fails Pydantic validation.
    """
    health = model_registry.health()
    if not health["artifact_exists"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Model artifact not found. "
                "Run `python scripts/train_model.py` to generate it."
            ),
        )

    features = [
        body.severity_numeric,
        body.alert_count,
        body.time_to_detect_minutes,
        body.affected_services,
        body.on_call_escalations,
        body.duplicate_alert_ratio,
        body.blast_radius_pct,
    ]

    try:
        result = model_registry.predict(features)
    except Exception as exc:
        log.exception("Inference error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model inference failed. Check server logs.",
        ) from exc

    log.info(
        "inference anomaly_score=%.4f is_anomalous=%s latency_ms=%.2f user=%s",
        result["anomaly_score"],
        result["is_anomalous"],
        result["inference_latency_ms"],
        current_user.get("sub", "unknown"),
    )

    return AnomalyResponse(
        anomaly_score=result["anomaly_score"],
        is_anomalous=result["is_anomalous"],
        confidence=result["confidence"],
        model_version=MODEL_VERSION,
        inference_latency_ms=result["inference_latency_ms"],
    )


@router.get(
    "/anomaly/health",
    summary="Model registry health",
    description="Returns the load state and version of the anomaly model artifact.",
    status_code=status.HTTP_200_OK,
)
async def inference_health(
    current_user: Annotated[dict, Depends(get_current_user)],  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    """Return model registry health state."""
    return model_registry.health()
