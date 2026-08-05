"""
tests/unit/test_inference_router.py
=====================================
Unit tests for api/routers/inference.py

Covers:
  - _sanitize_for_log: newline stripping, None handling
  - detect_anomaly: happy path (200), artifact missing (503),
    predict exception (503)
  - inference_health: returns registry health dict including anomaly_threshold
  - structlog integration: inference logs emitted as structlog events,
    not stdlib logging (regression guard for ML-03)

All external dependencies (model_registry, get_current_user) are mocked.
No real model artifact or JWT is required.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.inference import _sanitize_for_log, router

# ---------------------------------------------------------------------------
# App fixture — mount router with auth dependency overridden
# ---------------------------------------------------------------------------

FAKE_USER: dict[str, Any] = {"sub": "testuser", "role": "admin"}


def _make_client(registry_mock: MagicMock) -> TestClient:
    """Return a TestClient with model_registry and get_current_user patched."""
    from api.dependencies import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER

    with patch("api.routers.inference.model_registry", registry_mock):
        client = TestClient(app, raise_server_exceptions=False)
        return client, registry_mock


def _healthy_registry(is_anomalous: bool = False) -> MagicMock:
    reg = MagicMock()
    reg.health.return_value = {"artifact_exists": True, "model_version": "1.0.0"}
    reg.predict.return_value = {
        "anomaly_score": 0.42,
        "is_anomalous": is_anomalous,
        "confidence": 0.75,
        "inference_latency_ms": 1.2,
    }
    return reg


VALID_BODY = {
    "severity_numeric": 2,
    "alert_count": 10,
    "time_to_detect_minutes": 30.0,
    "affected_services": 3,
    "on_call_escalations": 1,
    "duplicate_alert_ratio": 0.1,
    "blast_radius_pct": 20.0,
}


# ---------------------------------------------------------------------------
# _sanitize_for_log
# ---------------------------------------------------------------------------


def test_sanitize_strips_newlines() -> None:
    assert "\n" not in _sanitize_for_log("line1\nline2")
    assert "\r" not in _sanitize_for_log("line1\rline2")


def test_sanitize_none_returns_unknown() -> None:
    assert _sanitize_for_log(None) == "unknown"


# ---------------------------------------------------------------------------
# detect_anomaly — happy path
# ---------------------------------------------------------------------------


def test_detect_anomaly_happy_path() -> None:
    reg = _healthy_registry()
    client, patched_reg = _make_client(reg)
    with patch("api.routers.inference.model_registry", patched_reg):
        resp = client.post("/api/v1/inference/anomaly", json=VALID_BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert "anomaly_score" in data
    assert "is_anomalous" in data


def test_detect_anomaly_anomalous_result() -> None:
    reg = _healthy_registry(is_anomalous=True)
    client, patched_reg = _make_client(reg)
    with patch("api.routers.inference.model_registry", patched_reg):
        resp = client.post("/api/v1/inference/anomaly", json=VALID_BODY)
    assert resp.status_code == 200
    assert resp.json()["is_anomalous"] is True


# ---------------------------------------------------------------------------
# detect_anomaly — error paths
# ---------------------------------------------------------------------------


def test_detect_anomaly_artifact_missing_returns_503() -> None:
    reg = MagicMock()
    reg.health.return_value = {"artifact_exists": False}
    client, patched_reg = _make_client(reg)
    with patch("api.routers.inference.model_registry", patched_reg):
        resp = client.post("/api/v1/inference/anomaly", json=VALID_BODY)
    assert resp.status_code == 503


def test_detect_anomaly_predict_exception_returns_503() -> None:
    reg = _healthy_registry()
    reg.predict.side_effect = RuntimeError("model exploded")
    client, patched_reg = _make_client(reg)
    with patch("api.routers.inference.model_registry", patched_reg):
        resp = client.post("/api/v1/inference/anomaly", json=VALID_BODY)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# inference_health
# ---------------------------------------------------------------------------


def test_inference_health_returns_registry_health() -> None:
    reg = _healthy_registry()
    client, patched_reg = _make_client(reg)
    with patch("api.routers.inference.model_registry", patched_reg):
        resp = client.get("/api/v1/inference/anomaly/health")
    assert resp.status_code == 200
    assert resp.json()["artifact_exists"] is True


def test_inference_health_exposes_anomaly_threshold() -> None:
    """Registry health dict must include anomaly_threshold (ML-01 regression guard).

    After the ML-01 fix, ModelRegistry.health() returns `anomaly_threshold`
    reflecting the active env-var-configured threshold.  This test ensures
    that value is surfaced through the /health endpoint so ops can verify
    the running configuration without restarting or inspecting env vars.
    """
    reg = MagicMock()
    reg.health.return_value = {
        "artifact_exists": True,
        "model_version": "1.0.0",
        "anomaly_threshold": -0.05,  # simulates ANOMALY_THRESHOLD=-0.05 override
    }
    client, patched_reg = _make_client(reg)
    with patch("api.routers.inference.model_registry", patched_reg):
        resp = client.get("/api/v1/inference/anomaly/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "anomaly_threshold" in body, (
        "health endpoint must expose anomaly_threshold so operators can verify "
        "the active threshold without inspecting env vars"
    )
    assert body["anomaly_threshold"] == -0.05


# ---------------------------------------------------------------------------
# Structlog regression guard (ML-03)
# ---------------------------------------------------------------------------


def test_inference_router_uses_structlog_not_stdlib(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for ML-03: inference router must emit logs via structlog.

    Before ML-03 was fixed, api/routers/inference.py used stdlib
    `logging.getLogger(__name__)`, which bypassed the structlog PII-scrubbing
    and JSON pipeline configured in src/logger.py and
    observability/logging_config.py.

    This test imports the module and asserts that it does NOT import
    stdlib `logging` as its primary logger, and that the module-level
    `log` (or `logger`) variable is a structlog BoundLogger, not a
    stdlib Logger instance.
    """
    import logging as stdlib_logging

    import structlog

    # Re-import to get the module's actual logger binding
    import importlib
    import api.routers.inference as inference_module
    importlib.reload(inference_module)

    # The module must expose a structlog-bound logger, not a stdlib Logger
    module_logger = getattr(inference_module, "log", None) or getattr(
        inference_module, "logger", None
    )
    assert module_logger is not None, (
        "inference router must expose a module-level 'log' or 'logger' variable"
    )
    assert not isinstance(module_logger, stdlib_logging.Logger), (
        "inference router logger must NOT be a stdlib logging.Logger — "
        "use structlog (get_logger) to ensure PII scrubbing and JSON pipeline apply"
    )
