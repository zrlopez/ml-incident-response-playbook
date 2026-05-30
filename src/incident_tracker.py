"""
Incident tracker — thin re-export facade (R-P23).

This module was previously a monolith containing the ORM model, repository,
engine construction, and session factory. Those concerns have been extracted
into their proper architectural layers:

  ORM model + Base  →  src/models/incident.py
                        src/platform/database.py  (Base)
  Repository        →  src/repositories/incident_repository.py
  Engine + session  →  src/platform/database.py

This file is retained as a backward-compatibility facade. All public names
that callers have historically imported from this module continue to work
without modification. No callers or tests need to be updated.

Removal timeline:
  This facade may be removed once all callers have been migrated to import
  directly from the canonical modules above. Until then, treat this file
  as the stable public API surface for this package.

Remediation history:
  R-05      Replaced 6-line flat-file appender with async ORM + connection pool
  CR-1      Removed create_all bootstrap; startup now delegates to Alembic (2026-05-23)
  CR-2      Wired IncidentRepository.update_status() through domain state machine (2026-05-23)
  OPEN-01   Explicit updated_at write on every status/metadata transition (2026-05-24)
  OPEN-02   Cursor-based (keyset) pagination on list_open() and list_by_severity() (2026-05-24)
  KEYSET-01 Compound (created_at, id) tiebreaker added to keyset cursor WHERE clause
            to prevent silent row drops when incidents share the same created_at timestamp.
  R-P23     Refactored to thin facade; substance moved to canonical modules (2026-05-29).

Pagination discipline (OPEN-02 + KEYSET-01):
  - list_open() and list_by_severity() accept an optional before_id cursor.
  - KEYSET-01: The cursor WHERE uses a compound (created_at, id) predicate:
      WHERE (created_at < cursor.created_at)
         OR (created_at = cursor.created_at AND id < cursor.id)
  - The ix_incidents_keyset index (btree on created_at, id) covers this predicate.

Database URLs:
  - Local / test:  DATABASE_URL=sqlite+aiosqlite:///./incidents.db
  - Production:    DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/incidents
"""

from __future__ import annotations

from src.domain.incident_lifecycle import IncidentStatus, SeverityLevel
from src.models.incident import Incident
from src.platform.database import Base, _engine, _session_factory, get_session
from src.repositories.incident_repository import (
    IncidentRepository,
    InvalidTransitionError,
)

# Re-export init_db from platform — it relies on _engine which now lives there.
# We define it here as a thin wrapper so existing `from src.incident_tracker
# import init_db` calls continue to work.
import os
from sqlalchemy import text
import structlog as _structlog

_log = _structlog.get_logger(__name__)


async def init_db() -> None:
    """
    Verify the database connection and confirm Alembic migration state.

    CR-1 CHANGE: This function no longer calls Base.metadata.create_all.
    Schema creation and evolution are now exclusively owned by Alembic.
    Running `alembic upgrade head` before application startup is REQUIRED.

    Startup behavior:
      - Runs a lightweight connectivity check (SELECT 1).
      - On PostgreSQL: reads the alembic_version table and warns if the schema
        is behind the expected head revision. Does NOT block startup, but does
        emit a WARNING-level structured log event for ops visibility.
      - On SQLite (local/test): skips migration version check because SQLite
        test databases are initialised in-process during test setup.

    Raises:
        RuntimeError: If the database is unreachable at startup.
    """
    url_display = str(_engine.url).split("@")[-1]
    is_sqlite = str(_engine.url).startswith("sqlite")

    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        _log.info("database.connection_verified", url=url_display)
    except Exception as exc:
        _log.error("database.connection_failed", url=url_display, error=str(exc))
        raise RuntimeError(
            f"Database unreachable at startup ({url_display}): {exc}"
        ) from exc

    if is_sqlite:
        _log.info("database.migration_check_skipped", reason="sqlite_local_mode")
        return

    try:
        async with _engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            row = result.fetchone()
            version = row[0] if row else None
        if version is None:
            _log.warning(
                "database.migration_state_unknown",
                detail="alembic_version table is empty — run 'alembic upgrade head'",
            )
        else:
            _log.info("database.migration_verified", alembic_version=version)
    except Exception as exc:
        _log.warning(
            "database.migration_check_failed",
            detail=str(exc),
            action="ensure 'alembic upgrade head' ran before this container started",
        )


# ── Public re-export surface ───────────────────────────────────────────────────
# Everything that was previously defined in this module is re-exported here
# so existing imports continue to resolve without change.
__all__ = [
    "Base",
    "Incident",
    "IncidentStatus",
    "SeverityLevel",
    "InvalidTransitionError",
    "IncidentRepository",
    "get_session",
    "init_db",
]
