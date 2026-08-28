# Governance Exception Register

> **Purpose**: Record all approved deviations from the security and engineering
> baseline defined in `governance.md`. Each exception must be time-bounded,
> risk-accepted by a named decision-maker, and linked to a remediation ticket.
>
> **Review cadence**: Quarterly. Expired exceptions without renewal are
> automatically escalated to Platform Engineering lead.

---

## Active Exceptions

| ID | Control Deviated From | Justification | Risk Level | Expires | Owner | Ticket |
|---|---|---|---|---|---|---|
| EX-001 | SQLite in unit test conftest bypasses Alembic migration path | Enables zero-infra unit tests; SQLite schema is created from ORM metadata. Integration tests use full Alembic + Postgres path. See ADR-003. | Low | 2027-05-23 (annual renewal) | Platform Engineering | — |
| EX-002 | Redis fail-open policy on denylist check under outage | Fail-closed (blocking all auth) during Redis outage is operationally unacceptable for an incident response tool. Fail-open is documented in ADR-002 as an explicit risk. | Medium | 2027-01-01 | Security | INFRA-TBD |

---

## Closed / Resolved Exceptions

| ID | Control | Resolved Date | Resolution |
|---|---|---|---|
| — | — | — | — |

---

## Exception Request Process

1. Engineer raises exception request using `templates/governance-exception-request.md`.
2. Security reviews within 5 business days.
3. Platform Engineering lead approves or rejects.
4. Approved exception is added to this register with an expiry date ≤ 12 months.
5. Ticket created in issue tracker for remediation tracking.
6. Register reviewed quarterly; expired exceptions trigger escalation.

---

## Risk Level Definitions

| Level | Criteria |
|---|---|
| **Low** | No direct data exposure; compensating controls in place; limited blast radius |
| **Medium** | Potential indirect exposure; time-bounded; monitored via alerts |
| **High** | Direct security control deviation; requires CISO sign-off; max 90-day duration |
| **Critical** | Not permissible via exception register; requires architectural remediation |
