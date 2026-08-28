# ML Incident Response Playbook

[![CI](https://github.com/zrlopez/ml-incident-response-playbook/actions/workflows/secured_ci.yml/badge.svg)](https://github.com/zrlopez/ml-incident-response-playbook/actions/workflows/secured_ci.yml)
[![Docs](https://img.shields.io/website-up-down-green-red/https/mlops.zrl.dev.svg?label=docs)](https://mlops.zrl.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/LICENSE)

A **production-grade FastAPI service** for detecting, triaging, and resolving
ML system incidents — model degradation, data quality failures, pipeline
outages, and LLM cost spikes.

Built to demonstrate end-to-end ML operations engineering: hardened CI/CD,
structured observability, async PostgreSQL persistence, Redis-backed JWT
revocation, and Prometheus + OpenTelemetry instrumentation.

---

## What this is

| Layer | Stack |
|---|---|
| API | FastAPI + Pydantic v2 + SQLAlchemy 2.x async |
| Auth | PyJWT RS256 + bcrypt + Redis denylist |
| Persistence | PostgreSQL (prod) / SQLite (dev/test) via Alembic |
| Observability | Prometheus + OpenTelemetry (OTLP/gRPC) + structlog |
| CI/CD | GitHub Actions — secrets scan, SAST, dep-audit, unit + integration tests, Trivy container scan, SBOM |
| Diagrams | Mermaid (auto-rendered to PNG in CI) |

---

## Quick start

```bash
git clone https://github.com/zrlopez/ml-incident-response-playbook.git
cd ml-incident-response-playbook
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d postgres redis prometheus grafana
uvicorn api.app:app --reload --port 8000
```

See [Setup](setup.md) and [Deployment](deployment.md) for full details.

## Portfolio review path

If you only have a few minutes, start with:

1. [Operational Walkthrough](walkthrough.md)
2. [Architecture](architecture.md)
3. [Model Card](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/MODEL_CARD.md)
4. [Model Degradation Runbook](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/runbooks/model_degradation.md)
5. [CI/CD Conventions](ci-conventions.md)

---

## Navigation

- **[Architecture](architecture.md)** — component diagram, layer descriptions, request lifecycle, tech decisions
- **[Operational Walkthrough](walkthrough.md)** — narrative path from alert to postmortem
- **[API Reference](api_reference.md)** — endpoint contracts, auth flows, error schemas
- **[Monitoring](monitoring.md)** — Prometheus metrics, alert rules, drift detection
- **[Governance](governance.md)** — data handling, PII policy, SLA definitions
- **[Troubleshooting](troubleshooting.md)** — on-call triage steps
- **[Contributing](CONTRIBUTING.md)** — branch model, commit convention, PR checklist
