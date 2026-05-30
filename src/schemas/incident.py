"""
Incident response schemas — Phase 5 (API-RESP-01).

These Pydantic v2 models replace bare dict returns from the incident API routes.
All fields are validated at the boundary so callers receive typed, documented
contracts rather than opaque dicts. model_validate() is used with from_attributes=True
so both ORM objects and to_dict() dicts are accepted.

Models
------
IncidentResponse
    Single incident, mirrors Incident ORM fields.
IncidentListResponse
    Paginated list with cursor for next page (API-CURSOR-01).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

class IncidentResponse(BaseModel):
    """
    Typed representation of a single incident returned by the API.

    All datetime fields are serialised as ISO 8601 strings. Fields not present
    on the ORM record (owner, description, resolved_at) default to None so
    model_validate() succeeds even when the database row was created with
    minimal fields.
    """

    model_config = ConfigDict(
        from_attributes=True,  # accept ORM objects directly
        populate_by_name=True,
    )

    id: str = Field(..., description="UUID v4 identifier")
    title: str = Field(..., description="Short human-readable incident summary")
    severity: str = Field(..., description="SEV-1 | SEV-2 | SEV-3 | SEV-4")
    status: str = Field(..., description="Lifecycle status")
    category: str = Field(..., description="Incident category label")
    owner: Optional[str] = Field(default=None, description="Assigned owner")
    description: Optional[str] = Field(default=None, description="Extended description")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last modification timestamp (UTC)")
    resolved_at: Optional[datetime] = Field(
        default=None, description="Resolution timestamp (UTC), null until resolved"
    )

class IncidentListResponse(BaseModel):
    """
    Paginated list response for GET /incidents/.

    API-CURSOR-01: Clients should pass next_cursor as ?before_id= on
    the next request. When next_cursor is null, the last page has been reached.
    """

    incidents: List[IncidentResponse]
    next_cursor: Optional[str] = Field(
        default=None,
        description=(
            "Cursor for the next page. Pass as ?before_id= on the next request. "
            "Null when this is the last page."
        ),
    )
    count: int = Field(..., description="Number of incidents in this page")

class IncidentCreate(BaseModel):
    """
    Input schema for POST /incidents/.

    Validates all required fields at the API boundary before the request
    reaches the service layer. strict=True prevents silent coercion
    (e.g. int -> str for severity).
    """

    model_config = ConfigDict(strict=True)

    title: str = Field(..., description="Short human-readable incident summary")
    severity: str = Field(..., description="SEV-1 | SEV-2 | SEV-3 | SEV-4")
    category: str = Field(..., description="Incident category label")
    description: Optional[str] = Field(default=None, description="Extended description")
    owner: Optional[str] = Field(default=None, description="Assigned owner username")
    model_id: Optional[str] = Field(default=None, description="Associated model identifier")
    metadata: Optional[dict] = Field(default=None, description="Arbitrary key/value context")

class IncidentStatusUpdate(BaseModel):
    """
    Input schema for PATCH /incidents/{id}/status.

    Carries the target status and an optional resolution note. The service
    layer validates the transition via validate_status_transition() before
    persisting.
    """

    model_config = ConfigDict(strict=True)

    status: str = Field(
        ...,
        description="Target lifecycle status: investigating | mitigating | resolved | closed",
    )
    resolution_note: Optional[str] = Field(
        default=None,
        description="Required when transitioning to resolved or closed",
    )
