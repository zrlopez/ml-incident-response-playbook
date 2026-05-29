# ML Incident Response Playbook — Master Checklist

> **Branch:** `feat/phase-11-supply-chain` and forward
> **Last updated:** 2026-05-28
> **Convention:** Phases 1–10 are complete or in-flight. New work begins at Phase 11.
> **Execution order:** Supply Chain → Architecture → Coverage → Runbooks → Observability → Deployment → Portfolio

---

## ✅ Phase 1 — Security Hardening (COMPLETE)

- [x] Semgrep promoted to hard gate — no `continue-on-error` (CI-52)
- [x] `CI_POSTGRES_PASSWORD` secret reference replaces inline credential
- [x] Bandit hard gate separated from SARIF `|| true` escape hatch
- [x] Per-job `permissions:` blocks on every CI job
- [x] Permission audit comment block at top of `secured_ci.yml`

---

## ✅ Phase 4 (Infra) — Grafana + Prometheus Infra (COMPLETE)

- [x] `prometheus` service in `docker-compose.yml` (`prom/prometheus:v2.52.0`)
- [x] `grafana` service in `docker-compose.yml` (`grafana/grafana:11.0.0`)
- [x] `dashboards/ml_operations_overview.json` Grafana dashboard
- [x] `observability/prometheus.yml` scrape config referenced by compose

---

## ✅ Phase 7 (Partial) — HF Spaces Scaffolding (COMPLETE)

- [x] `README.md` HF Spaces YAML frontmatter (`sdk: docker`, `app_port: 8080`, `emoji: 🚨`)
- [x] `deploy-hf.yml` GitHub Actions workflow exists

---

## ✅ Phase 8 (Partial) — Portfolio Presentation (COMPLETE)

- [x] "Quick Proof of Quality" table in `README.md`
- [x] Live HF Space URL row populated
- [x] Operational docs row (`mlops.zrl.dev`) populated

---

## ✅ Phase 11 — Supply Chain (COMPLETE)

> **Branch:** `feat/phase-11-supply-chain`
> **Completed:** 2026-05-28 (CI-52 through CI-55)

- [x] Add `lockfile-check` CI job — detect `pip-compile` drift between `requirements.txt` and `pyproject.toml` (CI-55)
- [x] Verify `.github/CODEOWNERS` exists and covers security-sensitive paths (`api/`, `src/auth/`, `.github/workflows/`, `Dockerfile`) — confirmed present
- [x] Add `docs/branch-protection-policy.md` — document required status checks and merge rules — confirmed present
- [x] Add `pip-audit` step to `.pre-commit-config.yaml` (CI-55)
- [x] Add `make deps-compile` Makefile target (`pip-compile pyproject.toml → requirements.txt`) (CI-55)

---

## Phase 12 — Architecture Cleanup

> **Branch:** `feat/phase-12-architecture-cleanup`
> **Goal:** Refactor to clean layered architecture before writing more tests against the old structure.

- [ ] Write characterization tests for `src/incident_tracker.py` **before** refactoring (safety net)
- [ ] Refactor `src/incident_tracker.py` → thin facade over `src/domain/`, `src/services/`, `src/repositories/`
- [ ] Collapse deps to single source of truth: `pyproject.toml` + `pip-compile` generated `requirements.txt`
- [ ] Add minimum credible content to `infrastructure/` — Terraform stub (`main.tf`) + `README.md`
- [ ] Add minimum credible content to `dbt/` — `README.md` + one model stub (`models/incidents.sql`)
- [ ] Add minimum credible content to `orchestration/` — `README.md` explaining DAG trigger pattern
- [ ] Update Architecture Mermaid diagram in `README.md` to reflect real code path: `FastAPI → Auth → Services → Domain → Postgres/Redis`

---

## Phase 13 — Code Quality & Coverage

> **Branch:** `feat/phase-13-coverage`
> **Goal:** Raise coverage gates and close test gaps after architecture stabilizes.

- [ ] Add `pytest-xdist` to `requirements-dev.txt`
- [ ] Add `-n auto` flag to CI unit test step for parallel execution
- [ ] Raise integration coverage gate `40%` → `55%` (with CI changelog comment)
- [ ] Raise integration coverage gate `55%` → `65%` (second pass after new tests land)
- [ ] Fix README CI/CD section coverage discrepancy (`says ≥68%`, gate is `75%`)
- [ ] Add `tests/unit/test_model_registry_thread_safety.py` (50 concurrent workers)
- [ ] Add Redis denylist concurrency + expiry edge-case tests
- [ ] Add `tests/unit/test_incident_service_contract.py` (API↔service interface contract)
- [ ] Add `make ci-local` Makefile target (mirrors full CI run locally)

---

## Phase 14 — Runbook Realism

> **Branch:** `feat/phase-14-runbooks`
> **Goal:** Elevate runbooks from template stubs to operational-grade documents.

- [ ] Expand all existing runbooks to operational template format:
  - Metadata table (owner, severity, last-tested date)
  - Prometheus diagnostic query examples
  - Decision tree (Mermaid flowchart)
  - Escalation conditions and contacts
- [ ] Add `runbooks/model_rollback.md`
- [ ] Add `runbooks/feature_store_corruption.md`
- [ ] Add `runbooks/runbook_test_log.md` — game-day exercise evidence
- [ ] Add `configs/slos.yml` — numeric SLO definitions (error rate, latency p99, MTTR)

---

## Phase 15 — Observability (Metrics Endpoint)

> **Branch:** `feat/phase-15-observability`
> **Goal:** Wire live Prometheus metrics into the API so the Grafana dashboard has real data.

- [ ] Add `api/metrics.py` — Prometheus endpoint with `Counter`, `Histogram`, `Gauge` definitions
- [ ] Register metrics router in `api/main.py`
- [ ] Instrument `create_incident` path with metric labels (severity, status, latency)
- [ ] Bind runbook threshold values to real Prometheus query expressions in `configs/slos.yml`
- [ ] Update Grafana dashboard JSON to use real metric names from `api/metrics.py`

---

## Phase 16 — Hugging Face Spaces Deployment

> **Branch:** `feat/phase-16-hf-deployment`
> **Goal:** Get a live, publicly accessible API endpoint running on HF Spaces.

- [ ] Confirm HF Space org/slug — README currently shows `zrlo/ml-incident-api`, verify this is correct
- [ ] Verify `Dockerfile` port — HF `app_port: 8080` set in frontmatter; confirm FastAPI binds `0.0.0.0:8080`
- [ ] Provision external Postgres — Neon free tier (connect string → HF Secret `DATABASE_URL`)
- [ ] Provision external Redis — Upstash free tier (connect string → HF Secret `REDIS_URL`)
- [ ] Add `src/api/health.py` — structured `/health` endpoint checking DB + Redis liveness
- [ ] Add `scripts/seed_demo_user.py` — seeds read-only demo user on first boot
- [ ] Set all HF Space Secrets (`DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `DEV_*_PASSWORD`)
- [ ] Add `make deploy-hf` Makefile target — pushes to HF Space remote via `huggingface_hub` CLI
- [ ] Add `make hf-status` Makefile target — tails HF Space build logs
- [ ] Smoke-test live endpoint: `GET /health`, `POST /auth/token`, `GET /incidents`
- [ ] Update README live URL table with confirmed working HF Space URL

---

## Phase 17 — Portfolio Presentation (Final Polish)

> **Branch:** `feat/phase-17-portfolio-polish`
> **Goal:** Ensure the repo tells a clear, compelling story to senior reviewers in under 90 seconds.

- [ ] Add "What Senior Reviewers Will Find" section to README (above Feature Highlights)
- [ ] Add "Known Limitations" callout section to README (honest, professional)
- [ ] Update Roadmap section from placeholder bullets to strategic format:
  - Q3 2026 — concrete items
  - Q4 2026 — concrete items
  - Aspirational — long-term direction
- [ ] Verify all badge URLs resolve and are accurate
- [ ] Verify all internal doc links (`docs/`, `runbooks/`, ADRs) are not broken
- [ ] Cross-check `zrl.dev` portfolio page links to this repo with correct description
- [ ] Final README read-through — remove any stale Fly.io references
