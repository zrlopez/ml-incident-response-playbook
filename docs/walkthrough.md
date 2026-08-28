# Operational Walkthrough

This walkthrough shows how the repository fits together as a production-style ML incident response system. It is written for portfolio reviewers who want the operating model without reading every source file.

---

## Scenario

A production model starts behaving abnormally after an upstream feature pipeline changes shape. Alert volume rises, prediction confidence shifts, and user-facing services begin reporting degraded outcomes.

The system's job is not to magically fix the model. Its job is to make the response fast, auditable, and repeatable.

---

## 1. Alert and detection

Prometheus rules in `observability/alert_rules.yml` watch API health, incident volume, model drift, Redis denylist availability, pipeline freshness, and LLM token budget utilization.

When a threshold breaches, the responder starts from the relevant runbook:

- [`runbooks/model_degradation.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/runbooks/model_degradation.md)
- [`runbooks/data_quality_incident.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/runbooks/data_quality_incident.md)
- [`runbooks/pipeline_failure.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/runbooks/pipeline_failure.md)
- [`runbooks/api_outage.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/runbooks/api_outage.md)
- [`runbooks/llm_cost_spike.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/runbooks/llm_cost_spike.md)

Each runbook turns a noisy alert into a bounded diagnosis path.

---

## 2. Incident creation

The incident API records the event with severity, status, owner, timestamps, and audit metadata. The domain lifecycle keeps the state machine explicit:

```text
OPEN → INVESTIGATING → MITIGATING → RESOLVED → CLOSED
```

Invalid transitions are rejected, which prevents responders from skipping important operational steps during pressure.

Relevant code:

- [`api/routers/incidents.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/api/routers/incidents.py)
- [`src/services/incident_service.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/src/services/incident_service.py)
- [`src/domain/incident_lifecycle.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/src/domain/incident_lifecycle.py)
- [`src/repositories/incident_repository.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/src/repositories/incident_repository.py)

---

## 3. Auth, safety, and auditability

Write paths are protected by JWT authentication and role-aware dependencies. Logout and refresh-token invalidation use Redis-backed denylisting, while state-changing operations emit structured audit events.

This is intentionally heavier than a simple portfolio CRUD app: the security model is part of the demonstration.

Relevant code and docs:

- [`api/routers/auth.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/api/routers/auth.py)
- [`api/dependencies.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/api/dependencies.py)
- [`api/redis_denylist.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/api/redis_denylist.py)
- [`src/audit.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/src/audit.py)
- [ADR-003 Redis JWT Denylist](adr/ADR-003-redis-jwt-denylist.md)
- [ADR-004 JWT Algorithm Selection](adr/ADR-004-jwt-algorithm-selection.md)

---

## 4. ML anomaly scoring

The anomaly detector scores incident telemetry using a small IsolationForest model. The live Hugging Face Space exposes this inference layer as a read-only demo, while the production-style API keeps inference behind JWT auth.

The model is deliberately documented as a portfolio artifact: synthetic training data, clear limitations, no calibrated probability claims, and a concrete productionization path.

Relevant code and docs:

- [`app.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/app.py)
- [`api/routers/inference.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/api/routers/inference.py)
- [`ml_models/incident_anomaly/registry.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/ml_models/incident_anomaly/registry.py)
- [`scripts/train_model.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/scripts/train_model.py)
- [Model card](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/MODEL_CARD.md)
- [ADR-010 Anomaly Model Design](adr/ADR-010-anomaly-model-design.md)

---

## 5. Observability and dashboards

The API emits structured logs, Prometheus metrics, and OpenTelemetry spans. Grafana dashboard JSON and dashboard specifications show how responders would monitor incident volume, API health, drift, and operational KPIs.

Relevant files:

- [`api/metrics.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/api/metrics.py)
- [`observability/otel_setup.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/observability/otel_setup.py)
- [`observability/logging_config.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/observability/logging_config.py)
- [`dashboards/ml_operations_overview.json`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/dashboards/ml_operations_overview.json)
- [Dashboard specification](dashboards/dashboard_spec.md)
- [Monitoring guide](monitoring.md)

---

## 6. Mitigation and rollback

For model incidents, responders use the runbook to compare current behavior against baseline metrics, identify whether the issue is model, data, or infrastructure related, and choose a mitigation path.

Rollback and model lifecycle behavior are represented through the model registry service and runbooks rather than a live production deployment.

Relevant files:

- [`runbooks/model_rollback.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/runbooks/model_rollback.md)
- [`src/services/model_registry_service.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/src/services/model_registry_service.py)
- [`src/repositories/model_version_repository.py`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/src/repositories/model_version_repository.py)
- [ADR-010 Anomaly Model Design](adr/ADR-010-anomaly-model-design.md)

---

## 7. Post-incident closure

The repository includes postmortem templates, sample incident logs, severity policy, governance docs, and KPI definitions. This closes the loop from detection to learning, not just detection to code fix.

Relevant files:

- [`examples/sample_postmortem.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/examples/sample_postmortem.md)
- [`examples/sample_incident_log.md`](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/examples/sample_incident_log.md)
- [Postmortem template](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/docs/templates/postmortem_template.md)
- [Severity matrix](severity_matrix.md)
- [Incident KPIs](metrics/incident_kpis.md)

---

## What this demonstrates

| Portfolio signal | Demonstrated by |
|---|---|
| Incident response maturity | Runbooks, lifecycle enforcement, postmortem templates |
| MLOps awareness | Drift checks, anomaly scoring, model card, ADR-010 |
| Backend engineering | FastAPI routers, async repositories, SQLAlchemy models |
| Security posture | JWT design, Redis denylist, CI security gates, remediation log |
| Observability | Prometheus metrics, OTel setup, Grafana dashboard JSON |
| Platform thinking | Docker, Terraform, Kubernetes manifests, CI/CD workflows |

---

## Scope boundary

This project is a production-style portfolio artifact, not a hosted enterprise incident platform. The value is in the engineering shape: clear operational seams, explicit security decisions, realistic documentation, and enough runnable code to demonstrate competence without requiring a real on-call organization behind it.
