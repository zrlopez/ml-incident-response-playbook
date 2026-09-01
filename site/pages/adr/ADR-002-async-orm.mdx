# ADR-002: Async SQLAlchemy ORM for Incident Persistence

| Field        | Value                               |
|---|---|
| Status       | Accepted                            |
| Date         | 2026-05-23                          |
| Deciders     | Platform Engineering                |
| Supersedes   | —                                   |
| Superseded by| —                                   |

## Context
The original persistence layer was a 6-line flat-file appender with no locking,
no schema, and silent data corruption under concurrent writes. The API is async
(FastAPI + uvicorn) and requires a persistence layer that does not block the
event loop.

## Decision
Adopt SQLAlchemy 2.0 async ORM (`sqlalchemy.ext.asyncio`) with `asyncpg` for
PostgreSQL in production and `aiosqlite` for local development and unit tests.

## Rationale
- SQLAlchemy 2.0 async provides fully non-blocking ORM operations.
- `asyncpg` is the highest-performance async Postgres driver available.
- `aiosqlite` enables zero-infrastructure unit test runs.
- Enum types at the DB layer (`SAEnum`) enforce domain constraints at rest,
  not only in application code.
- `pool_pre_ping=True` handles idle-connection drops from Postgres
  without manual reconnection logic.
- Connection pooling via `pool_size` + `max_overflow` provides backpressure.

## Alternatives Considered
- **encode/databases**: Simpler API but less mature; lacks ORM features needed
  for typed repository pattern.
- **asyncpg raw**: Higher performance ceiling but requires manual query
  construction and schema management — too much accidental complexity for this
  system's scale.
- **Synchronous SQLAlchemy + thread pool**: Works but introduces latency jitter
  under concurrency and complicates test fixtures.

## Consequences
- All repository methods must be async.
- SQLite pool kwargs (`pool_size`, `max_overflow`) must be conditionally
  excluded; handled in `_build_engine()` with an `is_sqlite` guard.
- Schema changes require Alembic migrations (see ADR-003).
