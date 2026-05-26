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

import logging
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest


# ════════════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_tracer_provider() -> Any:
    """
    Reset the module-level _tracer_provider singleton before and after
    each test to prevent state bleed between tests.
    """
    import observability.otel_setup as otel
    otel._tracer_provider = None
    yield
    otel._tracer_provider = None


def _make_otel_mocks() -> dict[str, MagicMock]:
    """
    Build a minimal mock surface for the opentelemetry SDK.
    Returns a dict of named mocks keyed by the attribute they replace.
    """
    mock_provider = MagicMock()
    mock_exporter = MagicMock()
    mock_processor = MagicMock()
    mock_resource = MagicMock()
    mock_trace_module = MagicMock()

    mock_tracer_provider_cls = MagicMock(return_value=mock_provider)
    mock_exporter_cls = MagicMock(return_value=mock_exporter)
    mock_processor_cls = MagicMock(return_value=mock_processor)
    mock_resource_cls = MagicMock()
    mock_resource_cls.create = MagicMock(return_value=mock_resource)

    return {
        "trace": mock_trace_module,
        "Resource": mock_resource_cls,
        "TracerProvider": mock_tracer_provider_cls,
        "BatchSpanProcessor": mock_processor_cls,
        "OTLPSpanExporter": mock_exporter_cls,
        "provider": mock_provider,
        "exporter": mock_exporter,
        "processor": mock_processor,
    }


def _patch_otel_imports(mocks: dict[str, MagicMock]) -> list[Any]:
    """
    Return a list of patch() context managers that replace the OTel SDK
    with the provided mocks inside configure_otel's import block.
    """
    return [
        patch("opentelemetry.trace", mocks["trace"]),
        patch("opentelemetry.sdk.resources.Resource", mocks["Resource"]),
        patch("opentelemetry.sdk.trace.TracerProvider", mocks["TracerProvider"]),
        patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", mocks["BatchSpanProcessor"]),
        patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter",
            mocks["OTLPSpanExporter"],
        ),
    ]


# ════════════════════════════════════════════════════════════════════════════
# configure_otel()
# ════════════════════════════════════════════════════════════════════════════


class TestConfigureOtel:
    """Tests for configure_otel()."""

    def test_sdk_disabled_env_var_returns_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OTEL_SDK_DISABLED=true must skip all SDK setup."""
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
        import observability.otel_setup as otel
        otel.configure_otel()
        assert otel._tracer_provider is None

    def test_sdk_disabled_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_SDK_DISABLED", "TRUE")
        import observability.otel_setup as otel
        otel.configure_otel()
        assert otel._tracer_provider is None

    def test_import_error_returns_early_without_raising(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If OTel packages are absent, configure_otel() must not raise."""
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        with patch(
            "builtins.__import__",
            side_effect=lambda name, *a, **kw: (_ for _ in ()).throw(
                ImportError("no module named opentelemetry")
            ) if "opentelemetry" in name else __import__(name, *a, **kw),
        ):
            from observability import otel_setup
            # Re-import with patched __import__ simulation via direct approach
        # Use the simpler approach: patch the internal import block
        import observability.otel_setup as otel
        otel._tracer_provider = None

        # Patch the try-block imports to raise ImportError
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": None,
                "opentelemetry.trace": None,
                "opentelemetry.sdk": None,
                "opentelemetry.sdk.resources": None,
                "opentelemetry.sdk.trace": None,
                "opentelemetry.sdk.trace.export": None,
                "opentelemetry.exporter": None,
                "opentelemetry.exporter.otlp": None,
                "opentelemetry.exporter.otlp.proto": None,
                "opentelemetry.exporter.otlp.proto.grpc": None,
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": None,
            },
        ):
            import importlib
            importlib.reload(otel)
            otel.configure_otel()
        assert otel._tracer_provider is None

    def test_happy_path_sets_tracer_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Full happy path: provider is set and trace.set_tracer_provider called."""
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        mocks = _make_otel_mocks()

        import observability.otel_setup as otel

        with patch.object(otel, "configure_otel", wraps=otel.configure_otel):
            # Directly monkey-patch the module to bypass the internal try/import
            otel._tracer_provider = None

            mock_provider_instance = MagicMock()
            mock_provider_cls = MagicMock(return_value=mock_provider_instance)
            mock_trace = MagicMock()
            mock_resource = MagicMock()
            mock_resource_cls = MagicMock()
            mock_resource_cls.create.return_value = mock_resource
            mock_exporter = MagicMock()
            mock_exporter_cls = MagicMock(return_value=mock_exporter)
            mock_processor = MagicMock()
            mock_processor_cls = MagicMock(return_value=mock_processor)

            sdk_modules = {
                "opentelemetry.trace": mock_trace,
                "opentelemetry": MagicMock(trace=mock_trace),
            }

            with patch("opentelemetry.trace", mock_trace), \
                 patch("opentelemetry.sdk.resources.Resource", mock_resource_cls), \
                 patch("opentelemetry.sdk.trace.TracerProvider", mock_provider_cls), \
                 patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", mock_processor_cls), \
                 patch(
                     "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter",
                     mock_exporter_cls,
                 ):
                otel.configure_otel(
                    service_name="test-service",
                    otlp_endpoint="http://collector:4317",
                    environment="staging",
                )

            # Provider should be registered
            assert otel._tracer_provider is mock_provider_instance
            # Processor was added to provider
            mock_provider_instance.add_span_processor.assert_called_once()

    def test_resource_attributes_include_service_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        import observability.otel_setup as otel
        captured_attrs: list[dict[str, str]] = []

        mock_provider_instance = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider_instance)
        mock_trace = MagicMock()
        mock_resource_cls = MagicMock()

        def capture_resource(attrs: dict[str, str]) -> MagicMock:
            captured_attrs.append(attrs)
            return MagicMock()

        mock_resource_cls.create = capture_resource

        with patch("opentelemetry.trace", mock_trace), \
             patch("opentelemetry.sdk.resources.Resource", mock_resource_cls), \
             patch("opentelemetry.sdk.trace.TracerProvider", mock_provider_cls), \
             patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", MagicMock()), \
             patch(
                 "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter",
                 MagicMock(),
             ):
            otel.configure_otel(service_name="my-service", environment="prod")

        assert len(captured_attrs) == 1
        assert captured_attrs[0]["service.name"] == "my-service"
        assert captured_attrs[0]["deployment.environment"] == "prod"

    def test_fastapi_instrumentation_when_app_provided(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        import observability.otel_setup as otel

        mock_app = MagicMock()
        mock_instrumentor_cls = MagicMock()
        mock_instrumentor_instance = MagicMock()
        mock_instrumentor_cls.return_value = mock_instrumentor_cls
        mock_instrumentor_cls.instrument_app = MagicMock()

        mock_provider_instance = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider_instance)

        with patch("opentelemetry.trace", MagicMock()), \
             patch("opentelemetry.sdk.resources.Resource", MagicMock()), \
             patch("opentelemetry.sdk.trace.TracerProvider", mock_provider_cls), \
             patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", MagicMock()), \
             patch(
                 "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter",
                 MagicMock(),
             ), \
             patch(
                 "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor",
                 mock_instrumentor_cls,
             ):
            otel.configure_otel(app=mock_app)

        mock_instrumentor_cls.instrument_app.assert_called_once_with(mock_app)

    def test_fastapi_instrumentor_import_error_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing FastAPI instrumentor should log warning and continue."""
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        import observability.otel_setup as otel

        mock_app = MagicMock()
        mock_provider_instance = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider_instance)

        def raise_on_fastapi(name: str, *args: Any, **kwargs: Any) -> Any:
            if "fastapi" in name.lower():
                raise ImportError("no module named opentelemetry.instrumentation.fastapi")
            return MagicMock()

        with patch("opentelemetry.trace", MagicMock()), \
             patch("opentelemetry.sdk.resources.Resource", MagicMock()), \
             patch("opentelemetry.sdk.trace.TracerProvider", mock_provider_cls), \
             patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", MagicMock()), \
             patch(
                 "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter",
                 MagicMock(),
             ), \
             patch(
                 "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor",
                 side_effect=ImportError("missing"),
             ):
            # Should not raise even if FastAPI instrumentor is unavailable
            otel.configure_otel(app=mock_app)

    def test_no_app_skips_fastapi_instrumentation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        import observability.otel_setup as otel

        mock_instrumentor = MagicMock()
        mock_provider_instance = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider_instance)

        with patch("opentelemetry.trace", MagicMock()), \
             patch("opentelemetry.sdk.resources.Resource", MagicMock()), \
             patch("opentelemetry.sdk.trace.TracerProvider", mock_provider_cls), \
             patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", MagicMock()), \
             patch(
                 "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter",
                 MagicMock(),
             ), \
             patch(
                 "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor",
                 mock_instrumentor,
             ):
            otel.configure_otel(app=None)

        mock_instrumentor.instrument_app.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# shutdown_otel()
# ════════════════════════════════════════════════════════════════════════════


class TestShutdownOtel:
    """Tests for shutdown_otel()."""

    def test_shutdown_calls_provider_shutdown(self) -> None:
        import observability.otel_setup as otel
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()
        mock_provider.shutdown.assert_called_once()

    def test_shutdown_resets_singleton_to_none(self) -> None:
        import observability.otel_setup as otel
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()
        assert otel._tracer_provider is None

    def test_shutdown_noop_when_provider_is_none(self) -> None:
        import observability.otel_setup as otel
        otel._tracer_provider = None
        otel.shutdown_otel()  # must not raise

    def test_shutdown_exception_does_not_propagate(self) -> None:
        """SDK raises during shutdown — must be caught, not re-raised."""
        import observability.otel_setup as otel
        mock_provider = MagicMock()
        mock_provider.shutdown.side_effect = RuntimeError("collector unreachable")
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()  # must not raise
        assert otel._tracer_provider is None  # finally block still resets it

    def test_shutdown_logs_completion(self, caplog: pytest.LogCaptureFixture) -> None:
        import observability.otel_setup as otel
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        with caplog.at_level(logging.INFO, logger="observability.otel_setup"):
            otel.shutdown_otel()
        assert "otel.shutdown_complete" in caplog.text

    def test_multiple_shutdown_calls_are_safe(self) -> None:
        """Second call after first shutdown must be a no-op, not a crash."""
        import observability.otel_setup as otel
        mock_provider = MagicMock()
        otel._tracer_provider = mock_provider
        otel.shutdown_otel()
        otel.shutdown_otel()  # second call: provider is already None
        assert otel._tracer_provider is None
