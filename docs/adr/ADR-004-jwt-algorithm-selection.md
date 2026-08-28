# ADR-004 — JWT Algorithm Selection: HS256 (tests) / RS256 (production)

**Status:** Accepted  
**Date:** 2026-05-28  
**Author:** @zrlopez  
**Supersedes:** —  
**Superseded by:** —

---

## Context

The API requires authentication tokens that:

1. Are stateless (no server-side session store for token *validation*)
2. Support revocation (Redis denylist for logout)
3. Can be verified without sharing a secret between services in future multi-service deployments
4. Have a minimal dependency surface (no third-party auth server required for initial deployment)

Two algorithm families were in scope: **symmetric (HMAC-SHA)** and **asymmetric (RSA)**.

A secondary operational constraint was CI testability: unit tests must run without external secrets, and integration tests must not share production signing material.

---

## Decision

Use **RS256 (RSA + SHA-256) in production** and **HS256 (HMAC-SHA-256) in unit tests**.

### RS256 in production

- The private key signs tokens at issuance; the public key verifies them at every request.
- In a future multi-service architecture, downstream services can verify tokens using the public key alone — they never hold signing material.
- `src/auth/jwt_rs256.py` implements the `RS256KeyStore` class. The private key is loaded from `RSA_PRIVATE_KEY_PEM` (environment variable or secret). The public key is derived at startup.
- Key rotation is possible without application redeployment: update the secret, restart. No database migration required.

### HS256 in unit tests

- Ephemeral HMAC key injected via `JWT_SECRET_KEY` environment variable in CI (`ci-unit-test-secret-32chars-safe!!`)
- Avoids the need for RSA key generation in unit-test setup; keeps tests fast and self-contained
- The unit test CI job generates an ephemeral RSA key and injects `RSA_PRIVATE_KEY_PEM` for tests that exercise `RS256KeyStore` directly (see `Generate ephemeral RSA key for unit tests` step in `.github/workflows/secured_ci.yml`)

### Why not HS256 in production?

HS256 requires every verifier to hold the shared secret. In a single-process deployment this is acceptable, but it creates a secret-distribution problem as soon as verification moves to a second service. RS256 avoids this by separating signing (private key, held only by the auth service) from verification (public key, freely distributable).

### Why not ES256 (ECDSA)?

ES256 provides equivalent security to RS256 at smaller key sizes. It was not selected at initial implementation because:
- PyJWT's RS256 support is more widely documented in the Python ecosystem
- RSA key generation tooling (OpenSSL, Python `cryptography` library) is universally available
- The security profile of RS256 at 2048-bit is adequate for the threat model of this project

ES256 migration is tracked as a future improvement.

---

## Alternatives Considered

| Option | Reason Not Chosen |
|---|---|
| HS256 everywhere | Shared-secret distribution problem in multi-service; all verifiers must hold signing material |
| ES256 (ECDSA) | Equivalent security; not selected due to simpler RS256 tooling and documentation at implementation time |
| EdDSA (Ed25519) | Best-in-class performance and security; PyJWT support was less stable at evaluation time |
| OAuth2 + external IdP | Significant operational dependency for an initial deployment; adds auth server as a required infrastructure component |

---

## Consequences

**Positive:**
- Signing material never leaves the auth service; verifiers need only the public key
- Future service decomposition does not require secret distribution
- Ephemeral CI keys mean production signing material is never present in the test environment

**Negative / Trade-offs:**
- RSA key management adds operational complexity vs HS256 (key generation, rotation procedure, secret injection)
- Unit tests use a different algorithm than production; algorithm-confusion bugs could theoretically exist in algorithm-agnostic code paths (mitigated by integration tests running RS256)

---

## Related

- `src/auth/jwt_rs256.py` — RS256KeyStore implementation
- `tests/unit/test_key_store.py` — RS256 unit coverage
- `tests/unit/test_tokens_hs256.py` — HS256 unit coverage
- `.github/workflows/secured_ci.yml` — ephemeral key generation step
- ADR-001 — incident tracker architecture
- ADR-003 — Alpine vs Debian base image
