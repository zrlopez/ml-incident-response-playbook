"""
tests/integration/test_observability.py
=======================================
Observability integration tests for the remediated observability surface.

Scope:
  1. anomaly_detection.simple_threshold
  2. anomaly_detection.check_multiple
  3. otel_setup.configure_otel (disabled path + import-missing path)
  4. otel_setup.shutdown_otel

Explicitly not tested here:
  - logging_config.py (unread in this session; coverage remains incidental)
  - live OTLP exporter delivery to a real collector
    [GAP: integration target depends on external OTel infrastructure.]

Source authority:
  - observability/anomaly_detection.py
  - observability/otel_setup.py
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from observability.anomaly_detection import ThresholdResult, check_multiple, simple_threshold
from observability import otel_setup


# ---------------------------------------------------------------------------
# anomaly_detection.simple_threshold
# ---------------------------------------------------------------------------


def test_simple_threshold_high_breach():
    result = simple_threshold(current=130.0, baseline=100.0, pct=0.20, label="latency")

    assert isinstance(result, ThresholdResult)
    assert result.breached is True
    assert result.direction == "high"
    assert result.current == 130.0
    assert result.baseline == 100.0
    assert result.pct_deviation == 0.3
    assert "spiked" in result.message


def test_simple_threshold_low_breach():
    result = simple_threshold(current=70.0, baseline=100.0, pct=0.20, label="throughput")

    assert result.breached is True
    assert result.direction == "low"
    assert result.pct_deviation == -0.3
    assert "dropped" in result.message


def test_simple_threshold_within_range():
    result = simple_threshold(current=105.0, baseline=100.0, pct=0.20, label="error_rate")

    assert result.breached is False
    assert result.direction is None
    assert "within normal range" in result.message


def test_simple_threshold_check_low_false_suppresses_drop():
    result = simple_threshold(current=70.0, baseline=100.0, pct=0.20, check_low=False, label="coverage")

    assert result.breached is False
    assert result.direction is None


@pytest.mark.parametrize("baseline,pct", [(0.0, 0.2), (100.0, 0.0), (100.0, 1.5)])
def test_simple_threshold_invalid_inputs_raise(baseline: float, pct: float):
    with pytest.raises(ValueError):
        simple_threshold(current=100.0, baseline=baseline, pct=pct, label="metric")


# ---------------------------------------------------------------------------
# anomaly_detection.check_multiple
# ---------------------------------------------------------------------------


def test_check_multiple_mixed_results():
    results = check_multiple(
        {
            "latency": (130.0, 100.0),
            "throughput": (70.0, 100.0),
            "cpu": (98.0, 100.0),
        },
        pct=0.20,
    )

    assert set(results) == {"latency", "throughput", "cpu"}
    assert results["latency"].direction == "high"
    assert results["throughput"].direction == "low"
    assert results["cpu"].breached is False


# ---------------------------------------------------------------------------
# otel_setup
# ---------------------------------------------------------------------------


def test_configure_otel_disabled_env_noops(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    with patch("observability.otel_setup.log") as log_mock:
        otel_setup.configure_otel(service_name="svc", otlp_endpoint="http://collector:4317", environment="test")

    log_mock.info.assert_called_once()
    assert otel_setup._tracer_provider is None


def test_configure_otel_missing_packages_noops(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError("missing opentelemetry")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with patch("observability.otel_setup.log") as log_mock:
            otel_setup.configure_otel(service_name="svc", otlp_endpoint="http://collector:4317", environment="test")

    log_mock.warning.assert_called_once()
    assert otel_setup._tracer_provider is None


def test_shutdown_otel_flushes_provider():
    provider = MagicMock()
    otel_setup._tracer_provider = provider

    otel_setup.shutdown_otel()

    provider.shutdown.assert_called_once()
    assert otel_setup._tracer_provider is None
