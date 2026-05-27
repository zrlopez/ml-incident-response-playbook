"""
api/schemas.py
==============
Pydantic request / response models for the ML Incident Response API.

R-GOD Step 3: Extracted from api/app.py.  Contains auth-layer and incident
request models.  Kept separate from src/schemas/incident.py to avoid circular
imports — src/schemas owns DB-backed response shapes; this module owns the
HTTP request surface.

No FastAPI route imports.  Safe to use in tests without spinning up the app.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ── Auth schemas ──────────────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    sub: str
    role: str
    jti: str
    exp: int
    iat: int
    token_type: str = "access"


# ── Incident request schemas ─────────────────────────────────────────────────────
class IncidentCreate(BaseModel):
    """
    Request body for POST /incidents.
    Fields aligned with IncidentRepository.create() signature.
    """
    title: str = Field(..., min_length=5, max_length=200)
    severity: str = Field(...)
    category: str = Field(..., min_length=2, max_length=100)
    owner: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()


class StatusUpdate(BaseModel):
    """
    Request body for PATCH /incidents/{id}/status.
    Only the status field is accepted; all other fields are immutable via this endpoint.
    """
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"open", "investigating", "mitigating", "resolved", "closed"}
        if v.lower() not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return v.lower()


class IncidentUpdate(BaseModel):
    """Request body for PATCH /incidents/{id} (metadata-only updates)."""
    resolution_notes: str | None = Field(default=None, max_length=10000)
    severity: str | None = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}
        if v.upper() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.upper()
