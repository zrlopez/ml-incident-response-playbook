# Sample Postmortem — INC-0047

> **Purpose:** This is a fully worked example of a postmortem document.
> It accompanies the incident log in `examples/sample_incident_log.md` and
> demonstrates the expected depth and structure for all SEV-1 and SEV-2
> postmortems. All names, timestamps, and values are synthetic.
>
> **Template source:** `templates/postmortem_template.md`

---

## Incident Metadata

| Field | Value |
|-------|-------|
| **Incident ID** | INC-0047 |
| **Severity** | SEV-2 |
| **Category** | data-quality |
| **Duration** | 2 hours 18 minutes |
| **Postmortem Author** | @ml-platform-oncall |
| **Review Date** | 2026-04-16 |
| **Postmortem Status** | Final |

---

## Executive Summary

On 2026-04-14 at 09:05 CDT, a Kafka consumer lag event caused the hourly
clickstream aggregation job to read an incomplete snapshot of raw data. The
resulting feature batch contained a 41 % null rate in `session_duration_s`,
far above the 5 % alert threshold. The churn model was suppressed and fallback
cache scores were served for 72 minutes. No users experienced visible errors
and no training data was corrupted. Root cause was identified and mitigated
within 46 minutes of detection.

---

## Timeline

| Time (CDT) | Event |
|------------|-------|
| 07:55 | Producer burst hits `raw_clickstream` Kafka topic |
| 08:00 | `clickstream_agg_hourly` runs; consumer lag causes incomplete read |
| 09:05 | Great Expectations checkpoint fails; null rate = 41 % |
| 09:07 | Prometheus alert `FeatureNullRateHigh` fires |
| 09:12 | PagerDuty page sent; INC-0047 opened |
| 09:14 | On-call engineer acknowledges |
| 09:38 | Churn model scoring suspended; fallback cache activated |
| 09:45 | Affected batch quarantined |
| 09:58 | Kafka consumer lag resolved; raw data backfilled |
| 10:15 | `clickstream_agg_hourly` replayed successfully |
| 10:40 | Great Expectations re-run passes |
| 11:10 | Churn model scoring resumed |
| 11:30 | Incident resolved; all-clear sent |

---

## Root Cause Analysis

**Primary cause:** The `clickstream_agg_hourly` ETL job does not verify that
the Kafka consumer offset has caught up before reading from `raw_clickstream`.
When the consumer lagged behind a producer burst, the job executed on schedule
and read a partial table, producing a corrupted feature batch.

**Contributing factors:**

1. No Kafka consumer lag alert existed. The lag condition went undetected for
   65 minutes before downstream data quality checks caught it.
2. The ETL job treats a zero-row read as a non-error condition rather than
   raising an early warning.
3. The Great Expectations suite runs only at the feature table level, not
   at the raw ingestion level, adding detection latency.

**What went well:**

- The null-rate threshold alert in Prometheus fired correctly and quickly.
- The fallback cache design prevented user-facing errors during scoring
  suppression.
- The incident commander made the containment call (suspend scoring) within
  26 minutes of detection, before any corrupted predictions reached users.

**What went wrong:**

- No upstream observability on Kafka consumer lag meant the failure mode
  was invisible until data quality checks fired downstream.
- The ETL job's "success" exit code masked the partial read, misleading
  initial investigation.

---

## Impact

- **Users:** Zero visible errors. Stale-but-reasonable churn scores served
  from cache for 72 minutes.
- **Model:** No accuracy degradation measured on post-incident held-out set.
- **Data:** 1 feature batch (08:00 window) quarantined; replayed cleanly after
  raw data backfill.
- **Business:** No SLA breach. SEV-2 response time targets met.

---

## Action Items

| # | Action | Owner | Due | GitHub Issue |
|---|--------|-------|-----|--------------|
| 1 | Add Prometheus alert for Kafka consumer lag > 5 minutes on `clickstream-prod-consumer` | @data-eng | 2026-04-21 | #112 |
| 2 | Update `clickstream_agg_hourly` to assert consumer offset caught up before proceeding; fail fast otherwise | @data-eng | 2026-04-28 | #113 |
| 3 | Extend Great Expectations suite to validate `raw_clickstream` row count before aggregation | @ml-platform | 2026-04-21 | #114 |
| 4 | Document fallback cache TTL and scoring suppression logic in `runbooks/data_quality_incident.md` | @ml-platform | 2026-04-21 | #115 |
| 5 | Conduct blameless retro with ETL and ML Platform teams | @incident-commander | 2026-04-16 | #116 |

---

## Lessons Learned

- **Observability must be end-to-end.** Monitoring only at the feature layer
  creates a blind spot for upstream ingestion failures. We will add Kafka
  consumer lag metrics to the standard ML platform alert set.
- **Exit codes are not contracts.** A job that exits 0 after a partial read
  is harder to debug than one that fails loudly. We will update the ETL
  template to assert input completeness before proceeding.
- **Fast containment works.** Suspending the model within 26 minutes prevented
  user impact. This decision should be codified as the default for any
  data-quality incident where PSI > 0.20.

---

*Postmortem reviewed and approved by ML Platform lead on 2026-04-16.*
