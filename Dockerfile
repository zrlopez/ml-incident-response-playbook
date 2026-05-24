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
#   NOTE    To regenerate the digest pin:
#             docker pull python:3.12-slim
#             docker inspect python:3.12-slim --format='{{index .RepoDigests 0}}'
#           Update FROM line with the new digest before each base image update.

# ───────────────────────────────────────────────────────────────────
# Stage 1: dependency builder
# Purpose: compile wheels + install into isolated venv
# This stage is discarded after build; build tools never reach runtime.
# ───────────────────────────────────────────────────────────────────
# MED-A: Pinned to SHA-256 digest. Prevents tag-mutation supply chain attacks
# where an attacker pushes a malicious layer over a floating tag.
# To update: pull the new image and re-run `docker inspect ... --format='{{index .RepoDigests 0}}'`
FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203 AS builder

WORKDIR /build

# Install build dependencies (gcc, libpq-dev for asyncpg/psycopg2 wheels).
# These are NOT copied to the runtime stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# MED-A: Pin pip to exact version for reproducible builds.
# Note: To enable hash verification (supply chain hardening), run:
#   pip-compile --generate-hashes requirements.in -o requirements.txt
# Then use: pip install --require-hashes -r requirements.txt
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
FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203 AS runtime

# SEC-1: Runtime-only env vars. Never set secrets as ENV — use env_file or secrets.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# Install curl for HEALTHCHECK. No other extras.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root service account.
# --no-create-home: no home dir (reduces attack surface)
# --shell /bin/false: no interactive login possible
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false appuser

WORKDIR /app

# Copy the fully-built venv from the builder stage.
# The builder stage is discarded — gcc, libpq-dev, etc. never reach this layer.
COPY --from=builder /opt/venv /opt/venv

# SEC-1: COPY only source directories required at runtime.
# .dockerignore excludes: .env, .env.*, .git, tests/, docs/, *.md, __pycache__
# This prevents secrets, test code, and VCS history from entering the image.
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
# Kubernetes liveness probes do not use compose healthchecks.
# This makes container health inspectable with `docker inspect`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Production entrypoint.
# --workers 2: baseline; tune via WEB_CONCURRENCY env var at deploy time.
# --no-access-log: access logs routed through structlog middleware instead.
# --timeout-graceful-shutdown 30: allows in-flight requests to complete.
CMD ["uvicorn", "api.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "2", \
     "--no-access-log", \
     "--timeout-graceful-shutdown", "30"]
