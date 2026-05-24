# ADR-002: Redis-backed JWT Token Denylist

| Field        | Value                               |
|---|---|
| Status       | Accepted                            |
| Date         | 2026-05-23                          |
| Deciders     | Security, Platform Engineering      |
| Supersedes   | —                                   |

## Context
JWT tokens are stateless by design. Once issued, a token is valid until
expiry. The system needs to support immediate token revocation (logout,
suspected compromise, role change) without waiting for expiry.

## Decision
Maintain a Redis-backed denylist. On logout or forced revocation, the token's
JTI (JWT ID) is stored in Redis with a TTL matching the token's remaining
lifetime. Every authenticated request checks the denylist before proceeding.

## Rationale
- Redis O(1) key lookup adds negligible latency (~0.2 ms local, ~1 ms cloud).
- TTL-based automatic expiry means the denylist never grows unboundedly.
- Redis is already in the infrastructure footprint for rate limiting.
- The JTI-only approach stores the minimum necessary data (no PII).

## Alternatives Considered
- **Short token lifetimes only**: Reduces window of compromise but cannot
  provide immediate revocation. Unacceptable for security-sensitive operations.
- **Database-backed denylist**: Correct semantics but adds DB write pressure
  on every authentication path.
- **Opaque session tokens**: Fully stateful; eliminates the revocation problem
  but requires session storage for every request — higher operational cost.

## Consequences
- Redis is a required runtime dependency. Application startup fails if Redis
  is unreachable (fail-fast is intentional).
- Redis outage affects authentication. Mitigation: Redis Sentinel or Cluster
  for HA; circuit breaker on denylist check with fail-open policy documented
  as an explicit operational risk.
- Test environments that skip Redis must mock `is_token_revoked()`.
