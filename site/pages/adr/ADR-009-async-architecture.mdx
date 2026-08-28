# ADR-009 — Async-First Architecture: asyncio Throughout the Stack

**Status:** Accepted  
**Date:** 2026-05-28  
**Author:** @zrlopez  

## Context

ML incident response APIs are I/O-bound by nature — they query databases, call external alerting systems, and stream logs. During Phase 9, the router and repository layers were wired together and a decision was needed on whether to use synchronous or asynchronous I/O throughout.

## Decision

Adopt **asyncio end-to-end**: FastAPI async route handlers → async SQLAlchemy sessions → asyncpg driver → async Redis client. All background tasks use `asyncio.create_task` or FastAPI's `BackgroundTasks`. No synchronous database calls are permitted in request handlers.

## Consequences

- **Positive:** High concurrency under load without threading overhead — a single uvicorn worker can handle hundreds of concurrent in-flight requests.
- **Positive:** Consistent programming model across the entire stack reduces cognitive overhead.
- **Negative:** Async code is harder to debug; stack traces are less linear. pytest-asyncio is required for all tests touching async code.
- **Negative:** Blocking calls (e.g. a synchronous library) accidentally introduced into an async handler will stall the entire event loop. Enforced via mypy and code review.
- **Mitigation:** `anyio` is used in tests to ensure compatibility with both asyncio and trio backends.
