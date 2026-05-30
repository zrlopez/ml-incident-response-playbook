"""
src/repositories/incident_repository.py
========================================
IncidentRepository — extracted from src/incident_tracker.py (R-P23).

This module owns typed data-access operations for the ``incidents`` table.
It has no engine construction, no FastAPI lifespan concerns, and no
business-orchestration logic (that belongs in src/services/incident_service.py).

Responsibilities:
  - Typed CRUD operations against the incidents table
  - Lifecycle validation via domain policy (CR-2 / src.domain.incident_lifecycle)
  - Structured audit logging on every write

Does NOT own:
  - Business orchestration logic (use src.services.incident_service)
  - HTTP concerns (use the API layer)
  - Alert sending (use observability/logging_config.py send_alert)
  - Engine or session construction (use src.platform.database)

Removal history:
  R-P23: Class moved here from src/incident_tracker.py.
         src/incident_tracker.py retains a re-export for backward compatibility.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.incident_lifecycle import (
    IncidentStatus,
    SeverityLevel,
    validate_status_transition,
)
from src.models.incident import Incident

log = structlog.get_logger(__name__)


class InvalidTransitionError(ValueError):
    """
    Raised when a caller requests an incident status transition that violates
    the lifecycle policy defined in src.domain.incident_lifecycle.

    The API layer should map this to HTTP 409 Conflict with the reason string
    surfaced as a structured error body.
    """


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

    # ─ Reads ──────────────────────────────────────────────────────────────────

    async def get(self, incident_id: str) -> Incident | None:
        """Retrieve a single incident by primary key. Returns None if not found."""
        return await self._session.get(Incident, incident_id)

    def _keyset_cursor_clause(self, cursor_row: Incident):
        """
        Build a compound keyset cursor WHERE clause for stable pagination.

        KEYSET-01: Single-column cursor (created_at < cursor.created_at) is
        ambiguous when multiple incidents share the same created_at timestamp —
        rows created in the same tick can be silently skipped or duplicated
        depending on the DB page boundary.

        The compound predicate:
            (created_at < cursor.created_at)
            OR (created_at = cursor.created_at AND id < cursor.id)

        ..guarantees gapless, stable pagination as long as (created_at, id) is
        the ORDER BY key and the ix_incidents_keyset index covers both columns.

        Note: String UUID comparison is lexicographically ordered and consistent
        within a single page; it is NOT chronologically ordered. This is acceptable
        here because the tiebreaker is only invoked within the same timestamp tick,
        where insertion order within that tick is non-deterministic regardless.
        """
        return or_(
            Incident.created_at < cursor_row.created_at,
            and_(
                Incident.created_at == cursor_row.created_at,
                Incident.id < cursor_row.id,
            ),
        )

    async def list_open(
        self,
        limit: int = 100,
        before_id: str | None = None,
    ) -> list[Incident]:
        """
        Return non-CLOSED incidents ordered newest-first.

        OPEN-02 + KEYSET-01: Compound keyset (cursor) pagination via before_id.
          - When before_id is None, returns the first page (newest limit rows).
          - When before_id is supplied, the compound predicate
            (created_at, id) ensures gapless pagination even under high-velocity
            creation where multiple incidents can share the same created_at tick.
          - Limit is hard-capped at 1000.
          - Backed by ix_incidents_keyset composite index (btree on created_at, id).

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
            .order_by(Incident.created_at.desc(), Incident.id.desc())
            .limit(effective_limit)
        )

        if before_id is not None:
            cursor_row = await self.get(before_id)
            if cursor_row is None:
                raise ValueError(
                    f"Cursor incident_id '{before_id}' not found. "
                    "Pass a valid id from the previous page."
                )
            stmt = stmt.where(self._keyset_cursor_clause(cursor_row))

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

        OPEN-02 + KEYSET-01: Same compound cursor semantics as list_open().

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
            .order_by(Incident.created_at.desc(), Incident.id.desc())
            .limit(effective_limit)
        )

        if before_id is not None:
            cursor_row = await self.get(before_id)
            if cursor_row is None:
                raise ValueError(
                    f"Cursor incident_id '{before_id}' not found. "
                    "Pass a valid id from the previous page."
                )
            stmt = stmt.where(self._keyset_cursor_clause(cursor_row))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ─ Writes ─────────────────────────────────────────────────────────────────

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
        src.domain.incident_lifecycle before any mutation is applied. Invalid
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
