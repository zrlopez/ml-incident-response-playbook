"""
observability/otel_setup.py — OpenTelemetry bootstrap
======================================================
Configures the OTel SDK with:
  - OTLP gRPC trace exporter (configurable endpoint)
  - Resource attributes: service.name, deployment.environment
  - BatchSpanProcessor for low-overhead production use
  - Graceful SDK shutdown via shutdown_otel()

FastAPI auto-instrumentation is wired via the OTel FastAPI instrumentor
so every incoming request gets a root span with http.method, http.route,
http.status_code and trace_id automatically.

Usage:
    # In lifespan startup:
    from observability.otel_setup import configure_otel, shutdown_otel
    configure_otel(service_name="ml-incident-api", otlp_endpoint="http://otel-collector:4317")
    # In lifespan shutdown:
    shutdown_otel()

Environment variables (override configure_otel() defaults):
    OTEL_SERVICE_NAME            defaults to "ml-incident-api"
    OTEL_EXPORTER_OTLP_ENDPOINT  defaults to "http://localhost:4317"
    OTEL_SDK_DISABLED            set to "true" to disable tracing (e.g. local dev)

Dependencies (add to requirements.txt):
    opentelemetry-sdk
    opentelemetry-exporter-otlp-proto-grpc
    opentelemetry-instrumentation-fastapi
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

log = logging.getLogger(__name__)

# Module-level singleton so shutdown_otel() can reach the tracer provider.
_tracer_provider = None


def configure_otel(
    service_name: str = "ml-incident-api",
    otlp_endpoint: str = "http://localhost:4317",
    environment: str = "development",
    app: "Optional[Any]" = None,
) -> None:
    """
    Bootstrap OpenTelemetry tracing.  No-ops gracefully if the OTel
    packages are absent or OTEL_SDK_DISABLED=true is set.

    Args:
        service_name: Value for the ``service.name`` resource attribute.
        otlp_endpoint: OTLP gRPC collector endpoint (e.g. Grafana Tempo,
                        Jaeger, or the OpenTelemetry Collector).
        environment: Value for ``deployment.environment`` attribute.
        app: Optional FastAPI application instance.  If provided, the
             FastAPI instrumentor is applied automatically.
    """
    global _tracer_provider

    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        log.info("otel.disabled (OTEL_SDK_DISABLED=true)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        log.warning(
            "otel.packages_missing — install opentelemetry-sdk and "
            "opentelemetry-exporter-otlp-proto-grpc to enable tracing"
        )
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
        }
    )

    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _tracer_provider = provider

    # FastAPI auto-instrumentation (attaches middleware, no code changes needed)
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)
            log.info("otel.fastapi_instrumented")
        except ImportError:
            log.warning(
                "otel.fastapi_instrumentor_missing — install "
                "opentelemetry-instrumentation-fastapi"
            )

    log.info(
        "otel.configured",
        extra={
            "service_name": service_name,
            "otlp_endpoint": otlp_endpoint,
            "environment": environment,
        },
    )


def shutdown_otel() -> None:
    """Flush and shut down the tracer provider.  Called in lifespan shutdown."""
    global _tracer_provider
    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
            log.info("otel.shutdown_complete")
        except Exception as exc:  # pragma: no cover
            log.warning("otel.shutdown_error", extra={"error": str(exc)})
        finally:
            _tracer_provider = None
