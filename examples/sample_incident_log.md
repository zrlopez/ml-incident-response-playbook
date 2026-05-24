# Sample Incident Log

> **Purpose:** This document is a fully worked example of what an incident log
> entry looks like from detection through resolution. It is intended for
> onboarding, documentation reference, and dashboard demo data — not for
> production analytics.
>
> All names, timestamps, and values are synthetic.

---

## Incident Summary

| Field | Value |
|-------|-------|
| **Incident ID** | INC-0047 |
| **Title** | Null spike in upstream feature feed degrading churn model predictions |
| **Severity** | SEV-2 |
| **Category** | data-quality |
| **Status** | Resolved |
| **Opened** | 2026-04-14T09:12:00 CDT |
| **Mitigated** | 2026-04-14T09:58:00 CDT |
| **Resolved** | 2026-04-14T11:30:00 CDT |
| **Time to Detect** | 7 minutes |
| **Time to Mitigate** | 46 minutes |
| **Time to Resolve** | 138 minutes |
| **Incident Commander** | @ml-platform-oncall |
| **Team** | data-eng + ml-platform |

---

## Detection

- **09:05 CDT** — Great Expectations checkpoint `daily_feature_validation` failed
  on the `user_engagement_features` table. Null rate for column `session_duration_s`
  jumped from 0.3 % (baseline) to 41 % in the 08:00 batch.
- **09:07 CDT** — Prometheus alert `FeatureNullRateHigh` fired.
  `ml_feature_null_rate{feature="session_duration_s"} = 0.41 > threshold 0.05`.
- **09:12 CDT** — PagerDuty page sent to data-eng on-call.
  Incident INC-0047 opened in tracker.

---

## Triage

- **09:14 CDT** — On-call engineer acknowledged page.
- **09:18 CDT** — Confirmed null spike is isolated to the `session_duration_s`
  column. All other features in the batch are within tolerance.
- **09:22 CDT** — Traced to an upstream ETL job `clickstream_agg_hourly`.
  Job completed successfully (exit 0) but the source table `raw_clickstream`
  was missing rows for the 07:00–08:00 window due to a Kafka consumer lag event.
- **09:30 CDT** — Churn model still serving predictions. PSI score for
  `session_duration_s` calculated at 0.27 (threshold 0.20). SEV-2 confirmed.

---

## Mitigation

- **09:38 CDT** — Suspended live scoring for the churn model to prevent
  degraded predictions reaching the product surface. Fallback logic activated
  (last-known-good score served from cache).
- **09:45 CDT** — Affected batch quarantined to `dead_letter.feature_batches`
  table, tagged with `incident_id = INC-0047`.
- **09:58 CDT** — Kafka consumer lag resolved by restarting the consumer group
  `clickstream-prod-consumer`. Raw data backfilled for the missing window.
- **09:58 CDT** — Mitigation confirmed. Stakeholder update sent to `#ml-incidents`.

---

## Resolution

- **10:15 CDT** — Reran `clickstream_agg_hourly` for the 07:00–08:00 window.
  Null rate for `session_duration_s` returned to 0.2 % (within baseline).
- **10:40 CDT** — Great Expectations checkpoint re-run passed all assertions.
- **11:10 CDT** — Churn model scoring resumed. Predictions verified against
  held-out set — no statistically significant accuracy degradation detected.
- **11:30 CDT** — Incident resolved. All-clear sent to stakeholders.

---

## Impact Assessment

- **User-facing impact:** Minimal. Fallback cache served stale but reasonable
  churn scores for 72 minutes. No user-visible errors.
- **Model impact:** 72 minutes of scoring suppressed. No training data corrupted.
- **Data impact:** 1 batch (08:00 window) quarantined and later replayed cleanly.

---

## Root Cause

Kafka consumer group `clickstream-prod-consumer` fell behind due to an upstream
producer burst at 07:55 CDT. The `clickstream_agg_hourly` ETL job executed on
schedule at 08:00 CDT before the consumer caught up, resulting in an incomplete
read of the `raw_clickstream` table for that hour window.

---

## Action Items

| # | Action | Owner | Due | Status |
|---|--------|-------|-----|--------|
| 1 | Add Kafka consumer lag alert to Prometheus rule set | @data-eng | 2026-04-21 | Open |
| 2 | Update `clickstream_agg_hourly` to check consumer offset before proceeding | @data-eng | 2026-04-28 | Open |
| 3 | Add `session_duration_s` null-rate check to Great Expectations baseline suite | @ml-platform | 2026-04-21 | Open |
| 4 | Document fallback cache TTL in `runbooks/data_quality_incident.md` | @ml-platform | 2026-04-21 | Open |
