"""
Incident tracker — production-grade SQLAlchemy async implementation.

Remediation: R-05
Replaces the original 6-line flat-file appender that had no locking,
no schema, no query capability, and silent data corruption under concurrency.

Architecture:
  - SQLAlchemy 2.0 async ORM (asyncpg for PostgreSQL, aiosqlite for local/test)
  - Connection pool with pre-ping for resilience
  - Enum types for status and severity — invalid values rejected at DB layer
  - Full audit trail: created_at, updated_at, resolved_at (all UTC)
  - IncidentRepository: data access layer with typed methods
  - FastAPI async dependency via get_session()

Database configuration (via src/config.py):
  - Local / test:  database_url = "sqlite+aiosqlite:///./incidents.db"
  - Production:    database_url = "postgresql+asyncpg://user:pass@host:5432/incidents"

Migrations:
  Run `alembic upgrade head` to apply schema changes.
  Never modify __tablename__ without a corresponding Alembic migration.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from enum import Enum

import structlog
from sqlalchemy import DateTime, Enum as SAEnum, String, Text, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import get_settings

log = structlog.get_logger(__name__)


# ── Domain enumerations ────────────────────────────────────────────────────────────

class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class SeverityLevel(str, Enum):
    SEV1 = "SEV-1"
    SEV2 = "SEV-2"
    SEV3 = "SEV-3"
    SEV4 = "SEV-4"


# ── ORM model ─────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Incident(Base):
    """
    Production incident record.

    Every state change is timestamped. resolved_at is set automatically
    when status transitions to RESOLVED if not explicitly provided.
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
        """Serialise to JSON-safe dict for API responses."""
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


# ── Engine + session factory ──────────────────────────────────────────────────────────

def _build_engine(settings=None):
    """Build async SQLAlchemy engine from settings."""
    cfg = settings or get_settings()
    return create_async_engine(
        cfg.database_url,
        pool_size=cfg.db_pool_size,
        max_overflow=cfg.db_max_overflow,
        pool_pre_ping=cfg.db_pool_pre_ping,
        echo=(cfg.app_env == "development"),
    )


_engine = _build_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db() -> None:
    """
    Create all database tables.

    Call once at application startup via the FastAPI lifespan handler.
    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS semantics.
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database.initialized", url_prefix=str(_engine.url).split("@")[-1])


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI async dependency: yield a database session.

    Commits on clean exit, rolls back on exception.
    Session is always closed regardless of outcome.

    Usage:
        @app.get("/incidents")
        async def list_incidents(session: AsyncSession = Depends(get_session)):
            repo = IncidentRepository(session)
            return await repo.list_open()
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
    Data access layer for incident records.

    All methods are async. Every write is logged at INFO level with
    structured fields for SIEM routing (log_type="audit").
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        title: str,
        severity: SeverityLevel,
        category: str,
        owner: str | None = None,
        description: str | None = None,
    ) -> Incident:
        """Create and persist a new incident record."""
        incident = Incident(
            title=title,
            severity=severity,
            category=category,
            owner=owner,
            description=description,
        )
        self._session.add(incident)
        await self._session.flush()  # Populate generated ID before commit
        log.info(
            "incident.created",
            log_type="audit",
            incident_id=incident.id,
            severity=severity.value,
            category=category,
            owner=owner,
        )
        return incident

    async def get(self, incident_id: str) -> Incident | None:
        """Retrieve a single incident by ID. Returns None if not found."""
        return await self._session.get(Incident, incident_id)

    async def update_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        resolved_at: datetime | None = None,
    ) -> Incident:
        """
        Update the status of an existing incident.

        Automatically sets resolved_at to UTC now when transitioning to RESOLVED.

        Raises:
            ValueError: If the incident ID does not exist.
        """
        incident = await self.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id!r} not found")

        previous_status = incident.status
        incident.status = status

        if status == IncidentStatus.RESOLVED and incident.resolved_at is None:
            incident.resolved_at = resolved_at or datetime.now(timezone.utc)

        log.info(
            "incident.status_updated",
            log_type="audit",
            incident_id=incident_id,
            previous_status=previous_status.value,
            new_status=status.value,
            resolved_at=incident.resolved_at.isoformat() if incident.resolved_at else None,
        )
        return incident

    async def list_open(self, limit: int = 100) -> list[Incident]:
        """
        List all non-closed incidents, newest first.

        Args:
            limit: Maximum records to return. Capped at 1000 to prevent
                   accidental full-table scans in production.
        """
        effective_limit = min(limit, 1000)
        stmt = (
            select(Incident)
            .where(Incident.status != IncidentStatus.CLOSED)
            .order_by(Incident.created_at.desc())
            .limit(effective_limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_severity(
        self,
        severity: SeverityLevel,
        limit: int = 100,
    ) -> list[Incident]:
        """List all open incidents of a given severity, newest first."""
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
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
