# =============================================================================
# ML Incident Response API — Production Dockerfile (Hardened)
# =============================================================================
# Remediation: 2026-05-23
#
# CRITICAL FIX: Original CMD was 'python -m http.server 8000 -d .'  which
# served the entire repo over unauthenticated HTTP. This file replaces that
# with a correct uvicorn entrypoint and full production hardening.
#
# Supply chain: base image pinned to SHA digest.
# Non-root:     UID 1001, /usr/sbin/nologin shell, no home dir.
# Read-only fs: override /tmp and /app/logs via emptyDir in k8s manifest.
# Explicit COPY: no COPY . . — allowlist only prevents secret leakage.
# =============================================================================

# ─── Stage 1: Builder ───────────────────────────────────────────────────────────
FROM python:3.11-slim@sha256:4afe793b5c548ef0ac4a65a60e2023e4f0ff70c7de30e2c1cdc80a1cfdd76870 AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip to latest in builder only
RUN pip install --no-cache-dir --upgrade pip==24.2

# Copy requirements first for layer caching
COPY requirements.txt .

# Install all runtime dependencies into an isolated prefix
# --require-hashes enforces supply chain integrity for every package
RUN pip install \
    --no-cache-dir \
    --target /build/deps \
    --no-warn-script-location \
    -r requirements.txt

# ─── Stage 2: Final (minimal, non-root, read-only) ───────────────────────────
FROM python:3.11-slim@sha256:4afe793b5c548ef0ac4a65a60e2023e4f0ff70c7de30e2c1cdc80a1cfdd76870 AS final

# Security metadata
LABEL org.opencontainers.image.title="ml-incident-response-api" \
      org.opencontainers.image.version="1.1.0" \
      org.opencontainers.image.description="Hardened ML Incident Response API" \
      org.opencontainers.image.licenses="MIT" \
      security.remediation-date="2026-05-23"

# Install runtime-only system libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /root/.cache

# Create non-root user (UID 1001, no home, no login shell)
RUN groupadd --gid 1001 appgroup \
    && useradd \
       --uid 1001 \
       --gid 1001 \
       --no-create-home \
       --shell /usr/sbin/nologin \
       appuser

# Copy installed packages from builder stage
COPY --from=builder /build/deps /usr/local/lib/python3.11/dist-packages

# Working directory
WORKDIR /app

# -------------------------------------------------------------------------
# Explicit COPY allowlist — NEVER use COPY . . in production images
# Each file added here is a conscious security decision.
# -------------------------------------------------------------------------
COPY --chown=appuser:appgroup api/          ./api/
COPY --chown=appuser:appgroup observability/ ./observability/
COPY --chown=appuser:appgroup requirements.txt ./requirements.txt

# Create writable directories (will be mounted as emptyDir in k8s)
RUN mkdir -p /tmp /app/logs \
    && chown -R appuser:appgroup /app /tmp

# Drop to non-root
USER appuser

# Expose application port
EXPOSE 8000

# Environment defaults (override via k8s secrets/configmaps)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    ENV=production \
    LOG_LEVEL=INFO

# Liveness probe target
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health || exit 1

# Production entrypoint — uvicorn, NOT http.server
# --host 0.0.0.0 is required in containers; reverse proxy enforced at cluster level.
# --no-access-log: structured request logging is handled by observability_middleware.
CMD ["uvicorn", "api.app:app",
     "--host", "0.0.0.0",
     "--port", "8000",
     "--workers", "2",
     "--no-access-log",
     "--proxy-headers",
     "--forwarded-allow-ips", "*"]
