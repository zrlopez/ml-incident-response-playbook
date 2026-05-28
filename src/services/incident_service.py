# =============================================================================
# src/services/incident_service.py — Incident business logic layer
# =============================================================================
# Sits between the API layer (api/routers/) and the data-access layer
# (src/incident_tracker.py). Owns:
#   - Incident lifecycle orchestration
#   - Prometheus metric instrumentation (CI-53)
#   - Alert dispatch (structlog + async fire-and-forget)
#
# Constructor contract:
#   IncidentService(session: AsyncSession)
#   Internally wraps session in IncidentRepository so callers (routers)
#   never import IncidentRepository directly.
#
# Prometheus metrics instrumented here:
#   ml_incident_total     — incremented on every successful create
#   ml_active_incidents   — incremented on create, decremented on resolve/close
# =============================================================================
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from api.metrics import active_incidents, incident_total, inference_latency
from src.domain.incident_lifecycle import validate_status_transition
from src.incident_tracker import Incident, IncidentRepository, IncidentStatus, SeverityLevel

if TYPE_CHECKING:
    pass  # reserved for future forward-reference imports

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_RESOLVED_STATUSES: frozenset[str] = frozenset({"resolved", "closed"})


class IncidentService:
    """
    Application-layer orchestrator for incident lifecycle operations.

    Accepts an AsyncSession and constructs its own IncidentRepository, so
    routers pass the session directly (API-SVC-01).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = IncidentRepository(session)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    async def open_incident(
        self,
        title: str,
        severity: SeverityLevel,
        category: str,
        opened_by: str,
        owner: str | None = None,
        description: str | None = None,
    ) -> Incident:
        """
        Create a new incident in OPEN status and instrument Prometheus counters.

        Args:
            title:       Human-readable incident title.
            severity:    SeverityLevel enum value.
            category:    Free-form incident category string.
            opened_by:   Username of the requestor (for audit log).
            owner:       Optional assignee username.
            description: Optional extended description.
        """
        start = time.monotonic()
        incident = await self._repo.create(
            title=title,
            severity=severity,
            category=category,
            owner=owner,
            description=description,
        )
        elapsed = time.monotonic() - start

        inference_latency.observe(elapsed)
        incident_total.labels(
            severity=incident.severity.value,
            category=incident.category,
        ).inc()
        active_incidents.labels(severity=incident.severity.value).inc()

        logger.info(
            "incident.created",
            incident_id=str(incident.id),
            severity=incident.severity.value,
            category=incident.category,
            opened_by=opened_by,
        )
        return incident

    # ------------------------------------------------------------------
    # Status transition
    # ------------------------------------------------------------------
    async def transition_status(
        self,
        incident_id: str,
        new_status: IncidentStatus,
        transitioned_by: str,
    ) -> Incident:
        """
        Validate and apply a lifecycle status transition.

        Raises:
            ValueError: incident_id not found (propagated from repository).
            InvalidTransitionError: transition violates domain state machine.
        """
        incident = await self._repo.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident '{incident_id}' not found.")

        # validate_status_transition expects IncidentStatus enums on both sides
        validate_status_transition(incident.status, new_status)

        resolved_at: datetime | None = None
        if new_status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
            resolved_at = datetime.now(timezone.utc)

        updated = await self._repo.update_status(
            incident_id, new_status, resolved_at=resolved_at
        )

        if new_status.value in _RESOLVED_STATUSES:
            active_incidents.labels(severity=updated.severity.value).dec()

        logger.info(
            "incident.status_transitioned",
            incident_id=incident_id,
            old_status=incident.status.value,
            new_status=new_status.value,
            transitioned_by=transitioned_by,
        )
        return updated

    # ------------------------------------------------------------------
    # Metadata update
    # ------------------------------------------------------------------
    async def update_metadata(
        self,
        incident_id: str,
        updated_by: str,
        severity: str | None = None,
        resolution_notes: str | None = None,
    ) -> Incident:
        """
        Update mutable metadata fields (severity, resolution_notes).

        Does NOT change lifecycle status — use transition_status() for that.

        Raises:
            ValueError: incident_id not found.
        """
        incident = await self._repo.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident '{incident_id}' not found.")

        if severity is not None:
            try:
                incident.severity = SeverityLevel(severity)
            except ValueError:
                raise ValueError(
                    f"Invalid severity '{severity}'. Must be one of SEV-1..SEV-4."
                )

        if resolution_notes is not None:
            incident.resolution_notes = resolution_notes

        incident.updated_at = datetime.now(timezone.utc)  # OPEN-01

        logger.info(
            "incident.metadata_updated",
            incident_id=incident_id,
            severity=severity,
            has_resolution_notes=resolution_notes is not None,
            updated_by=updated_by,
        )
        return incident

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_incident(self, incident_id: str) -> Incident | None:
        """Return the incident record, or None if not found."""
        return await self._repo.get(incident_id)

    async def list_open(
        self,
        limit: int = 50,
        before_id: str | None = None,
    ) -> list[Incident]:
        """
        Return non-CLOSED incidents newest-first with cursor pagination.

        Args:
            limit:     Maximum rows (default 50, repository hard-caps at 1000).
            before_id: Keyset cursor — id of the last incident seen on the
                       previous page. Omit for the first page.
        """
        return await self._repo.list_open(limit=limit, before_id=before_id)
