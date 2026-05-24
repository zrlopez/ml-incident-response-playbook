# Onboarding Guide

> **Audience:** Engineers, data scientists, and analysts who are new to the
> ML Incident Response Playbook. Complete this guide before your first on-call
> shift or before contributing code to the repository.

---

## 1. What this repository is for

This repository contains the operational backbone for detecting, triaging, and
resolving incidents in ML-powered systems. It includes:

- A FastAPI incident tracking service (`api/`).
- Prometheus metrics and Grafana dashboard specifications.
- Runbooks for every incident category.
- Airflow DAGs for automated monitoring pipelines.
- Documentation on architecture, deployment, and governance.

Use this repository as the source of truth when an alert fires. Every runbook
links to the code and configuration that powers the alert.

---

## 2. Prerequisites

Before you start, make sure you have:

| Tool | Minimum version | Purpose |
|------|-----------------|---------|
| Python | 3.11 | API and scripts |
| Docker + Compose | 24.x | Local dev stack |
| `gh` CLI | 2.x | GitHub operations |
| `mermaid-js` CLI (optional) | latest | Render diagram files locally |

Install Python dependencies:

```bash
pip install -r requirements.txt       # runtime
pip install -r requirements-dev.txt   # test and lint tooling
```

---

## 3. Spin up the local stack

```bash
# Copy environment template and fill in any secrets
cp .env.example .env

# Start Postgres, Redis, and the API together
docker compose up --build

# Verify the API is healthy
curl http://localhost:8000/health
```

The API runs on port **8000**, Prometheus on **9090**, and Grafana on **3000**.
Default Grafana credentials are `admin / admin` (change on first login).

---

## 4. Understand the severity model

Every incident is assigned a severity at creation. Familiarise yourself with
the matrix in [`severity_matrix.md`](../severity_matrix.md) before triaging.

| Severity | Meaning | Target response |
|----------|---------|----------------|
| SEV-1 | Production impact, user-facing | Page immediately, war-room |
| SEV-2 | Degraded service, mitigatable | Respond within 30 minutes |
| SEV-3 | Non-critical degradation | Next-business-day review |
| SEV-4 | Observation / investigation | Backlog triage |

---

## 5. Find the right runbook

Runbooks live in `runbooks/`. Each file maps to an incident category:

| Incident type | Runbook |
|---------------|---------|
| API outage | [`runbooks/api_outage.md`](../runbooks/api_outage.md) |
| Data quality | [`runbooks/data_quality_incident.md`](../runbooks/data_quality_incident.md) |
| Model degradation | [`runbooks/model_degradation.md`](../runbooks/model_degradation.md) |
| Pipeline failure | [`runbooks/pipeline_failure.md`](../runbooks/pipeline_failure.md) |
| LLM cost spike | [`runbooks/llm_cost_spike.md`](../runbooks/llm_cost_spike.md) |

Each runbook follows the same structure: **Detection → Triage → Mitigation →
Postmortem**. Read the runbook for your incident type before taking any action.

---

## 6. Make your first contribution

1. Fork the repo or create a branch from `main`.
2. Read [`CONTRIBUTING.md`](../CONTRIBUTING.md) if it exists, otherwise follow
   the commit message convention in `CHANGELOG.md`.
3. Run the test suite before pushing:

   ```bash
   pytest tests/ -v
   ```

4. Keep every new file consistent with the documentation standard described in
   `docs/operational_principles.md`.
5. Open a pull request against `main` and request a review from the on-call
   team lead.

---

## 7. Key contacts and escalation

| Role | Responsibility | Contact |
|------|----------------|---------|
| ML Platform on-call | API and infrastructure incidents | PagerDuty rotation |
| Data Engineering on-call | Pipeline and data quality incidents | Slack `#data-oncall` |
| Security on-call | Auth failures and credential incidents | PagerDuty security rotation |
| FinOps | LLM cost and budget alerts | Slack `#finops` |

---

## 8. First week checklist

- [ ] Run `docker compose up` successfully.
- [ ] Read the `README.md` end to end.
- [ ] Review `severity_matrix.md`.
- [ ] Open and read the runbook for your team's primary incident category.
- [ ] Create a sample incident via the API (`POST /incidents`) and verify it
      appears in the tracker.
- [ ] Review at least one merged pull request to understand code conventions.
- [ ] Shadow one on-call handoff with your team lead.
