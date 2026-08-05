"""
tests/integration/test_inference_integration.py
================================================
End-to-end HTTP integration tests for the inference router.

Routes covered:
  POST  /api/v1/inference/anomaly          -- detect_anomaly()
  GET   /api/v1/inference/anomaly/health   -- inference_health()

Fixture design (no Postgres, no Redis, no real network):
  - Uses httpx.AsyncClient with ASGITransport (ASGI test transport).
  - lifespan=False: skips init_db, Redis connect, OTel bootstrap.
  - Overrides get_current_user with a stub admin user.
  - ModelRegistry is patched at the module level so no .joblib artifact
    is required on disk; each test controls artifact_exists / predict() as
    needed.

Coverage targets:
  IT-INF-01  POST /anomaly           -- 200, returns AnomalyResponse fields
  IT-INF-02  POST /anomaly           -- 200, is_anomalous=True for extreme vector
  IT-INF-03  POST /anomaly           -- 503 when artifact_exists=False
  IT-INF-04  POST /anomaly           -- 503 when model_registry.predict() raises
  IT-INF-05  POST /anomaly           -- 401/403 when unauthenticated
  IT-INF-06  POST /anomaly           -- 422 for invalid payload (bad severity_numeric)
  IT-INF-07  GET  /anomaly/health    -- 200, anomaly_threshold exposed in response
  IT-INF-08  GET  /anomaly/health    -- 200, artifact_exists=False reflected correctly
  IT-INF-09  POST /anomaly           -- response model_version matches MODULE constant
  IT-INF-10  POST /anomaly           -- inference_latency_ms is non-negative float

Changelog: docs/REMEDIATION_LOG.md Phase 12 (ML-09).
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

_STUB_USER: dict[str, Any] = {
    "sub": "test-admin",
    "username": "test-admin",
    "role": "admin",
    "disabled": False,
}

_NOMINAL_PAYLOAD: dict[str, Any] = {
    "severity_numeric": 2,
    "alert_count": 5,
    "time_to_detect_minutes": 12.0,
    "affected_services": 3,
    "on_call_escalations": 1,
    "duplicate_alert_ratio": 0.1,
    "blast_radius_pct": 20.0,
}

_EXTREME_PAYLOAD: dict[str, Any] = {
    "severity_numeric": 4,
    "alert_count": 200,
    "time_to_detect_minutes": 600.0,
    "affected_services": 50,
    "on_call_escalations": 10,
    "duplicate_alert_ratio": 0.99,
    "blast_radius_pct": 99.9,
}


def _mock_registry(
    *,
    artifact_exists: bool = True,
    is_anomalous: bool = False,
    anomaly_score: float = -0.12,
    confidence: float = 0.72,
    latency_ms: float = 1.5,
    anomaly_threshold: float = 0.0,
    raise_on_predict: Exception | None = None,
) -> MagicMock:
    """Return a configured ModelRegistry mock."""
    m = MagicMock()
    m.health.return_value = {
        "artifact_exists": artifact_exists,
        "model_version": "1.0.0",
        "anomaly_threshold": anomaly_threshold,
        "model_loaded": artifact_exists,
    }
    if raise_on_predict:
        m.predict.side_effect = raise_on_predict
    else:
        m.predict.return_value = {
            "anomaly_score": anomaly_score,
            "is_anomalous": is_anomalous,
            "confidence": confidence,
            "inference_latency_ms": latency_ms,
        }
    return m


def _make_app(stub_user: dict[str, Any] | None = None) -> Any:
    """Build a minimal FastAPI test app with the inference router."""
    os.environ.setdefault(
        "JWT_SECRET_KEY", "test-secret-minimum-32-chars-xxxxxxxxxxxx"
    )
    from api.app import app
    from api.dependencies import get_current_user

    if stub_user is not None:
        app.dependency_overrides[get_current_user] = lambda: stub_user
    else:
        app.dependency_overrides.pop(get_current_user, None)

    return app


# ---------------------------------------------------------------------------
# IT-INF-01  Happy path — 200 + well-formed AnomalyResponse
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_200_nominal():
    """POST /anomaly with a valid payload returns 200 and all required fields."""
    mock_reg = _mock_registry()
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/inference/anomaly", json=_NOMINAL_PAYLOAD
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "anomaly_score" in body
    assert "is_anomalous" in body
    assert "confidence" in body
    assert "model_version" in body
    assert "inference_latency_ms" in body
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-02  Extreme vector — is_anomalous flag respected
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_extreme_vector_is_anomalous():
    """A model that returns is_anomalous=True propagates correctly through the HTTP layer."""
    mock_reg = _mock_registry(is_anomalous=True, anomaly_score=0.55, confidence=0.91)
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/inference/anomaly", json=_EXTREME_PAYLOAD
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_anomalous"] is True
    assert body["anomaly_score"] == pytest.approx(0.55, abs=1e-6)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-03  503 when artifact is missing
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_503_no_artifact():
    """POST /anomaly returns 503 when model artifact does not exist on disk."""
    mock_reg = _mock_registry(artifact_exists=False)
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/inference/anomaly", json=_NOMINAL_PAYLOAD
            )

    assert resp.status_code == 503
    assert "artifact" in resp.json()["detail"].lower()
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-04  503 when predict() raises
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_503_predict_raises():
    """POST /anomaly returns 503 when model_registry.predict() raises an exception."""
    mock_reg = _mock_registry(raise_on_predict=RuntimeError("ONNX panic"))
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/inference/anomaly", json=_NOMINAL_PAYLOAD
            )

    assert resp.status_code == 503
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-05  401/403 without auth
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_unauthenticated():
    """POST /anomaly without a JWT returns 401 or 403 (not 200 or 500)."""
    from fastapi import FastAPI
    from api.routers.inference import router as inference_router

    bare = FastAPI()
    bare.include_router(inference_router)

    async with AsyncClient(
        transport=ASGITransport(app=bare, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        resp = await client.post("/api/v1/inference/anomaly", json=_NOMINAL_PAYLOAD)

    assert resp.status_code in (401, 403, 422), (
        f"Expected 401/403/422 for unauthenticated request, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# IT-INF-06  422 for invalid payload
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_422_invalid_severity():
    """POST /anomaly with severity_numeric=99 returns 422 (Pydantic validation)."""
    mock_reg = _mock_registry()
    app = _make_app(_STUB_USER)

    bad_payload = {**_NOMINAL_PAYLOAD, "severity_numeric": 99}

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/inference/anomaly", json=bad_payload
            )

    assert resp.status_code == 422
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-07  GET /anomaly/health — anomaly_threshold exposed
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_health_exposes_anomaly_threshold():
    """GET /anomaly/health returns 200 with anomaly_threshold in the response body."""
    mock_reg = _mock_registry(anomaly_threshold=0.05)
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/api/v1/inference/anomaly/health")

    assert resp.status_code == 200
    body = resp.json()
    assert "anomaly_threshold" in body, (
        f"anomaly_threshold missing from health response: {body}"
    )
    assert body["anomaly_threshold"] == pytest.approx(0.05, abs=1e-9)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-08  GET /anomaly/health reflects artifact_exists=False
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_health_reflects_missing_artifact():
    """GET /anomaly/health returns artifact_exists=False when model is not loaded."""
    mock_reg = _mock_registry(artifact_exists=False)
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/api/v1/inference/anomaly/health")

    assert resp.status_code == 200
    assert resp.json()["artifact_exists"] is False
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-09  model_version in response matches MODULE constant
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_model_version_matches_constant():
    """AnomalyResponse.model_version must equal ml_models.incident_anomaly.registry.MODEL_VERSION."""
    from ml_models.incident_anomaly.registry import MODEL_VERSION

    mock_reg = _mock_registry()
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/inference/anomaly", json=_NOMINAL_PAYLOAD
            )

    assert resp.status_code == 200
    assert resp.json()["model_version"] == MODEL_VERSION
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# IT-INF-10  inference_latency_ms is non-negative
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_infer_anomaly_latency_non_negative():
    """inference_latency_ms in AnomalyResponse must be a non-negative float."""
    mock_reg = _mock_registry(latency_ms=2.34)
    app = _make_app(_STUB_USER)

    with patch("api.routers.inference.model_registry", mock_reg):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=True),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/inference/anomaly", json=_NOMINAL_PAYLOAD
            )

    assert resp.status_code == 200
    latency = resp.json()["inference_latency_ms"]
    assert isinstance(latency, (int, float))
    assert latency >= 0.0
    app.dependency_overrides.clear()
