# Runbooks Index

This directory contains operational runbooks for the ML Incident Response platform.
Each runbook maps to one or more incident categories surfaced by the API and covers
diagnosis, mitigation, validation, and closure for that category.

## Quick Reference

| Runbook | Primary Signal | Typical SEV |
|---|---|---|
| [API Outage](./api_outage.md) | 5xx spike, timeouts, `/ready` probe failing | SEV-1 / SEV-2 |
| [Model Degradation](./model_degradation.md) | Accuracy / KPI drop below threshold | SEV-2 / SEV-3 |
| [Data Quality Incident](./data_quality_incident.md) | Schema drift, null rate spike, volume anomaly | SEV-2 / SEV-3 |
| [Pipeline Failure](./pipeline_failure.md) | DAG failure, job timeout, stale feature store | SEV-1 / SEV-2 |
| [LLM Cost Spike](./llm_cost_spike.md) | Token spend rate anomaly, quota breach risk | SEV-2 / SEV-3 |

## Escalation Matrix

| Time without mitigation | Action |
|---|---|
| 0–15 min | On-call primary works the runbook |
| 15–30 min | Page on-call secondary |
| 30–45 min | Page engineering manager |
| 45+ min (SEV-1) | Executive stakeholder notification |

## Status Lifecycle

All incidents tracked via `PATCH /incidents/{id}/status` follow this machine:

```
OPEN → INVESTIGATING → MITIGATING → RESOLVED → CLOSED
```

Update status at each phase transition. Do not skip states.

## Post-Incident Review (PIR) Triggers

A full PIR is **required** when any of the following are true:
- SEV-1 incident of any duration
- SEV-2 incident lasting > 30 minutes
- Any incident that caused data loss or corruption
- Any incident that breached an external SLA
- Any incident where the root cause remains unknown at closure

A lightweight retrospective note is sufficient for all other incidents.

## Related Docs

- API reference: `GET /incidents/`, `PATCH /incidents/{id}/status`
- Auth: `POST /auth/token`, `POST /auth/logout`
- Observability: `GET /health`, `GET /ready`
