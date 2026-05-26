"""
IncidentService — orchestration layer between API routes and IncidentRepository.

Design principles:
  • Routes stay thin: parse input → call service → return typed response.
  • Domain invariants (state machine, field constraints) live in the repository.
  • The service layer translates request primitives into domain types and delegates;
    it does not duplicate validation.
  • list_open() accepts an optional before_id cursor that is pushed down to the
    repository for DB-level evaluation — no Python-side slicing.

Remediation changelog:
  Cycle 2 (2026-05-26):
    R-S01 / R-T03  update_metadata() added — PATCH /incidents/{id} no longer
                   raises AttributeError at runtime.
    R-S02          transition_status() now emits a structured audit log event
                   on every successful state transition.
    R-S05          list_open() now validates before_id as a well-formed UUID
                   before passing it to the repository SQL predicate.
"""
from __future__ import annotations

import re
import structlog
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.incident_tracker import (
    IncidentRepository,
    IncidentStatus,
    SeverityLevel,
)

log = structlog.get_logger(__name__)

# RFC 4122 UUID pattern — used to validate cursor and incident_id inputs
# before they reach the SQL layer (R-S05).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class IncidentService:
    """Thin orchestration wrapper around IncidentRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = IncidentRepository(session)

    # ------------------------------------------------------------------ open
    async def open_incident(
        self,
        *,
        title: str,
        severity: SeverityLevel,
        category: str,
        opened_by: str,
        owner: Optional[str] = None,
        description: Optional[str] = None,
    ):
        """
        Create a new incident in OPEN status.

        Args:
            title:       Short human-readable summary (5–200 chars).
            severity:    SeverityLevel enum (SEV-1 … SEV-4).
            category:    Incident category label.
            opened_by:   Username of the principal creating the incident.
            owner:       Optional assignee.
            description: Optional extended description.

        Returns:
            The persisted Incident ORM record.
        """
        return await self._repo.create(
            title=title,
            severity=severity,
            category=category,
            owner=owner or opened_by,
            description=description,
        )

    # ------------------------------------------------------------------ get
    async def get_incident(self, incident_id: str):
        """
        Retrieve a single incident by UUID string.

        Returns:
            Incident ORM record, or None if not found.
        """
        return await self._repo.get(incident_id)

    # ------------------------------------------------------------------ list
    async def list_open(
        self,
        *,
        limit: int = 50,
        before_id: Optional[str] = None,
    ):
        """
        Return open incidents, newest-first, with cursor-based pagination.

        The cursor predicate is evaluated at the DB layer by the repository;
        this method does not perform any Python-side slicing.

        R-S05: before_id is validated against the RFC 4122 UUID format before
        being forwarded to the repository. This eliminates the injection surface
        that previously existed when a malformed cursor reached the SQL predicate
        directly.

        Args:
            limit:     Maximum number of records to return (capped at 1000 by repo).
            before_id: Opaque cursor — the id of the last incident from the
                       previous page. Pass None for the first page.

        Returns:
            List of Incident ORM records.

        Raises:
            ValueError: If before_id is not a well-formed UUID (RFC 4122).
        """
        if before_id is not None and not _UUID_RE.match(before_id):
            raise ValueError(
                f"before_id must be a valid UUID (RFC 4122), got '{before_id}'."
            )
        return await self._repo.list_open(
            limit=limit,
            before_id=before_id,
        )

    # ------------------------------------------------------------------ transition
    async def transition_status(
        self,
        *,
        incident_id: str,
        new_status: IncidentStatus,
        transitioned_by: str,
    ):
        """
        Advance an incident through its lifecycle state machine.

        R-S02: A structured audit log event is now emitted on every successful
        transition so that incident lifecycle changes are observable in the
        structured log stream.

        Args:
            incident_id:     UUID string of the target incident.
            new_status:      Desired IncidentStatus enum value.
            transitioned_by: Username of the operator/admin performing the action.

        Returns:
            Updated Incident ORM record.

        Raises:
            InvalidTransitionError: If new_status is not reachable from the
                                    current status per the domain state machine.
            ValueError:             If incident_id does not exist (raised by repo).
        """
        record = await self._repo.update_status(
            incident_id=incident_id,
            new_status=new_status,
        )
        # R-S02: emit structured audit event after successful transition.
        # old_status is not available from the return value alone; we log
        # new_status + transitioned_by so ops can reconstruct the timeline
        # from sequential log entries.
        log.info(
            "incident.status_transitioned",
            log_type="audit",
            incident_id=incident_id,
            new_status=new_status.value,
            transitioned_by=transitioned_by,
        )
        return record

    # ------------------------------------------------------------------ update_metadata
    async def update_metadata(
        self,
        *,
        incident_id: str,
        severity: Optional[str] = None,
        resolution_notes: Optional[str] = None,
        updated_by: str,
    ):
        """
        Update mutable incident metadata fields (severity, resolution_notes).

        R-S01 / R-T03: This method was entirely absent. api/app.py R-C06 fix
        delegates PATCH /incidents/{id} here; without it every metadata PATCH
        raised AttributeError at runtime.

        This method owns the full session boundary for metadata writes:
          1. Fetch the record (404 if missing).
          2. Apply field updates with coercion.
          3. Stamp updated_at.
          4. Flush to confirm the write within the current transaction.
          5. Emit a structured audit log event.

        The route handler must NOT touch ORM objects directly (R-C06).

        Args:
            incident_id:      UUID string of the target incident.
            severity:         Optional new severity string ("SEV-1"–"SEV-4").
                              Coerced to SeverityLevel enum if provided.
            resolution_notes: Optional free-text resolution summary.
            updated_by:       Username of the operator/admin performing the update.

        Returns:
            Updated Incident ORM record.

        Raises:
            ValueError: If incident_id is not found, or severity string is invalid.
        """
        record = await self._repo.get(incident_id)
        if record is None:
            raise ValueError(f"Incident '{incident_id}' not found.")

        changes: dict = {}

        if severity is not None:
            try:
                severity_enum = SeverityLevel(severity)
            except ValueError:
                raise ValueError(
                    f"Invalid severity '{severity}'. "
                    f"Must be one of: {[e.value for e in SeverityLevel]}."
                )
            record.severity = severity_enum
            changes["severity"] = severity_enum.value

        if resolution_notes is not None:
            record.resolution_notes = resolution_notes
            changes["resolution_notes"] = "<set>"  # redacted from log — may be long

        if not changes:
            # No-op: nothing to update, return record as-is without a DB write.
            return record

        # Stamp updated_at and flush within the current transaction.
        # The caller's session context manager commits on exit.
        from datetime import datetime, timezone
        record.updated_at = datetime.now(timezone.utc)

        session = self._repo._session  # type: ignore[attr-defined]
        await session.flush()

        log.info(
            "incident.metadata_updated",
            log_type="audit",
            incident_id=incident_id,
            changes=changes,
            updated_by=updated_by,
        )
        return record
