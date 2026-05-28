"""
src/schemas/model.py
====================
Pydantic v2 schemas for the model registry API (Phase 7).

Models
------
ModelVersionResponse
    Single model version record as returned by GET /api/v1/models.
ModelListResponse
    Paginated list of model versions.
ModelActivateResponse
    Confirmation envelope returned by POST /api/v1/models/{version}/activate.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ModelVersionResponse(BaseModel):
    """
    Typed representation of a single model version in the registry.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    version: str = Field(..., description="Semver model version string (e.g. '1.0.0')")
    status: str = Field(
        ...,
        description="One of: active | inactive | quarantined | shadow | canary",
    )
    artifact_file: str = Field(..., description="Artifact filename (basename only)")
    artifact_exists: bool = Field(
        ..., description="Whether the artifact file is present on disk"
    )
    registered_at: datetime = Field(..., description="When this version was registered (UTC)")
    activated_at: Optional[datetime] = Field(
        default=None, description="When this version was last set as active (UTC)"
    )
    sha256_verified: bool = Field(
        default=False,
        description="Whether the artifact SHA-256 matched the manifest on last load",
    )
    metrics: Optional[dict] = Field(
        default=None,
        description="Evaluation metrics dict (e.g. precision, recall, f1) if available",
    )


class ModelListResponse(BaseModel):
    """
    Paginated list of model versions.
    """

    versions: List[ModelVersionResponse]
    count: int = Field(..., description="Number of versions returned")
    active_version: Optional[str] = Field(
        default=None, description="Currently active version string"
    )


class ModelActivateResponse(BaseModel):
    """
    Confirmation envelope for POST /api/v1/models/{version}/activate.
    """

    activated_version: str = Field(..., description="Version that was activated")
    previous_version: Optional[str] = Field(
        default=None, description="Version that was previously active"
    )
    activated_at: datetime = Field(..., description="Activation timestamp (UTC)")
    message: str = Field(..., description="Human-readable confirmation message")
