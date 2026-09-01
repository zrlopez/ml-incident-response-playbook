# Metrics Reference

All Prometheus metrics are exposed at the `/metrics` endpoint by
`prometheus-fastapi-instrumentator` plus custom gauges and counters registered
in `observability/drift_check.py` and `observability/anomaly_detection.py`.
Alert thresholds are defined in `observability/alert_rules.yml` and are
loadable with `promtool check rules observability/alert_rules.yml`.

---

## HTTP Metrics

Emitted automatically by `prometheus-fastapi-instrumentator` for every
request handled by `api/app.py`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `http_requests_total` | Counter | `method`, `route`, `status` | Total HTTP requests by method, route, and status code |
| `http_request_duration_seconds` | Histogram | `method`, `route` | Request latency; used for P95/P99 SLO evaluation |

### Alert thresholds

| Alert | Threshold | Severity | Window |
|---|---|---|---|
| `APIHighP99Latency` | P99 > 2.0s | warning | `for: 5m` |
| `APILatencyCritical` | P99 > 5.0s | critical | `for: 2m` |
| `APIHighErrorRate` | 5xx rate > 5% | warning | `for: 5m` |
| `APICriticalErrorRate` | 5xx rate > 20% | critical | `for: 2m` |
| `JWTAuthFailureSpike` | 401 rate > 1/s | warning | `for: 3m` |
| `JWTAuthFailureCritical` | 401 rate > 10/s | critical | `for: 1m` |

---

## ML Model Metrics

Registered in `observability/drift_check.py` and exported via the shared
Prometheus registry.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `ml_psi_score` | Gauge | `model`, `feature` | Population Stability Index for a given model/feature pair. PSI < 0.10 = stable; 0.10–0.20 = minor drift; ≥ 0.20 = major drift |
| `ml_feature_drift_ratio` | Gauge | `model`, `feature` | Relative mean deviation (drift ratio) for scalar features |
| `ml_drift_events_total` | Counter | `model`, `severity` | Cumulative drift events by severity (`minor_drift`, `major_drift`) |
| `ml_anomaly_threshold_breaches_total` | Counter | `model` | Cumulative anomaly score threshold breaches (score < 0.0) |
| `ml_prediction_latency_seconds` | Histogram | `model` | Wall-clock time for `model.decision_function()` calls |

### Alert thresholds

| Alert | Threshold | Severity | Window |
|---|---|---|---|
| `ModelMinorDrift` | PSI > 0.10 | warning | `for: 10m` |
| `ModelMajorDrift` | PSI ≥ 0.20 | critical | `for: 5m` |
| `ModelDriftEventSpike` | > 3 major events in 30m | critical | `for: 0m` |
| `AnomalyBreachRateHigh` | > 0.5 breaches/s (10m avg) | warning | `for: 5m` |
| `PredictionVolumeSilence` | 0 predictions in 15m | critical | `for: 10m` |
| `PredictionLatencyDegraded` | p95 > 1.0s | warning | `for: 5m` |

---

## Infrastructure Metrics

Emitted by kube-state-metrics and the Redis exporter where applicable.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `alembic_migration_head_lag` | Gauge | `job` | Number of unapplied Alembic migrations on the running DB head |
| `redis_memory_used_bytes` | Gauge | `job` | Redis heap used (bytes); denylist and rate-limit data |
| `redis_memory_max_bytes` | Gauge | `job` | Redis `maxmemory` ceiling (bytes) |
| `kube_pod_container_status_restarts_total` | Counter | `namespace`, `pod`, `container` | Kubernetes pod restart counter |

### Alert thresholds

| Alert | Threshold | Severity | Window |
|---|---|---|---|
| `AlembicMigrationLag` | head_lag > 0 | warning | `for: 5m` |
| `RedisHighMemoryUsage` | mem_used / mem_max > 85% | warning | `for: 5m` |
| `PodRestartLoop` | > 3 restarts in 15m | critical | `for: 0m` |

---

## PSI Reference

The Population Stability Index thresholds used in `drift_check.py` and
`alert_rules.yml` follow the financial-industry standard:

| PSI Range | Interpretation | Recommended Action |
|---|---|---|
| < 0.10 | Stable | Monitor normally |
| 0.10 – 0.20 | Minor drift | Investigate; schedule retraining evaluation |
| ≥ 0.20 | Major drift | Open P1 ML incident; halt serving if confidence threshold breached |

---

## Runbook Links

All alerts include a `runbook_url` annotation pointing to the live runbooks
at [mlops.zrl.dev](https://mlops.zrl.dev):

- [Latency Runbook](https://mlops.zrl.dev/runbooks/latency-runbook)
- [Incident Response Runbook](https://mlops.zrl.dev/runbooks/incident-response-runbook)
- [Drift Runbook](https://mlops.zrl.dev/runbooks/drift-runbook)
- [Security Runbook](https://mlops.zrl.dev/runbooks/security-runbook)
