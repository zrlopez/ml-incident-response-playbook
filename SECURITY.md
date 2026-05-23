# Security Policy — ml-incident-response-playbook

## Supported Versions

| Version | Supported |
|---------|------------------|
| `main`  | ✅ Active support |

Only the latest commit on `main` receives security fixes.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report security issues via one of the following channels:

- **GitHub Private Vulnerability Reporting** (preferred):
  Navigate to **Security → Advisories → Report a vulnerability** in this repository.
- **Email:** noreply@users.noreply.github.com

You will receive an acknowledgment within **72 hours** and a status update within **7 days**.

## Scope

In-scope for this policy:
- Authentication and authorization weaknesses in `api/app.py`
- Secret exposure or credential leakage
- Injection vulnerabilities (SQL, template, command)
- CI/CD pipeline security issues
- Dependency vulnerabilities in `requirements.txt`
- Airflow DAG privilege escalation
- Container escape vectors in `Dockerfile`

Out-of-scope:
- Theoretical attacks without demonstrated impact
- Issues in dependencies not directly used by this project
- Automated scanner reports without manual triage

## Security Controls Active in This Repository

| Control | Implementation |
|---------|---------------|
| Authentication | OAuth2 Bearer JWT with RBAC (`api/app.py`) |
| Secret scanning | TruffleHog in CI (`ci_cd/secure-ci.yml`) |
| Dependency CVE scan | pip-audit in CI with `--strict` flag |
| SAST | Bandit in CI |
| Branch protection | PRs required; CI must pass before merge |
| Input validation | Pydantic models on all API endpoints |
| Template injection | Jinja2 autoescape enforced |
| Structured logging | structlog throughout ETL, API, anomaly detection |

## Dependency Update Policy

Dependency security updates are applied within:
- **Critical CVE:** 24 hours
- **High CVE:** 72 hours
- **Medium CVE:** 7 days
- **Low CVE:** Next scheduled release

Dependabot is configured to monitor Python and GitHub Actions dependencies.

## Disclosure Policy

This project follows **coordinated disclosure**. Reporters are credited in the release notes
(unless they request anonymity) after the fix is released.
