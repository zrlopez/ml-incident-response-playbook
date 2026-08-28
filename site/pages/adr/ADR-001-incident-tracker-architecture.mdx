# ADR-001 — Incident Tracker Architecture: ORM + Repository Layer

**Status:** Accepted  
**Date:** 2026-05-28  
**Author:** @zrlopez  
**Supersedes:** —  
**Superseded by:** —

---

## Context

Early versions of the incident tracker used a flat-file appender (`incidents.json`). This approach was discarded because it provided no transactional safety, no schema enforcement, no concurrent-write protection, and no query capability beyond full-file scans. The project needed a persistence layer that could:

- Support both SQLite (local/test, zero-ops setup) and PostgreSQL (production)
- Provide non-blocking I/O compatible with FastAPI's async event loop
- Enforce enum-level constraints at the database layer
- Support Alembic-managed schema evolution without application-level `create_all` calls
- Produce a clean data-access interface that the service and API layers could depend on without coupling to ORM internals

A secondary constraint was testability: the repository layer needed to be injectable (via FastAPI `Depends()`) so unit tests could operate against SQLite in-process without spinning up PostgreSQL.

---

## Decision

Adopt **SQLAlchemy 2.x async ORM** with an **`IncidentRepository` class** as the single data-access boundary.

Key implementation choices:

1. **SQLAlchemy 2.x `AsyncSession` + `async_sessionmaker`** — eliminates event-loop blocking on all DB I/O paths. `pool_pre_ping=True` guards against stale idle connections without requiring heartbeat jobs.

2. **`IncidentRepository` typed data-access layer** (`src/incident_tracker.py`) — all reads and writes go through this class. It does not own business logic, HTTP concerns, or alert sending. Each of those responsibilities lives in a dedicated layer.

3. **Alembic schema ownership (CR-1)** — `init_db()` performs a connectivity check and reads `alembic_version` for an ops warning. It does **not** call `Base.metadata.create_all()`. Schema creation and evolution are exclusively owned by Alembic. This prevents the application from silently creating a schema that diverges from the migration history.

4. **Domain state machine (CR-2)** — `update_status()` delegates lifecycle validation to `src.domain.incident_lifecycle.validate_status_transition()` before any mutation. Invalid transitions raise `InvalidTransitionError`, which the API layer maps to HTTP 409. Every attempt — allowed or rejected — is audit-logged via structlog.

5. **Compound keyset pagination (OPEN-02, KEYSET-01)** — `list_open()` and `list_by_severity()` use `(created_at, id)` as the compound cursor key. A single-column cursor on `created_at` is ambiguous when multiple incidents are created within the same timestamp tick; the compound predicate guarantees gapless, stable pagination. The `ix_incidents_keyset` index covers both columns.

6. **`_build_engine()` module-level singleton** — retained for backward compatibility with existing test shims. A future refactor (tracked in the `Platform note` inline comment) will move engine construction to `src/platform/database.py` and inject it via `Depends()` for full testability.

---

## Alternatives Considered

| Option | Reason Not Chosen |
|---|---|
| Flat-file JSON appender | No transactional safety, no concurrent-write protection, no query capability (R-05) |
| Raw `asyncpg` without ORM | Eliminates type-safe column mapping and Alembic migration integration; higher boilerplate for common CRUD |
| SQLModel (Pydantic + SQLAlchemy hybrid) | Additional abstraction layer over SQLAlchemy with smaller community and slower async story at evaluation time |
| Tortoise ORM | Mature async ORM but less ecosystem integration with Alembic; smaller community than SQLAlchemy |

---

## Consequences

**Positive:**
- Single async data-access interface; all DB I/O is non-blocking
- Schema evolution is fully auditable via Alembic migration history
- Domain lifecycle rules cannot be bypassed by calling `update_status()` directly
- Compound keyset pagination prevents silent row loss under high-velocity incident creation
- SQLite/PostgreSQL duality allows zero-ops local dev and CI (unit tests) without Docker

**Negative / Trade-offs:**
- `_build_engine()` module-level singleton makes full dependency injection for the engine slightly awkward in tests; mitigated by env-var `DATABASE_URL` swap
- Alembic discipline requirement: every schema change must produce a migration before merge

---

## Related

- `src/incident_tracker.py` — implementation
- `src/domain/incident_lifecycle.py` — state machine
- `alembic/` — migration history
- ADR-002 — JWT algorithm selection
