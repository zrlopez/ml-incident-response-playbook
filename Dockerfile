# =============================================================================
# ML Incident Response API — Production Dockerfile
# =============================================================================
# REPLACES: the previous root Dockerfile that incorrectly ran
#           `python -m http.server` (unauthenticated filesystem exposure).
#
# This file is now the canonical build target.  The multi-stage build:
#   1. builder  — installs dependencies with hash verification
#   2. final    — minimal runtime image, non-root user, no build tools
#
# Build:   docker build -t ml-incident-api:latest .
# Run:     docker run --env-file .env -p 8000:8000 ml-incident-api:latest
# =============================================================================

# ── Stage 1: dependency builder ────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:032c52613401895aa3d418a4b9b8b6953a40a3b5f9adfddd8c8e5be7c10f4e73 AS builder

# Fail the build fast if any pip step fails
SHELL ["/bin/sh", "-euxo", "pipefail", "-c"]

RUN pip install --no-cache-dir --upgrade pip==24.3.1

WORKDIR /build
COPY requirements.txt .

# Install into a dedicated prefix so the final stage can COPY just the site-packages
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: minimal runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:032c52613401895aa3d418a4b9b8b6953a40a3b5f9adfddd8c8e5be7c10f4e73

SHELL ["/bin/sh", "-euxo", "pipefail", "-c"]

# Install only security updates; remove package cache
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Non-root user: uid/gid 1001, no home directory, no shell
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid 1001 --no-create-home \
               --shell /usr/sbin/nologin appuser

WORKDIR /app

# Copy only the installed Python packages from the builder stage
COPY --from=builder /install /usr/local

# Copy application source — explicit list prevents secret/test leakage
COPY api/            ./api/
COPY observability/  ./observability/
COPY pipelines/      ./pipelines/
COPY ml_models/      ./ml_models/

# Create writable dirs before dropping privileges
RUN mkdir -p /app/logs /tmp/app \
    && chown -R appuser:appgroup /app /tmp/app

USER appuser

# Expose only the application port (not the filesystem server port)
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Exec-form CMD: no shell, no PID 1 shell wrapper, clean signal handling
CMD ["uvicorn", "api.app:app",
     "--host", "0.0.0.0",
     "--port", "8000",
     "--workers", "2",
     "--log-config", "/dev/null",
     "--no-access-log"]
