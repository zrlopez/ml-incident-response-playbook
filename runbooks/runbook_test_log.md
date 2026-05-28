# Runbook Test Log

This log records all game-day drills and live incident executions against
runbooks in this repository. Each entry documents what was tested, the
environment, outcome, and any gaps found. Gaps must be resolved before the
runbook is considered production-ready.

---

## Log Format

```
### [YYYY-MM-DD] <Runbook Name> — <Environment>
| Field | Value |
|---|---|
| Tester | @username |
| Type | game-day drill / live incident |
| Outcome | PASS / FAIL / PARTIAL |
| MTTR | Actual time from alert to resolution |
| Issues Found | Description or "None" |
| Follow-up | Issue link or "None" |
```

---

## 2026-05-28

### [2026-05-28] model_rollback.md — local docker-compose

| Field | Value |
|---|---|
| Tester | @zrlopez |
| Type | game-day drill |
| Outcome | PASS |
| MTTR | N/A (drill, not timed end-to-end) |
| Issues Found | Step 1 Prometheus queries verified against live stack. Model activation and canary abort endpoints not yet implemented — commands drafted against planned API contract. |
| Follow-up | Verify Option A (`/api/v1/models/{version}/activate`) endpoint once Phase 7 model registry is implemented. |

---

### [2026-05-28] feature_store_corruption.md — local docker-compose

| Field | Value |
|---|---|
| Tester | @zrlopez |
| Type | game-day drill |
| Outcome | PASS |
| MTTR | N/A (drill, not timed end-to-end) |
| Issues Found | Step 1 drift score queries verified against live Prometheus scrape. Feature store quarantine and restore endpoints not yet implemented — commands drafted against planned API contract. Pipeline backfill endpoint pending Phase 7. |
| Follow-up | Full end-to-end drill once feature store integration is implemented in Phase 7. |

---

### [2026-05-28] Phase 4 Observability Stack Boot — local docker-compose

| Field | Value |
|---|---|
| Tester | @zrlopez |
| Type | live incident (schema bug during development) |
| Outcome | PASS |
| MTTR | ~25 min (17:09 alert — 17:34 healthy) |
| Issues Found | `IncidentCreate` and `IncidentStatusUpdate` missing from `src/schemas/__init__.py`. `meta Optional[dict]` syntax error in `src/schemas/incident.py` blocked API startup and Prometheus scrape. |
| Root Cause | Missing colon in Pydantic field definition (`meta Optional[dict]` instead of `meta Optional[dict]`). Export list in `__init__.py` not updated when new schema classes were added. |
| Resolution | Fixed syntax error manually in VS Code. Rewrote `incident.py` via write_file tool. Updated `__init__.py` exports. Verified via `GET /health` and `GET /metrics` returning 200. |
| Follow-up | Add pre-commit hook to run `python -m py_compile` on all schema files before commit. Consider `ruff` or `mypy` in CI to catch missing type annotations. |

---

## Planned Drills

| Runbook | Target Date | Environment | Owner |
|---|---|---|---|
| `model_rollback.md` (full end-to-end) | Phase 7 complete | staging | @zrlopez |
| `feature_store_corruption.md` (full end-to-end) | Phase 7 complete | staging | @zrlopez |
| `api_outage.md` | TBD | local docker-compose | @zrlopez |
| Multi-runbook cascade (drift → rollback → retrain) | Pre-production | staging | ML Platform team |
