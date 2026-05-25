# ML Incident Response Platform — Governance Policy

**Document ID:** GOV-001
**Version:** 2.0.0
**Owner:** ML Platform Governance Board
**Last Updated:** 2026-05-23
**Review Cycle:** Quarterly (next review: 2026-08-23)
**Classification:** Internal — Operational

---

## 1. Purpose and Scope

This policy governs the operational use, change management, incident accountability,
and compliance controls for the ML Incident Response Platform. It applies to all
personnel with read or write access to production ML systems, incident records,
and the operational runbook system.

Deviations from this policy require written approval from the Platform Governance Board
and must be documented as exceptions in the active incident ticket.

---

## 2. RACI Matrix

| Activity | Incident Commander | ML Platform SRE | ML Engineer | Data Engineer | Platform Governance |
|---|---|---|---|---|---|
| Declare SEV-1 / SEV-2 | **A** | **R** | I | I | I |
| Execute runbook | I | **R** | C | C | I |
| Authorise rollback | **A** | **R** | C | I | I |
| Stakeholder communications | **R** | C | I | I | I |
| Postmortem facilitation | C | **R** | C | C | I |
| Runbook change approval | I | C | C | I | **A/R** |
| Compliance and audit reporting | I | C | I | I | **R** |
| Data classification decisions | I | I | C | **R** | **A** |
| Break-glass production change | **A** | **R** | I | I | **A** |

**R** = Responsible  |  **A** = Accountable  |  **C** = Consulted  |  **I** = Informed

---

## 3. Severity and Escalation Policy

### SEV-1 — Production Crisis
- **Criteria:** Full outage, > 10% user impact, SLO breach > 30 minutes, or confirmed revenue impact
- **Acknowledgment SLA:** ≤ 5 minutes
- **Mitigation SLA:** ≤ 30 minutes
- **Resolution SLA:** ≤ 4 hours
- **Escalation:** ML Platform Lead + VP Engineering at T+30 min if not mitigated
- **Communication:** `#ml-incidents-sev1`; stakeholder update every 15 minutes
- **Postmortem:** Required within 48 hours, blameless format

### SEV-2 — Major Degradation
- **Criteria:** Partial outage; SLO degraded but not fully breached; workaround available
- **Acknowledgment SLA:** ≤ 15 minutes
- **Mitigation SLA:** ≤ 2 hours
- **Resolution SLA:** ≤ 24 hours
- **Escalation:** ML Platform Lead at T+2 h if not mitigated
- **Postmortem:** Required within 1 week

### SEV-3 — Limited Impact
- **Criteria:** Single team or system affected; no SLO breach; workaround exists
- **Acknowledgment SLA:** ≤ 1 hour
- **Mitigation SLA:** ≤ 8 hours
- **Resolution SLA:** ≤ 72 hours
- **Escalation:** SRE Lead at T+8 h if not mitigated
- **Postmortem:** Recommended for recurring patterns

### SEV-4 — Low Impact
- **Criteria:** No user impact; minor degradation; caught by automated monitoring
- **Acknowledgment SLA:** ≤ 4 hours
- **Resolution SLA:** Next sprint
- **Postmortem:** Not required

---

## 4. SLO Reference Table

| SLO ID | Service | Metric | Target | Measurement Window |
|---|---|---|---|---|
| ML-SLO-001 | Incident API | HTTP 5xx error rate | < 1% | 28-day rolling |
| ML-SLO-002 | Incident API | P99 response latency | < 2 seconds | 28-day rolling |
| ML-SLO-003 | ML Models | P95 prediction accuracy | ≥ 92% | 7-day rolling |
| ML-SLO-004 | ETL Pipelines | Run completion within 2 h | ≥ 99% of scheduled runs | Weekly |
| ML-SLO-005 | Drift Detection | Alert-to-acknowledgment for SEV-1 | 100% within 5 minutes | Monthly |

**Error Budget Policy:** If any SLO error budget falls below 20% of the monthly allowance, all
non-critical deployments are frozen until the budget is restored or explicitly waived by the
Platform Governance Board.

---

## 5. Change Management Policy

### 5.1 Runbook and Diagram Changes
All changes to `runbooks/*.md` or `diagrams/*.mmd` require:
- Pull request with at least one `@ml-platform-sre` review approval
- No self-merges (enforced via `.github/CODEOWNERS`)
- PR description must state: motivation, risk, and test scenario
- Version bump in the runbook front-matter header (`version:` field)

### 5.2 Infrastructure and API Changes
All changes to `infrastructure/`, `ci_cd/`, or `api/` require:
- Two-reviewer approval (enforced via CODEOWNERS)
- Successful CI pipeline including all security gates
- Staging deployment and smoke-test validation before production promotion
- Rollback plan documented in the PR description

### 5.3 Emergency Break-Glass Changes
For SEV-1 incidents requiring immediate production changes:
- Single on-call SRE approval is permitted
- The change must be tracked in the active incident ticket with justification
- Full retrospective review is required in the postmortem
- The change must be re-applied through the normal process within 72 hours
- All break-glass events are reported to the Platform Governance Board

---

## 6. Data Governance and Classification

| Classification | Definition | Handling Requirements |
|---|---|---|
| **PUBLIC** | Architecture docs, runbook structure, diagram templates | No restrictions |
| **INTERNAL** | Incident logs, metric baselines, operational configs | Internal access only |
| **CONFIDENTIAL** | PII-adjacent data in logs, non-secret auth headers | Encrypted at rest and in transit; PII scrubbed before log emission |
| **RESTRICTED** | Private keys, DB credentials, JWT secrets, API tokens | Vault or K8s Secrets only; never in code, configs, or logs |

**PII Handling Rule:** All application log events MUST pass through the PII scrubbing
processor chain in `observability/logging_config.py` before emission.
Incident records must not contain raw user identifiers, email addresses, or IP addresses.

**LLM Input/Output Rule:** No CONFIDENTIAL or RESTRICTED data may be sent to
external LLM providers (GPT, Claude, Gemini, etc.) without explicit Data Processing
Agreement (DPA) approval from Legal.

---

## 7. Audit and Compliance Controls

### 7.1 Audit Log Requirements
All audit events (incident creation, status changes, resolution, access control
decisions) must include:
- Actor identity (`sub` from JWT, or service account name)
- Timestamp (UTC ISO-8601)
- Action type (e.g. `incident.created`, `incident.status_updated`)
- Affected resource ID
- `log_type: "audit"` field for SIEM routing

Audit logs must be:
- Retained for a minimum of **90 days** (configurable via `LOG_RETENTION_DAYS`)
- Forwarded to an immutable sink (CloudWatch Logs with Object Lock, Splunk, or Elastic)
- Accessible to the compliance team on request

### 7.2 SOC 2 Type II Control Mapping

| SOC 2 Control | Implementation in This Repository |
|---|---|
| CC6.1 — Logical access controls | JWT RBAC in `api/app.py`; `@require_role` decorator |
| CC6.2 — Authentication | OAuth2 password flow; bcrypt hashing via passlib |
| CC6.8 — Vulnerability prevention | pip-audit + Bandit + Trivy in CI pipeline |
| CC7.2 — Anomaly monitoring | `observability/anomaly_detection.py`; `monitoring/alert_rules.yml` |
| CC7.4 — Incident response | This runbook system; `severity_matrix.md` |
| CC8.1 — Change management | Branch protection + CODEOWNERS + CI security gates |

### 7.3 GDPR Article 22 Obligations
Where ML models make or assist automated decisions that materially affect individuals:
- Model version, feature inputs, and decision output must be logged per prediction
- Data subjects have a right to human review; the escalation path must be documented
  in the relevant runbook
- Model fairness and demographic parity metrics must be tracked alongside accuracy

---

## 8. Incident Accountability

- Every SEV-1 or SEV-2 incident must have a named **Incident Commander** assigned
  within 5 minutes of declaration
- The IC is accountable for: timeline accuracy, stakeholder communication cadence,
  and postmortem scheduling
- **Postmortems are blameless by policy.** Findings must target systems, processes,
  and tooling — not individuals
- All action items must carry: owner, due date, severity classification, and
  a linked ticket reference

---

## 9. Third-Party and LLM Provider Risk

For systems that consume external LLM APIs:
- Provider availability SLA must be tracked against the platform error budget
- Rate limits and cost controls must be configured (see `runbooks/llm_cost_spike.md`)
- Fallback behaviour (degraded mode, cached responses, circuit breaker) must be
  defined, tested, and documented
- Provider outages are classified as SEV-2 or above if they affect user-facing features

---

## 10. Policy Violations

Violations of this governance policy must be reported to the Platform Governance Board.
Repeated or intentional violations may result in:
- Access revocation
- Escalation to the Information Security team
- Formal HR review

**Reporting channels:**
- Internal: `#ml-governance-escalations`
- Security incidents: `security@[your-domain]`
- Anonymous: [Link to whistleblower policy]
