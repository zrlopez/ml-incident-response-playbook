# Remediation Log — ML Incident Response API

> Auto-maintained by remediation sessions. Last updated: 2026-05-23

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

## Phase 1 — Infrastructure, Config, CI/CD (Complete)

| ID | Finding | Sev | Status | Files Changed |
|---|---|---|---|---|
| CI-06 | `pip-audit` had `|| true` bypass — CVE findings silently ignored | HIGH | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-07 | No semgrep SAST — OWASP Top 10 pattern coverage gap | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-08 | GitHub Actions not pinned to SHA digest — tag-mutation risk | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (all actions pinned) |
| CI-09 | Coverage threshold 70% — insufficient for security-critical auth paths | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (raised to 75%) |
| CI-10 | No branch protection rules on `main` | MED | ⚠️ Pending | GitHub repo settings — manual action required (see below) |
| CI-11 | No dependency-review action on PRs | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-12 | Container scan built with ENVIRONMENT=test (misses production-mode issues) | LOW | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-13 | REDIS_PASSWORD not in CI test environment | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-14 | GitHub Actions workflow-level `write-all` permissions | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (per-job least-privilege) |
| CFG-01 | No centralized Settings class — os.environ scattered across modules | MED | ✅ CLOSED | `src/config.py` (pydantic-settings + startup validation) |
| CFG-02 | `.gitignore` had unresolved merge conflict markers | HIGH | ✅ CLOSED | `.gitignore` |
| CFG-03 | `.env.example` missing REDIS_PASSWORD and DEV_*_PASSWORD | MED | ✅ CLOSED | `.env.example` |

---

## Phase 2 — Architecture (In Progress)

| ID | Finding | Sev | Status | Files Changed |
|---|---|---|---|---|
| ARCH-01 | HS256 symmetric JWT — upgrade to RS256 + JWKS rotation | HIGH | ✅ CLOSED | `src/auth/jwt_rs256.py`; JWKS endpoint `/.well-known/jwks.json`; kid-based rotation window; graceful HS256 fallback |
| ARCH-02 | `passlib` → `argon2-cffi` password hashing (OWASP 2024) | HIGH | ✅ CLOSED | `src/auth/password.py`, `requirements.txt`; bcrypt fallback gated by `ALLOW_BCRYPT_FALLBACK` |
| ARCH-03 | `_USERS` dict → `PostgresUserRepository` database-backed | HIGH | ✅ CLOSED | `src/users/repository.py`, `api/app.py` lifespan wired; `alembic/versions/0001_initial_schema.py` |
| ARCH-04 | Secrets via Vault / AWS Secrets Manager (zero-secret images) | HIGH | ✅ CLOSED | `docs/policies/secrets_management.md`; AWS SM + Vault/k8s + GCP CR runbooks; operator checklist |
| ARCH-05 | GDPR `/users/me/export` and `/users/me` DELETE endpoints | MED | ✅ CLOSED | `api/gdpr_routes.py` mounted in `app.py`; Art. 15 + Art. 17 routes live |
| ARCH-06 | Argon2 rehash-on-login migration (zero-downtime) | MED | ✅ CLOSED | `src/auth/password.py` (`maybe_rehash`), `src/users/repository.py` (`authenticate`) |
| ARCH-07 | Rate limiting: per-user sliding window via Redis | MED | ✅ CLOSED | `api/rate_limit.py` wired to `POST /incidents/`; `app.state.redis` exposed for dep |

---

## Manual Actions Required

### CI-10: Branch Protection Rules (GitHub UI — cannot be automated in files)

1. Go to: **Settings → Rules → Rulesets → New branch ruleset**
2. Target: `main` branch
3. Enable:
   - ✔ Require a pull request before merging
   - ✔ Required approvals: **1** (raise to 2 for production)
   - ✔ Dismiss stale reviews when new commits are pushed
   - ✔ Require status checks to pass: `secrets-scan`, `dependency-audit`, `sast`, `test`, `container-scan`
   - ✔ Require branches to be up to date before merging
   - ✔ Block force pushes
   - ✔ Restrict deletions
4. Save ruleset

### Secrets to Add in GitHub Actions

Go to: **Settings → Secrets and variables → Actions → New repository secret**

| Secret Name | Value | Notes |
|---|---|---|
| `CI_JWT_SECRET_KEY` | `openssl rand -hex 32` output | Used by test job only |
| `CI_REDIS_PASSWORD` | `openssl rand -hex 32` output | Used by test job Redis service |
| `SEMGREP_APP_TOKEN` | From semgrep.dev account | Optional; semgrep still runs without it |

### Remove .DS_Store from git history (if already committed)

```bash
# Check if tracked:
git ls-files --error-unmatch .DS_Store 2>/dev/null && echo 'TRACKED' || echo 'OK'

# If tracked, remove from index:
git rm -r --cached '*.DS_Store' && git commit -m 'fix(LOW-A): remove .DS_Store from tracking'
```

---

## Security Posture Snapshot

| Dimension | Before | After Ph.0+1 | Phase 2 Target |
|---|---|---|---|
| Auth token revocation | ❌ Broken (RuntimeError) | ✅ Async-native Redis | ✅ + JWKS rotation |
| Dev credential exposure | ❌ Silent fallback `"admin-dev-only"` | ✅ Hard fail at startup | ✅ DB-backed users |
| Image attack surface | ❌ +800 MB (Airflow) | ✅ API-only dependencies | ✅ Distroless runtime |
| Redis exposure | ❌ No auth, all interfaces | ✅ AUTH + loopback only | ✅ TLS + Vault rotation |
| Request DoS protection | ❌ No limit, no timeout | ✅ 1 MB + 30s | ✅ Per-user rate limits |
| Security headers | ⚠️ Partial, HSTS unconditional | ✅ OWASP compliant, env-aware | ✅ |
| Image supply chain | ⚠️ Floating tag | ✅ SHA-256 digest pinned | ✅ Sigstore cosign |
| CI secret hygiene | ❌ JWT secret echoed in logs | ✅ GitHub Actions secret | ✅ + OIDC token auth |
| CI SAST coverage | ⚠️ Bandit only | ✅ Bandit + mypy + semgrep | ✅ + CodeQL |
| Dependency CVE gate | ❌ `|| true` bypass | ✅ Hard gate on CRIT/HIGH | ✅ + Renovate auto-PR |
| Config management | ❌ `os.environ` scattered | ✅ Centralized pydantic-settings | ✅ + Vault injection |
| Password hashing | ⚠️ passlib (unmaintained) | ⚠️ bcrypt pinned | ✅ argon2-cffi |
| User store | ❌ In-memory dict | ❌ In-memory dict | ✅ PostgresUserRepository |
| Test isolation | ❌ `lru_cache` bleed | ✅ `cache_clear` autouse | ✅ |
| VCS hygiene | ❌ Merge conflicts, .DS_Store | ✅ Clean .gitignore | ✅ |
