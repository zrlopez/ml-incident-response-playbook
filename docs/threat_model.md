# Threat Model — ML Incident Response Playbook API

**Version:** 1.0  
**Date:** 2026-08-05  
**Author:** @zrlopez  
**Reviewed by:** (pending)

---

## Overview

This document enumerates the trust boundaries, attacker goals, security
controls, and residual risks for the ML Incident Response Playbook API.
It is intended to be read alongside `SECURITY.md` (controls inventory)
and the ADRs for auth (ADR-004), denylist (ADR-003), and observability
(ADR-007).

---

## System Boundaries

```
┌─────────────────────────────────────────────────────────┐
│  External (Untrusted)                                   │
│                                                         │
│   Browser / API Client ──(HTTPS)──► FastAPI             │
│   Attacker                                             │
└────────────────────┬────────────────────────────────────┘
                     │  JWT RS256 (verified at every request)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  API Tier (Semi-Trusted)                                │
│                                                         │
│   FastAPI + Uvicorn                                     │
│   ├─ JWT RS256 verification (no HS256 accepted)         │
│   ├─ Redis denylist check (fail-closed)                 │
│   ├─ Rate limiter (slowapi, per-IP + per-user)          │
│   ├─ MaxBodySizeMiddleware (1 MB cap)                   │
│   ├─ PII scrubbing (structlog processors)               │
│   └─ OTel trace export (OTLP, trusted collector)        │
└──────────┬──────────────┬──────────────────────────────┘
           │              │
           ▼              ▼
┌──────────────┐  ┌───────────────────────────────────────┐
│  Redis        │  │  PostgreSQL / SQLite (Trusted)        │
│  (JWT deny-  │  │                                       │
│   list, rate │  │  incidents, users (argon2id hashes)   │
│   counters)  │  │  Alembic-managed schema               │
└──────────────┘  └───────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────┐
│  Model Artifact (Read-Only Trusted)                     │
│                                                         │
│  isolation_forest_v1.joblib  (SHA-256 verified)         │
│  isolation_forest_v1.joblib.sha256  (manifest)         │
└─────────────────────────────────────────────────────────┘
```

---

## Attacker Goals (STRIDE)

| Threat | Goal | Primary Vector |
|---|---|---|
| **Spoofing** | Impersonate a valid user | Forged JWT, credential stuffing |
| **Tampering** | Modify incidents or model artifact | SQL injection, direct DB access, model file replacement |
| **Repudiation** | Deny performing an action | Bypass audit logging |
| **Information Disclosure** | Exfiltrate PII or internal system details | Log injection, error messages leaking paths, JWT payload inspection |
| **Denial of Service** | Exhaust API / Redis / DB resources | Request flooding, large payloads, connection exhaustion |
| **Elevation of Privilege** | Gain admin-level access | JWT role claim manipulation, IDOR on incident endpoints |

---

## Controls by Threat

### Spoofing

| Control | Implementation | Gaps |
|---|---|---|
| RS256 JWT verification | `src/auth/jwt_rs256.py` — only RS256 accepted, algorithm confusion impossible | Private key rotation procedure not documented |
| Token revocation | `api/redis_denylist.py` — JTI-based, fail-closed | Fail-closed means Redis outage blocks all authenticated requests |
| argon2id password hashing | `src/auth/` — PHC standard, memory-hard | No multi-factor authentication |
| Rate limiting on auth endpoints | `api/rate_limit.py` — per-IP slowapi | Distributed attackers can rotate IPs |

### Tampering

| Control | Implementation | Gaps |
|---|---|---|
| Model artifact SHA-256 | `ml_models/incident_anomaly/registry.py` — verified on load | SHA-256 sentinel is zero (not pinned until CI ships manifest) |
| Parameterized SQL (ORM) | SQLAlchemy ORM — no raw string interpolation | N/A — fully mitigated |
| Alembic migration ownership | Schema changes require code review + migration file | No schema drift detection in CI |

### Information Disclosure

| Control | Implementation | Gaps |
|---|---|---|
| PII scrubbing | `observability/logging_config.py` — field-level redaction + regex | Does not cover third-party library logs (e.g., uvicorn access log) |
| No path exposure in model health | `registry.health()` — returns basename only (SEC-05) | N/A |
| Structured error responses | FastAPI exception handlers — no stack traces in 4xx/5xx responses | N/A |
| JWKS public key only | `api/routers/auth.py` — JWKS endpoint exposes public key only | N/A |

### Denial of Service

| Control | Implementation | Gaps |
|---|---|---|
| MaxBodySizeMiddleware | `api/middleware.py` — 1 MB default cap | Cap is not configurable at runtime (env var would improve ops) |
| Per-IP rate limiting | `api/rate_limit.py` — slowapi | No global rate limit across all users combined |
| Request timeout | `api/middleware.py` — RequestTimeoutMiddleware | Timeout value is hardcoded, not env-configurable |
| Redis HA required | `api/redis_denylist.py` — documents Sentinel/Cluster requirement | Not implemented in the demo (single Redis instance) |

### Elevation of Privilege

| Control | Implementation | Gaps |
|---|---|---|
| Role claims in JWT | `src/auth/jwt_rs256.py` — `roles` claim in payload | No RBAC enforcement at the router level (all authenticated users have equal access) |
| Token type enforcement | Auth router rejects access tokens on refresh endpoint and vice versa | N/A |

---

## Redis Outage Behaviour

The denylist is **fail-closed** by design (ADR-003). Under Redis outage:

1. `RedisDenylist.is_denied()` raises `DenylistUnavailableError`
2. The auth dependency propagates a `503 Service Unavailable`
3. **All authenticated requests fail** until Redis recovers

**Risk:** A Redis outage becomes a full API outage for authenticated endpoints.  
**Mitigation options (not yet implemented):**
- Circuit breaker with a short fail-open window + audit log entry
- Redis Sentinel / Cluster for HA
- Degrade gracefully: allow access for non-revoked tokens (fail-open with
  risk acceptance) during confirmed Redis maintenance windows

**Recommended runbook update:** `runbooks/api_outage.md` should include
a Redis-specific path that covers the fail-closed behavior.

---

## Residual Risks

| Risk | Severity | Likelihood | Mitigation Status |
|---|---|---|---|
| Zero-sentinel SHA-256 (model artifact unverified) | Medium | Low (controlled environment) | Deferred — waiting for CI manifest pipeline |
| No RBAC at router level | Medium | Medium | Deferred — Phase 2 (role enforcement per ADR-004 intent) |
| Single Redis instance (no HA) | High | Low (demo only) | Deferred — not applicable to portfolio demo |
| Private key rotation procedure undocumented | Medium | Low | Deferred — add ops runbook in Phase 2 |
| Uvicorn access logs bypass PII scrubbing | Low | Medium | Mitigated by disabling uvicorn access log in production config |

---

## Out of Scope

- Supply-chain attacks on GitHub Actions (covered by `secured_ci.yml` SHA-pinning and TruffleHog)
- Container runtime escapes (covered by non-root user, read-only filesystem recommendation)
- Network-layer attacks (covered by infrastructure — not in this repo's scope)
- Multi-tenant isolation (single-tenant demo system)
