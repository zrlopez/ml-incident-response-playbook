# ML Incident Response Playbook

[![CI](https://github.com/zrlopez/ml-incident-response-playbook/actions/workflows/secured_ci.yml/badge.svg)](https://github.com/zrlopez/ml-incident-response-playbook/actions/workflows/secured_ci.yml)
[![Docs](https://github.com/zrlopez/ml-incident-response-playbook/actions/workflows/docs.yml/badge.svg)](https://mlops.zrl.dev/)
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
cp .env.example .env          # fill in secrets
docker compose up --build     # API at http://localhost:8000
```

See [Setup](setup.md) and [Deployment](deployment.md) for full details.

---

## Navigation

- **[Architecture](architecture.md)** — component diagram, layer descriptions, request lifecycle, tech decisions
- **[API Reference](api_reference.md)** — endpoint contracts, auth flows, error schemas
- **[Monitoring](monitoring.md)** — Prometheus metrics, alert rules, drift detection
- **[Governance](governance.md)** — data handling, PII policy, SLA definitions
- **[Troubleshooting](troubleshooting.md)** — on-call triage steps
- **[Contributing](CONTRIBUTING.md)** — branch model, commit convention, PR checklist
