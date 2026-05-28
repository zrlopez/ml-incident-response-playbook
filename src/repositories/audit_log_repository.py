"""
src/repositories/audit_log_repository.py
=========================================
Data-access layer for IncidentAuditLog — Phase 8 (OPEN-06).

Design constraints
------------------
- Append-only: only INSERT operations.  No UPDATE, no DELETE.
- All reads return newest-first (occurred_at DESC).
- Caller is responsible for committing the session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit_log import AuditEventType, IncidentAuditLog

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class AuditLogRepository:
    """Async repository for the incident_audit_log table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write (append-only)
    # ------------------------------------------------------------------
    async def log_event(
        self,
        *,
        incident_id: str,
        event_type: AuditEventType,
        actor: str,
        old_value: str | None = None,
        new_value: str | None = None,
        occurred_at: datetime | None = None,
    ) -> IncidentAuditLog:
        """
        Append a single audit event row.

        Args:
            incident_id: UUID of the parent incident.
            event_type:  One of the AuditEventType enum values.
            actor:       Username of the user who triggered the event.
            old_value:   Previous value (status string, severity string, etc.).
            new_value:   New value after the change.
            occurred_at: Defaults to utcnow() if not supplied.

        Returns:
            The newly created IncidentAuditLog ORM instance (not yet committed).
        """
        row = IncidentAuditLog(
            id=str(uuid.uuid4()),
            incident_id=incident_id,
            event_type=event_type,
            old_value=old_value,
            new_value=new_value,
            actor=actor,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        self._session.add(row)
        await self._session.flush()  # assign DB defaults; caller commits

        log.info(
            "audit_log.event_written",
            incident_id=incident_id,
            event_type=event_type.value,
            actor=actor,
            old_value=old_value,
            new_value=new_value,
        )
        return row

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_events_for_incident(
        self,
        incident_id: str,
        limit: int = 200,
    ) -> Sequence[IncidentAuditLog]:
        """
        Return all audit events for a given incident, newest-first.

        Args:
            incident_id: UUID of the incident.
            limit:       Maximum rows to return (default 200, hard cap 1000).
        """
        effective_limit = min(limit, 1000)
        stmt = (
            select(IncidentAuditLog)
            .where(IncidentAuditLog.incident_id == incident_id)
            .order_by(IncidentAuditLog.occurred_at.desc())
            .limit(effective_limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_status_transitions(
        self,
        incident_id: str,
    ) -> Sequence[IncidentAuditLog]:
        """
        Return only STATUS_TRANSITION events for MTTA/MTTR calculation.
        Ordered oldest-first so callers can walk the lifecycle timeline.
        """
        stmt = (
            select(IncidentAuditLog)
            .where(
                IncidentAuditLog.incident_id == incident_id,
                IncidentAuditLog.event_type == AuditEventType.STATUS_TRANSITION,
            )
            .order_by(IncidentAuditLog.occurred_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
