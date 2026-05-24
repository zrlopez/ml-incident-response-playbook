# Validation Rules

> **Purpose:** This document catalogues every data validation rule enforced
> across the ML Incident Response platform. It is the single source of truth
> for what constitutes a valid record at each layer: API payloads, feature
> batches, and pipeline outputs.
>
> The programmatic implementations of these rules live in:
> - API layer: `api/models.py` (Pydantic schemas)
> - Batch layer: `validation/schema_checks.py`
> - Data layer: Great Expectations checkpoints (run by the Airflow DAG)

---

## 1. Incident Record Rules

Applied on every `POST /incidents` request and on batch imports.

| Field | Rule | Error message |
|-------|------|---------------|
| `incident_id` | Must match pattern `INC-[0-9]{4,}` | `incident_id must use INC- prefix` |
| `title` | Non-empty string, max 200 characters | `title is required and must be under 200 chars` |
| `severity` | One of: `SEV-1`, `SEV-2`, `SEV-3`, `SEV-4` | `Unsupported severity` |
| `category` | One of: `api`, `data-quality`, `model-drift`, `cost-spike`, `pipeline-failure`, `security` | `Unsupported category` |
| `status` | One of: `open`, `triaged`, `mitigated`, `resolved`, `closed` | `Unsupported status` |
| `summary` | Non-empty string, max 2000 characters | `summary is required` |
| `created_at` | ISO 8601 timestamp with timezone | `created_at must be ISO 8601` |
| `resolved_at` | ISO 8601 timestamp; must be >= `created_at` | `resolved_at cannot precede created_at` |

**Enforcement:** `api/models.py` (Pydantic) → HTTP 422 on violation.
Batch enforcement: `validation/schema_checks.py` → `ValidationResult(valid=False)`.

---

## 2. Feature Batch Rules

Applied by the Great Expectations checkpoint before the feature batch is
written to the feature store.

| Check | Threshold | Action on failure |
|-------|-----------|-------------------|
| Row count >= prior-day average × 0.7 | Configurable | Quarantine batch; open SEV-2 |
| Null rate per required feature column | ≤ 5 % | Quarantine batch; open SEV-2 |
| No new unexpected columns | Exact schema match | Quarantine batch; open SEV-3 |
| Column type unchanged vs schema | Exact type match | Quarantine batch; open SEV-2 |
| PSI per feature vs training baseline | < 0.20 | Open SEV-2 if exceeded |

**Enforcement:** Great Expectations suite `daily_feature_validation`,
scheduled via `orchestration/ml_incident_dag.py`.

---

## 3. API Response Rules

All API responses are validated by Pydantic before serialisation.

| Field | Rule |
|-------|------|
| Pagination `page` | Integer ≥ 1 |
| Pagination `page_size` | Integer 1–100; default 20 |
| Datetime fields | Serialised as ISO 8601 with UTC offset |
| Enum fields | Always serialised as string values, never integers |

---

## 4. Token / Auth Rules

Applied on every authenticated request in `api/auth.py`.

| Rule | Behaviour on violation |
|------|------------------------|
| JWT signature valid | HTTP 401 `invalid_token` |
| JWT `exp` claim not expired | HTTP 401 `token_expired` |
| JWT `jti` not in Redis denylist | HTTP 401 `token_revoked` + counter increment |
| `sub` claim present | HTTP 401 `missing_subject` |

---

## 5. Adding a New Rule

1. Document the rule in the appropriate section of this file.
2. Implement it in `api/models.py` (for API layer) or `validation/schema_checks.py`
   (for batch layer).
3. Add a test in `tests/test_validation.py` that covers both the passing and
   failing case.
4. If the rule produces a new metric (e.g. a counter for failures), register
   it in `monitoring/metrics.md`.
