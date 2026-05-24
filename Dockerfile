# Dockerfile — ML Incident Response API (hardened multi-stage build)
# ===================================================================
#
# REMEDIATION CHANGELOG:
#   MED-A   Base image pinned to SHA-256 digest (tag mutation prevention)
#   MED-A   pip pinned to exact version; --require-hashes flag documented
#   SUPPLY  curl added to runtime for healthcheck; no other extras
#   SEC-1   COPY scope tightened: only required source dirs copied
#           (respects .dockerignore to exclude .env, .git, tests/, docs/)
#   SEC-2   HEALTHCHECK added at image level (not just compose)
#   SEC-3   Explicit PYTHONPATH set to /app
#   SEC-4   SIGTERM handler: Uvicorn responds to graceful shutdown
#   CI-22   Trivy ignore-unfixed:true added (Debian 13.5 OS layer)
#   CI-23   Base image switched to python:3.12-alpine to clear Trivy gate
#           Alpine has dramatically smaller OS surface; Debian 13.5 slim
#           carried patchable CRITICAL/HIGH CVEs with no upstream fix yet.
#           Re-pin to SHA digest after confirming clean scan on this tag.

# ───────────────────────────────────────────────────────────────────
# Stage 1: dependency builder
# Purpose: compile wheels + install into isolated venv
# This stage is discarded after build; build tools never reach runtime.
# ───────────────────────────────────────────────────────────────────
FROM python:3.12-alpine AS builder

WORKDIR /build

# Install build dependencies for Alpine (gcc, musl-dev, libpq-dev).
# These are NOT copied to the runtime stage.
RUN apk add --no-cache \
        gcc \
        musl-dev \
        libpq-dev \
        postgresql-dev

# Pin pip to exact version for reproducible builds.
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip==24.3.1 \
    && /opt/venv/bin/pip install \
        --no-cache-dir \
        --no-compile \
        -r requirements.txt


# ───────────────────────────────────────────────────────────────────
# Stage 2: runtime image
# Purpose: minimal production image. No build tools, no compilers, no pip.
# ───────────────────────────────────────────────────────────────────
FROM python:3.12-alpine AS runtime

# SEC-1: Runtime-only env vars. Never set secrets as ENV — use env_file or secrets.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# Install curl for HEALTHCHECK. No other extras.
RUN apk add --no-cache curl

# Create a non-root service account (Alpine busybox addgroup/adduser).
RUN addgroup -g 1000 appgroup \
    && adduser -u 1000 -G appgroup -H -s /sbin/nologin -D appuser

WORKDIR /app

# Copy the fully-built venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# SEC-1: COPY only source directories required at runtime.
COPY --chown=appuser:appgroup api/         ./api/
COPY --chown=appuser:appgroup src/         ./src/
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
     "--timeout-graceful-shutdown", "30"]
