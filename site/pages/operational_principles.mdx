# Operational Principles

> These principles govern how the ML Incident Response team detects, responds
> to, and learns from incidents. They are not aspirational statements — they
> are working agreements that every team member is expected to follow during
> an active incident and in the documentation they produce afterward.

---

## 1. Standardise the response path

Every incident follows the same four-stage lifecycle regardless of severity:
**Detection → Triage → Mitigation → Postmortem**. Consistency reduces cognitive
load under pressure and makes runbooks reusable across teams.

- Use the incident tracker for every issue, even small ones. If it is not in
  the tracker, it did not happen.
- Assign an incident ID at the moment of detection, before any investigation
  begins.
- Do not skip stages. If mitigation happens before formal triage, document
  both retroactively.

---

## 2. Document ownership clearly

Ambiguous ownership is the leading cause of delayed response.

- Every incident has exactly one **Incident Commander** who coordinates the
  response and owns all external communication.
- Every service and pipeline has a documented owner listed in the relevant
  runbook. If an owner is missing, opening a pull request to add one is
  itself an action item.
- On-call rotations are published and updated at least two weeks in advance.

---

## 3. Prefer containment before broad changes

The first objective during a live incident is to reduce blast radius, not to
fix the root cause.

- Quarantine bad data before investigating why it arrived.
- Disable a model or feature flag before debugging the model itself.
- Roll back a deployment before trying to patch it in production.
- Broad changes during an active incident require explicit sign-off from the
  Incident Commander.

---

## 4. Communicate early and often

Silence during an incident is a signal of confusion, not competence.

- Send the first stakeholder update within **15 minutes** of opening a SEV-1
  or SEV-2 incident, even if you have no root cause yet.
- Use the templates in `templates/` for all external updates.
- Prefer over-communication during an incident and concise summaries afterward.
- When an incident is resolved, send a final "all-clear" update before closing
  the ticket.

---

## 5. Capture learning after every incident

A postmortem is not a blame exercise. It is a structured commitment to
not repeating a failure.

- Every SEV-1 and SEV-2 incident requires a written postmortem within **48
  hours** of resolution. Use the template in `templates/postmortem_template.md`.
- Every postmortem must include at least one concrete, assignable action item
  with an owner and a due date.
- Action items are tracked as GitHub issues and reviewed at the monthly
  incident review meeting.
- Recurring incidents (same root cause appearing more than once) are
  automatically elevated to a systemic review.

---

## 6. Keep the runbooks current

A runbook that does not reflect the live system is worse than no runbook,
because it creates false confidence.

- Any change to infrastructure, alerting rules, or service topology must
  include a runbook update in the same pull request.
- Runbooks are reviewed for accuracy at least once per quarter.
- If you follow a runbook and it is wrong, update it before closing the
  incident — even if only one sentence changes.

---

## 7. Build observability first

You cannot respond to what you cannot see.

- New features and pipelines are not considered production-ready without at
  least one alert rule and one dashboard panel.
- Metrics must be documented in `monitoring/metrics.md` before they are used
  in alert rules.
- Observability is a first-class deliverable, not a follow-up task.
