"""
IncidentService — business orchestration layer for incident lifecycle management.

ARCH-SVC-01: Extracted from inline route handler logic in api/app.py.

Responsibilities:
  This service layer sits between the API route handlers (api/app.py) and the
  data access layer (src/incident_tracker.IncidentRepository). It is responsible
  for:
    - Coordinating create/update operations with structured audit context
    - Providing the correct integration point for future alert and notification hooks
    - Keeping route handlers thin (HTTP concerns only)

What this service does NOT own:
  - HTTP request/response concerns (remain in api/app.py)
  - Database session management (owned by src/platform/database.py)
  - ORM query construction (owned by IncidentRepository)
  - JWT/authentication concerns (owned by src/auth/)

Integration hooks (future):
  The stub comments below mark the exact locations where alert and notification
  integrations should be wired. No implementation is provided; the structure
  is intentional scaffolding.

  SEV1/SEV2 incidents: wire src.integrations.pagerduty.trigger_incident()
  RESOLVED status:     wire src.integrations.slack.post_resolution_summary()
  CLOSED status:       wire src.integrations.metrics.record_postmortem_due()
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.incident_tracker import (
    Incident,
    IncidentRepository,
    IncidentStatus,
    InvalidTransitionError,
    SeverityLevel,
)

log = structlog.get_logger(__name__)


class IncidentService:
    """
    Orchestrates incident lifecycle operations.

    Constructed per-request with a scoped AsyncSession from get_session().
    Not a singleton — do not store state on the instance across requests.

    Example usage in a route handler:
        from src.services.incident_service import IncidentService
        from src.platform.database import get_session

        @app.post("/incidents/")
        async def create_incident(
            body: CreateIncidentRequest,
            session: Annotated[AsyncSession, Depends(get_session)],
            current_user: Annotated[dict, Depends(require_role("analyst", "admin"))],
        ):
            service = IncidentService(session)
            incident = await service.open_incident(
                title=body.title,
                severity=body.severity,
                category=body.category,
                opened_by=current_user["sub"],
                owner=body.owner,
                description=body.description,
            )
            return IncidentResponse.model_validate(incident.to_dict())
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = IncidentRepository(session)

    async def open_incident(
        self,
        title: str,
        severity: SeverityLevel,
        category: str,
        opened_by: str,
        owner: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Incident:
        """
        Create a new incident in OPEN status.

        The `opened_by` argument is the authenticated username and is included
        in the audit log. It is NOT the same as `owner` (the assigned responder).

        Future integration hooks:
          - SEV1/SEV2: trigger PagerDuty incident after repo.create() returns.
          - All severities: post to #incidents Slack channel (configurable).

        Args:
            title:       Short human-readable incident description.
            severity:    Severity level (SeverityLevel enum).
            category:    Incident category label (e.g., 'model_drift', 'data_pipeline').
            opened_by:   Authenticated username who opened the incident (audit context).
            owner:       Optional assigned responder username or team handle.
            description: Optional extended description.

        Returns:
            Persisted Incident ORM object in OPEN status.
        """
        incident = await self._repo.create(
            title=title,
            severity=severity,
            category=category,
            owner=owner,
            description=description,
        )

        log.info(
            "incident.service.opened",
            log_type="audit",
            incident_id=incident.id,
            severity=severity.value,
            category=category,
            opened_by=opened_by,
        )

        # FUTURE: if severity in (SeverityLevel.SEV1, SeverityLevel.SEV2):
        #     await alert_service.trigger_pagerduty(incident)

        return incident

    async def transition_status(
        self,
        incident_id: str,
        new_status: IncidentStatus,
        transitioned_by: str,
        resolved_at: Optional[datetime] = None,
    ) -> Incident:
        """
        Transition an incident to a new lifecycle status.

        Delegates lifecycle policy enforcement to IncidentRepository.update_status(),
        which validates against the domain state machine and raises
        InvalidTransitionError on invalid transitions.

        Future integration hooks:
          - RESOLVED: post resolution summary to Slack, record MTTA to metrics.
          - CLOSED:   schedule postmortem reminder, close PagerDuty incident.

        Args:
            incident_id:     UUID of the incident to transition.
            new_status:      Target lifecycle status.
            transitioned_by: Authenticated username performing the transition (audit).
            resolved_at:     Optional explicit resolution timestamp (RESOLVED only).

        Returns:
            Updated Incident ORM object.

        Raises:
            ValueError:             If incident_id does not exist.
            InvalidTransitionError: If the requested transition violates lifecycle policy.
        """
        incident = await self._repo.update_status(
            incident_id=incident_id,
            new_status=new_status,
            resolved_at=resolved_at,
        )

        log.info(
            "incident.service.status_transitioned",
            log_type="audit",
            incident_id=incident_id,
            new_status=new_status.value,
            transitioned_by=transitioned_by,
        )

        # FUTURE: if new_status == IncidentStatus.RESOLVED:
        #     await notification_service.post_resolution_summary(incident)

        return incident

    async def get_incident(
        self,
        incident_id: str,
    ) -> Optional[Incident]:
        """Retrieve a single incident by ID. Returns None if not found."""
        return await self._repo.get(incident_id)

    async def list_open(
        self,
        limit: int = 50,
        before_id: Optional[str] = None,
    ) -> list[Incident]:
        """
        Return non-CLOSED incidents, newest first, with cursor pagination.

        API-01: This method is the service layer wrapper for the DB-level cursor
        pagination implemented in IncidentRepository.list_open(). Route handlers
        should call this method rather than the repository directly.

        Args:
            limit:     Page size (default 50, hard cap enforced in repository).
            before_id: Cursor for next page. Pass the `id` of the last
                       incident from the previous response.

        Returns:
            List of Incident ORM objects for this page.
        """
        return await self._repo.list_open(limit=limit, before_id=before_id)
