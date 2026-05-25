"""
IncidentService — orchestration layer between API routes and IncidentRepository.

Design principles:
  • Routes stay thin: parse input → call service → return typed response.
  • Domain invariants (state machine, field constraints) live in the repository.
  • The service layer translates request primitives into domain types and delegates;
    it does not duplicate validation.
  • list_open() accepts an optional before_id cursor that is pushed down to the
    repository for DB-level evaluation — no Python-side slicing.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.incident_tracker import (
    IncidentRepository,
    IncidentStatus,
    SeverityLevel,
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

        Args:
            limit:     Maximum number of records to return (capped at 1000 by repo).
            before_id: Opaque cursor — the id of the last incident from the
                       previous page. Pass None for the first page.

        Returns:
            List of Incident ORM records.

        Raises:
            ValueError: If before_id does not correspond to an existing incident.
        """
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

        Args:
            incident_id:     UUID string of the target incident.
            new_status:      Desired IncidentStatus enum value.
            transitioned_by: Username of the operator/admin performing the action.

        Returns:
            Updated Incident ORM record.

        Raises:
            InvalidTransitionError: If new_status is not reachable from the
                                    current status per the domain state machine.
            HTTPException 404:     Not raised here; callers must check
                                    get_incident() first.
        """
        return await self._repo.update_status(
            incident_id=incident_id,
            new_status=new_status,
        )
