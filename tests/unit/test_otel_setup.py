"""
tests/unit/test_otel_setup.py

Unit tests for observability/otel_setup.py.

Coverage targets (39 stmts across 126 lines):
  - configure_otel(): OTEL_SDK_DISABLED early return; ImportError graceful
    degradation; full happy path with mocked SDK; resource attributes;
    BatchSpanProcessor wired; FastAPI instrumentor happy + ImportError path.
  - shutdown_otel(): flushes when provider exists; no-ops when provider is None;
    exception during shutdown is logged without re-raising.
  - Module-level singleton (_tracer_provider) is reset on each shutdown.

All OpenTelemetry SDK imports are mocked — the SDK is not required at test time.
This matches production CI environments that may not install the full OTel stack.
"""
from __future__ import annotations

import importlib
import logging
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_tracer_provider() -> Any:
    import observability.otel_setup as otel
    otel._tracer_provider = None
    yield
    otel._tracer_provider = None


def _reload_otel_setup() -> ModuleType:
    import observability.otel_setup as otel
    return importlib.reload(otel)


def _install_fake_otel_modules(raise_fastapi_import_error: bool = False) -> dict[str, Any]:
    trace_module = ModuleType("opentelemetry.trace")
    trace_module.set_tracer_provider = MagicMock()  # type: ignore[attr-defined]

    resource_module = ModuleType("opentelemetry.sdk.resources")
    resource_module.Resource = MagicMock()  # type: ignore[attr-defined]
    resource_module.Resource.create = MagicMock(return_value=MagicMock(name="resource"))  # type: ignore[attr-defined]  # noqa: E501

    trace_export_module = ModuleType("opentelemetry.sdk.trace.export")
    trace_export_module.BatchSpanProcessor = MagicMock(return_value=MagicMock(name="processor"))  # type: ignore[attr-defined]  # noqa: E501

    sdk_trace_module = ModuleType("opentelemetry.sdk.trace")
    sdk_trace_module.TracerProvider = MagicMock(return_value=MagicMock(name="provider"))  # type: ignore[attr-defined]  # noqa: E501

    exporter_module = ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    exporter_module.OTLPSpanExporter = MagicMock(return_value=MagicMock(name="exporter"))  # type: ignore[attr-defined]  # noqa: E501

    fastapi_module = ModuleType("opentelemetry.instrumentation.fastapi")
    fastapi_instrumentor = MagicMock()
    fastapi_instrumentor.instrument_app = MagicMock()
    if raise_fastapi_import_error:
        fastapi_instrumentor.side_effect = ImportError("missing fastapi instrumentor")
    fastapi_module.FastAPIInstrumentor = fastapi_instrumentor  # type: ignore[attr-defined]

    modules: dict[str, Any] = {
        "opentelemetry": ModuleType("opentelemetry"),
        "opentelemetry.trace": trace_module,
        "opentelemetry.sdk": ModuleType("opentelemetry.sdk"),
        "opentelemetry.sdk.resources": resource_module,
        "opentelemetry.sdk.trace": sdk_trace_module,
        "opentelemetry.sdk.trace.export": trace_export_module,
        "opentelemetry.exporter": ModuleType("opentelemetry.exporter"),
        "opentelemetry.exporter.otlp": ModuleType("opentelemetry.exporter.otlp"),
        "opentelemetry.exporter.otlp.proto": ModuleType("opentelemetry.exporter.otlp.proto"),
        "opentelemetry.exporter.otlp.proto.grpc": ModuleType("opentelemetry.exporter.otlp.proto.grpc"),  # noqa: E501
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": exporter_module,
        "opentelemetry.instrumentation": ModuleType("opentelemetry.instrumentation"),
        "opentelemetry.instrumentation.fastapi": fastapi_module,
    }
    return {
        "modules": modules,
        "trace_module": trace_module,
        "resource_module": resource_module,
        "sdk_trace_module": sdk_trace_module,
        "trace_export_module": trace_export_module,
        "exporter_module": exporter_module,
        "fastapi_instrumentor": fastapi_instrumentor,
    }


class TestConfigureOtel:
    def test_sdk_disabled_env_var_returns_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
        otel = _reload_otel_setup()
        otel.configure_otel()
        assert otel._tracer_provider is None

    def test_sdk_disabled_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_SDK_DISABLED", "TRUE")
        otel = _reload_otel_setup()
        otel.configure_otel()
        assert otel._tracer_provider is None

    def test_import_error_returns_early_without_raising(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: E501
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        with patch.dict(sys.modules, {"opentelemetry": None}):
            otel = _reload_otel_setup()
            otel.configure_otel()
            assert otel._tracer_provider is None

    def test_happy_path_sets_tracer_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        fake = _install_fake_otel_modules()
        with patch.dict(sys.modules, fake["modules"], clear=False):
            otel = _reload_otel_setup()
            otel.configure_otel(
                service_name="test-service",
                otlp_endpoint="http://collector:4317",
                environment="staging",
            )
            assert otel._tracer_provider is not None
            fake["trace_module"].set_tracer_provider.assert_called_once()
            fake["resource_module"].Resource.create.assert_called_once()
            fake["sdk_trace_module"].TracerProvider.assert_called_once()
            fake["trace_export_module"].BatchSpanProcessor.assert_called_once()
            fake["exporter_module"].OTLPSpanExporter.assert_called_once()

    def test_resource_attributes_include_service_name(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: E501
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        fake = _install_fake_otel_modules()
        captured: dict[str, Any] = {}

        def capture(attrs: dict[str, Any]) -> MagicMock:
            captured.update(attrs)
            return MagicMock(name="resource")

        fake["resource_module"].Resource.create.side_effect = capture
        with patch.dict(sys.modules, fake["modules"], clear=False):
            otel = _reload_otel_setup()
            otel.configure_otel(service_name="my-service", environment="prod")
        assert captured["service.name"] == "my-service"
        assert captured["deployment.environment"] == "prod"

    def test_fastapi_instrumentation_when_app_provided(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: E501
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        fake = _install_fake_otel_modules()
        mock_app = MagicMock()
        with patch.dict(sys.modules, fake["modules"], clear=False):
            otel = _reload_otel_setup()
            otel.configure_otel(app=mock_app)
        fake["fastapi_instrumentor"].instrument_app.assert_called_once_with(mock_app)

    def test_fastapi_instrumentor_import_error_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: E501
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        fake = _install_fake_otel_modules(raise_fastapi_import_error=True)
        mock_app = MagicMock()
        with patch.dict(sys.modules, fake["modules"], clear=False):
            otel = _reload_otel_setup()
            otel.configure_otel(app=mock_app)
        assert otel._tracer_provider is not None

    def test_no_app_skips_fastapi_instrumentation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        fake = _install_fake_otel_modules()
        with patch.dict(sys.modules, fake["modules"], clear=False):
            otel = _reload_otel_setup()
            otel.configure_otel(app=None)
        fake["fastapi_instrumentor"].instrument_app.assert_not_called()


class TestShutdownOtel:
    def test_shutdown_calls_provider_shutdown(self) -> None:
        otel = _reload_otel_setup()
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()
        mock_provider.shutdown.assert_called_once()

    def test_shutdown_resets_singleton_to_none(self) -> None:
        otel = _reload_otel_setup()
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()
        assert otel._tracer_provider is None

    def test_shutdown_noop_when_provider_is_none(self) -> None:
        otel = _reload_otel_setup()
        otel._tracer_provider = None
        otel.shutdown_otel()

    def test_shutdown_exception_does_not_propagate(self) -> None:
        otel = _reload_otel_setup()
        mock_provider = MagicMock()
        mock_provider.shutdown.side_effect = RuntimeError("collector unreachable")
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()
        assert otel._tracer_provider is None

    def test_shutdown_logs_completion(self, caplog: pytest.LogCaptureFixture) -> None:
        otel = _reload_otel_setup()
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        with caplog.at_level(logging.INFO, logger="observability.otel_setup"):
            otel.shutdown_otel()
        assert "otel.shutdown_complete" in caplog.text

    def test_multiple_shutdown_calls_are_safe(self) -> None:
        otel = _reload_otel_setup()
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()
        otel.shutdown_otel()
        assert otel._tracer_provider is None
