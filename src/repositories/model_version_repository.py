# =============================================================================
# src/repositories/model_version_repository.py — Phase 9
# =============================================================================
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.model_version import ModelVersion, ModelVersionStatus


class ModelVersionRepository:
    """Async repository for ModelVersion rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    async def upsert(
        self,
        *,
        version: str,
        status: ModelVersionStatus,
        artifact_file: str,
        sha256: Optional[str] = None,
        metrics: Optional[dict] = None,
        registered_at: Optional[datetime] = None,
        activated_at: Optional[datetime] = None,
    ) -> ModelVersion:
        now = datetime.now(timezone.utc)
        row_data = {
            "version": version,
            "status": status,
            "artifact_file": artifact_file,
            "sha256": sha256,
            "metrics_json": json.dumps(metrics) if metrics is not None else None,
            "registered_at": registered_at or now,
            "activated_at": activated_at,
        }
        stmt = (
            sqlite_upsert(ModelVersion)
            .values(**row_data)
            .on_conflict_do_update(
                index_elements=["version"],
                set_={
                    k: row_data[k]
                    for k in ("status", "artifact_file", "sha256", "metrics_json", "activated_at")
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
        return await self.get(version)  # type: ignore[return-value]

    async def set_status(
        self,
        version: str,
        status: ModelVersionStatus,
        activated_at: Optional[datetime] = None,
    ) -> Optional[ModelVersion]:
        values: dict = {"status": status}
        if status == ModelVersionStatus.ACTIVE:
            values["activated_at"] = activated_at or datetime.now(timezone.utc)
        stmt = (
            update(ModelVersion)
            .where(ModelVersion.version == version)
            .values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()
        return await self.get(version)

    async def deactivate_all(self) -> None:
        """Demote every currently-ACTIVE row to INACTIVE."""
        stmt = (
            update(ModelVersion)
            .where(ModelVersion.status == ModelVersionStatus.ACTIVE)
            .values(status=ModelVersionStatus.INACTIVE)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def get(self, version: str) -> Optional[ModelVersion]:
        result = await self._session.execute(
            select(ModelVersion).where(ModelVersion.version == version)
        )
        return result.scalar_one_or_none()

    async def get_active(self) -> Optional[ModelVersion]:
        result = await self._session.execute(
            select(ModelVersion).where(
                ModelVersion.status == ModelVersionStatus.ACTIVE
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[ModelVersion]:
        result = await self._session.execute(
            select(ModelVersion).order_by(ModelVersion.registered_at.desc())
        )
        return list(result.scalars().all())
