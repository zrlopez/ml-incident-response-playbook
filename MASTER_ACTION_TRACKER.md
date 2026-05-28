# MASTER ACTION TRACKER
> **Living document** — updated at the close of every remediation cycle.
> Do NOT edit manually between cycles; let the remediation agent maintain it.

---

## Repository Anchor

| Item | Value |
|---|---|
| Repo | `zrlopez/ml-incident-response-playbook` |
| HEAD after Cycle 1 | `970e37fa1a1c41e44040a1636e30725d5d6ba4c6` |
| HEAD after Cycle 2 | _(this commit)_ |
| Branch | `main` |
| Last updated | 2026-05-27 20:01 CDT |

---

## ✅ Completed Fixes (all cycles)

| ID | File(s) | What was done | Cycle | Commit |
|---|---|---|---|---|
| **SEC-01** | `src/config.py`, `api/config.py`, `src/auth/tokens.py` | `jwt_secret_key` promoted from `str` → `SecretStr`. `del _raw_jwt_secret` after wrap. `get_jwt_secret()` is sole unwrap point. All three `JWT_SECRET` usages in `tokens.py` replaced. | 1 | `0f7a4dd` |
| **SEC-02** | `api/stub_users.py` | Env-guard confirmed correct. No code change needed. Defence-in-depth via SEC-03. | 1 | n/a |
| **SEC-03** | `.dockerignore` | `api/stub_users.py` excluded from Docker build context. | 1 | `970e37f` |
| **SEC-04** | `docker-compose.yml`, `docker-compose.override.yml` | `command:` override extracted to override file. Dockerfile CMD is authoritative. | 1 | `970e37f` |
| **INFRA-05** | `Makefile` | Seven `docker/*` targets added. No existing target modified. | 1 | `970e37f` |
| **BLOCKER-01** | `api/lifespan.py` | Unconditional `from api.stub_users import _USERS` wrapped in `try/except ImportError`. Production fast-fail with clear `RuntimeError` if stub absent and DATABASE_URL is not Postgres. | 2 | _(this commit)_ |
| **CRIT-01** | `api/config.py`, all consumers | Full grep audit confirms only `tokens.py` consumed `JWT_SECRET`; that usage replaced in Cycle 1. No remaining raw-string consumers. Closed as confirmed-clean. | 2 | n/a |
| **CRIT-02** | `api/app.py` | Confirmed: no `stub_users` import at module level. Closed as confirmed-clean. | 2 | n/a |

---

## 🚨 Active Blockers

_None_ — BLOCKER-01 resolved this cycle.

---

## ⚠️ Remaining Open Items

### HIGH
| ID | File | Issue |
|---|---|---|
| HIGH-01 | `api/middleware.py` | Review for any raw secret or credential logging. |
| HIGH-02 | `api/redis_denylist.py` | Confirm REDIS_PASSWORD not logged in plain text on connection errors. |
| HIGH-03 | `api/gdpr_routes.py` | Audit for PII in structured log fields. |
| HIGH-04 | `.env.example` | Confirm JWT_SECRET_KEY placeholder is clearly marked and long enough to pass SecretStr validator outside test env. |

### MEDIUM
| ID | File | Issue |
|---|---|---|
| MED-01 | `Dockerfile` | FROM digest-pin still deferred (R-06 TODO). Needs verified sha256 digest substitution. |
| MED-02 | `api/rate_limit.py` | Verify rate-limit keys cannot be bypassed via X-Forwarded-For spoofing. |
| MED-03 | `src/auth/key_store.py` | Confirm RSA_PRIVATE_KEY_PEM not logged plain. |
| MED-04 | `docker-compose.prod.yml` | Audit for `command:` override or `.:/app` mount. |

### LOW
| ID | File | Issue |
|---|---|---|
| LOW-01 | `src/config.py` | `slack_webhook_url` should be `SecretStr`. |
| LOW-02 | `pyproject.toml` | Confirm ruff rules include `S`, `ERA`, `G` for ongoing secret-safety coverage. |

---

## 🔗 Unresolved Dependencies / Coupling Risks

| Dependency | Risk | Status |
|---|---|---|
| `src/config.py:jwt_algorithm` Literal (HS only) vs `api/config.py:JWT_ALGORITHM` (RS allowed) | Drift between two algorithm configs. `jwt_rs256.py` manages RS256 independently — no regression, but could confuse future contributors. | ⚠️ Monitor |
| `docker-compose.override.yml` auto-merge | CI must call `docker compose -f docker-compose.yml up` explicitly. Needs audit of `.github/workflows`. | ⚠️ Needs CI audit |
| HS256 fallback path in `tokens.py` + `JWT_ALGORITHM=RS256` | If operator sets `JWT_ALGORITHM=RS256` without loading RSA keys, HS256 path will call `jwt.encode(payload, get_jwt_secret(), algorithm="RS256")` — PyJWT will raise at runtime, not startup. Add explicit guard. | ⚠️ Cycle 3 candidate |

---

## ⚠️ Risky Unfinished Refactors

| Refactor | Risk | Notes |
|---|---|---|
| R-GOD (God-object extraction) | Steps 1–6 of 8 complete. Steps 7–8 unknown scope. | Audit in Cycle 3. |
| RS256/HS256 dual-path algorithm confusion | Latent misconfiguration risk. | Cycle 3 candidate. |
| `src/config.py` vs `api/config.py` algorithm drift | Two parallel configs. | Consolidation in a future cycle. |

---

## ✅ Architectural Consistency Checks (post Cycle 2)

| Check | Result |
|---|---|
| `tokens.py` only accesses JWT secret via `get_jwt_secret()` | ✅ |
| `api/config.py` exposes no raw secret string after import | ✅ |
| `src/config.py` `SecretStr` validator accepts test placeholder | ✅ |
| `stub_users.py` absent from Docker image | ✅ |
| `docker-compose.yml` `command:` no longer present | ✅ |
| `docker-compose.override.yml` contains only dev overrides, no secrets | ✅ |
| Makefile existing targets unmodified | ✅ |
| `api/app.py` has no module-level stub import | ✅ |
| `api/lifespan.py` stub import guarded with try/except ImportError | ✅ |
| No active startup-blocking regressions | ✅ |

---

## Cycle Log

| Cycle | Commits | Items closed | Items opened | Notes |
|---|---|---|---|---|
| 0 (pre-work) | prior to `0f7a4dd` | — | All un-✅ items | Baseline state |
| 1 | `0f7a4dd`, `970e37f` | SEC-01–04, INFRA-05 | BLOCKER-01, CRIT-01, CRIT-02 | lifespan stub import risk surfaced |
| 2 | `a5c9f9e` (tracker), _(this commit)_ | BLOCKER-01, CRIT-01, CRIT-02 | none critical | All blockers cleared; HIGH-01–04 next |
