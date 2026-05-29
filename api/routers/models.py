"""
api/routers/models.py
=====================
Model registry REST endpoints.

Routes
------
  GET  /api/v1/models                         — list all registered versions
  GET  /api/v1/models/active                  — get the currently active version
  GET  /api/v1/models/{version}               — get a specific version
  POST /api/v1/models                         — register a new version
  POST /api/v1/models/{version}/activate      — promote a version to active
  POST /api/v1/models/{version}/quarantine    — quarantine a version (admin only)

Phase 9 changes
---------------
- All read routes use get_active_async / list_versions_async so data is
  sourced from the model_versions DB table rather than the in-memory cache.
- All write routes use register_version_async / activate_version_async /
  quarantine_version_async to persist mutations to the DB.
- Each handler receives a DB-backed ModelRegistryService via Depends(get_model_service).

Security:
  - All routes require a valid Bearer JWT.
  - register, activate, and quarantine additionally require the ``admin`` role.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from src.platform.database import get_session
from src.schemas.model import (
    ModelActivateResponse,
    ModelListResponse,
    ModelRegisterRequest,
    ModelVersionResponse,
)
from src.services.model_registry_service import ModelRegistryService

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/models",
    tags=["model-registry"],
)


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

async def get_model_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelRegistryService:
    """Yield a DB-backed ModelRegistryService for the request lifetime."""
    return await ModelRegistryService.create_db_backed(session)


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
    svc: Annotated[ModelRegistryService, Depends(get_model_service)],
) -> ModelListResponse:
    """Return all versions in the model registry catalogue."""
    versions = await svc.list_versions_async()
    # Derive active version from the already-fetched list — avoids a second DB round-trip.
    active_version = next(
        (v["version"] for v in versions if v["status"] == "active"), None
    )
    return ModelListResponse(
        versions=[_record_to_schema(v) for v in versions],
        count=len(versions),
        active_version=active_version,
    )


@router.get(
    "/active",
    response_model=ModelVersionResponse,
    summary="Get the currently active model version",
    status_code=status.HTTP_200_OK,
)
async def get_active_model(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    svc: Annotated[ModelRegistryService, Depends(get_model_service)],
) -> ModelVersionResponse:
    """Return the currently active model version record."""
    record = await svc.get_active_async()
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
    svc: Annotated[ModelRegistryService, Depends(get_model_service)],
) -> ModelVersionResponse:
    """Return the registry record for a specific version string."""
    record = await svc.get_version_async(version)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model version {version!r} not found in registry.",
        )
    return _record_to_schema(record)


@router.post(
    "",
    response_model=ModelVersionResponse,
    summary="Register a new model version",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def register_model(
    body: ModelRegisterRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    svc: Annotated[ModelRegistryService, Depends(get_model_service)],
) -> ModelVersionResponse:
    """Register a new artifact version as inactive."""
    try:
        record = await svc.register_version_async(
            version=body.version,
            artifact_file=body.artifact_file,
            metrics=body.metrics,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    log.info(
        "models.register",
        version=body.version,
        actor=current_user.get("sub", "unknown"),
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
    svc: Annotated[ModelRegistryService, Depends(get_model_service)],
) -> ModelActivateResponse:
    """
    Promote *version* to the active slot, demoting the previous active version.

    Raises:
        404: version not registered.
        409: artifact file missing on disk.
    """
    try:
        new_record, previous = await svc.activate_version_async(version)
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
        "models.activate",
        version=version,
        previous_version=previous,
        actor=current_user.get("sub", "unknown"),
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
    svc: Annotated[ModelRegistryService, Depends(get_model_service)],
) -> ModelVersionResponse:
    """
    Mark *version* as quarantined, blocking its use in inference.
    If it was active, no version will be active until one is explicitly promoted.
    """
    try:
        record = await svc.quarantine_version_async(version)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    log.warning(
        "models.quarantine",
        version=version,
        actor=current_user.get("sub", "unknown"),
    )
    return _record_to_schema(record)
