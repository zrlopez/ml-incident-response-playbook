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

**Out-of-scope:**
- Theoretical attacks without demonstrated impact
- Issues in transitive dependencies not directly imported by this project
- Automated scanner reports submitted without manual triage
- Vulnerabilities in development-only tooling (`requirements-dev.txt`) with no production path

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
| Container scanning | Trivy (CRITICAL/HIGH, ignore-unfixed) | ✅ Active |
| SBOM generation | anchore/sbom-action (SPDX-JSON, 365-day retention) | ✅ Active |
| Input validation | Pydantic v2 models on all API endpoints | ✅ Active |
| Security headers | HSTS, CSP, X-Frame-Options, X-Content-Type-Options, nosniff | ✅ Active |
| Rate limiting | SlowAPI per-IP on `/auth/token`; per-user sliding window via Redis | ✅ Active |
| Audit logging | structlog with `log_type="audit"` on all state-changing operations | ✅ Active |
| Vulnerability disclosure | GitHub Private Vulnerability Reporting (encrypted) | ✅ Active |
| Branch protection | Ruleset enforced on `main`: PR required, CI must pass, force-push blocked, deletions restricted | ✅ Active |
| Actions SHA pinning | GitHub Actions workflows pinned to commit SHA digests | ✅ Active |
| Commit signing | GPG/SSH signed commits enforced via branch ruleset | ✅ Active |
| Cosign artifact signing | Container image signing via Sigstore | 🔲 Planned |

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
