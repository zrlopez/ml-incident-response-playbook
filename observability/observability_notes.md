# Observability Stack Runbook

This document covers the operational configuration, local development setup,
production deployment, and incident diagnosis procedures for the
opentelemetry → collector → Jaeger + Prometheus observability stack used by
the ML Incident Response API.

Last reviewed: 2026-05-23

---

## Stack Overview

The platform uses three complementary observability signals:

| Signal | Tool | Purpose |
|---|---|---|
| Distributed traces | OpenTelemetry SDK → OTLP → Jaeger | Request path, latency breakdown, cross-service correlation |
| Metrics | prometheus-fastapi-instrumentator → Prometheus | Throughput, error rate, latency SLOs, feature drift |
| Structured logs | structlog → stdout (JSON) → Loki or CloudWatch | Audit trail, error context, PII-scrubbed events |

All three signals share a common `trace_id` field injected by the
`trace_and_security_headers` middleware in `api/app.py`. This allows
correlation of a single request across Jaeger traces, Prometheus exemplars,
and log lines.

---

## Local Development Setup

The `docker-compose.yml` at the repo root starts the full observability stack
locally. No cloud account required.

```bash
# Start the full stack: API + Redis + Jaeger + Prometheus
docker compose up --build

# Verify the API is ready
curl http://localhost:8000/ready

# Open Jaeger UI
open http://localhost:16686

# Open Prometheus UI
open http://localhost:9090

# Query current API error rate
curl -g 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=rate(http_requests_total{status=~"5.."}[5m])'
```

To disable tracing in local dev without stopping Jaeger:
```bash
export OTEL_SDK_DISABLED=true
uvicorn api.app:app --reload
```

---

## OTel Configuration Reference

All OTel parameters are consumed in `observability/otel_setup.py`.

| Environment Variable | Default | Description |
|---|---|---|
| `OTEL_SERVICE_NAME` | `ml-incident-api` | Service name in Jaeger / trace backend |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector endpoint |
| `OTEL_SDK_DISABLED` | `false` | Set `true` to disable tracing entirely |
| `ENVIRONMENT` | `development` | Populates `deployment.environment` resource attribute |

In production, point `OTEL_EXPORTER_OTLP_ENDPOINT` at your OTel Collector
sidecar or centralized collector. The collector handles fan-out to Jaeger,
Tempo, or any OTLP-compatible backend without changing the application.

---

## Production Deployment

### Kubernetes (recommended)

Deploy the OTel Collector as a DaemonSet sidecar. The API pod exports traces
to `localhost:4317` (the DaemonSet NodePort). This avoids a direct dependency
on a centralized collector pod in the hot path.

```yaml
# Relevant API container env vars in your Helm values.yaml
env:
  - name: OTEL_SERVICE_NAME
    value: ml-incident-api
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: http://$(NODE_IP):4317   # NODE_IP injected via Downward API
  - name: ENVIRONMENT
    value: production
```

### Prometheus scrape

See `monitoring/metrics.md` for the full scrape configuration. The
`/metrics` endpoint is available on port 8000 with no authentication.
Restrict scrape access at the network level (namespace NetworkPolicy or
VPC security group) rather than adding auth to the metrics endpoint.

---

## Trace Correlation

Every HTTP response carries an `X-Trace-Id` header (UUID4, set by the
`trace_and_security_headers` middleware). This ID is also bound to the
structlog context for the duration of the request, so every log line
emitted during that request carries the same `trace_id` field.

To correlate a user-reported issue:
1. Ask the user for the `X-Trace-Id` header from their failed request.
2. Search Jaeger: `http://jaeger:16686/trace/<trace_id>`
3. Search Loki / CloudWatch: `{service="ml-incident-api"} | json | trace_id="<id>"`
4. Check Prometheus for the time window: use the trace timestamp ±30s.

---

## Common Failure Modes

### Traces not appearing in Jaeger

1. Confirm `OTEL_SDK_DISABLED` is not set to `true`.
2. Check that the OTLP endpoint is reachable from the API pod:
   ```bash
   kubectl exec -it <api-pod> -- curl -v http://otel-collector:4317
   ```
3. Review API startup logs for `otel.configured` event. If absent, the
   OTel packages are not installed or `configure_otel()` failed silently.
4. Verify the OTel Collector config has a `traces` pipeline with an
   `otlp` receiver and a `jaeger` or `otlphttp` exporter.

### Prometheus scrape returning 404

1. Confirm `prometheus-fastapi-instrumentator` is installed:
   ```bash
   pip show prometheus-fastapi-instrumentator
   ```
2. The instrumentator mounts `/metrics` automatically on app startup.
   If the endpoint is missing, the instrumentator likely failed to
   instrument the app. Check startup logs for import errors.
3. Verify the scrape target in `prometheus.yml` points to port 8000,
   not the Uvicorn debug port (8001).

### `RedisDenylistUnavailable` alert firing

This alert means token revocation is non-functional. Users who have
explicitly logged out may remain able to authenticate until their token
naturally expires (default: 30 minutes for access tokens).

Immediate actions:
1. Check Redis pod status: `kubectl get pod -l app=redis`
2. If Redis is down, investigate OOM kill (check resource limits) or
   persistent volume failure.
3. Do NOT restart the API while Redis is down — the in-process denylist
   state is lost on restart. Wait for Redis to recover.
4. If Redis cannot recover within 15 minutes, rotate all JWT secrets
   (`JWT_SECRET_KEY` env var) and rolling-restart the API. This
   invalidates all existing tokens, forcing re-authentication.

### Structlog producing unformatted output

The logging configuration in `observability/logging_config.py` emits
JSON in production and a human-readable format in development. If you
see raw Python log output instead of structured JSON:
1. Confirm `configure_logging()` is called before any `structlog.get_logger()`
   calls at module level.
2. Check `ENVIRONMENT` is set to `production` in the deployment.
3. Verify `structlog` is installed and the version matches `requirements.txt`.

---

## Alert Acknowledgement

When an alert fires from `monitoring/alert_rules.yml`, the on-call operator
should:

1. Open the alert in Alertmanager.
2. Follow the `runbook_url` annotation — it maps to a file in `runbooks/`.
3. Create an incident via `POST /incidents` with the alert name as the title
   and `SEV-2` (degraded performance) or `SEV-1` (full outage) as severity.
4. Update the incident status as the investigation progresses.
5. Close the incident with resolution notes when the alert resolves.

This closes the loop between the monitoring stack and the incident tracking
API — every alert that fires becomes an auditable incident record.
