# MASTER ACTION TRACKER
> **Living document** — updated at the close of every remediation cycle.
> Do NOT edit manually between cycles; let the remediation agent maintain it.

---

## Repository Anchor

| Item | Value |
|---|---|
| Repo | `zrlopez/ml-incident-response-playbook` |
| HEAD after Cycle 1 | `970e37fa1a1c41e44040a1636e30725d5d6ba4c6` |
| HEAD after Tracker creation | _(this commit)_ |
| Branch | `main` |
| Last updated | 2026-05-27 20:01 CDT |

---

## ✅ Completed Fixes (Cycle 1)

| ID | File(s) | What was done | Commit |
|---|---|---|---|
| **SEC-01** | `src/config.py`, `api/config.py`, `src/auth/tokens.py` | `jwt_secret_key` promoted from `str` → `SecretStr`. Raw env string deleted immediately after wrap (`del _raw_jwt_secret`). `get_jwt_secret()` helper is the single authorised unwrap point. All three `JWT_SECRET` call-sites in `tokens.py` replaced with `get_jwt_secret()`. | `0f7a4dd` |
| **SEC-02** | `api/stub_users.py` | Env-guard at import already correct (`ENVIRONMENT not in {development, test}` → `RuntimeError`). No code change needed. Defence-in-depth covered by SEC-03. | n/a |
| **SEC-03** | `.dockerignore` | `api/stub_users.py` excluded from Docker build context. The file is stripped before `COPY --chown=appuser:appgroup api/ ./api/` runs — runtime image cannot import it. | `970e37f` |
| **SEC-04** | `docker-compose.yml`, `docker-compose.override.yml` (new) | `command: uvicorn … --reload` removed from `docker-compose.yml`; moved to `docker-compose.override.yml`. Dockerfile `CMD` is now authoritative for all non-local-dev environments. | `970e37f` |
| **INFRA-05** | `Makefile` | Seven `docker/*` targets added (`build`, `up`, `up-prod`, `down`, `logs`, `shell`, `clean-volumes`). No existing target modified. | `970e37f` |

---

## 🚨 Active Blocker (must fix before Cycle 2 continues)

| ID | Severity | File | Issue | Fix required |
|---|---|---|---|---|
| **BLOCKER-01** | CRITICAL | `api/lifespan.py` | Unconditional `from api.stub_users import _USERS` on the `else` branch (non-Postgres path). Since `api/stub_users.py` is now excluded from the Docker image via `.dockerignore`, the container will raise `ModuleNotFoundError` at startup whenever `DATABASE_URL` is not a Postgres URL. | Wrap import in `try/except ImportError` OR move the stub import inside the `else` block with a graceful fallback message. **This is the first task of Cycle 2.** |

---

## ⚠️ Remaining Open Items (not yet started)

### CRITICAL
| ID | File | Issue |
|---|---|---|
| CRIT-01 | `api/dependencies.py` | Verify no direct `JWT_SECRET` attribute access remains after SEC-01 migration (grep needed). |
| CRIT-02 | `api/app.py` | Confirm `stub_users` is not imported at module level (would break on Docker even with BLOCKER-01 fix in lifespan). |

### HIGH
| ID | File | Issue |
|---|---|---|
| HIGH-01 | `api/middleware.py` | Review for any raw secret or credential logging. |
| HIGH-02 | `api/redis_denylist.py` | Confirm REDIS_PASSWORD not logged in plain text on connection errors. |
| HIGH-03 | `api/gdpr_routes.py` | Audit for PII in structured log fields. |
| HIGH-04 | `.env.example` | Confirm JWT_SECRET_KEY placeholder is clearly marked as “replace before use” and is long enough to pass SecretStr validator (ENVIRONMENT != test). |

### MEDIUM
| ID | File | Issue |
|---|---|---|
| MED-01 | `Dockerfile` | FROM digest-pin still deferred (R-06 TODO inline). Needs verified sha256 digest substitution. |
| MED-02 | `api/rate_limit.py` | Verify rate-limit keys cannot be bypassed via X-Forwarded-For spoofing (get_remote_address vs trusted proxy headers). |
| MED-03 | `src/auth/key_store.py` | `KeyRotationStore.from_env()` — confirm it does not read `RSA_PRIVATE_KEY_PEM` as plain str in logs. |
| MED-04 | `docker-compose.prod.yml` | Already exists. Audit it doesn’t re-introduce `command:` override or mount `.:/app`. |

### LOW
| ID | File | Issue |
|---|---|---|
| LOW-01 | `src/config.py` | `slack_webhook_url` is a plain `str`; should be `SecretStr` if it contains a secret token. |
| LOW-02 | `pyproject.toml` | Confirm ruff rules include `S` (bandit), `ERA`, `G` (logging-format) for ongoing secret-safety lint coverage. |

---

## 🔗 Unresolved Dependencies / Coupling Risks

| Dependency | Risk | Status |
|---|---|---|
| `api/lifespan.py` → `api/stub_users.py` | Import will `ModuleNotFoundError` in Docker since stub excluded from image. **BLOCKER-01.** | 🚨 Open |
| `api/config.py:JWT_SECRET` (SecretStr) → all consumers | Any module doing `from api.config import JWT_SECRET` and then using it as a raw string will get a `SecretStr` object, not a `str`. `str(JWT_SECRET)` returns `'**********'` not the value. | ✅ Fixed for `tokens.py`; CRIT-01 audits remaining consumers |
| `src/config.py:Settings.jwt_algorithm` Literal restriction | Previously accepted RS256; now restricted to `HS256/384/512`. `jwt_rs256.py` manages RS256 keys independently — no regression. `api/config.py:JWT_ALGORITHM` still accepts RS256 via its own allowlist. Potential drift. | ⚠️ Monitor |
| `docker-compose.override.yml` auto-merge | Docker Compose automatically merges on `docker compose up`. CI must call `docker compose -f docker-compose.yml up` explicitly to avoid the override. Must be enforced in `.github/workflows`. | ⚠️ Needs CI audit |

---

## ⚠️ Risky Unfinished Refactors

| Refactor | Risk | Notes |
|---|---|---|
| R-GOD (God-object extraction from `api/app.py`) | Steps 1–6 of 8 appear complete (config, stub_users, tokens, dependencies, redis_denylist, lifespan). Steps 7–8 unknown. Any revert or partial state risks broken imports. | Audit `api/app.py` in Cycle 2. |
| RS256 / HS256 dual-path in `tokens.py` | The HS256 fallback path uses `JWT_ALGORITHM` from `api/config.py` which can be RS256 — but `tokens.py` passes it to `jwt.encode()` with a raw string secret. This is a latent algorithm-confusion risk if someone sets `JWT_ALGORITHM=RS256` without loading RSA keys. | Medium risk; document and gate in Cycle 2. |
| `src/config.py` `jwt_algorithm` Literal vs `api/config.py` `JWT_ALGORITHM` string | Two parallel algorithm configs. Drift between them is possible. | Needs consolidation in a future cycle. |

---

## ✅ Architectural Consistency Checks (post Cycle 1)

| Check | Result |
|---|---|
| `tokens.py` only accesses JWT secret via `get_jwt_secret()` | ✅ Confirmed |
| `api/config.py` exposes no raw secret string after import | ✅ Confirmed (`del _raw_jwt_secret` executes at module load) |
| `src/config.py` `SecretStr` validator accepts test placeholder | ✅ Confirmed (`ENVIRONMENT=test` branch returns placeholder) |
| `stub_users.py` absent from Docker image | ✅ Confirmed (`.dockerignore` entry added) |
| `docker-compose.yml` `command:` no longer present | ✅ Confirmed |
| `docker-compose.override.yml` contains only dev overrides, no secrets | ✅ Confirmed |
| Makefile existing targets unmodified | ✅ Confirmed (additive only) |
| `api/lifespan.py` stub import guard present | ❌ **MISSING** — unconditional import is BLOCKER-01 |

---

## Cycle Log

| Cycle | Commits | Items closed | Items opened | Notes |
|---|---|---|---|---|
| 0 (pre-work) | prior to `0f7a4dd` | — | All items above not marked ✅ | Baseline state |
| 1 | `0f7a4dd`, `970e37f` | SEC-01, SEC-02, SEC-03, SEC-04, INFRA-05 | BLOCKER-01, CRIT-01, CRIT-02 | lifespan stub import risk surfaced |
| 2 | _(pending)_ | BLOCKER-01, CRIT-01, CRIT-02 (minimum) | TBD | Next cycle |
