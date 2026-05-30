# Security Policy — ml-incident-response-playbook

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main`  | ✅ Active support |

Only the latest commit on `main` receives security fixes.

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Use GitHub's native **Private Vulnerability Reporting** (encrypted, maintainer-only):

👉 [Submit a confidential security advisory](https://github.com/zrlopez/ml-incident-response-playbook/security/advisories/new)

This channel is encrypted and visible only to repository maintainers.
Private Vulnerability Reporting is enabled on this repository.

**Response SLAs:**

| Severity | Acknowledgment | Initial Triage | Resolution Target |
|---|---|---|---|
| Critical | 24 hours | 48 hours | 7 days |
| High | 48 hours | 5 business days | 30 days |
| Medium | 72 hours | 7 business days | 90 days |
| Low | 7 days | Next review cycle | Best effort |

Reporters are credited in release notes unless anonymity is requested.

---

## Scope

**In-scope:**
- Authentication and authorization weaknesses (`api/app.py`, `src/auth/`)
- Secret exposure or credential leakage (any file)
- Injection vulnerabilities (SQL, template, command)
- CI/CD pipeline security (`.github/workflows/`)
- Dependency vulnerabilities (`requirements.txt`, `pyproject.toml`)
- Container escape vectors (`Dockerfile`)
- JWT algorithm confusion or token forgery
- Redis denylist bypass conditions
- RBAC privilege escalation paths
- GDPR data subject rights endpoints (`api/gdpr_routes.py`) — unauthorized access or bypass of Art. 15 export or Art. 17 erasure

**Out-of-scope:**
- Theoretical attacks without demonstrated impact
- Issues in transitive dependencies not directly imported by this project
- Automated scanner reports submitted without manual triage
- Vulnerabilities in development-only tooling (`requirements-dev.txt`) with no production path

---

## Required CI Secrets

The following repository secrets must be configured for CI to pass. Set these under
`Settings → Secrets and variables → Actions → Repository secrets`.

| Secret | Purpose |
|---|---|
| `CODACY_PROJECT_TOKEN` | Coverage upload to Codacy |
| `SEMGREP_APP_TOKEN` | Semgrep Cloud authentication |
| `CI_JWT_SECRET_KEY` | JWT secret for integration test runner |
| `CI_POSTGRES_PASSWORD` | Postgres password for integration test service container |
| `CI_REDIS_PASSWORD` | Redis password for integration test service container (optional) |
| `DEV_ADMIN_PASSWORD` | Seed admin user password in integration tests |
| `DEV_ANALYST_PASSWORD` | Seed analyst user password in integration tests |
| `DEV_OPERATOR_PASSWORD` | Seed operator user password in integration tests |
| `COSIGN_PRIVATE_KEY` | cosign private key for container image signing (release workflow) |
| `COSIGN_PASSWORD` | Passphrase for `COSIGN_PRIVATE_KEY` |

> **Note for Dependabot PRs:** Dependabot does not have access to repository secrets.
> The `Verify required CI secrets are present` step is skipped for `dependabot[bot]` actor runs.

---

## Security Controls

| Control | Implementation | Status |
|---|---|---|
| Authentication | OAuth2 Bearer JWT (HS256/RS256) with RBAC | ✅ Active |
| Token revocation | Redis-backed JWT denylist with TTL expiry | ✅ Active |
| Password hashing | argon2id via argon2-cffi (OWASP 2024) | ✅ Active |
| Secret scanning | TruffleHog v3 in CI (push + PR events) | ✅ Active |
| Dependency CVE scan | pip-audit with strict gate | ✅ Active |
| SAST | Bandit (medium/medium gate) + mypy + CodeQL | ✅ Active |
| Semgrep hard gate | semgrep-action (p/python + p/secrets + p/owasp-top-ten; ERROR severity) | ✅ Active |
| Container scanning | Trivy (CRITICAL/HIGH, ignore-unfixed) | ✅ Active |
| Container hardening | `no-new-privileges:true` + `read_only: true` on api service | ✅ Active |
| SBOM generation | anchore/sbom-action (SPDX-JSON, 365-day retention) | ✅ Active |
| Cosign artifact signing | Container image signing via Sigstore (release workflow) | ✅ Active |
| Input validation | Pydantic v2 models on all API endpoints | ✅ Active |
| Security headers | HSTS, CSP, X-Frame-Options, X-Content-Type-Options, nosniff | ✅ Active |
| Rate limiting | SlowAPI per-IP on `/auth/token`; per-user sliding window via Redis | ✅ Active |
| Audit logging | structlog with `log_type="audit"` on all state-changing operations | ✅ Active |
| Audit log stream | Dedicated `src/audit.py` typed event stream; schema: `observability/audit_log_schema.json` | ✅ Active |
| GDPR data subject rights | Art. 15 export + Art. 17 erasure via `api/gdpr_routes.py`; soft-delete with 30-day retention | ✅ Active |
| PII pseudonymisation | SHA-256 username hashing in all structured log fields (HIGH-03) | ✅ Active |
| Vulnerability disclosure | GitHub Private Vulnerability Reporting (encrypted) | ✅ Active |
| Branch protection | Ruleset enforced on `main`: PR required, CI must pass, force-push blocked, deletions restricted | ✅ Active |
| Actions SHA pinning | GitHub Actions workflows pinned to commit SHA digests | ✅ Active |
| Commit signing | GPG/SSH signed commits enforced via branch ruleset | ✅ Active |
| Coverage reporting | Codacy via `codacy/codacy-coverage-reporter-action` | ✅ Active |

---

## Dependency Update Policy

| Severity | Response Time |
|---|---|
| Critical CVE | 24 hours |
| High CVE | 72 hours |
| Medium CVE | 7 days |
| Low CVE | Next scheduled update |

Dependabot monitors Python packages and GitHub Actions dependencies weekly.
pip-audit runs in CI on every push and PR with a hard gate on Critical and High findings.

---

## Disclosure Policy

This project follows **coordinated disclosure**. Security advisories are published
after a fix is released and the reporter is notified. Reporters are credited by name
or handle unless they request anonymity.
