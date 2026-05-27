"""
api/routers/incidents.py
========================
Incident CRUD routes for the ML Incident Response API.

R-GOD Step 9: Extracted from api/app.py.  Contains:
  POST   /incidents/                  — create incident (analyst, admin)
  GET    /incidents/                  — list open incidents, cursor pagination
  GET    /incidents/{incident_id}     — fetch single incident
  PATCH  /incidents/{incident_id}/status  — lifecycle transition
  PATCH  /incidents/{incident_id}     — metadata update

Invariants carried from app.py:
  API-SVC-01  All routes delegate to IncidentService.
  API-RESP-01 All routes return typed IncidentResponse.
  API-CURSOR-01 list uses DB-level cursor pagination via before_id.
  R-C07       update_status has no pre-check get; repository owns existence check.
  R-C06       update_metadata delegates entirely to IncidentService.update_metadata().
"""
from __future__ import annotations

from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from src.incident_tracker import InvalidTransitionError  # noqa: E402
from api.rate_limit import check_user_rate_limit
from api.schemas import IncidentCreate, StatusUpdate, IncidentUpdate
from src.incident_tracker import IncidentStatus, SeverityLevel, get_session
from src.schemas.incident import IncidentResponse, IncidentListResponse
from src.services.incident_service import IncidentService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post(
    "/",
    status_code=201,
    response_model=IncidentResponse,
    dependencies=[Depends(check_user_rate_limit("incidents"))],
)
async def create_incident(
    incident: IncidentCreate,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """Create a new incident in OPEN status. Requires analyst or admin role."""
    try:
        severity_enum = SeverityLevel(incident.severity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid severity '{incident.severity}'. Must be one of SEV-1..SEV-4.",
        )
    service = IncidentService(session)
    record = await service.open_incident(
        title=incident.title,
        severity=severity_enum,
        category=incident.category,
        opened_by=current_user["username"],
        owner=incident.owner,
        description=incident.description,
    )
    return IncidentResponse.model_validate(record.to_dict())


@router.get("/", response_model=IncidentListResponse)
async def list_incidents(
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
    before_id: Optional[str] = None,
) -> IncidentListResponse:
    """
    List open incidents, newest-first, with cursor-based pagination.

    API-CURSOR-01: DB-level cursor predicate evaluated in the repository.
    Clients pass the returned next_cursor as ?before_id= for the next page.
    """
    service = IncidentService(session)
    try:
        page = await service.list_open(limit=limit, before_id=before_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    incidents = [IncidentResponse.model_validate(i.to_dict()) for i in page]
    next_cursor = incidents[-1].id if len(incidents) == limit else None
    log.info(
        "incident.list",
        returned=len(incidents),
        has_next_page=next_cursor is not None,
        requested_by=current_user["username"],
    )
    return IncidentListResponse(
        incidents=incidents,
        next_cursor=next_cursor,
        count=len(incidents),
    )


@router.get("/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: str,
    current_user: Annotated[dict, Depends(require_role("analyst", "admin", "operator"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """Retrieve a single incident by UUID. Requires analyst, operator, or admin."""
    service = IncidentService(session)
    record = await service.get_incident(incident_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )
    return IncidentResponse.model_validate(record.to_dict())


@router.patch("/{incident_id}/status", response_model=IncidentResponse)
async def update_incident_status(
    incident_id: str,
    update: StatusUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """
    Transition an incident to a new lifecycle status.

    R-C07: Pre-check get_incident() removed; repository update_status() is the
    single authoritative existence check, eliminating the TOCTOU race window.
    """
    try:
        new_status_enum = IncidentStatus(update.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown status '{update.status}'.",
        )
    service = IncidentService(session)
    try:
        record = await service.transition_status(
            incident_id=incident_id,
            new_status=new_status_enum,
            transitioned_by=current_user["username"],
        )
    except InvalidTransitionError:
        # Let the app-level exception handler return 409 with structured body.
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return IncidentResponse.model_validate(record.to_dict())


@router.patch("/{incident_id}", response_model=IncidentResponse)
async def update_incident_metadata(
    incident_id: str,
    update: IncidentUpdate,
    current_user: Annotated[dict, Depends(require_role("operator", "admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentResponse:
    """
    Update mutable incident metadata (resolution_notes, severity).

    R-C06: Route delegates entirely to IncidentService.update_metadata().
    Does NOT change lifecycle status — use PATCH /{id}/status for that.
    """
    service = IncidentService(session)
    try:
        record = await service.update_metadata(
            incident_id=incident_id,
            severity=update.severity,
            resolution_notes=update.resolution_notes,
            updated_by=current_user["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except HTTPException:
        raise
    return IncidentResponse.model_validate(record.to_dict())
