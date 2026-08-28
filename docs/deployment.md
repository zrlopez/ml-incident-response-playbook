# Deployment Guide

This guide covers local development, Docker Compose stack setup, environment
variable reference, and production deployment notes for the ML Incident
Response API.

Last updated: 2026-05-23

---

## Quick Start (Local Development)

```bash
# 1. Clone and enter the repo
git clone https://github.com/zrlopez/ml-incident-response-playbook.git
cd ml-incident-response-playbook

# 2. Create and activate a virtual environment
python3.11 -m venv .venv && source .venv/bin/activate

# 3. Install all dependencies (runtime + dev tools)
pip install -r requirements-dev.txt

# 4. Set required environment variables
export JWT_SECRET_KEY="dev-only-secret-minimum-32-characters-long"
export ENVIRONMENT=development
export DATABASE_URL=sqlite+aiosqlite:///./incidents.db
export REDIS_URL=redis://localhost:6379/0

# 5. Start Redis (needed for JWT denylist)
docker run -d -p 6379:6379 redis:7-alpine

# 6. Start the API with hot reload
uvicorn api.app:app --reload --port 8000

# 7. Verify
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Interactive API docs are available at `http://localhost:8000/docs`.

---

## Docker Compose Stack

`docker-compose.yml` starts the local backing and observability services:
PostgreSQL, Redis, Prometheus, and Grafana. Run the FastAPI service locally with
Uvicorn, or combine the base file with `docker-compose.prod.yml` for the
containerized API profile.

```bash
# Start local backing services and dashboards
docker compose up -d postgres redis prometheus grafana

# Start the API locally
uvicorn api.app:app --reload --port 8000

# Run integration tests against the live backing services
pytest tests/integration/ -v

# Stop and remove containers
docker compose down
```

| Service | Port | Purpose |
|---|---|---|
| `postgres` | 5432 | Primary relational store |
| `redis` | 6379 | JWT denylist |
| `prometheus` | 9090 | Metrics collection and alerting |
| `grafana` | 3000 | Dashboard UI |

---

## Environment Variable Reference

All variables are read by `pydantic-settings` in `api/app.py`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET_KEY` | **Yes** | — | HMAC-SHA256 signing key. Minimum 32 characters. Rotate by restarting the API (all tokens invalidated). |
| `ENVIRONMENT` | **Yes** | — | One of `development`, `staging`, `production`. Controls CORS policy, log format, and dev user fixture activation. |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./incidents.db` | SQLAlchemy connection string. Use `postgresql+asyncpg://user:pass@host/db` in production. |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL for the JWT denylist. Supports `rediss://` for TLS. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `http://localhost:4317` | OTLP gRPC collector endpoint. |
| `OTEL_SERVICE_NAME` | No | `ml-incident-api` | Service name appearing in Jaeger / trace backend. |
| `OTEL_SDK_DISABLED` | No | `false` | Set `true` to disable OTel tracing (useful in unit test environments). |
| `ALERT_EMAIL` | No | — | Email address for alert notifications (future use). |

---

## Health and Readiness Probes

Two endpoints are available for orchestration health checks:

```
GET /health   — Always returns 200 {"status": "ok"} if the process is alive.
GET /ready    — Returns 200 only when the database and Redis connections
               are confirmed healthy. Returns 503 if either is unavailable.
```

Configure Kubernetes liveness probes against `/health` and readiness probes
against `/ready`. The readiness probe ensures the pod does not receive
traffic before its dependencies are available.

---

## Production Deployment Notes

### Security checklist before deploying to production

- [ ] `JWT_SECRET_KEY` is at least 32 random characters, stored in a secrets
  manager (AWS Secrets Manager, GCP Secret Manager, or Kubernetes Secret).
- [ ] `ENVIRONMENT=production` is set. This disables the dev user fixture
  and enables strict CORS.
- [ ] `DATABASE_URL` points to PostgreSQL with TLS (`sslmode=require`).
- [ ] `REDIS_URL` uses a password-protected Redis instance (`redis://:password@host:6379`).
- [ ] The `/metrics` endpoint is not publicly accessible (restrict at the
  ingress or load balancer level to the Prometheus scraper IP).
- [ ] TLS termination is handled at the ingress layer (nginx, AWS ALB, or
  Cloudflare). The API itself does not handle TLS directly.

### Rolling update procedure

1. Build and push the new image: `docker build -t ghcr.io/zrlopez/ml-incident-api:sha-<SHA> .`
2. Run the CI pipeline to confirm all checks pass.
3. Update the image tag in your Kubernetes Deployment or Compose file.
4. Apply with `kubectl rollout` (zero-downtime rolling update):
   ```bash
   kubectl set image deployment/ml-incident-api api=ghcr.io/zrlopez/ml-incident-api:sha-<SHA>
   kubectl rollout status deployment/ml-incident-api
   ```
5. If the rollout fails, roll back immediately:
   ```bash
   kubectl rollout undo deployment/ml-incident-api
   ```

### JWT secret rotation

Rotating `JWT_SECRET_KEY` invalidates **all** currently active tokens. Users
will need to re-authenticate. To rotate:
1. Generate a new secret: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update the secret in your secrets manager.
3. Trigger a rolling restart: `kubectl rollout restart deployment/ml-incident-api`
4. The Redis denylist does not need to be flushed — old tokens signed with
   the previous key will fail signature verification before the denylist
   is checked.
