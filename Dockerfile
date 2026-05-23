# =============================================================================
# ML Incident Response API - Hardened Production Dockerfile
# Remediation 2026-05-23
# CRIT-03: Replaced `python -m http.server` CMD with uvicorn
# =============================================================================
FROM python:3.11-slim AS builder

ARG APP_VERSION=dev

RUN pip install --no-cache-dir --upgrade pip==24.0

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS final

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1

# Non-root system user
RUN groupadd --gid 1001 --system appgroup && \
    useradd  --uid 1001 --system --gid appgroup \
             --no-create-home --shell /usr/sbin/nologin appuser

# Upgrade OS packages to reduce CVE surface
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /install /usr/local
COPY api/           ./api/
COPY observability/ ./observability/
COPY configs/       ./configs/

RUN install -d -m 0755 -o appuser -g appgroup /app/logs /tmp/app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/health',timeout=5)"

# FIXED: was `python -m http.server 8000 -d .` (served entire filesystem)
# Now runs the FastAPI application via uvicorn.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--access-log"]
