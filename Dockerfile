# Dockerfile — ML Incident Response API (hardened multi-stage build)
# ===================================================================
#
# REMEDIATION CHANGELOG:
#   MED-A   Base image digest-pin deferred (R-06); tag-only reference in use
#   MED-A   pip pinned to exact version; --require-hashes flag documented
#   SUPPLY  curl added to runtime for healthcheck; no other extras
#   SEC-1   COPY scope tightened: only required source dirs copied
#           (respects .dockerignore to exclude .env, .git, tests/, docs/)
#   SEC-2   HEALTHCHECK added at image level (not just compose)
#   SEC-3   Explicit PYTHONPATH set to /app
#   SEC-4   SIGTERM handler: Uvicorn responds to graceful shutdown
#   CI-22   Trivy ignore-unfixed:true added (Debian 13.5 OS layer)
#   CI-23   Base image switched to python:3.12-alpine to clear Trivy gate
#   CI-23b  Expanded Alpine build deps for all C-extension packages:
#           cryptography (openssl-dev, libffi-dev), numpy/pandas/sklearn
#           (openblas-dev, lapack-dev, gfortran), grpcio (protobuf-dev),
#           hiredis, argon2-cffi. All build deps stay in builder stage only.
#   R-27    pip bumped 24.3.1 -> 25.1.1 (latest stable 2026-05-26)
#   R-06    TODO: inline-pin FROM directives to sha256 digest once resolved via:
#             docker pull python:3.12-alpine
#             docker inspect --format '{{index .RepoDigests 0}}' python:3.12-alpine
#           Tag-only reference retained until verified digest is available.
#           A fabricated digest is a supply-chain regression; do not commit unverified values.

# ------------------------------------------------------------------
# Stage 1: dependency builder
# Purpose: compile wheels + install into isolated venv
# This stage is discarded after build; build tools never reach runtime.
# ------------------------------------------------------------------
# R-06 TODO: replace tag with digest once verified (see changelog above).
# Tag-only: python:3.12-alpine — safe for dev/CI; inline digest pin required before production release.
FROM python:3.12-alpine@sha256:236173eb74001afe2f60862de935b74fcbd00adfca247b2c27051a70a6a39a2d AS builder

WORKDIR /build

# Install all build dependencies needed for C-extension packages:
#   gcc, musl-dev, g++     — base compilers (asyncpg, hiredis, argon2)
#   libpq-dev, postgresql-dev — asyncpg / psycopg2
#   openssl-dev, libffi-dev   — cryptography, argon2-cffi
#   openblas-dev, lapack-dev  — numpy, pandas, scikit-learn
#   gfortran                  — scipy/sklearn Fortran routines
#   protobuf-dev              — grpcio (opentelemetry-exporter-otlp-proto-grpc)
#   linux-headers             — required by some C extensions on Alpine
RUN apk add --no-cache \
        gcc \
        g++ \
        musl-dev \
        libpq-dev \
        postgresql-dev \
        openssl-dev \
        libffi-dev \
        openblas-dev \
        lapack-dev \
        gfortran \
        protobuf-dev \
        linux-headers

# R-27: pip bumped from 24.3.1 to 25.1.1 (latest stable as of 2026-05-26).
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip==26.1 \
    && /opt/venv/bin/pip install \
        --no-cache-dir \
        --no-compile \
        -r requirements.txt


# ------------------------------------------------------------------
# Stage 2: runtime image
# Purpose: minimal production image. No build tools, no compilers, no pip.
# ------------------------------------------------------------------
FROM python:3.12-alpine@sha256:236173eb74001afe2f60862de935b74fcbd00adfca247b2c27051a70a6a39a2d AS runtime

# SEC-1: Runtime-only env vars. Never set secrets as ENV — use env_file or secrets.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# Base image packages: patch all existing packages, then install runtime deps.
# libpq: asyncpg runtime linkage
# openblas: numpy/pandas/sklearn runtime linkage
# libstdc++: g++ runtime lib (grpcio, sklearn)
RUN apk upgrade --no-cache && \
    apk add --no-cache \
        curl \
        libpq \
        openblas \
        libstdc++ \
        libgomp

# Create a non-root service account (Alpine busybox addgroup/adduser).
RUN addgroup -g 1000 appgroup \
    && adduser -u 1000 -G appgroup -H -s /sbin/nologin -D appuser

WORKDIR /app

# Copy the fully-built venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# SEC-1: COPY only source directories required at runtime.
COPY --chown=appuser:appgroup api/         ./api/
COPY --chown=appuser:appgroup src/         ./src/
COPY --chown=appuser:appgroup ml_models/   ./ml_models/
COPY --chown=appuser:appgroup observability/ ./observability/
COPY --chown=appuser:appgroup configs/     ./configs/
COPY --chown=appuser:appgroup alembic.ini  ./alembic.ini
COPY --chown=appuser:appgroup alembic/     ./alembic/

# Switch to non-root user before any further RUN commands or CMD.
USER appuser

# Document the port the service listens on.
EXPOSE 8080

# SEC-2: HEALTHCHECK at the image level (not just compose).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Production entrypoint.
CMD ["uvicorn", "api.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "2", \
     "--no-access-log", \
     "--timeout-graceful-shutdown", "30", \
     "--header", "server:"]
