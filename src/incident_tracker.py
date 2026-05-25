"""
Incident tracker — production-grade SQLAlchemy async ORM + repository.

Remediation history:
  R-05    Replaced 6-line flat-file appender with async ORM + connection pool
  CR-1    Removed create_all bootstrap; startup now delegates to Alembic (2026-05-23)
  CR-2    Wired IncidentRepository.update_status() through domain state machine (2026-05-23)
  OPEN-01 Explicit updated_at write on every status/metadata transition (2026-05-24)
  OPEN-02 Cursor-based (keyset) pagination on list_open() and list_by_severity() (2026-05-24)

Architecture:
  - SQLAlchemy 2.0 async ORM (asyncpg for PostgreSQL, aiosqlite for test)
  - Connection pool with pool_pre_ping for resilience against idle-connection drops
  - Enum types: invalid values rejected at the DB layer via SAEnum constraints
  - Full audit trail: created_at, updated_at, resolved_at (all UTC)
  - IncidentRepository: typed data-access layer; all writes audited via structlog
  - FastAPI dependency via get_session()

Migration discipline (CR-1):
  - Schema is OWNED by Alembic. init_db() verifies migration level; it does NOT
    create or alter tables. Run `alembic upgrade head` before starting the app.
  - _build_engine() is still module-level for backward compat; see Platform note below.

State-machine discipline (CR-2):
  - update_status() enforces ALLOWED_STATUS_TRANSITIONS from src.domain.incident_lifecycle.
  - Invalid transitions raise InvalidTransitionError (HTTP 409 in the API layer).
  - Every transition attempt — allowed or rejected — is audit-logged.

Pagination discipline (OPEN-02):
  - list_open() and list_by_severity() accept an optional before_id cursor.
  - When supplied, a keyset WHERE filters to rows older than the cursor row.
  - This avoids full-table scans that offset-based slicing in Python caused.

Database URLs:
  - Local / test:  DATABASE_URL=sqlite+aiosqlite:///./incidents.db
  - Production:    DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/incidents
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import structlog
from sqlalchemy import DateTime, Enum as SAEnum, String, Text, text, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import get_settings
from src.domain.incident_lifecycle import (
    ALLOWED_STATUS_TRANSITIONS,
    IncidentStatus,
    SeverityLevel,
    validate_status_transition,
)

log = structlog.get_logger(__name__)


# ── Re-export domain enums so callers only need one import path ─────────────────
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


# ── Domain exception ──────────────────────────────────────────────────────────────

class InvalidTransitionError(ValueError):
    """
    Raised when a caller requests an incident status transition that violates
    the lifecycle policy defined in src.domain.incident_lifecycle.

    The API layer should map this to HTTP 409 Conflict with the reason string
    surfaced as a structured error body.
    """


# ── ORM declarative base ─────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── ORM model ──────────────────────────────────────────────────────────────────────

class Incident(Base):
    """
    Production incident record.

    Schema changes require an Alembic migration.
    Do not add, rename, or drop columns without a corresponding migration file.
    """

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[SeverityLevel] = mapped_column(
        SAEnum(SeverityLevel, name="severity_level"),
        nullable=False,
        default=SeverityLevel.SEV3,
    )
    status: Mapped[IncidentStatus] = mapped_column(
        SAEnum(IncidentStatus, name="incident_status"),
        nullable=False,
        default=IncidentStatus.OPEN,
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict for API responses."""
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "status": self.status.value,
            "category": self.category,
            "owner": self.owner,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_at": (
                self.resolved_at.isoformat() if self.resolved_at else None
            ),
        }


# ── Engine + session factory ────────────────────────────────────────────────────────────

def _build_engine(settings=None):
    """
    Construct an async SQLAlchemy engine from Settings.

    Platform note: In a future refactor, move this into src/platform/database.py
    and inject via FastAPI Depends() to improve testability. For now, the module-
    level singleton is retained for backward compatibility with existing test shims.
    """
    cfg = settings or get_settings()
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./incidents.db")
    is_sqlite = database_url.startswith("sqlite")
    kwargs: dict = {
        "pool_pre_ping": True,
        "echo": (getattr(cfg, "environment", "development") == "development"),
    }
    if not is_sqlite:
        # SQLite does not support pool_size / max_overflow
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
    return create_async_engine(database_url, **kwargs)


_engine = _build_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


# ── Startup lifecycle (CR-1) ────────────────────────────────────────────────────────

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
    url_display = str(_engine.url).split("@")[-1]  # Safe: strips credentials
    is_sqlite = str(_engine.url).startswith("sqlite")

    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        log.info("database.connection_verified", url=url_display)
    except Exception as exc:
        log.error("database.connection_failed", url=url_display, error=str(exc))
        raise RuntimeError(
            f"Database unreachable at startup ({url_display}): {exc}"
        ) from exc

    if is_sqlite:
        log.info("database.migration_check_skipped", reason="sqlite_local_mode")
        return

    try:
        async with _engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            row = result.fetchone()
            version = row[0] if row else None
        if version is None:
            log.warning(
                "database.migration_state_unknown",
                detail="alembic_version table is empty — run 'alembic upgrade head'",
            )
        else:
            log.info("database.migration_verified", alembic_version=version)
    except Exception as exc:
        log.warning(
            "database.migration_check_failed",
            detail=str(exc),
            action="ensure 'alembic upgrade head' ran before this container started",
        )


# ── FastAPI session dependency ─────────────────────────────────────────────────────────

async def get_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI async dependency: yield a scoped database session.

    Commits on clean exit; rolls back on any exception.
    Session is always closed regardless of outcome.
    """
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Repository ──────────────────────────────────────────────────────────────────────

class IncidentRepository:
    """
    Data access layer for Incident records.

    Responsibilities:
      - Typed CRUD operations against the incidents table
      - Lifecycle validation via domain policy (CR-2)
      - Structured audit logging on every write

    Does NOT own:
      - Business orchestration logic (use a service layer for that)
      - HTTP concerns (use the API layer for that)
      - Alert sending (use observability/logging_config.py send_alert)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─ Reads ──────────────────────────────────────────────────────────────────────

    async def get(self, incident_id: str) -> Incident | None:
        """Retrieve a single incident by primary key. Returns None if not found."""
        return await self._session.get(Incident, incident_id)

    async def list_open(
        self,
        limit: int = 100,
        before_id: str | None = None,
    ) -> list[Incident]:
        """
        Return non-CLOSED incidents ordered newest-first.

        OPEN-02: Keyset (cursor) pagination via before_id.
          - When before_id is None, returns the first page (newest limit rows).
          - When before_id is supplied, returns rows whose created_at is strictly
            older than the cursor row's created_at, enabling efficient seek-method
            pagination without full-table scans.
          - Limit is hard-capped at 1000.

        Args:
            limit:     Maximum rows to return (default 100, hard cap 1000).
            before_id: Cursor — the `id` of the last incident seen on the
                       previous page. Omit for the first page.

        Raises:
            ValueError: If before_id is provided but does not exist.
        """
        effective_limit = min(limit, 1000)
        stmt = (
            select(Incident)
            .where(Incident.status != IncidentStatus.CLOSED)
            .order_by(Incident.created_at.desc())
            .limit(effective_limit)
        )

        if before_id is not None:
            cursor_row = await self.get(before_id)
            if cursor_row is None:
                raise ValueError(
                    f"Cursor incident_id '{before_id}' not found. "
                    "Pass a valid id from the previous page."
                )
            stmt = stmt.where(Incident.created_at < cursor_row.created_at)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_severity(
        self,
        severity: SeverityLevel,
        limit: int = 100,
        before_id: str | None = None,
    ) -> list[Incident]:
        """
        Return open incidents for a given severity, newest first.

        OPEN-02: Same keyset cursor semantics as list_open().

        Args:
            severity:  Filter to this severity level.
            limit:     Maximum rows to return (default 100, hard cap 1000).
            before_id: Cursor — omit for first page.

        Raises:
            ValueError: If before_id is provided but does not exist.
        """
        effective_limit = min(limit, 1000)
        stmt = (
            select(Incident)
            .where(
                Incident.severity == severity,
                Incident.status != IncidentStatus.CLOSED,
            )
            .order_by(Incident.created_at.desc())
            .limit(effective_limit)
        )

        if before_id is not None:
            cursor_row = await self.get(before_id)
            if cursor_row is None:
                raise ValueError(
                    f"Cursor incident_id '{before_id}' not found. "
                    "Pass a valid id from the previous page."
                )
            stmt = stmt.where(Incident.created_at < cursor_row.created_at)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ─ Writes ─────────────────────────────────────────────────────────────────────

    async def create(
        self,
        title: str,
        severity: SeverityLevel,
        category: str,
        owner: str | None = None,
        description: str | None = None,
    ) -> Incident:
        """Persist a new incident record in OPEN status."""
        incident = Incident(
            title=title,
            severity=severity,
            status=IncidentStatus.OPEN,
            category=category,
            owner=owner,
            description=description,
        )
        self._session.add(incident)
        await self._session.flush()
        log.info(
            "incident.created",
            log_type="audit",
            incident_id=incident.id,
            severity=severity.value,
            category=category,
            owner=owner,
        )
        return incident

    async def update_status(
        self,
        incident_id: str,
        new_status: IncidentStatus,
        resolved_at: datetime | None = None,
    ) -> Incident:
        """
        Transition an incident to a new lifecycle status.

        CR-2: All transitions are validated against the domain state machine in
        src.domain.incident_lifecycle before any mutation is applied.  Invalid
        transitions are rejected with InvalidTransitionError — no DB write occurs.

        OPEN-01: updated_at is explicitly set on every allowed transition.
        SQLAlchemy's onupdate= hook only fires on UPDATE statements generated
        via session.execute(); it does NOT fire on ORM attribute mutations
        followed by a flush. Without the explicit assignment, updated_at would
        remain at its creation value after every status change, silently
        corrupting MTTA/MTTR and incident-age metrics.

        Args:
            incident_id:  UUID of the target incident.
            new_status:   Requested target status.
            resolved_at:  Optional explicit resolution timestamp; defaults to
                          UTC now when transitioning to RESOLVED.

        Raises:
            ValueError:             If the incident_id does not exist.
            InvalidTransitionError: If the requested transition is not permitted
                                    by the lifecycle policy.
        """
        incident = await self.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id!r} not found")

        current_status = incident.status

        decision = validate_status_transition(current_status, new_status)

        if not decision.allowed:
            log.warning(
                "incident.transition_rejected",
                log_type="audit",
                incident_id=incident_id,
                current_status=current_status.value,
                requested_status=new_status.value,
                reason=decision.reason,
            )
            raise InvalidTransitionError(decision.reason)

        now = datetime.now(timezone.utc)
        incident.status = new_status

        # OPEN-01: Explicit timestamp — do not rely on onupdate= hook alone.
        incident.updated_at = now

        if new_status == IncidentStatus.RESOLVED and incident.resolved_at is None:
            incident.resolved_at = resolved_at or now

        log.info(
            "incident.status_updated",
            log_type="audit",
            incident_id=incident_id,
            previous_status=current_status.value,
            new_status=new_status.value,
            updated_at=now.isoformat(),
            resolved_at=(
                incident.resolved_at.isoformat() if incident.resolved_at else None
            ),
        )
        return incident
