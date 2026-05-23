# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0] — 2026-05-23

Initial public release of the ML Incident Response Playbook.

### Added

- Repository scaffold: runbooks, templates, diagrams, severity matrix, data dictionary, and example incident artifacts.
- Five ML/AI incident runbooks with Mermaid decision-tree flowcharts.
- Hardened FastAPI application (`api/app.py`) with JWT authentication, RBAC, Pydantic input validation, CORS middleware, and security headers.
- Observability layer: `structlog` JSON logging with PII scrubbing processor, `audit()` event emitter, and `send_alert()` stub for Slack/PagerDuty/SNS integration.
- Distributed request tracing via `X-Trace-Id` correlation header injected per request.
- Rate limiting on `/auth/token` endpoint (5 requests/minute per IP via SlowAPI).
- Token refresh endpoint and in-memory token revocation blocklist (`/auth/logout`).
- Hardened `Dockerfile` with multi-stage build, SHA-pinned base image, non-root UID 1001, read-only root filesystem, explicit file allowlist, and `REQUIREMENTS_HASH` enforcement.
- Kubernetes manifests with full pod security standards: `runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, `capabilities: drop: [ALL]`, `seccompProfile: RuntimeDefault`.
- Kubernetes `Ingress` resource with TLS termination via cert-manager.
- `HorizontalPodAutoscaler` and `PodDisruptionBudget` for high-availability pod scheduling.
- `NetworkPolicy` with namespace-scoped ingress and egress rules (PostgreSQL egress limited to `ml-platform` namespace).
- Secure CI/CD pipeline (`ci_cd/secure-ci.yml`) with TruffleHog secret scanning, Bandit SAST, pip-audit, mypy type checking, test coverage gate, Trivy container image scanning, CycloneDX SBOM generation, and DAST smoke test.
- All GitHub Actions pinned to full commit SHAs for supply chain integrity.
- `SECURITY.md` vulnerability disclosure policy.
- Dependabot configuration for automated dependency version alerts.
- `.env.example` and comprehensive `.gitignore`.
- Airflow-style DAG example and orchestration templates.
- dbt project with models, macros, snapshots, and tests.
- ETL pipeline template with validation, error handling, and idempotency.
- Anomaly detection and drift check helpers.
- `MIT License`.

### Fixed

- **CRIT:** Repaired broken `create_access_token()` function — missing `data` parameter name caused a `SyntaxError` at module import, making the API undeployable.
- **CRIT:** Replaced root `Dockerfile` CMD (`python -m http.server`) with production uvicorn invocation — previous command served the entire filesystem unauthenticated.
- **CRIT:** Fixed all Kubernetes manifest resources using `meta:` instead of `metadata:` — `kubectl apply` was failing schema validation entirely.
- **HIGH:** Migrated from `python-jose` (CVE-2024-33663 algorithm confusion) to `PyJWT >= 2.9.0`.
- **HIGH:** Replaced unmaintained `passlib` (last release 2020) with `bcrypt` directly.
- **HIGH:** Moved `JWT_SECRET_KEY` from `echo >> $GITHUB_ENV` to a GitHub Actions secret reference — eliminates CI log secret exposure.
- **HIGH:** Wired `configure_logging()` into app startup — PII scrubbing was defined but never active.
- **HIGH:** Wired `audit()` calls on all authentication and incident mutation events — audit channel was previously silent.
- **MED:** Fixed CORS allowed-origins parser producing `[""]` (truthy empty string) when `CORS_ALLOWED_ORIGINS` is unset.
- **MED:** Replaced `int(time.time() * 1000)` incident ID generation with `uuid4` — eliminates millisecond-window collision risk.
- **MED:** Scoped Kubernetes PostgreSQL egress from `0.0.0.0/0` to specific namespace selector — prevents SSRF data exfiltration.
- **MED:** Pinned Kubernetes container image from mutable tag to SHA digest.
- **LOW:** Added `.DS_Store` to `.gitignore` and removed committed macOS metadata files.

### Security

- Authentication: JWT (HS256) with `PyJWT 2.9.0`, RBAC (`admin` / `analyst` / `operator`), rate limiting, refresh token rotation, and revocation blacklist.
- Container: SHA-pinned base image, non-root execution, read-only filesystem, no secrets in image build context.
- Kubernetes: Full pod security context, scoped NetworkPolicy, TLS ingress, HPA, PDB.
- CI: Secret scanning gate blocks all downstream jobs; container image scanned with Trivy at CRITICAL/HIGH threshold; SBOM artifact generated per build.
- Logging: PII scrubbing active on all log fields; audit events tagged `log_type=audit` for SIEM routing.
