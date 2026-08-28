# Grafana Dashboard Specification

> **Purpose:** This document defines every panel in the ML Incident Response
> Grafana dashboard. It is the source of truth for the dashboard JSON in
> `dashboards/`. Engineers adding new Prometheus metrics must add the
> corresponding panel here before the metric is used in an alert rule.
>
> **Dashboard UID:** `ml-incident-response-v1`
> **Grafana folder:** `ML Platform`
> **Datasource:** Prometheus (default)
> **Refresh interval:** 30 seconds

---

## Row 1 — Incident Volume & SLA

### Panel 1.1 — Incident Rate (SEV-1 / SEV-2)

- **Type:** Time-series
- **Query:**
  ```promql
  rate(ml_incident_created_total{severity=~"SEV-1|SEV-2"}[5m])
  ```
- **Legend:** `{{severity}} {{category}}`
- **Y-axis:** Incidents / second
- **Alert rule:** `SEV1IncidentRateHigh` (threshold > 0.01 / s over 10 min)
- **Purpose:** Earliest possible signal that SEV-1 or SEV-2 incidents are
  accumulating faster than normal.

### Panel 1.2 — Mean Time to Detect (MTTD) — 30-day rolling

- **Type:** Stat
- **Query:**
  ```promql
  avg_over_time(ml_incident_time_to_detect_minutes[30d])
  ```
- **Thresholds:** Green ≤ 10 min, Yellow ≤ 20 min, Red > 20 min
- **Purpose:** SLA compliance view for leadership reporting.

### Panel 1.3 — Mean Time to Resolve (MTTR) — 30-day rolling

- **Type:** Stat
- **Query:**
  ```promql
  avg_over_time(ml_incident_time_to_resolve_minutes[30d])
  ```
- **Thresholds:** Green ≤ 240 min (4 h), Yellow ≤ 480 min, Red > 480 min
- **Purpose:** Tracks whether resolution velocity is improving month-over-month.

### Panel 1.4 — Incidents by Category (Last 30 days)

- **Type:** Bar chart
- **Query:**
  ```promql
  sum by (category) (increase(ml_incident_created_total[30d]))
  ```
- **Purpose:** Identifies which incident categories are driving volume so
  engineering effort can be prioritised.

---

## Row 2 — API Health

### Panel 2.1 — Request Rate

- **Type:** Time-series
- **Query:**
  ```promql
  rate(http_requests_total{job="ml-incident-api"}[1m])
  ```
- **Legend:** `{{method}} {{handler}} {{status}}`
- **Purpose:** Baseline traffic visibility; spikes correlate with incident
  creation bursts or runaway clients.

### Panel 2.2 — Error Rate (5xx)

- **Type:** Time-series
- **Query:**
  ```promql
  rate(http_requests_total{job="ml-incident-api",status=~"5.."}[1m])
  ```
- **Thresholds:** Green = 0, Red > 0.01 / s
- **Alert rule:** `APIErrorRateHigh`

### Panel 2.3 — P99 Latency

- **Type:** Time-series
- **Query:**
  ```promql
  histogram_quantile(0.99,
    rate(http_request_duration_seconds_bucket{job="ml-incident-api"}[5m])
  )
  ```
- **Thresholds:** Green ≤ 0.5 s, Yellow ≤ 1 s, Red > 1 s

### Panel 2.4 — Revoked Token Access Attempts

- **Type:** Stat + time-series
- **Query:**
  ```promql
  increase(ml_revoked_token_access_total[1h])
  ```
- **Purpose:** Security signal. Any non-zero value warrants investigation.
- **Alert rule:** `RevokedTokenAccessSpike`

---

## Row 3 — Model & Data Health

### Panel 3.1 — Feature PSI Heatmap

- **Type:** Heatmap
- **Query:**
  ```promql
  ml_feature_psi
  ```
- **Legend:** `{{feature}} {{model}}`
- **Thresholds:** Green < 0.10, Yellow 0.10–0.20, Red > 0.20
- **Purpose:** At-a-glance view of which features are drifting.

### Panel 3.2 — Feature Null Rate

- **Type:** Time-series
- **Query:**
  ```promql
  ml_feature_null_rate{pipeline="clickstream_agg_hourly"}
  ```
- **Alert rule:** `FeatureNullRateHigh` (threshold > 0.05)

### Panel 3.3 — LLM Token Budget Utilisation

- **Type:** Gauge
- **Query:**
  ```promql
  ml_llm_token_budget_utilisation_pct
  ```
- **Thresholds:** Green ≤ 60 %, Yellow ≤ 80 %, Red > 80 %
- **Alert rule:** `LLMTokenBudgetHigh`

---

## Row 4 — Infrastructure

### Panel 4.1 — Postgres Connection Pool Utilisation

- **Type:** Stat
- **Query:**
  ```promql
  pg_stat_activity_count / on() pg_settings_max_connections * 100
  ```
- **Thresholds:** Green ≤ 50 %, Yellow ≤ 75 %, Red > 75 %

### Panel 4.2 — Redis Memory Utilisation

- **Type:** Time-series
- **Query:**
  ```promql
  redis_memory_used_bytes / redis_memory_max_bytes * 100
  ```
- **Thresholds:** Green ≤ 60 %, Yellow ≤ 80 %, Red > 80 %

---

## Adding a New Panel

1. Add the Prometheus metric to `monitoring/metrics.md`.
2. Add the panel definition to this file under the appropriate row.
3. Add any new alert rule to `monitoring/alert_rules.yml`.
4. Export the updated dashboard JSON from Grafana and commit it to
   `dashboards/ml_incident_response_v1.json`.
5. Reference the new panel in the relevant runbook if it supports triage.
