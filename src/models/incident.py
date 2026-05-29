"""
src/models/incident.py
=======================
Incident ORM model — extracted from src/incident_tracker.py (R-P23).

This module owns the SQLAlchemy ORM definition for the ``incidents`` table.
It has no repository logic, no engine construction, and no FastAPI concerns.

Schema changes require an Alembic migration.
Do not add, rename, or drop columns without a corresponding migration file.

Architecture note:
  Base is imported from src.platform.database so that all ORM models in the
  project share a single metadata registry — required for Alembic autogenerate
  to see the complete schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.domain.incident_lifecycle import IncidentStatus, SeverityLevel
from src.platform.database import Base

if TYPE_CHECKING:
    from src.models.audit_log import IncidentAuditLog


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
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Back-reference to append-only audit log rows (OPEN-06)
    audit_logs: Mapped[list[IncidentAuditLog]] = relationship(
        "IncidentAuditLog",
        back_populates="incident",
        lazy="raise",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict for API responses.

        PYDANTIC-01 NOTE: The canonical API-layer serialiser is now
        src.schemas.incident.IncidentResponse, which is a typed Pydantic model
        that validates response shape at the serialization boundary. Use that
        for all new API routes. to_dict() is retained for backward compatibility
        with existing internal callers and tests.
        """
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
