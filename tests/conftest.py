"""
Shared pytest fixtures for all test tiers.

This conftest is loaded by every test in tests/unit/, tests/integration/,
and tests/contract/. Fixtures are scoped appropriately:

  function scope  — default, re-created per test
  module scope    — shared across all tests in a file
  session scope   — created once per pytest invocation

SQLite async fixtures (unit tests):
  These use aiosqlite with in-process schema creation so unit tests are fully
  isolated and require no external services. They do NOT call alembic; schema
  is created from ORM metadata directly. This is correct for unit tests and
  intentionally diverges from the production migration path — integration tests
  use the Alembic + Postgres path instead.

Postgres fixtures (integration tests):
  DATABASE_URL env var must be set to a live Postgres URL. In CI this is
  provided by the Postgres service container. Locally, run:
    docker run -p 5432:5432 -e POSTGRES_PASSWORD=dev postgres:16-alpine
  and set DATABASE_URL accordingly.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def anyio_backend():
    """Restrict anyio to asyncio only — eliminates [trio] duplicate test variants."""
    return "asyncio"


from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ── Environment guards ──────────────────────────────────────────────────────────────

# Ensure test runs never accidentally target production
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")
# JWT secret placeholder for unit tests that exercise auth routes
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-key-minimum-32-chars-long-for-testing-only"
)


# ── Settings cache isolation (HIGH-D REMEDIATION) ────────────────────────────────

try:
    from src.config import get_settings as _get_settings
    _HAS_SETTINGS = True
except ImportError:
    _HAS_SETTINGS = False
    _get_settings = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _clear_settings_lru_cache():
    """
    HIGH-D REMEDIATION: Clear pydantic-settings lru_cache before and after
    every test function.

    WHY THIS MATTERS:
        get_settings() is decorated with @lru_cache. Without clearing it,
        env var overrides applied inside one test (e.g. monkeypatching
        JWT_SECRET_KEY or ENVIRONMENT) persist silently into all subsequent
        tests in the same pytest session. This can:
          - Cause auth tests to pass against a stale secret
          - Mask ENVIRONMENT=production guard failures
          - Produce non-deterministic test ordering bugs

    Graceful fallback: if src.config does not exist (early project state or
    alternative config layout), the fixture becomes a no-op rather than
    crashing the entire test suite.
    """
    if _HAS_SETTINGS and hasattr(_get_settings, "cache_clear"):
        _get_settings.cache_clear()
    yield
    if _HAS_SETTINGS and hasattr(_get_settings, "cache_clear"):
        _get_settings.cache_clear()


# ── SQLite async session (unit tests) ───────────────────────────────────────────────

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def sqlite_engine():
    """
    In-memory SQLite async engine with schema created from ORM metadata.
    Dropped after each test function for full isolation.
    """
    from src.incident_tracker import Base

    engine = create_async_engine(SQLITE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def sqlite_session(sqlite_engine) -> AsyncIterator[AsyncSession]:
    """
    Async SQLAlchemy session bound to the in-memory SQLite engine.
    Each test gets a clean session; commits are real within the in-memory DB.
    """
    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def incident_repo(sqlite_session: AsyncSession):
    """
    IncidentRepository bound to the in-memory SQLite session.
    Use this fixture for repository-level unit tests.
    """
    from src.incident_tracker import IncidentRepository

    return IncidentRepository(sqlite_session)


# ── Postgres session (integration tests) ─────────────────────────────────────────────

_POSTGRES_URL = os.getenv("DATABASE_URL", "")


@pytest_asyncio.fixture(scope="module")
async def postgres_engine():
    """
    Module-scoped Postgres engine. Requires DATABASE_URL to point to a live
    Postgres instance with Alembic migrations already applied.
    Skip entire module if DATABASE_URL is not set.
    """
    if not _POSTGRES_URL or "sqlite" in _POSTGRES_URL:
        pytest.skip("DATABASE_URL not set or not Postgres — skipping integration tests")

    engine = create_async_engine(_POSTGRES_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def postgres_session(postgres_engine) -> AsyncIterator[AsyncSession]:
    """
    Function-scoped Postgres session. Uses SAVEPOINT-based rollback to keep
    integration tests isolated without dropping/recreating schema.
    """
    factory = async_sessionmaker(postgres_engine, expire_on_commit=False)
    async with factory() as session:
        # Begin a SAVEPOINT so we can roll back after each test
        await session.begin_nested()
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def pg_incident_repo(postgres_session: AsyncSession):
    """
    IncidentRepository bound to a Postgres session with SAVEPOINT isolation.
    Use this fixture for Postgres-specific integration tests.
    """
    from src.incident_tracker import IncidentRepository

    return IncidentRepository(postgres_session)
