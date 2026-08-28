# Monitoring & Observability

> **Status:** Active | **Version:** 1.0.0 | **Last Updated:** 2026-05-23

This document describes the full observability stack for the ML Incident Response Platform: what is instrumented, where metrics flow, how dashboards are organized, and what alert thresholds are configured and why.

---

## Observability Stack Overview

The platform uses a three-pillar observability approach: **metrics** (Prometheus + Grafana), **structured logs** (JSON → stdout → Loki), and **distributed traces** (OpenTelemetry → Tempo). Each pillar is independently queryable but they share a unified correlation key: the `trace_id` emitted by the FastAPI middleware on every request and included in all log lines and spans.

The `docker-compose.yml` in the repo root starts the local backing and observability services: PostgreSQL, Redis, Prometheus, and Grafana. The FastAPI application runs separately with Uvicorn during local development. In production, each component maps to a managed equivalent (e.g., Grafana Cloud, Datadog, or an internal Prometheus federation).

---

## Metrics

### Scrape Configuration

Prometheus scrapes the `/metrics` endpoint on the FastAPI service every **30 seconds**. The endpoint is internal-only (blocked by the middleware rate limiter for external callers) and returns standard Prometheus text exposition format.

All metrics are prefixed `mlplatform_` to avoid collision in federated environments.

### Key Metrics and Thresholds

#### Incident Metrics

**`mlplatform_incidents_open{severity="p1"}`**  
Gauge. Any value > 0 for more than 5 minutes fires a P1 PagerDuty alert. P1 incidents should never remain unacknowledged.

**`mlplatform_incidents_open{severity="p2"}`**  
Gauge. Alert fires if > 3 concurrent P2 incidents are open, indicating systemic issues rather than isolated model failures.

**`mlplatform_time_to_acknowledge_seconds`**  
Histogram. Measured from `incidents.created_at` to the first `status_change` timeline event. The p95 target is ≤ 900 seconds (15 min) for P2 incidents.

#### Drift Metrics

**`mlplatform_drift_psi_composite{model_id, model_name}`**  
Gauge. Updated after every drift assessment run. Grafana alert fires at `> 0.15` (warning, P3 incident auto-created) and `> 0.25` (critical, P2 incident auto-created).

**`mlplatform_drift_detections_total{drift_detected="true"}`**  
Counter. Rate-of-change tracked over 24h windows. A sudden spike in drift detection rate across multiple models indicates a data pipeline issue upstream rather than model-specific degradation — this triggers a separate runbook (`runbooks/pipeline-data-quality.md`).

#### API Health Metrics

**`mlplatform_api_request_duration_seconds{endpoint="/incidents", method="POST"}`**  
Histogram. Alert fires if p99 latency exceeds 500ms for more than 2 minutes. Incident creation is on the critical path for the automated alerting pipeline.

**HTTP error rate:**  
Derived metric: `rate(mlplatform_api_request_duration_seconds_count{status_code=~"5.."}[5m]) / rate(mlplatform_api_request_duration_seconds_count[5m])`. Alert at > 1% error rate.

---

## Logging

### Format

All application logs are written as structured JSON to stdout. Each log line includes:

```json
{
  "timestamp": "2026-05-23T14:33:00.123Z",
  "level": "INFO",
  "logger": "mlplatform.incidents.router",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "message": "Incident created",
  "incident_id": "550e8400-e29b-41d4-a716-446655440000",
  "severity": "p2",
  "model_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "user": "mlops-pipeline-sa"
}
```

**PII policy:** User-submitted free-text fields (`description`, `body`) are never logged in their entirety. They are replaced with `[REDACTED:free_text]` in log lines. This mirrors the annotation QA PII handling pattern from the data operations background of this project's author.

### Log Levels

| Level | When used |
|---|---|
| `DEBUG` | Detailed internal state; disabled in production by default (`LOG_LEVEL=INFO`) |
| `INFO` | Normal operational events: requests, state transitions, drift assessments |
| `WARNING` | Recoverable conditions: retry attempts, deprecated endpoint calls, near-threshold PSI |
| `ERROR` | Unrecoverable errors that returned 5xx to the caller |
| `CRITICAL` | Service-level failures: database connection loss, Redis unavailable |

### Log Retention

Local dev: logs are not retained (ephemeral container). Staging: 14-day retention in Loki. Production: 90-day retention with cold storage archival to S3-compatible object store after 30 days.

---

## Alerting

### Alert Routing

Alerts flow from Prometheus Alertmanager → PagerDuty. Routing rules are based on the `severity` label on the firing alert:

| Alert severity | PagerDuty urgency | Notification channel |
|---|---|---|
| P1 | Critical (immediate phone call) | Primary on-call + secondary on-call + #ml-incidents Slack |
| P2 | High (push notification) | Primary on-call + #ml-incidents Slack |
| P3 | Low (email) | #ml-drift-alerts Slack |
| P4 | Info (weekly digest) | #ml-ops-digest Slack |

### Defined Alerts

| Alert name | Condition | Severity | Auto-creates incident? |
|---|---|---|---|
| `P1IncidentUnacknowledged` | `mlplatform_incidents_open{severity="p1"} > 0` for 5m | P1 | No (incident already exists) |
| `DriftWarning` | `mlplatform_drift_psi_composite > 0.15` for 10m | P3 | Yes |
| `DriftCritical` | `mlplatform_drift_psi_composite > 0.25` for 5m | P2 | Yes |
| `APIHighErrorRate` | Error rate > 1% for 2m | P2 | Yes |
| `APIHighLatency` | p99 POST /incidents > 500ms for 2m | P3 | Yes |
| `RedisDenylistUnreachable` | Redis health check fails for 1m | P1 | Yes |

---

## Dashboards

Grafana dashboards are stored as JSON in `dashboards/`. They are provisioned automatically on `docker compose up`.

| Dashboard | File | Purpose |
|---|---|---|
| ML Platform Overview | `dashboards/overview.json` | High-level KPIs: open incidents by severity, drift detection rate, API health |
| Drift Deep Dive | `dashboards/drift.json` | Per-model PSI trends, feature-level heatmap, assessment cadence |
| Incident Operations | `dashboards/incidents.json` | MTTA, MTTR, incident volume over time, severity distribution |
| API Performance | `dashboards/api.json` | Request rates, latency percentiles, error rates by endpoint |

---

## Health Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Returns `200 OK` when the service is running. Does not check dependencies. Used by load balancer. |
| `GET /health/ready` | Returns `200 OK` only when PostgreSQL and Redis are reachable. Used by Kubernetes readiness probe. |
| `GET /health/live` | Returns `200 OK` as long as the process is alive. Used by Kubernetes liveness probe. |
| `GET /metrics` | Prometheus text exposition. Internal-only. |

---

## Runbook: Monitoring System Self-Failure

If Prometheus itself becomes unavailable:

1. Check `docker compose ps` — ensure the `prometheus` container is running
2. Verify scrape target health at `http://localhost:9090/targets`
3. If Alertmanager is unreachable, manually page the on-call engineer via PagerDuty web UI
4. Escalate to `P1` if the monitoring outage exceeds 15 minutes — blind production operation is a P1 event by definition

For Redis failure specifically (impacts JWT denylist integrity): follow `runbooks/redis-failure.md`. A Redis outage means revoked tokens may be re-accepted until Redis recovers; the mitigtion is to temporarily reduce JWT TTL to 60 seconds via the `JWT_EXPIRE_SECONDS` env var hot-reload.
