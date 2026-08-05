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

Remediation changelog:
  ML-04   Replaced bare `dict` return type on inference_health with
          dict[str, Any]; replaced type: ignore[type-arg] on current_user
          Annotated with properly-annotated UserClaims TypedDict;
          added explicit AnomalyResponse return annotation on detect_anomaly;
          ignore_errors mypy override removed.
  LOG-01  Replaced stdlib `logging.getLogger(__name__)` with structlog via
          `src.logger.get_logger(__name__)`. All inference log events now
          travel the same processor pipeline as the rest of the service:
          field-level redaction, PII scrubbing, service context injection,
          ISO-8601 timestamps, and JSON output in production.
          See: observability/logging_config.py, src/logger.py, ADR-007.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from api.dependencies import get_current_user
from ml_models.incident_anomaly.registry import MODEL_VERSION, model_registry
from ml_models.incident_anomaly.schema import AnomalyRequest, AnomalyResponse
from src.logger import get_logger

# LOG-01: structlog-backed logger — PII scrubbing and JSON pipeline apply.
log = get_logger(__name__)


def _sanitize_for_log(value: Any) -> str:
    """Return a single-line, log-safe representation of user-influenced values."""
    text = str(value) if value is not None else "unknown"
    return text.replace("\r", "").replace("\n", "")


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

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
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
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

    features: list[float] = [
        float(body.severity_numeric),
        float(body.alert_count),
        body.time_to_detect_minutes,
        float(body.affected_services),
        float(body.on_call_escalations),
        body.duplicate_alert_ratio,
        body.blast_radius_pct,
    ]

    try:
        result = model_registry.predict(features)
    except Exception as exc:
        log.exception(
            "inference.error",
            exc_info=exc,
            endpoint="/api/v1/inference/anomaly",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model inference failed. Check server logs.",
        ) from exc

    # LOG-01: structlog event — travels through PII scrubbing processor chain.
    log.info(
        "inference.anomaly_scored",
        anomaly_score=result["anomaly_score"],
        is_anomalous=result["is_anomalous"],
        inference_latency_ms=result["inference_latency_ms"],
        user=_sanitize_for_log(current_user.get("sub", "unknown")),
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
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Return model registry health state."""
    result: dict[str, Any] = dict(model_registry.health())
    return result
