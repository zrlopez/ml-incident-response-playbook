# Sample Status Updates — INC-0047

> **Purpose:** This document shows the three standard stakeholder updates
> that are sent during a SEV-2 incident: the **initial notice**, the
> **progress update**, and the **all-clear**. The structure mirrors the
> template in `templates/incident_update_template.md`.
>
> Use these examples when drafting live updates — copy, fill in the
> highlighted fields, and send to the appropriate Slack channel or
> status page.

---

## Update 1 — Initial Notice (T+15 min)

> **When to send:** Within 15 minutes of opening a SEV-1 or SEV-2 incident,
> even if root cause has not yet been identified.

```
SEV-2 INCIDENT OPEN — INC-0047

Time: 2026-04-14 09:27 CDT
Incident Commander: @ml-platform-oncall
Affected system: Churn prediction pipeline (data-quality)

Summary:
We are investigating an elevated null rate in the session_duration_s
feature column. The churn model continues to serve predictions from
cache while we investigate. No user-facing errors have been observed.

Current status: INVESTIGATING
Next update: 2026-04-14 10:00 CDT (or sooner if status changes)

Slack thread: #ml-incidents
Incident tracker: INC-0047
```

---

## Update 2 — Progress Update (T+60 min)

> **When to send:** When mitigation is confirmed or when there is a
> material change in status. Should not exceed 60 minutes without
> an update for SEV-2 incidents.

```
SEV-2 UPDATE — INC-0047 — MITIGATED

Time: 2026-04-14 10:12 CDT
Incident Commander: @ml-platform-oncall

Summary:
Root cause identified: Kafka consumer lag caused the 08:00 clickstream
aggregation job to read an incomplete data window, producing a corrupt
feature batch with a 41% null rate in session_duration_s.

Mitigation applied:
- Churn model scoring was suspended at 09:38 CDT.
- Fallback cache scores have been served to users since 09:38 CDT
  with no reported errors.
- The affected feature batch was quarantined at 09:45 CDT.
- Kafka consumer lag was resolved and raw data backfilled at 09:58 CDT.
- The aggregation job was successfully replayed at 10:15 CDT.

Current status: MITIGATED — validation in progress
Expected resolution: 2026-04-14 11:30 CDT
Next update: All-clear once model scoring is confirmed healthy

Slack thread: #ml-incidents
Incident tracker: INC-0047
```

---

## Update 3 — All-Clear / Resolution Notice

> **When to send:** Once the system is fully restored, monitoring confirms
> healthy metrics, and the incident is formally closed.

```
SEV-2 RESOLVED — INC-0047 — ALL CLEAR

Time: 2026-04-14 11:30 CDT
Incident Commander: @ml-platform-oncall
Total duration: 2 hours 18 minutes

Summary:
The churn prediction pipeline has been fully restored. Churn model
scoring resumed at 11:10 CDT after successful validation against a
held-out evaluation set (no statistically significant accuracy
degradation detected).

Great Expectations checkpoint passed all assertions at 10:40 CDT.
Null rate for session_duration_s is 0.2%, within the 0.5% baseline.

User impact: None. Fallback cache served stale-but-reasonable scores
during the 72-minute suppression window.

Action items have been logged as GitHub issues #112-#116 and will be
reviewed at the weekly incident review.

Postmortem due: 2026-04-16 (within 48 hours of resolution)

Current status: RESOLVED
Incident tracker: INC-0047
```

---

## Formatting Notes

- Keep updates short. Three to five sentences per section is the target.
- Always include the incident ID, current status, and next-update time.
- Avoid jargon in the stakeholder summary — describe impact, not implementation.
- Attach the full incident log link for anyone who wants deeper detail.
