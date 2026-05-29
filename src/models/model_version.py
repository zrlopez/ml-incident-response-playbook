"""
src/models/model_version.py
============================
ModelVersion ORM table — Phase 8.

Replaces the in-memory dict in ModelRegistryService with durable storage.
Each row represents one registered model artifact version.

Schema
------
  version        str PK       — semver string (e.g. "1.0.0")
  status         ModelVersionStatus enum
  artifact_file  str          — filename relative to ml_models/artifacts/
  sha256         str | None   — expected SHA-256 hex digest
  metrics_json   str | None   — JSON-serialised evaluation metrics dict
  registered_at  datetime UTC
  activated_at   datetime | None UTC

Design notes
------------
- Uses same DeclarativeBase (src.incident_tracker.Base) so one Alembic target.
- ix_model_version_status index supports fast "find active" query.
- Only one row may have status='active' at a time — enforced by service layer.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SAEnum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database import Base

class ModelVersionStatus(str, enum.Enum):
    ACTIVE      = "active"
    INACTIVE    = "inactive"
    CANARY      = "canary"
    SHADOW      = "shadow"
    QUARANTINED = "quarantined"

class ModelVersion(Base):
    """
    Durable record for a registered ML model artifact version.

    Invariants (enforced by ModelRegistryService):
      - At most one row has status=ACTIVE at any time.
      - activated_at is set when status transitions to ACTIVE.
      - Rows are never deleted — use QUARANTINED status instead.
    """

    __tablename__ = "model_versions"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[ModelVersionStatus] = mapped_column(
        SAEnum(ModelVersionStatus, name="model_version_status"),
        nullable=False,
        default=ModelVersionStatus.INACTIVE,
    )
    artifact_file: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_model_version_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<ModelVersion version={self.version!r} status={self.status.value!r}>"
        )
