"""
alembic/env.py — Async Alembic environment for SQLAlchemy 2.0
=============================================================
Phase 3 remediation (ARCH-03 wiring):
  - Configures async engine via DATABASE_URL env var
  - Imports both Base metadata targets (incidents + users tables)
  - run_migrations_online() uses AsyncEngine.connect() pattern
  - Works with both postgresql+asyncpg (prod) and sqlite+aiosqlite (dev/CI)

Usage:
    # Apply all pending migrations:
    alembic upgrade head

    # Generate a new migration after model changes:
    alembic revision --autogenerate -m "describe change"

    # Rollback one step:
    alembic downgrade -1

Database URL resolution order:
    1. DATABASE_URL environment variable
    2. alembic.ini sqlalchemy.url (fallback, dev only)

Notes:
  - run_migrations_online() includes a ThreadPoolExecutor fallback for
    environments where an event loop is already running (e.g. pytest-asyncio).
    This prevents RuntimeError: 'This event loop is already running'.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Import all ORM models so Alembic autogenerate can see them ────────────────
# Both modules share the same DeclarativeBase (src.incident_tracker.Base)
# so importing them here is sufficient — Alembic will diff against Base.metadata.
from src.incident_tracker import Base  # noqa: F401  (Incident model registered here)
from src.users.repository import UserRecord  # noqa: F401  (UserRecord registered here)
# Phase 8: register new ORM models so Alembic autogenerate includes them
from src.models.audit_log import IncidentAuditLog  # noqa: F401
from src.models.model_version import ModelVersion  # noqa: F401

# ── Alembic Config ─────────────────────────────────────────────────────────────
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use full metadata for autogenerate comparisons
target_metadata = Base.metadata


def get_database_url() -> str:
    """Resolve DATABASE_URL: env var takes precedence over alembic.ini."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    ini_url = config.get_main_option("sqlalchemy.url", "")
    if ini_url and ini_url != "driver://user:pass@localhost/dbname":
        return ini_url
    # Safe local fallback for dev — never used in CI or production
    return "sqlite+aiosqlite:///./incidents.db"


# ── Offline migrations (generates SQL without a live DB connection) ────────────
def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.
    Emits SQL to stdout / a file without requiring a live database connection.
    Useful for generating reviewed migration scripts for production DBAs.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations (async engine) ──────────────────────────────────────────
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create async engine, acquire connection, run migrations."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # NullPool: no connection reuse during migrations
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Entry point for online migrations (called by Alembic CLI).

    Handles two runtime contexts:
      1. No running event loop (standard CLI use) — calls asyncio.run() directly.
      2. Running event loop already present (e.g. pytest-asyncio, Jupyter) —
         uses a ThreadPoolExecutor to run migrations in a fresh thread with its
         own event loop, preventing RuntimeError: 'This event loop is already running'.
    """
    try:
        asyncio.get_running_loop()
        # A loop is already running — delegate to a thread with its own loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, run_async_migrations())
            future.result()
    except RuntimeError:
        # No running loop — safe to call asyncio.run() directly.
        asyncio.run(run_async_migrations())


# ── Dispatch ───────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
