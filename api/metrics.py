# =============================================================================
# api/metrics.py — Prometheus metrics registry
# =============================================================================
# Defines all application-level Prometheus metrics for the ML Incident
# Response API. A single module-level registry prevents duplicate-collector
# errors across hot-reload cycles in development.
#
# Metrics exported:
#   ml_incident_total            Counter   — incidents created (severity, category)
#   ml_inference_duration_seconds Histogram — model inference latency
#   ml_active_incidents          Gauge     — open incidents by severity
#   ml_drift_score_latest        Gauge     — most recent PSI drift score per feature
#
# Usage:
#   from api.metrics import incident_total, inference_latency, active_incidents, drift_score
#
# Registration:
#   Router is included in api/app.py via app.include_router(metrics_router)
# =============================================================================
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["observability"])

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
incident_total: Counter = Counter(
    "ml_incident_total",
    "Total number of incidents created, partitioned by severity and category.",
    labelnames=["severity", "category"],
)

auth_failure_total: Counter = Counter(
    "ml_auth_failure_total",
    "Total JWT authentication failures (maps to JWTAuthFailureSpike alert).",
    labelnames=["reason"],
)

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------
inference_latency: Histogram = Histogram(
    "ml_inference_duration_seconds",
    "End-to-end model inference latency in seconds.",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------
active_incidents: Gauge = Gauge(
    "ml_active_incidents",
    "Currently open (non-resolved) incidents, partitioned by severity.",
    labelnames=["severity"],
)

drift_score: Gauge = Gauge(
    "ml_drift_score_latest",
    "Most recent Population Stability Index (PSI) drift score per feature.",
    labelnames=["feature_name"],
)


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------
@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    """Prometheus scrape target — returns all registered metrics in text/plain format.

    This endpoint is intentionally excluded from the OpenAPI schema
    (include_in_schema=False) to keep the public API docs clean.

    Scrape config: see observability/prometheus.yml
    Grafana dashboards: see dashboards/ml_operations_overview.json
    """
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
