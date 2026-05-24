# ADR-003: Alembic as Authoritative Schema Migration System

| Field        | Value                               |
|---|---|
| Status       | Accepted                            |
| Date         | 2026-05-23 (CR-1 remediation)       |
| Deciders     | Platform Engineering, DBA           |
| Supersedes   | create_all bootstrap (informal)     |

## Context
The original `init_db()` used `Base.metadata.create_all`, which:
- Requires DDL privileges at runtime (violates least-privilege)
- Provides no rollback mechanism
- Cannot express ALTER TABLE operations
- Produces no auditable schema history
- Creates schema drift risk between environments

## Decision
Alembic is the sole mechanism for all schema creation and evolution.
`init_db()` now only verifies connectivity and reads `alembic_version`.
The application process has no DDL privileges in production.

## Migration discipline
- Every schema change ships with a versioned migration in `migrations/versions/`.
- Migrations are applied by the deployment pipeline (`alembic upgrade head`)
  before the new application container starts.
- Rollback is `alembic downgrade -1` (or to a named revision).
- `compare_type=True` in `env.py` enables column type change detection.

## Consequences
- CI must run `alembic upgrade head` against a Postgres service container
  before integration tests (implemented in `.github/workflows/ci.yml`).
- Local development requires running migrations after `git pull` if schema
  changed: `alembic upgrade head`.
- SQLite unit tests bypass Alembic and use `create_all` in-process; this
  is intentional and documented in `tests/conftest.py`.
