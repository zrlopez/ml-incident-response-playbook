"""
Pydantic schemas for Incident API responses.

PYDANTIC-01: Replaces the bare dict returned by Incident.to_dict().
Using a typed Pydantic model as the API response ensures:
  1. Response shape is validated at the serialization boundary — not just at input.
  2. OpenAPI schema generation is accurate and complete in /docs.
  3. Missing or incorrectly typed fields are caught at test time, not runtime.
  4. Response shape changes require an explicit model change, making
     breaking API changes visible in code review.

Usage in route handlers:
    from src.schemas.incident import IncidentResponse, IncidentListResponse

    @app.get("/incidents/{incident_id}", response_model=IncidentResponse)
    async def get_incident(...):
        incident = await repo.get(incident_id)
        return IncidentResponse.model_validate(incident.to_dict())
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class IncidentResponse(BaseModel):
    """
    Typed representation of a single incident as returned by the API.

    Serialized from Incident.to_dict() via model_validate().
    All datetime fields are ISO 8601 UTC strings at the ORM layer;
    Pydantic coerces them to datetime objects for structured validation
    and re-serializes to ISO 8601 on JSON output.
    """

    model_config = ConfigDict(
        # Allow construction from ORM dict (to_dict output) or ORM object directly.
        # populate_by_name enables both alias and field name construction.
        populate_by_name=True,
        # Freeze the model after construction to prevent accidental mutation
        # of response objects in route handler logic.
        frozen=True,
    )

    id: str = Field(..., description="UUID of the incident (UUID4 format).")
    title: str = Field(..., max_length=255, description="Short human-readable incident title.")
    severity: str = Field(
        ...,
        description="Incident severity level. One of: SEV1, SEV2, SEV3, SEV4.",
        pattern=r"^SEV[1-4]$",
    )
    status: str = Field(
        ...,
        description="Current lifecycle status. One of: OPEN, INVESTIGATING, MITIGATED, RESOLVED, CLOSED.",
    )
    category: str = Field(..., max_length=100, description="Incident category / type label.")
    owner: Optional[str] = Field(None, max_length=255, description="Assigned owner username or team.")
    description: Optional[str] = Field(None, description="Extended incident description.")
    created_at: str = Field(..., description="ISO 8601 UTC creation timestamp.")
    updated_at: str = Field(..., description="ISO 8601 UTC last-updated timestamp.")
    resolved_at: Optional[str] = Field(
        None,
        description="ISO 8601 UTC resolution timestamp. Null until incident reaches RESOLVED status.",
    )


class IncidentListResponse(BaseModel):
    """
    Paginated list of incidents.

    API-01: The list_incidents endpoint now returns a cursor-paginated response.
    Clients should pass next_cursor as the before_id query parameter to fetch
    the next page. When next_cursor is null, the client has reached the last page.

    Example response:
        {
          "incidents": [...],
          "next_cursor": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
          "count": 50
        }
    """

    model_config = ConfigDict(frozen=True)

    incidents: list[IncidentResponse] = Field(
        ...,
        description="Page of incident records, ordered newest-first.",
    )
    next_cursor: Optional[str] = Field(
        None,
        description=(
            "Opaque cursor for the next page. Pass as ?before_id=<value> "
            "to retrieve the subsequent page. Null when no further pages exist."
        ),
    )
    count: int = Field(..., description="Number of incidents returned in this response.")
