# Metric Catalog

This catalog documents every metric emitted or consumed by the ML Incident
Response platform. Each entry includes the metric name as it appears in
Prometheus, its type, labels, unit, source, alert rule cross-reference, and
guidance on normal operating ranges.

Last reviewed: 2026-05-23  
Prometheus scrape interval: 60s (alert_rules.yml groups: 60s, security group: 30s)

---

## HTTP / API Metrics

Emitted by `prometheus-fastapi-instrumentator` (mounted at `/metrics`).
All metrics carry an `env` label set from the `ENVIRONMENT` env var.

### `http_requests_total`
| Field | Value |
|---|---|
| Type | Counter |
| Labels | `method`, `handler`, `status`, `env` |
| Unit | requests |
| Source | prometheus-fastapi-instrumentator |
| Alert | `APIHighErrorRate` (status=~`5..` rate > 5% for 2m) |

Cumulative count of HTTP requests handled. Use `rate()` over a 5-minute
window for current throughput. The `APIHighErrorRate` alert fires when the
5xx rate exceeds 5% of total traffic for two consecutive minutes.

**Normal range:** 5xx rate < 1% in production. P99 latency < 2s.

---

### `http_request_duration_seconds`
| Field | Value |
|---|---|
| Type | Histogram |
| Labels | `method`, `handler`, `status`, `env` |
| Unit | seconds |
| Buckets | 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0 |
| Source | prometheus-fastapi-instrumentator |
| Alert | `APILatencyP99Breach` (P99 > 2.0s for 5m) |

Request duration histogram. Use `histogram_quantile(0.99, rate(...[5m]))`
for P99. The `APILatencyP99Breach` alert triggers after five minutes of
sustained P99 > 2 seconds, giving time for transient spikes to resolve.

**Normal range:** P50 < 100ms, P95 < 500ms, P99 < 2s for authenticated endpoints.

---

## ML Model Metrics

Custom gauges pushed from model evaluation pipelines (Airflow tasks or
dedicated scoring jobs). These are **not** emitted by the API container.
They must be pushed to the Prometheus Pushgateway or a remote-write-capable
time-series store.

### `ml_model_accuracy_p95`
| Field | Value |
|---|---|
| Type | Gauge |
| Labels | `model_name`, `model_version`, `env` |
| Unit | ratio (0.0–1.0) |
| Source | model evaluation pipeline |
| Alert | `ModelAccuracyDegradation` (value < 0.92 for 5m) |

P95 accuracy across the evaluation window. Published after each
scheduled evaluation run. A value of 0.95 means 95% of predictions
in the evaluation set were correct at the P95 percentile.

**Normal range:** ≥ 0.92 (SLO ML-SLO-003). Values below 0.85 indicate
potential model failure and should trigger immediate incident creation.

---

### `ml_feature_drift_ratio`
| Field | Value |
|---|---|
| Type | Gauge |
| Labels | `feature_name`, `env` |
| Unit | ratio (dimensionless) |
| Source | `monitoring/drift_check.py` → `scan_features()` |
| Alert | `FeatureDriftBreach` (value > 0.20 for 10m) |

Relative deviation of the current-window feature mean from the training
baseline. Computed by `scan_features()` and exported via `prometheus_client`
Gauge. A value of 0.25 means the current mean is 25% away from the baseline.

**Normal range:** < 0.10 (stable). 0.10–0.20 warrants investigation.
> 0.20 triggers the `FeatureDriftBreach` alert after 10 sustained minutes.

---

## Pipeline Metrics

### `ml_pipeline_last_success_timestamp`
| Field | Value |
|---|---|
| Type | Gauge |
| Labels | `pipeline_name`, `env` |
| Unit | Unix timestamp (seconds) |
| Source | Airflow DAG completion hook |
| Alert | `PipelineSLABreach` (time() - value > 7200 for 0m — instant) |

Epoch timestamp of the last successful pipeline run. The `PipelineSLABreach`
alert fires immediately (for: 0m) when any pipeline has not completed
successfully in over two hours. Set this metric at DAG completion via the
Prometheus Pushgateway or Airflow’s StatsD exporter.

**Normal range:** `time() - ml_pipeline_last_success_timestamp` < 7200s.

---

### `ml_incident_created_total`
| Field | Value |
|---|---|
| Type | Counter |
| Labels | `severity`, `category`, `env` |
| Unit | incidents |
| Source | `api/app.py` → `POST /incidents` handler |
| Alert | `IncidentVolumeSpike` (1h rate > 1.5x 24h baseline for 5m) |

Cumulative count of incidents created via the API. Increment this counter
in the `create_incident` route. The `IncidentVolumeSpike` alert detects
when the current hour’s creation rate exceeds 1.5x the 24-hour rolling
baseline, which indicates systemic platform degradation rather than
normal incident activity.

---

## LLM Cost Metrics

### `llm_tokens_consumed_total`
| Field | Value |
|---|---|
| Type | Counter |
| Labels | `model_id`, `request_type`, `env` |
| Unit | tokens |
| Source | LLM inference wrapper / gateway |
| Alert | `LLMCostSpike` (1h rate > 2x 24h baseline for 10m) |

Cumulative LLM token consumption. Increment at inference time from the
LLM gateway layer. The `LLMCostSpike` alert catches runaway retry loops
or prompt-injection attacks that inflate token usage before they appear
on the billing statement.

**Normal range:** track weekly and set budget alerts at 1.5x weekly baseline.

---

## Security Metrics

### `ml_revoked_token_access_total`
| Field | Value |
|---|---|
| Type | Counter |
| Labels | `env` |
| Unit | attempts |
| Source | `api/app.py` → `is_token_revoked()` |
| Alert | `RevokedTokenAccessAttempts` (increase > 10 in 10m, instant) |

Count of requests that presented a JWT found in the Redis denylist.
This counter should be near zero in normal operation. More than 10
attempts in 10 minutes is a signal of credential theft or replay attack.
Add `ml_revoked_token_access_total.inc()` in the `is_token_revoked()` path
in `api/app.py` to activate this metric.

---

## Prometheus Scrape Configuration

```yaml
# prometheus.yml scrape_configs excerpt
scrape_configs:
  - job_name: ml-incident-api
    static_configs:
      - targets: ["ml-incident-api:8000"]
    metrics_path: /metrics
    scrape_interval: 15s

  - job_name: redis
    static_configs:
      - targets: ["redis:9121"]  # redis_exporter sidecar
    scrape_interval: 30s
```

The `/metrics` endpoint is mounted automatically by
`prometheus-fastapi-instrumentator` when the app starts. No additional
route registration is required.
