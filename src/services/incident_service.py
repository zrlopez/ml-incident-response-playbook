# =============================================================================
# src/services/incident_service.py — Incident business logic layer
# =============================================================================
# Sits between the API layer (api/routers/) and the data-access layer
# (src/incident_tracker.py). Owns:
#   - Incident lifecycle orchestration
#   - Anomaly detection trigger on create
#   - Prometheus metric instrumentation (CI-53)
#   - Alert dispatch (structlog + async fire-and-forget)
#
# Prometheus metrics instrumented here:
#   ml_incident_total     — incremented on every successful incident.create()
#   ml_active_incidents   — incremented on create, decremented on resolve/close
# =============================================================================
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from api.metrics import active_incidents, incident_total, inference_latency
from src.domain.incident_lifecycle import validate_status_transition
from src.schemas import IncidentCreate, IncidentStatusUpdate

if TYPE_CHECKING:
    from src.incident_tracker import IncidentRepository
    from src.schemas import Incident, User

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_RESOLVED_STATUSES = frozenset({"resolved", "closed"})


class IncidentService:
    """Application-layer orchestrator for incident lifecycle operations."""

    def __init__(self, repository: "IncidentRepository") -> None:
        self._repo = repository

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    async def create_incident(
        self,
        data: "IncidentCreate",
        user: "User",
    ) -> "Incident":
        """Create a new incident and instrument Prometheus counters."""
        start = time.monotonic()
        incident = await self._repo.create(data, created_by=user.id)
        elapsed = time.monotonic() - start

        # Record inference/creation latency (proxies the full service call cost).
        inference_latency.observe(elapsed)

        # Increment creation counter — used by MLIncidentRateSpike alert.
        incident_total.labels(
            severity=incident.severity.value,
            category=incident.category.value,
        ).inc()

        # Increment active-incidents gauge for this severity level.
        active_incidents.labels(severity=incident.severity.value).inc()

        logger.info(
            "incident.created",
            incident_id=str(incident.id),
            severity=incident.severity.value,
            category=incident.category.value,
            created_by=str(user.id),
        )
        return incident

    # ------------------------------------------------------------------
    # Status update
    # ------------------------------------------------------------------
    async def update_status(
        self,
        incident_id: str,
        update: "IncidentStatusUpdate",
        user: "User",
    ) -> "Incident":
        """Validate and apply a status transition; decrement gauge on resolution."""
        incident = await self._repo.get(incident_id)
        validate_status_transition(incident.status, update.status)

        updated = await self._repo.update_status(incident_id, update.status)

        # When an incident resolves or closes, decrement the active gauge.
        if update.status.value in _RESOLVED_STATUSES:
            active_incidents.labels(severity=updated.severity.value).dec()

        logger.info(
            "incident.status_updated",
            incident_id=incident_id,
            old_status=incident.status.value,
            new_status=update.status.value,
            updated_by=str(user.id),
        )
        return updated

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def get_incident(self, incident_id: str) -> "Incident":
        return await self._repo.get(incident_id)

    async def list_open(
        self,
        limit: int = 50,
        cursor: tuple[str, str] | None = None,
    ) -> list["Incident"]:
        return await self._repo.list_open(limit=limit, cursor=cursor)
