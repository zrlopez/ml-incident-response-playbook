# ADR-003 — Container Base Image: Alpine vs Debian

**Status:** Accepted  
**Date:** 2026-05-28  
**Author:** @zrlopez  
**Supersedes:** —  
**Superseded by:** —

---

## Context

The production container image must:

1. Pass a Trivy CRITICAL/HIGH hard gate with `--ignore-unfixed`
2. Minimize attack surface (fewer installed packages = fewer CVE exposure points)
3. Support all C-extension Python dependencies used by the project: `asyncpg`, `cryptography`, `argon2-cffi`, `hiredis`, `grpcio` (OpenTelemetry OTLP exporter), `numpy`, `pandas`, `scikit-learn`
4. Produce a final runtime image that is as small as practical

Initially a `python:3.12-slim` (Debian-based) image was used. Trivy scans of the Debian 13.5 OS layer produced persistent CRITICAL/HIGH findings on unfixed Debian packages that were blocking the CI gate.

---

## Decision

Use **`python:3.12-alpine`** as the base image, pinned to a SHA-256 digest in both the builder and runtime stages.

```dockerfile
FROM python:3.12-alpine@sha256:236173eb74001afe2f60862de935b74fcbd00adfca247b2c27051a70a6a39a2d AS builder
FROM python:3.12-alpine@sha256:236173eb74001afe2f60862de935b74fcbd00adfca247b2c27051a70a6a39a2d AS runtime
```

### Why Alpine

- Alpine uses musl libc + BusyBox — dramatically smaller OS package set than Debian slim
- Fewer installed packages = smaller Trivy scan surface; the CRITICAL/HIGH Debian OS-layer findings that blocked CI are absent from Alpine images
- Multi-stage build keeps the runtime image free of build compilers (`gcc`, `g++`, `gfortran`, etc.) — they are installed only in the builder stage and never reach the runtime layer

### Why digest-pinned

- A tag reference (`:3.12-alpine`) is mutable; the upstream image can change without the Dockerfile changing
- A `@sha256:` digest is immutable; the exact image bytes are fixed
- A fabricated or unverified digest is a supply-chain regression worse than a tag reference — it can silently pull a different image without failing verification
- The digest was obtained via `docker pull python:3.12-alpine && docker inspect --format '{{index .RepoDigests 0}}' python:3.12-alpine` and committed only after local verification

### Build complexity trade-off

Alpine's musl libc requires additional build-time dependencies for C-extension packages that assume glibc. The builder stage installs:

```
gcc, g++, musl-dev, libpq-dev, postgresql-dev   # asyncpg
openssl-dev, libffi-dev                          # cryptography, argon2-cffi
openblas-dev, lapack-dev, gfortran               # numpy, pandas, scikit-learn
protobuf-dev                                     # grpcio (OTel OTLP exporter)
linux-headers                                    # miscellaneous C extensions
```

All build-time dependencies are discarded after the builder stage; the runtime image receives only compiled wheels via `COPY --from=builder /opt/venv /opt/venv`.

### Non-root user

The runtime image creates a non-root service account (`appuser`, UID 1000, GID 1000) with no home directory and `/sbin/nologin` shell. All `COPY` directives use `--chown=appuser:appgroup`. `USER appuser` is set before `CMD`.

---

## Alternatives Considered

| Option | Reason Not Chosen |
|---|---|
| `python:3.12-slim` (Debian) | Persistent CRITICAL/HIGH unfixed CVEs in Debian 13.5 OS layer blocked Trivy hard gate (CI-23) |
| `python:3.12` (full Debian) | Superset of slim; larger attack surface; same CVE problem |
| `gcr.io/distroless/python3` | No shell — breaks `RUN` commands and health-check curl; pip install not available in distroless |
| `chainguard/python` | Hardened Wolfi-based image; evaluated but musl C-extension compatibility identical to Alpine; would require Chainguard subscription for non-public images at scale |

---

## Consequences

**Positive:**
- Trivy CRITICAL/HIGH gate passes clean (`--ignore-unfixed`)
- Runtime image contains no compilers, no build tools, no package manager beyond the venv
- Digest pin makes the build reproducible and tamper-evident
- Non-root appuser eliminates container-escape privilege escalation paths

**Negative / Trade-offs:**
- Builder stage complexity: 10+ Alpine build packages required for C-extension compilation
- musl libc has subtle behavioral differences from glibc; any new C-extension dependency must be verified to build cleanly on Alpine before merge
- Digest must be manually updated when the base image needs a security refresh; a Dependabot rule or CI check for stale digests is tracked as a Phase 3 improvement

---

## Related

- `Dockerfile` — implementation
- `.trivyignore` — any accepted CVE suppressions
- `.github/workflows/secured_ci.yml` — `container-scan` job (Trivy hard gate)
- ADR-001 — incident tracker architecture
- ADR-002 — JWT algorithm selection
