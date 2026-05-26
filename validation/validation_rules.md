# Validation Rules

> **Purpose:** This document catalogues every data validation rule enforced
> across the ML Incident Response platform. It is the single source of truth
> for what constitutes a valid record at each layer: API payloads, feature
> batches, and pipeline outputs.
>
> The programmatic implementations of these rules live in:
> - API layer: `api/models.py` (Pydantic schemas)
> - Batch / unit layer: `validation/schema_checks.py`
> - Data layer: Great Expectations checkpoints (run by the Airflow DAG)
>
> **Enforcement status legend**
> - ✅ **ENFORCED** — implemented in code, covered by tests
> - ⚠️ **PARTIAL** — rule exists in code but test coverage is incomplete
> - 🔲 **PLANNED** — documented here, implementation pending

---

## 1. Incident Record Rules

Applied on every `POST /incidents` request and on batch imports.

| Field | Rule | Error / Warning | Status |
|-------|------|-----------------|--------|
| `incident_id` | Must match pattern `INC-[0-9]{4,}` | Warning: `incident_id does not match expected pattern` | ✅ |
| `title` | Non-empty string, ≤ 200 characters | Error: `title must not be empty` / `title exceeds 200 characters` | ✅ |
| `severity` | One of: `SEV-1`, `SEV-2`, `SEV-3`, `SEV-4` | Error: `Unsupported severity` | ✅ |
| `category` | One of: `api`, `data_quality`, `model_drift`, `cost_spike`, `pipeline_failure`, `security` | Error: `Unsupported category` | ✅ |
| `status` | One of: `OPEN`, `INVESTIGATING`, `MITIGATING`, `RESOLVED`, `CLOSED` | Error: `Unsupported status` | ✅ |
| `summary` | Non-empty string, ≤ 2000 characters | Error: `summary must not be empty` / `summary exceeds 2000 characters` | ✅ |
| `created_at` | ISO 8601 timestamp with UTC offset | Warning: `should be ISO 8601 with UTC offset` | ✅ |
| `resolved_at` | ISO 8601; must be `>= created_at` | Error: `resolved_at cannot precede created_at` | ✅ |
| `updated_at` | ISO 8601; must be `>= created_at` | Error: `updated_at cannot precede created_at` | ✅ |
| `acknowledged_at` | ISO 8601 format check (if present) | Warning: format violation | ✅ |

**Batch enforcement:** `validation/schema_checks.py` → `validate_incident_record()` → `ValidationResult(valid=False)`.
**API enforcement:** `api/models.py` (Pydantic) → HTTP 422 on violation.

---

## 2. Feature Batch Rules

Applied by `validate_feature_batch_record()` and the Great Expectations checkpoint
before a feature batch is written to the feature store.

| Check | Threshold | Action on failure | Status |
|-------|-----------|-------------------|--------|
| Row count ≥ `FEATURE_BATCH_MIN_ROW_COUNT` (1) | Configurable | Error; quarantine batch | ✅ |
| Null rate per required feature column | ≤ 5% | Error; quarantine batch; open SEV-2 | ✅ |
| PSI per feature vs training baseline | < 0.20 | Error; open SEV-2 drift incident | ✅ |
| `schema_fingerprint` present and non-empty | Non-empty string | Warning; schema drift undetectable | ✅ |
| `psi_scores` dict non-empty | At least 1 entry | Warning; drift cannot be computed | ✅ |
| No new unexpected columns | Exact schema match | Quarantine batch; open SEV-3 | 🔲 (GE suite) |
| Column type unchanged vs schema | Exact type match | Quarantine batch; open SEV-2 | 🔲 (GE suite) |

**Programmatic enforcement:** `validation/schema_checks.py` → `validate_feature_batch_record()`.
**Pipeline enforcement:** Great Expectations suite `daily_feature_validation`,
scheduled via `orchestration/ml_incident_dag.py`.

---

## 3. API Response Rules

All API responses are validated by Pydantic before serialisation.

| Field | Rule | Status |
|-------|------|--------|
| Pagination `page` | Integer ≥ 1 | ✅ |
| Pagination `page_size` | Integer 1–100; default 20 | ✅ |
| Cursor `before_id` | Positive integer or null | ✅ |
| Datetime fields | Serialised as ISO 8601 with UTC offset | ✅ |
| Enum fields | Always serialised as string values, never integers | ✅ |

---

## 4. Token / Auth Rules

Applied on every authenticated request in `api/auth.py`.

| Rule | Behaviour on violation | Status |
|------|------------------------|--------|
| JWT signature valid (RS256 in prod, HS256 in dev/CI) | HTTP 401 `invalid_token` | ✅ |
| JWT `exp` claim not expired | HTTP 401 `token_expired` | ✅ |
| JWT `jti` not in Redis denylist | HTTP 401 `token_revoked` + counter increment | ✅ |
| `sub` claim present | HTTP 401 `missing_subject` | ✅ |
| `/auth/refresh` rate limit ≤ 5/min | HTTP 429 | ✅ |
| Brute-force counter per user (Redis INCR, 60s TTL) | HTTP 429 after threshold | ✅ |

---

## 5. State Machine Transitions

Enforced by `validate_state_transition(current_state, next_state)` in `validation/schema_checks.py`.
Any transition not in this table is an error.

| From | Allowed Next States | Notes |
|------|--------------------|---------|
| `OPEN` | `INVESTIGATING` | Acknowledgement required before investigation |
| `INVESTIGATING` | `MITIGATING`, `RESOLVED` | Direct to RESOLVED if no mitigation phase needed |
| `MITIGATING` | `RESOLVED` | Mitigation must complete before resolution |
| `RESOLVED` | `CLOSED` | PIR must be completed before CLOSED (policy, not enforced in code) |
| `CLOSED` | _(none)_ | Terminal state |

---

## 6. Batch Validation Helper

`validate_batch(records)` in `validation/schema_checks.py` runs
`validate_incident_record()` across a list of records and tags each
`ValidationResult` with a `context` string (e.g. `record[3]`) for
traceability. Use this in ETL pipelines and integration tests for the
cursor pagination routes (OPEN-02).

```python
from validation.schema_checks import validate_batch

results = validate_batch(incident_list)
failed = [r for r in results if not r.valid]
for r in failed:
    print(r.context, r.errors)
```

---

## 7. Adding a New Rule

1. Document the rule in the appropriate section above with enforcement status.
2. Implement it in `api/models.py` (API layer) or `validation/schema_checks.py`
   (batch/unit layer).
3. Add a test in `tests/unit/test_validation.py` covering both the passing
   and failing case.
4. If the rule produces a new metric (e.g. a failure counter), register it
   in `observability/alert_rules.yml`.
5. Update the enforcement status column in this document to ✅ ENFORCED.
