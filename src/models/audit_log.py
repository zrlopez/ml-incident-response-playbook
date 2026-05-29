"""
src/models/audit_log.py
========================
IncidentAuditLog ORM model — Phase 8 (OPEN-06).

Every status transition and metadata update on an Incident writes one row
here.  This table is the source of truth for MTTA / MTTR calculations.

Schema
------
  id            UUID PK (str)
  incident_id   FK → incidents.id (cascade delete)
  event_type    AuditEventType enum
  old_value     str | None  — previous value (status string, severity string, …)
  new_value     str | None  — new value
  actor         str         — username of the user who triggered the event
  occurred_at   datetime    — UTC timestamp of the event

Design notes
------------
- Uses the same DeclarativeBase as Incident so Alembic autogenerate sees it.
- ForeignKey is a string reference ("incidents.id") to avoid circular imports.
- ix_audit_incident_id index supports efficient per-incident audit queries.
- ix_audit_occurred_at index supports time-range MTTA/MTTR queries.

R-P23 change: Base imported from src.platform.database (was: src.incident_tracker).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.platform.database import Base

if TYPE_CHECKING:
    from src.models.incident import Incident


class AuditEventType(str, enum.Enum):
    STATUS_TRANSITION = "status_transition"
    METADATA_UPDATE   = "metadata_update"
    CREATED           = "created"
    QUARANTINED       = "quarantined"


class IncidentAuditLog(Base):
    """
    Append-only audit log for incident lifecycle events.

    Never mutate rows in this table — only INSERT, never UPDATE/DELETE.
    """

    __tablename__ = "incident_audit_log"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    incident_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        SAEnum(AuditEventType, name="audit_event_type"),
        nullable=False,
    )
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship back to parent Incident (lazy="raise" prevents N+1 by default)
    incident: Mapped[Incident] = relationship(
        "Incident",
        back_populates="audit_logs",
        lazy="raise",
    )

    __table_args__ = (
        Index("ix_audit_incident_id", "incident_id"),
        Index("ix_audit_occurred_at", "occurred_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<IncidentAuditLog id={self.id!r} incident_id={self.incident_id!r} "
            f"event_type={self.event_type.value!r} actor={self.actor!r}>"
        )
