# ADR-008 — Database Persistence Strategy: SQLite (dev) / PostgreSQL (prod)

**Status:** Accepted  
**Date:** 2026-05-28  
**Author:** @zrlopez  

## Context

The incident tracker requires durable persistence for incidents, audit events, and user records. During Phase 8, the system needed to support both local development (no external services) and production deployment (Hugging Face Spaces with a persistent volume).

## Decision

Use **aiosqlite** for local development and test environments, and **asyncpg + PostgreSQL** for production. SQLAlchemy's async engine abstraction allows the same ORM models and repository layer to work against both backends by switching the `DATABASE_URL` environment variable. Alembic handles schema migrations for both.

## Consequences

- **Positive:** Zero-dependency local dev; no Docker required to run tests. Production uses a battle-tested async driver with connection pooling.
- **Positive:** Repository layer is fully backend-agnostic — no raw SQL leaks into business logic.
- **Negative:** SQLite and PostgreSQL have subtle behavioral differences (e.g. JSON handling, constraint enforcement) that can mask bugs in dev that only surface in prod.
- **Mitigation:** Integration tests run against SQLite but CI spins up a PostgreSQL container via testcontainers for the full suite.
