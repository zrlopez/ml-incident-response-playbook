# Architecture

The ML Incident Response platform is a production-grade FastAPI service with
SQLite (dev) / PostgreSQL (prod) persistence, Redis-backed JWT revocation,
Prometheus metrics, OpenTelemetry distributed tracing, and a hardened CI
pipeline. This document describes every layer, how they connect, and the
reasoning behind key design decisions.

Last updated: 2026-05-23

---

## Component Diagram

```mermaid
graph TD
    subgraph Clients
        U["API Client\n(curl / SDK)"]
    end

    subgraph API Layer ["API Layer (api/)"] 
        APP["FastAPI app\napi/app.py"]
        MW["Middleware\n• CORS\n• X-Trace-Id injection\n• Security headers"]
        AUTH["Auth routes\n/auth/token\n/auth/refresh\n/auth/logout"]
        INC["Incident routes\n/incidents CRUD"]
        HEALTH["/health  /ready  /metrics"]
    end

    subgraph Data Layer ["Data Layer (src/)"]
        REPO["IncidentRepository\nsrc/incident_repository.py"]
        DB[("SQLite / PostgreSQL\nincidents table")]
        SESSION["AsyncSession\nSQLAlchemy 2.x"]
    end

    subgraph Auth Layer
        JWT["JWT (PyJWT)\naccess + refresh tokens"]
        BCRYPT["bcrypt\npassword hashing"]
        DENY[("Redis\ntoken denylist")]
    end

    subgraph Observability
        PROM["Prometheus\n/metrics endpoint"]
        OTEL["OTel SDK\nobservability/otel_setup.py"]
        STRUCT["structlog\nJSON structured logs"]
        COLLECTOR["OTel Collector"]
        JAEGER["Jaeger\ndistributed traces"]
    end

    subgraph Monitoring
        DRIFT["drift_check.py\nml_feature_drift_ratio"]
        RULES["alert_rules.yml\nPrometheus alerting"]
        ALERT["Alertmanager"]
    end

    subgraph CI
        GHA[".github/workflows/ci.yml"]
        TRUFFLEHOG["TruffleHog\nsecret scan"]
        BANDIT["Bandit SAST"]
        AUDIT["pip-audit CVE scan"]
        RUFF["ruff lint + format"]
        PYTEST["pytest coverage ≥70%"]
    end

    U -->|HTTPS| MW
    MW --> APP
    APP --> AUTH
    APP --> INC
    APP --> HEALTH
    AUTH --> JWT
    AUTH --> BCRYPT
    AUTH -->|token revocation| DENY
    INC --> REPO
    REPO --> SESSION
    SESSION --> DB
    APP --> OTEL
    APP --> STRUCT
    APP --> PROM
    OTEL --> COLLECTOR
    COLLECTOR --> JAEGER
    DRIFT -->|gauge| PROM
    PROM --> RULES
    RULES --> ALERT
```

---

## Layer Descriptions

### API Layer (`api/`)

`api/app.py` is the single FastAPI application. It registers:
- A lifespan context manager that runs `init_db()` on startup and
  `shutdown_otel()` on shutdown.
- Three middleware layers: CORS (configured for the `ENVIRONMENT`), a
  `trace_and_security_headers` middleware that injects a UUID4 `X-Trace-Id`
  header on every response and binds it to the structlog context, and HTTP
  security headers (X-Content-Type-Options, X-Frame-Options, etc.).
- Auth routes (`/auth/*`) for token issuance, refresh, and logout (denylist).
- Incident CRUD routes (`/incidents/*`).
- Health and readiness probes (`/health`, `/ready`) and a Prometheus
  metrics endpoint (`/metrics`).

### Authentication Layer

JWT access tokens (15-minute TTL) and refresh tokens (7-day TTL) are issued
using PyJWT with HS256. Passwords are hashed with `bcrypt` directly (no
passlib wrapper — passlib has been unmaintained since 2022 and breaks with
bcrypt ≥4.0). Logout adds the token’s JTI (JWT ID) to a Redis sorted set
with a TTL matching the token’s expiry. `is_token_revoked()` fails closed:
if Redis is unreachable, the token is treated as revoked and access is
denied. The `RedisDenylistUnavailable` Prometheus alert fires within 1
minute if Redis goes down.

### Data Layer (`src/`)

`IncidentRepository` wraps all database access behind an async interface.
It uses SQLAlchemy 2.x with `AsyncSession` so the FastAPI event loop is
never blocked by I/O. In development, the database is SQLite (`incidents.db`
in the project root). In production, set `DATABASE_URL` to a
`postgresql+asyncpg://` connection string. `init_db()` runs `create_all()`
on startup, so there is no migration step required for the initial schema.
Alembic is available for subsequent schema migrations.

### Monitoring Layer (`monitoring/`)

`drift_check.py` provides three functions:
- `drift_ratio()` — relative mean deviation for scalar features.
- `psi_score()` — Population Stability Index for binned distributions.
- `scan_features()` — batch evaluation with Prometheus gauge export.

`alert_rules.yml` is valid Prometheus 2.x alerting rule syntax (loadable
with `promtool check rules`). It defines six alert groups covering API
error rate, latency, model accuracy, feature drift, pipeline SLA, incident
volume, LLM cost, and Redis denylist availability.

### Observability Layer (`observability/`)

`otel_setup.py` bootstraps the OTel SDK with a BatchSpanProcessor → OTLP
gRPC exporter. It no-ops gracefully if the OTel packages are absent or
`OTEL_SDK_DISABLED=true`. `logging_config.py` configures structlog to emit
machine-parseable JSON in production and a human-readable format in
development, with automatic exception formatting and caller context.

### CI Layer (`.github/workflows/`)

Six jobs run on every push and PR to `main`: secret scanning (TruffleHog,
SHA-pinned), dependency CVE audit (pip-audit), SAST (Bandit), lint + type
check (ruff + mypy), tests with coverage gate (≥70%), and documentation
presence check. All `actions/*` and tool actions are pinned to full commit
SHAs. Secret scanning gates all other jobs; tests gate only on the three
security/quality jobs completing cleanly.

---

## Request Lifecycle

A `POST /incidents` request from an authenticated client:

1. **Middleware** — CORS check, `X-Trace-Id` UUID4 generated and bound to
   structlog context.
2. **OTel** — FastAPIInstrumentor creates a root span with `http.method`,
   `http.route`, `http.status_code`.
3. **Auth dependency** — `get_current_user()` extracts the Bearer token,
   verifies the JWT signature, checks the Redis denylist.
4. **Route handler** — validates `IncidentCreate` schema (Pydantic v2),
   calls `IncidentRepository.create()`.
5. **Repository** — inserts the row via `AsyncSession`, returns the
   committed `Incident` ORM object.
6. **Response** — serialized to `IncidentResponse` schema, HTTP 201
   returned. `X-Trace-Id` is visible in the response headers for client-side
   correlation.
7. **Prometheus** — `http_requests_total` and `http_request_duration_seconds`
   incremented/observed by the instrumentator.
8. **Logs** — structlog emits a JSON line with `trace_id`, `user_id`,
   `incident_id`, `severity`, and `duration_ms`.

---

## Technology Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Web framework | FastAPI | Native async, Pydantic v2 validation, OpenAPI auto-docs |
| ORM | SQLAlchemy 2.x async | Non-blocking I/O, type-safe, Alembic migration support |
| Auth | PyJWT + bcrypt | Minimal surface area; passlib unmaintained since 2022 |
| Token revocation | Redis sorted set | O(1) lookup; TTL-automatic expiry; no manual cleanup |
| Metrics | prometheus-fastapi-instrumentator | Zero-config HTTP metrics; standard Prometheus exposition |
| Tracing | OpenTelemetry SDK (OTLP) | Vendor-neutral; works with Jaeger, Tempo, Honeycomb, Datadog |
| Logging | structlog | Structured JSON output; processor chain; trace_id binding |
| Drift detection | Custom PSI + relative deviation | PSI is the financial-industry standard for model monitoring |
| Lint | ruff | Replaces black + flake8 in a single binary; ~10x faster |
| Secret scanning | TruffleHog (SHA-pinned) | Detects verified secrets; pinning prevents supply chain risk |
