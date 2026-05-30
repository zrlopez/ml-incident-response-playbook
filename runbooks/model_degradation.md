# Runbook: Model Degradation

**Last reviewed:** 2026-05-28  |  **Lifecycle stage:** OPEN → INVESTIGATING → MITIGATING → RESOLVED → CLOSED

## Metadata

| Field | Value |
|---|---|
| Severity | SEV-1 (P99 > 2 s for 5 m) / SEV-2 (P99 > 1 s for 5 m) / SEV-3 (accuracy drop > 5%) |
| MTTR Target | 4 h (SEV-1) / 8 h (SEV-2) / 24 h (SEV-3) |
| On-call Owner | ML Platform |
| Last Tested | 2026-05-28 (local docker-compose drill) |
| Related Alerts | `MLModelDegradationP99`, `MLDriftScoreHigh`, `MLIncidentRateSpike` |

---

## Alert Trigger

This runbook is initiated when **any** of the following Prometheus alerts fire:

| Alert Name | Prometheus Expression | Threshold | Severity |
|---|---|---|---|
| `MLModelDegradationP99` | `histogram_quantile(0.99, rate(ml_inference_duration_seconds_bucket[5m]))` | > 2.0 s for 5 m | SEV-1 |
| `MLModelDegradationP99Warning` | `histogram_quantile(0.99, rate(ml_inference_duration_seconds_bucket[5m]))` | > 1.0 s for 5 m | SEV-2 |
| `MLDriftScoreHigh` | `ml_drift_score_latest{feature_name=~".+"}` | PSI > 0.2 | SEV-2 |
| `MLIncidentRateSpike` | `rate(ml_incident_total[1h])` | > 10/hr | SEV-2 |
| `MLAPICriticalErrorRate` | `rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])` | > 20% | SEV-1 |

---

## Purpose

Use this runbook when a deployed model appears to be underperforming even though the
pipeline and API are healthy. The goal is to quickly confirm whether the issue is
caused by data drift, label drift, a bad deployment, or a downstream dependency change.

## SLO Thresholds

| Metric | Warning | SEV-3 | SEV-2 |
|---|---|---|---|
| Accuracy / primary KPI | Drops > 2% vs. baseline | Drops > 5% | Drops > 10% |
| Prediction latency (p99) | > 500 ms | > 1 s for 5 min | > 2 s for 5 min |
| Feature null rate | > 0.5% | > 2% | > 5% |
| Input volume | ±20% vs. 7-day avg | ±40% | ±60% |

## Typical Signals

- Accuracy, precision, recall, or a business KPI drops below threshold.
- User feedback becomes consistently negative.
- Latency stays stable but prediction quality degrades.
- Performance differs measurably from the last known stable baseline.

## Immediate Actions (first 5 minutes)

1. Confirm the alert is real — query Prometheus directly:
   ```bash
   # Current P99 inference latency (MLModelDegradationP99 fires if > 2.0s)
   curl -s http://localhost:9090/api/v1/query \
     --data-urlencode 'query=histogram_quantile(0.99, rate(ml_inference_duration_seconds_bucket[5m]))' \
     | jq '.data.result[] | {metric: .metric, value: .value[1]}'

   # Current feature drift score (MLDriftScoreHigh fires if PSI > 0.2)
   curl -s http://localhost:9090/api/v1/query \
     --data-urlencode 'query=ml_drift_score_latest' \
     | jq '.data.result[] | {feature: .metric.feature_name, psi: .value[1]}'

   # Incident creation rate over 1h (MLIncidentRateSpike fires if > 10/hr)
   curl -s http://localhost:9090/api/v1/query \
     --data-urlencode 'query=rate(ml_incident_total[1h])' \
     | jq '.data.result'
   ```
2. Check whether a model deployment occurred recently:
   ```bash
   kubectl rollout history deployment/ml-model-server -n production
   # or query your model registry for the last promotion timestamp
   ```
3. Compare current metrics to the last stable baseline in the Grafana dashboard:
   `http://localhost:3000/d/ml-ops-overview` (ML Operations Overview)
4. Open the incident and set status to INVESTIGATING:
   ```bash
   curl -X PATCH https://<API_HOST>/incidents/<ID>/status \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "investigating"}'
   ```
5. Notify the model owner and incident commander.

## Escalation Path

| Elapsed | Action |
|---|---|
| 0–15 min | On-call primary + model owner investigate |
| 15 min | Page on-call secondary if no root cause |
| 30 min | Page engineering manager |
| 60 min (SEV-2) | Stakeholder notification; consider rollback |

## Diagnostic Checklist

- [ ] Was there a model version or config change in the last 24 h?
- [ ] Did input feature distributions shift? (check `ml_drift_score_latest` in Grafana)
- [ ] Did label quality or ground-truth pipeline change?
- [ ] Did input request volume change materially (> 40%)? (check `rate(ml_incident_total[1h])`)
- [ ] Did a preprocessing or feature-engineering step change?
- [ ] Is the model server returning errors silently? (check prediction error logs)
- [ ] Is the issue limited to one model version, region, or cohort?

## Mermaid Flowchart

```mermaid
flowchart TD
    A[Degradation alert fires] --> B{MLModelDegradationP99 or MLDriftScoreHigh?}
    B -- Latency --> C[Query: histogram_quantile 0.99]
    B -- Drift --> D[Query: ml_drift_score_latest by feature]
    C --> E{P99 > 2.0s confirmed?}
    D --> F{PSI > 0.2 confirmed?}
    E -- Yes --> G[Check recent model deployment]
    E -- No --> H[Monitoring false positive — close]
    F -- Yes --> I[Identify drifted feature, freeze upstream pipeline]
    G --> J{Deployment in last 24h?}
    J -- Yes --> K[Roll back to last stable version]
    J -- No --> L[Expand: labels, volume, downstream deps]
    K --> M[Monitor ml_inference_duration_seconds for 15 min]
    L --> M
    I --> M
    M --> N{Stable?}
    N -- Yes --> O[RESOLVED]
    N -- No --> P[Escalate to secondary + data team]
```

## Mitigation Steps

```bash
# Option A: Roll back model server to last stable version
kubectl rollout undo deployment/ml-model-server -n production
kubectl rollout status deployment/ml-model-server -n production

# Option B: Toggle feature flag / disable affected route
curl -X POST https://<API_HOST>/admin/feature-flags/model-v2 \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"enabled": false}'

# Option C: Freeze upstream feature pipeline if data-driven
airflow dags pause <DAG_ID>
```

Set status to MITIGATING once action is in progress:
```bash
curl -X PATCH https://<API_HOST>/incidents/<ID>/status \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "mitigating"}'
```

## Validation Steps

- P99 latency (`ml_inference_duration_seconds`) returns below 500 ms for ≥ 15 consecutive minutes.
- `ml_drift_score_latest` drops below PSI 0.1 for all features.
- Feature null rate back below 0.5%.
- No new error patterns in prediction audit logs.
- Model owner confirms metrics are stable in Grafana (`ml-ops-overview` dashboard).

```bash
# Spot-check P99 latency post-mitigation (should be < 0.5)
curl -s http://localhost:9090/api/v1/query \
  --data-urlencode 'query=histogram_quantile(0.99, rate(ml_inference_duration_seconds_bucket[5m]))' \
  | jq '.data.result[] | .value[1]'
```

## Closure Criteria

- [ ] P99 latency stable below 500 ms for ≥ 15 minutes post-mitigation.
- [ ] Root cause identified or bounded.
- [ ] Model version, drift source, or config change documented.
- [ ] Incident status set to RESOLVED then CLOSED via API.
- [ ] Retraining or data correction ticket created if drift confirmed.
- [ ] PIR scheduled per trigger criteria.

## Post-Incident Review Triggers

- **Required PIR:** KPI dropped > 10% (SEV-2); root cause unknown at closure; data corruption confirmed.
- **Lightweight note:** Isolated version regression with clean rollback and known root cause.

## Runbook Validation

| Date | Tester | Environment | Outcome | Notes |
|---|---|---|---|---|
| 2026-05-28 | @zrlopez | local docker-compose | PASS | Alert trigger table and Prometheus curl commands validated |

## Related Runbooks

- [Data Quality Incident](./data_quality_incident.md) — if feature drift or corrupt inputs caused the degradation
- [Pipeline Failure](./pipeline_failure.md) — if a stale feature store is driving the issue
- [API Outage](./api_outage.md) — if prediction errors are actually serving errors
