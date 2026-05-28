# Severity Matrix

This matrix defines severity levels for all incidents handled by the ML
Incident Response API. Severity is set at triage time and drives SLO,
paging, and post-incident review (PIR) obligations.

Severity labels map directly to alert `severity` labels in
`observability/alert_rules.yml` and to the `IncidentSeverity` enum in
`src/domain/incident.py`.

---

## Level Definitions

| Level | Name | Definition | User / Business Impact |
|---|---|---|---|
| **SEV-1** | Critical | Full outage or critical production failure. Core ML inference path is down or returning incorrect results for ≥ 20% of requests. | Large customer or business impact. Executive visibility likely. Contractual SLO breach possible. |
| **SEV-2** | High | Major degradation with measurable user or business impact. A primary API path is degraded but not fully down. Workaround may exist. | Visible to a significant portion of users. Cross-functional coordination required. |
| **SEV-3** | Medium | Limited or partial degradation. No broad outage. A secondary subsystem (e.g. metrics export, non-critical alert rule) is affected. | Minimal direct user impact. Standard incident handling sufficient. |
| **SEV-4** | Low | Low-impact issue. No immediate production risk. Cosmetic, logging, or non-critical configuration drift. | No broad user impact. Track and remediate in normal workflow. |

---

## Response SLOs

| Level | Acknowledge | Initial Response | Resolution Target | PIR Required |
|---|---|---|---|---|
| SEV-1 | 5 min | 15 min | 4 hours | Yes — within 48 hours |
| SEV-2 | 15 min | 30 min | 8 hours | Yes — within 5 business days |
| SEV-3 | 2 hours | 4 hours | 3 business days | Recommended |
| SEV-4 | Next business day | Next business day | 2 sprints | No |

---

## Alert → Severity Mapping

The following table maps Prometheus alert names from `alert_rules.yml` to
their corresponding incident severity level. Alerts fire with a `severity`
label of `critical` or `warning`; the column below shows the equivalent
incident severity when a human on-call triages the alert.

| Alert | `severity` label | Incident SEV |
|---|---|---|
| `APILatencyCritical` | critical | SEV-1 |
| `APICriticalErrorRate` | critical | SEV-1 |
| `ModelMajorDrift` | critical | SEV-1 |
| `ModelDriftEventSpike` | critical | SEV-1 |
| `PredictionVolumeSilence` | critical | SEV-1 |
| `JWTAuthFailureCritical` | critical | SEV-1 |
| `PodRestartLoop` | critical | SEV-1 |
| `APIHighP99Latency` | warning | SEV-2 |
| `APIHighErrorRate` | warning | SEV-2 |
| `ModelMinorDrift` | warning | SEV-2 |
| `AnomalyBreachRateHigh` | warning | SEV-2 |
| `PredictionLatencyDegraded` | warning | SEV-3 |
| `JWTAuthFailureSpike` | warning | SEV-3 |
| `AlembicMigrationLag` | warning | SEV-3 |
| `RedisHighMemoryUsage` | warning | SEV-3 |
| `JWTKeyRotationOverdue` | info | SEV-4 |

---

## Escalation Policy

1. **SEV-1 / SEV-2**: Page the primary on-call immediately via AlertManager.
   If no acknowledgement within the SLO window, auto-escalate to the
   secondary on-call and notify the engineering lead.
2. **SEV-3**: Notify the on-call channel in Slack. No immediate page required
   unless the issue worsens to SEV-2 within 2 hours.
3. **SEV-4**: File a GitHub issue with the `incident` label. No paging.

Runbook links for each alert are in `observability/alert_rules.yml` under
the `runbook_url` annotation and are published to
[mlops.zrl.dev/runbooks](https://mlops.zrl.dev/runbooks).

---

## Downgrade / Upgrade Policy

- Any responder may **upgrade** severity at any time during active triage
  if new information indicates broader impact than initially assessed.
- Only the incident commander may **downgrade** severity, and only after
  confirming that the original trigger condition has fully resolved.
- Severity changes must be logged in the incident record with a timestamp
  and rationale.
