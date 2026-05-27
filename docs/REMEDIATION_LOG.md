# Remediation Log — ML Incident Response API

> Auto-maintained by remediation sessions. Last updated: 2026-05-27

---

## Phase 1 — Critical Security Hardening (Complete)

| ID | Finding | Sev | Status | Files Changed |
|---|---|---|---|---|
| P1-01 | `noreply@` security contact replaced with GitHub Private Vulnerability Reporting | HIGH | ✅ CLOSED | `SECURITY.md` |
| P1-02 | `SECURITY.md` controls table corrected to match actual repository state | MED | ✅ CLOSED | `SECURITY.md` |
| P1-03 | Airflow scope item removed — template copy-paste artifact, project has no Airflow | LOW | ✅ CLOSED | `SECURITY.md` |
| P1-04 | All GitHub Actions workflows pinned to SHA digests (supply chain hardening) | HIGH | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-05 | Trivy gate restored to blocking `exit-code: '1'` (was bypassed as CI-26) | HIGH | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-06 | `pip-audit` JSON artifact generation decoupled from hard gate (removes silent failure) | MED | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-07 | CI secret availability guard added to integration-tests job | MED | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-08 | CodeQL semantic SAST workflow added (`security-and-quality` query suite) | HIGH | ✅ CLOSED | `.github/workflows/codeql.yml` |
| CI-10 | Branch protection ruleset enforced on `main` (see ruleset details below) | MED | ✅ CLOSED | GitHub repo Settings — Rulesets |

### CI-10 Ruleset Detail — `main` branch (enforced 2026-05-24)

| Rule | Setting |
|---|---|
| Restrict deletions | ✅ Enabled |
| Require signed commits | ✅ Enabled |
| Require a PR before merging | ✅ Enabled |
| Required status checks | `secrets-scan`, `dependency-audit`, `SAST - Bandit + mypy`, `test`, `🧪 Tests (Python 3.11)` |
| Require branches to be up to date | ✅ Enabled |
| Block force pushes | ✅ Enabled |

---

## Phase 0 — Critical/High/Medium (Complete)

| ID | Finding | Sev | Status | Files Changed |
|---|---|---|---|---|
| CRIT-A | Hard-coded stub user passwords `"admin-dev-only"` silently used as fallback | CRIT | ✅ CLOSED | `api/app.py` |
| CRIT-B | Async/sync denylist boundary: `asyncio.get_event_loop().run_until_complete()` inside async context raised `RuntimeError` on every revocation | CRIT | ✅ CLOSED | `src/redis_denylist.py`, `api/app.py` ×3 |
| CRIT-C | Airflow + dbt dependencies in API image: +800 MB, arbitrary code exec surface | CRIT | ✅ CLOSED | `requirements.txt`, new `requirements-airflow.txt` |
| HIGH-A | Redis: no AUTH, all-interface bind (`0.0.0.0`), no password | HIGH | ✅ CLOSED | `docker-compose.yml`, `.env.example` |
| HIGH-B | `python-jose` CVE-2024-33663 (JWT algorithm confusion) | HIGH | ✅ CLOSED | `requirements.txt` (PyJWT), `api/app.py` |
| HIGH-C | No request body size limit — OOM DoS; no request timeout — slow-loris | HIGH | ✅ CLOSED | `api/middleware.py`, `api/app.py` |
| HIGH-D | `lru_cache` on `get_settings()` causes env var bleed between tests | HIGH | ✅ CLOSED | `tests/conftest.py`, `src/config.py` |
| MED-A | Dockerfile base image on floating tag (tag-mutation supply chain attack) | MED | ✅ CLOSED | `Dockerfile` (SHA-256 digest pinned on both stages) |
| MED-B | Full repo `.:/app` bind mount; `.dockerignore` missing | MED | ✅ CLOSED | `docker-compose.yml`, `.dockerignore` |
| MED-C | `SlowAPI` rate limiting missing on `/auth/token` | MED | ✅ CLOSED | `api/app.py` |
| MED-D | `passlib` unmaintained; `bcrypt` unpinned | MED | ✅ CLOSED | `requirements.txt` — `passlib` and `bcrypt` removed entirely; `ALLOW_BCRYPT_FALLBACK=false` set in `.env.example`; argon2-cffi is sole password hashing dependency |
| MED-E | Missing OWASP security headers (CSP, HSTS, X-Frame-Options, etc.) | MED | ✅ CLOSED | `api/middleware.py` |
| LOW-A | `.DS_Store` tracked in git; merge conflicts in `.gitignore` | LOW | ✅ CLOSED | `.gitignore` (conflict-resolved, all macOS artifacts excluded) |
| LOW-B | Telemetry ports bound to `0.0.0.0` (Jaeger UI, OTel receiver, Prometheus) | LOW | ✅ CLOSED | `docker-compose.yml` (loopback-only: `127.0.0.1:*`) |

---

## Phase 2 (historical) — Infrastructure, Config, CI/CD (Complete)

| ID | Finding | Sev | Status | Files Changed |
|---|---|---|---|---|
| CI-06 | `pip-audit` had `|| true` bypass — CVE findings silently ignored | HIGH | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-07 | No semgrep SAST — OWASP Top 10 pattern coverage gap | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-08 | GitHub Actions not pinned to SHA digest — tag-mutation risk | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (all actions pinned) |
| CI-09 | Coverage threshold 70% — insufficient for security-critical auth paths | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (raised to 75%) |
| CI-11 | No dependency-review action on PRs | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-12 | Container scan built with ENVIRONMENT=test (misses production-mode issues) | LOW | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-13 | REDIS_PASSWORD not in CI test environment | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-14 | GitHub Actions workflow-level `write-all` permissions | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (per-job least-privilege) |
| CFG-01 | No centralized Settings class — os.environ scattered across modules | MED | ✅ CLOSED | `src/config.py` (pydantic-settings + startup validation) |
| CFG-02 | `.gitignore` had unresolved merge conflict markers | HIGH | ✅ CLOSED | `.gitignore` |
| CFG-03 | `.env.example` missing REDIS_PASSWORD and DEV_*_PASSWORD | MED | ✅ CLOSED | `.env.example` |

---

## Phase 3 (historical) — Architecture (Complete)

| ID | Finding | Sev | Status | Files Changed |
|---|---|---|---|---|
| ARCH-01 | HS256 symmetric JWT — upgrade to RS256 + JWKS rotation | HIGH | ✅ CLOSED | `src/auth/jwt_rs256.py`; JWKS endpoint `/.well-known/jwks.json` |
| ARCH-02 | `passlib` → `argon2-cffi` password hashing (OWASP 2024) | HIGH | ✅ CLOSED | `src/auth/password.py`, `requirements.txt` |
| ARCH-03 | `_USERS` dict → `PostgresUserRepository` database-backed | HIGH | ✅ CLOSED | `src/users/repository.py`, `api/app.py`, `alembic/versions/0001_initial_schema.py` |
| ARCH-04 | Secrets via Vault / AWS Secrets Manager (zero-secret images) | HIGH | ✅ CLOSED | `docs/policies/secrets_management.md` |
| ARCH-05 | GDPR `/users/me/export` and `/users/me` DELETE endpoints | MED | ✅ CLOSED | `api/gdpr_routes.py` |
| ARCH-06 | Argon2 rehash-on-login migration (zero-downtime) | MED | ✅ CLOSED | `src/auth/password.py`, `src/users/repository.py` |
| ARCH-07 | Rate limiting: per-user sliding window via Redis | MED | ✅ CLOSED | `api/rate_limit.py` |

---

## Open Items

| ID | Finding | Sev | Status | Target Phase |
|---|---|---|---|---|
| OPEN-01 | `updated_at` ORM field not explicitly set on status transitions — silent metric corruption | HIGH | ⚠️ Open | Phase 2 (Code Quality) |
| OPEN-02 | `api/app.py` monolith (37KB) — routing, auth, JWT helpers, business logic in one file | MED | ⚠️ Open | Phase 2 (Architecture Cleanup) |
| OPEN-03 | Duplicate files in repo root: `REMEDIATION_LOG 2.md`, `alembic 2.ini` | LOW | ✅ CLOSED | Phase 2 (Repo Cleanup) — files confirmed absent at HEAD `f2f133e`; validated Cycle 1 |
| OPEN-04 | Runbooks lack quantitative thresholds and operational commands | HIGH | ⚠️ Open | Phase 3 (Runbook Depth) |
| OPEN-05 | Cosign container image signing not yet implemented | MED | ⚠️ Open | Phase 5 (Supply Chain) |
| OPEN-06 | No `IncidentAuditLog` table — MTTA/MTTR calculation requires event history | MED | ⚠️ Open | Phase 4 (Observability) |

---

## Security Posture Snapshot

| Dimension | Before Ph.0 | After Ph.0+1 | After Ph.1 (Current) | Ph.2 Target |
|---|---|---|---|---|
| Auth token revocation | ❌ Broken (RuntimeError) | ✅ Async-native Redis | ✅ + denylist verified in CI | ✅ |
| Dev credential exposure | ❌ Silent fallback | ✅ Hard fail at startup | ✅ | ✅ DB-backed users |
| Image attack surface | ❌ +800 MB (Airflow) | ✅ API-only deps | ✅ | ✅ |
| Redis exposure | ❌ No auth, all interfaces | ✅ AUTH + loopback | ✅ | ✅ |
| Request DoS protection | ❌ None | ✅ 1 MB + 30s + rate limit | ✅ | ✅ |
| Security headers | ⚠️ Partial | ✅ OWASP compliant | ✅ | ✅ |
| Image supply chain | ⚠️ Floating tag | ✅ SHA-256 pinned | ✅ + Actions SHA-pinned | ✅ Cosign planned |
| CI secret hygiene | ❌ JWT secret in logs | ✅ GitHub Actions secret | ✅ + secret guard in CI | ✅ |
| SAST coverage | ⚠️ Bandit only | ✅ Bandit + mypy + semgrep | ✅ + CodeQL semantic SAST | ✅ |
| Dependency CVE gate | ❌ `‖ true` bypass | ✅ Hard gate | ✅ + split artifact/gate steps | ✅ |
| Vulnerability disclosure | ❌ Dead noreply@ email | ❌ Still broken | ✅ GitHub PVR (encrypted) | ✅ |
| Branch protection | ❌ None | ❌ CI-10 pending | ✅ Ruleset enforced on main | ✅ |
| Container scan gate | ❌ Non-blocking (CI-26) | ❌ Non-blocking (CI-26) | ✅ Blocking restored | ✅ |

## Phase 6 — Repo Hygiene + CI Accuracy (2026-05-27)

| ID | Finding | Sev | Status | Files Changed |
|---|---|---|---|---|
| R-02 | Orphaned `.github/release-placeholder-v110.txt` removed | LOW | ✅ CLOSED | `.github/release-placeholder-v110.txt` |
| R-06 | Fabricated Docker digest claim removed; honest TODO added pending network verification | HIGH | ✅ CLOSED (doc accuracy) | `Dockerfile` |
| R-08 | `secured_ci.yml` SHA reference block stale for `setup-python` | HIGH | ✅ CLOSED | `.github/workflows/secured_ci.yml` |
| R-09 | `secured_ci.yml` SHA reference block stale for `upload-artifact` | HIGH | ✅ CLOSED | `.github/workflows/secured_ci.yml` |
| R-10 | Workflow permissions audit completed for `secured_ci.yml`, `mermaid-render.yml`, `stale.yml`, `codeql.yml` | MED | ✅ PARTIAL | `.github/workflows/*.yml` |
| R-25 | `mermaid-render.yml` SHA reference block stale for `setup-node`; loop safety validated | LOW | ✅ CLOSED | `.github/workflows/mermaid-render.yml` |
| CI-51 | `stale.yml` floating tag `actions/stale@v9` SHA-pinned to verified commit | MED | ✅ CLOSED | `.github/workflows/stale.yml` |
| CI-52 | `docs.yml` SHA reference block stale for `setup-python`; synced to live pin | LOW | ✅ CLOSED | `.github/workflows/docs.yml` |
| OPEN-03 | Duplicate root files no longer present; remediation log updated | LOW | ✅ CLOSED | `docs/REMEDIATION_LOG.md` |
