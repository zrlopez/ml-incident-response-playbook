"""
Database platform module — engine, session factory, Base, and FastAPI dependency.

ARCH-DB-01: Extracted from src/incident_tracker.py.
ARCH-DB-02 (R-P23): Base (DeclarativeBase) moved here so all ORM models share
  a single metadata registry. Previously, audit_log.py imported Base from
  src.incident_tracker, creating an implicit coupling between the model layer
  and the infrastructure layer. All models now import Base from this module.

Rationale:
  _build_engine() and get_session() were module-level concerns in incident_tracker.py
  (the module itself flagged this as a future refactor in its Platform note).
  Separating database infrastructure from the ORM/repository layer:

    1. Improves testability: tests can substitute the engine or session factory
       without patching the incident_tracker module namespace.
    2. Clarifies responsibility boundaries: incident_tracker.py owns the ORM model
       and repository; this module owns the connection infrastructure.
    3. Enables reuse if additional repositories (e.g., UserRepository,
       AuditLogRepository) are added — they all share one engine, not separate ones.

Usage in FastAPI route handlers:
    from src.platform.database import get_session
    from sqlalchemy.ext.asyncio import AsyncSession
    from typing import Annotated

    @app.get("/incidents/")
    async def list_incidents(
        session: Annotated[AsyncSession, Depends(get_session)],
        ...
    ):
        repo = IncidentRepository(session)
        ...

Usage in tests:
    Override the get_session dependency in the test client fixture:

    app.dependency_overrides[get_session] = lambda: test_session_context()
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings

log = structlog.get_logger(__name__)


# ── Single shared DeclarativeBase ──────────────────────────────────────────────
# All ORM models in this project must inherit from this Base so that
# Base.metadata contains the complete schema for Alembic autogenerate.
class Base(DeclarativeBase):
    pass


def _build_engine(settings=None):
    """
    Construct an async SQLAlchemy engine from Settings.

    Engine parameters:
      - pool_pre_ping=True: validates connections before use, preventing
        stale-connection errors after network interruptions or DB restarts.
      - pool_size=5: base connection pool size (PostgreSQL only).
      - max_overflow=10: connections beyond pool_size allowed under load.
      - echo=True in development: logs all SQL statements for debugging.
        Set ENVIRONMENT=production to disable SQL echo.

    SQLite note:
      pool_size and max_overflow are PostgreSQL-specific parameters.
      SQLite (used in local/test environments) uses a thread-local connection
      model incompatible with these parameters; they are omitted when the
      DATABASE_URL starts with 'sqlite'.
    """
    cfg = settings or get_settings()
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./incidents.db")
    is_sqlite = database_url.startswith("sqlite")

    kwargs: dict = {
        "pool_pre_ping": True,
        "echo": (getattr(cfg, "environment", "development") == "development"),
    }
    if not is_sqlite:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10

    engine = create_async_engine(database_url, **kwargs)
    log.debug("database.engine_created", url=database_url.split("@")[-1])
    return engine


# Module-level singletons — constructed once at import time.
# In tests: use app.dependency_overrides[get_session] to substitute.
_engine = _build_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI async dependency: yield a scoped database session.

    Transaction behavior:
      - Commits on clean exit from the route handler.
      - Rolls back on any unhandled exception.
      - Session is always closed (via async context manager) regardless of outcome.

    To override in tests:
        from src.platform.database import get_session
        app.dependency_overrides[get_session] = my_test_session_factory
    """
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
