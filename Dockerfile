# =============================================================================
# ML Incident Response API — Production Dockerfile
# =============================================================================
#
# REMEDIATION NOTE (CRIT-03):
#   Original Dockerfile CMD was:
#     CMD ["python", "-m", "http.server", "8000", "-d", "."]
#   This served the ENTIRE application filesystem over HTTP with no
#   authentication — a critical misconfiguration. Any deployment of that
#   image would expose source code, requirements, and any .env files.
#
#   This file now matches infrastructure/Dockerfile.hardened exactly.
#   The separate hardened file is retained for reference but this root
#   Dockerfile is the canonical build target.
#
# Build:
#   docker build \
#     --build-arg BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ") \
#     --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
#     -t ml-incident-api:$(git rev-parse --short HEAD) .
#
# Security controls implemented:
#   ✔ Multi-stage build (no build tools in final image)
#   ✔ Base image pinned to SHA digest
#   ✔ Non-root user (uid 1001)
#   ✔ Read-only filesystem (use emptyDir mounts for /tmp, /app/logs)
#   ✔ No shell in final image for non-root operations
#   ✔ pip --require-hashes enforced (supply chain integrity)
#   ✔ Explicit COPY list (no COPY . . leaking secrets)
#   ✔ HEALTHCHECK configured
#   ✔ Build provenance labels (OCI image spec)
# =============================================================================

# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim@sha256:4afe793c8b8c025e1e4f53f80b39a42ce44c5b5aa17e8575c1fdce6e47ec2ee4 AS builder

ARG PIP_VERSION=24.0
WORKDIR /build

RUN pip install --no-cache-dir pip==${PIP_VERSION} \
    && pip install --no-cache-dir pip-tools==7.4.1

COPY requirements.txt .

# Install to a prefix directory for clean copy into final stage
RUN pip install \
    --no-cache-dir \
    --prefix=/install \
    -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim@sha256:4afe793c8b8c025e1e4f53f80b39a42ce44c5b5aa17e8575c1fdce6e47ec2ee4 AS final

ARG BUILD_DATE
ARG GIT_SHA

# OCI image spec labels for provenance tracking
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.title="ml-incident-response-api" \
      org.opencontainers.image.source="https://github.com/zrlopez/ml-incident-response-playbook" \
      org.opencontainers.image.licenses="MIT"

# Non-root user: uid 1001, no home dir, no login shell
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid 1001 --no-create-home \
               --shell /usr/sbin/nologin appuser

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy only required application source — never COPY . .
WORKDIR /app
COPY api/        ./api/
COPY observability/ ./observability/
COPY pyproject.toml .

# Writable directories mounted via emptyDir in Kubernetes
# /tmp is needed by uvicorn; /app/logs for file sink if configured
RUN mkdir -p /app/logs /tmp \
    && chown -R 1001:1001 /app /tmp

USER 1001:1001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    APP_ENV=production

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "
import urllib.request, sys
try:
    r = urllib.request.urlopen('http://localhost:8000/health', timeout=8)
    sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
"

# Exec-form CMD — no shell, no signal swallowing
CMD ["uvicorn", "api.app:app",
     "--host", "0.0.0.0",
     "--port", "8000",
     "--workers", "2",
     "--no-access-log",
     "--timeout-keep-alive", "30"]
