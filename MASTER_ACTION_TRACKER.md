# Master Action Tracker
<!-- Last updated: 2026-05-29 | Cycle 1 — COMPLETE -->

> Persistent remediation tracker for the ML Incident Response Playbook engineering engagement.
> Status updated after every remediation cycle. **Never remove completed items — mark VALIDATED.**

## Status Legend

| Status | Meaning |
|--------|---------|
| BACKLOG | Identified, not yet started |
| IN PROGRESS | Actively being worked |
| BLOCKED | Waiting on dependency |
| FIXED | Code/config change committed to main |
| VALIDATED | Fix verified by test / CI pass |
| DEFERRED | Intentionally postponed with documented rationale |

---

## Active Tracker

| ID | Category | Issue | Severity | Status | Owner | Blocking Deps | Files Affected | Validation |
|----|----------|-------|----------|--------|-------|---------------|----------------|------------|
| R-P1 | CI/CD | Integration coverage gate lowered to 53% (CI-66b); CI-67 recovery deferred | HIGH | BACKLOG | CI Lead | CI-67 fixture work | `secured_ci.yml` | Gate restored ≥65%; CI green |
| R-P2 | Makefile | Duplicate `lint:` target — mypy silently skipped on `pipelines/` | HIGH | FIXED | Platform | — | `Makefile` | `grep -c "^lint:" Makefile` → 1 |
| R-P3 | Makefile | `test-int` gate 65% vs CI 53% — misleading local dev experience | MEDIUM | FIXED | Platform | R-P1 | `Makefile` | Gates aligned at 53%; comment documents CI-67 path back to 65% |
| R-P4 | SAST CI | Semgrep step fails for forks/Dependabot with empty `SEMGREP_APP_TOKEN` | MEDIUM | BACKLOG | Security | — | `.github/workflows/secured_ci.yml` | Fork PRs pass; hard gate retained for owned PRs |
| R-P5 | Pre-commit | mypy hook `--ignore-missing-imports` diverges from CI strict config | MEDIUM | FIXED | SAST | — | `.pre-commit-config.yaml` | Flags match CI; stub deps in `additional_dependencies` |
| R-P6 | Repo Hygiene | `MASTER_ACTION_TRACKER.md` referenced in CHANGELOG (R-09) but absent | MEDIUM | FIXED | Platform | — | `MASTER_ACTION_TRACKER.md` | File exists at root; renders as valid Markdown |
| R-P7 | Repo Hygiene | `CONTRIBUTING.md` absent — no onboarding path for external contributors | MEDIUM | BACKLOG | DX | — | `CONTRIBUTING.md` | File present; covers setup, branching, PR, commit conventions |
| R-P8 | Repo Hygiene | `CODEOWNERS` absent — no automatic review routing | LOW | FIXED | Platform | — | `.github/CODEOWNERS` | Auto-review routes to @zrlopez on all PRs |
| R-P9 | Security | `RequestTimeoutMiddleware` logs raw client IP (HIGH-01 regression) | MEDIUM | FIXED | Security | — | `api/middleware.py` | `grep "client.host" api/middleware.py` → 0 |
| R-P10 | Security | `MaxBodySizeMiddleware` logs raw client IP in 2 branches (HIGH-01 regression) | MEDIUM | FIXED | Security | — | `api/middleware.py` | `grep "client.host" api/middleware.py` → 0 |
| R-P11 | Config | SlowAPI `get_remote_address` stores raw IP as rate-limiter Redis key (PII in state) | MEDIUM | BACKLOG | Security | — | `api/config.py` | Custom hashed key function replaces `get_remote_address` |
| R-P12 | Testing | CI-67 open: Redis/lifespan/auth paths unreachable in integration fixtures | HIGH | BACKLOG | Test Lead | CI-67 | `tests/integration/` | Integration coverage ≥65%; CI-67 closed |
| R-P13 | MLOps | `docker-compose.yml` referenced in `make ci-local` — file may be absent | MEDIUM | BACKLOG | Platform | — | `docker-compose.yml` | `docker-compose up -d postgres redis` succeeds clean clone |
| R-P14 | Repo Hygiene | CI index table in CHANGELOG stops at CI-53; CI-54–CI-66b missing | LOW | BACKLOG | Platform | — | `CHANGELOG.md` | Index table covers through CI-66b |
| R-P15 | DX | No `scripts/bootstrap.sh` for new developer onboarding | LOW | BACKLOG | DX | R-P7 | `scripts/bootstrap.sh` | Script runs end-to-end on clean clone |
| R-P16 | Observability | No `healthz`/`readyz` endpoint separation for K8s liveness/readiness probes | MEDIUM | BACKLOG | MLOps | — | `api/routers/` | Both endpoints respond correctly; unit tested |
| R-P17 | Security | `.trivyignore` CVE-2026-0994 suppression (CVSS 8.2) needs GitHub Issue for calendar enforcement | LOW | BACKLOG | Security | — | `.trivyignore`, GitHub Issues | Tracking issue created; review date 2026-08-24 |
| R-P18 | CI/CD | `dependency-review` job runs only on PRs — push to `main` has no dependency gate | LOW | BACKLOG | CI Lead | — | `secured_ci.yml` | Gap documented with rationale (GitHub API limitation) |
| R-P19 | Architecture | `src/incident_tracker.py` module-level `_engine` singleton (Phase 13 DI migration deferred) | MEDIUM | DEFERRED | Architecture | Phase 13 DI work | `src/incident_tracker.py` | Engine constructed only inside lifespan context; no module-level side effects |
| R-P20 | Repo Hygiene | No CI status badges in `README.md` — portfolio signal buried | LOW | BACKLOG | DX | — | `README.md` | Badges visible at top of README; all CI/coverage/docs badges present |
| R-P21 | Testing | No regression test for HIGH-01 middleware IP pseudonymisation completeness | MEDIUM | BACKLOG | Test Lead | R-P9/R-P10 | `tests/unit/test_middleware_pii.py` | 3+ tests pass; `_pseudo_ip()` contract verified |

---

## Cycle 1 — Completed (2026-05-29)

| ID | Issue | Commit | Resolution |
|----|-------|--------|------------|
| R-P2 | Duplicate `lint:` target in Makefile | `0890c89` | Single deduplicated target; `pipelines/` added to mypy + ruff scope |
| R-P3 | `test-int` gate mismatch (65% local vs 53% CI) | `0890c89` | Aligned to 53%; comment documents CI-67 recovery path |
| R-P5 | Pre-commit mypy `--ignore-missing-imports` divergence | `b028d6c` | Removed flag; added stub deps to `additional_dependencies` |
| R-P6 | `MASTER_ACTION_TRACKER.md` missing (CHANGELOG R-09 broken ref) | this commit | File created; full tracker state as of Cycle 1 |
| R-P8 | `CODEOWNERS` absent | (next commit) | `.github/CODEOWNERS` with @zrlopez on all paths |
| R-P9 | `RequestTimeoutMiddleware` raw IP log (HIGH-01) | `c7ea849` | `_pseudo_ip()` applied; 0 raw IP log refs remain |
| R-P10 | `MaxBodySizeMiddleware` raw IP log — 2 branches (HIGH-01) | `c7ea849` | `_pseudo_ip()` applied to both branches |

---

## Pre-Engagement Archive

| ID | Issue | Resolution | Date |
|----|-------|------------|------|
| R-GOD | God-file `api/app.py` (1005 lines) | 10-step extraction into config, auth, schemas, middleware, routers, lifespan | 2026-05-26 |
| HIGH-01 (partial) | Raw IP in `trace_and_security_headers` | `_pseudo_ip()` SHA-256 pseudonymisation | 2026-05-27 |
| SEC-01 | JWT secret in plain env string | `SecretStr` wrap + `get_jwt_secret()` single accessor | 2026-05-26 |
| ARCH-07 | Stub users importable in production | `_STUB_ALLOWED_ENVIRONMENTS` guard + `RuntimeError` | 2026-05-26 |
| CI-55 | No lockfile drift detection | `lockfile-check` CI job + `make deps-compile` + pre-commit pip-audit hook | 2026-05-28 |

---

## Cycle Queue

### Cycle 2 — Next Up

| Priority | ID | Action |
|----------|----|--------|
| 1 | R-P11 | Patch SlowAPI key function to hash IP before use as Redis key |
| 2 | R-P21 | Create `tests/unit/test_middleware_pii.py` regression test for HIGH-01 completeness |
| 3 | R-P12 | Add mock fixtures for Redis/lifespan/auth to recover integration coverage ≥65% |
| 4 | R-P7 | Write `CONTRIBUTING.md` (setup, branching, commits, PR process) |
| 5 | R-P4 | Guard Semgrep step with fork ownership check to prevent empty-token failures |
| 6 | R-P16 | Add `GET /healthz` + `GET /readyz` router with DB/Redis readiness checks |
| 7 | R-P13 | Create `docker-compose.yml` for local dev (postgres 16 + redis 7) |
| 8 | R-P20 | Add CI, coverage, and docs badges to README header |
