"""
api/routers/models.py
=====================
Model registry REST endpoints (Phase 7).

Routes
------
  GET  /api/v1/models                         — list all registered versions
  GET  /api/v1/models/active                  — get the currently active version
  GET  /api/v1/models/{version}               — get a specific version
  POST /api/v1/models/{version}/activate      — promote a version to active
  POST /api/v1/models/{version}/quarantine    — quarantine a version (admin only)

Security:
  - All routes require a valid Bearer JWT.
  - activate and quarantine additionally require the ``admin`` role.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_current_user, require_role
from src.schemas.model import (
    ModelActivateResponse,
    ModelListResponse,
    ModelVersionResponse,
)
from src.services.model_registry_service import model_registry_service

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/models",
    tags=["model-registry"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_to_schema(record: dict) -> ModelVersionResponse:
    return ModelVersionResponse(
        version=record["version"],
        status=record["status"],
        artifact_file=record["artifact_file"],
        artifact_exists=record["artifact_exists"],
        registered_at=record["registered_at"],
        activated_at=record.get("activated_at"),
        sha256_verified=record.get("sha256_verified", False),
        metrics=record.get("metrics"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ModelListResponse,
    summary="List all registered model versions",
    status_code=status.HTTP_200_OK,
)
async def list_models(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ModelListResponse:
    """Return all versions in the model registry catalogue."""
    versions = model_registry_service.list_versions()
    return ModelListResponse(
        versions=[_record_to_schema(v) for v in versions],
        count=len(versions),
        active_version=model_registry_service.active_version,
    )


@router.get(
    "/active",
    response_model=ModelVersionResponse,
    summary="Get the currently active model version",
    status_code=status.HTTP_200_OK,
)
async def get_active_model(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ModelVersionResponse:
    """Return the currently active model version record."""
    record = model_registry_service.get_active()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active model version found. Activate a version first.",
        )
    return _record_to_schema(record)


@router.get(
    "/{version}",
    response_model=ModelVersionResponse,
    summary="Get a specific model version",
    status_code=status.HTTP_200_OK,
)
async def get_model_version(
    version: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ModelVersionResponse:
    """Return the registry record for a specific version string."""
    record = model_registry_service.get_version(version)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model version {version!r} not found in registry.",
        )
    return _record_to_schema(record)


@router.post(
    "/{version}/activate",
    response_model=ModelActivateResponse,
    summary="Activate a registered model version",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role("admin"))],
)
async def activate_model(
    version: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ModelActivateResponse:
    """
    Promote *version* to the active slot, demoting the previous active version.

    Raises:
        404: version not registered.
        409: artifact file missing on disk.
    """
    try:
        new_record, previous = model_registry_service.activate_version(version)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    log.info(
        "models.activate version=%s previous=%s user=%s",
        version,
        previous,
        current_user.get("sub", "unknown"),
    )

    return ModelActivateResponse(
        activated_version=version,
        previous_version=previous,
        activated_at=new_record["activated_at"] or datetime.now(timezone.utc),
        message=f"Version {version!r} is now active.",
    )


@router.post(
    "/{version}/quarantine",
    response_model=ModelVersionResponse,
    summary="Quarantine a model version",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role("admin"))],
)
async def quarantine_model(
    version: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ModelVersionResponse:
    """
    Mark *version* as quarantined, blocking its use in inference.
    If it was active, no version will be active until one is explicitly promoted.
    """
    try:
        record = model_registry_service.quarantine_version(version)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    log.warning(
        "models.quarantine version=%s user=%s",
        version,
        current_user.get("sub", "unknown"),
    )
    return _record_to_schema(record)
