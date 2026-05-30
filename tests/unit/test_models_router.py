"""
tests/unit/test_models_router.py
=================================
Unit tests for api/routers/models.py — targets the 33 uncovered lines.

Covered:
  - list_models: returns ModelListResponse with correct active_version derivation
  - get_active_model: 200 with active record; 404 when no active version
  - get_model_version: 200 with record; 404 when version not found
  - register_model: 201 with new record; 409 on duplicate (ValueError)
  - activate_model: 200 response; 404 on KeyError; 409 on ValueError
  - quarantine_model: 200 response; 404 on KeyError
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_models_router.db")
os.environ.setdefault("JWT_SECRET_KEY", "ci-unit-test-secret-32chars-safe!!")


def _make_record(
    version: str = "v1.0",
    status: str = "inactive",
    activated_at=None,
) -> dict:
    return {
        "version": version,
        "status": status,
        "artifact_file": f"models/{version}.pkl",
        "artifact_exists": True,
        "registered_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "activated_at": activated_at,
        "sha256_verified": True,
        "metrics": {"accuracy": 0.95},
    }


def _make_svc(
    versions=None,
    active_record=None,
    get_version_record=None,
    register_record=None,
    activate_result=None,
    quarantine_record=None,
    activate_side_effect=None,
    register_side_effect=None,
    get_version_side_effect=None,
    quarantine_side_effect=None,
):
    svc = MagicMock()
    svc.list_versions_async = AsyncMock(return_value=versions or [])
    svc.get_active_async = AsyncMock(return_value=active_record)
    svc.get_version_async = AsyncMock(
        return_value=get_version_record,
        side_effect=get_version_side_effect,
    )
    svc.register_version_async = AsyncMock(
        return_value=register_record or _make_record("v2.0"),
        side_effect=register_side_effect,
    )
    svc.activate_version_async = AsyncMock(
        return_value=activate_result or (_make_record("v1.0", "active", datetime.now(timezone.utc)), "none"),
        side_effect=activate_side_effect,
    )
    svc.quarantine_version_async = AsyncMock(
        return_value=quarantine_record or _make_record("v1.0", "quarantined"),
        side_effect=quarantine_side_effect,
    )
    return svc


ADMIN_USER = {"username": "alice", "role": "admin", "sub": "alice", "disabled": False}
USER_USER = {"username": "bob", "role": "user", "sub": "bob", "disabled": False}


class TestListModels:
    @pytest.mark.asyncio
    async def test_returns_all_versions(self):
        from api.routers.models import list_models
        v1 = _make_record("v1.0", "active")
        v2 = _make_record("v2.0", "inactive")
        svc = _make_svc(versions=[v1, v2])
        result = await list_models(current_user=ADMIN_USER, svc=svc)
        assert result.count == 2
        assert result.active_version == "v1.0"

    @pytest.mark.asyncio
    async def test_active_version_none_when_none_active(self):
        from api.routers.models import list_models
        svc = _make_svc(versions=[_make_record("v1.0", "inactive")])
        result = await list_models(current_user=ADMIN_USER, svc=svc)
        assert result.active_version is None


class TestGetActiveModel:
    @pytest.mark.asyncio
    async def test_returns_active_record(self):
        from api.routers.models import get_active_model
        record = _make_record("v1.0", "active", datetime.now(timezone.utc))
        svc = _make_svc(active_record=record)
        result = await get_active_model(current_user=ADMIN_USER, svc=svc)
        assert result.version == "v1.0"

    @pytest.mark.asyncio
    async def test_404_when_no_active(self):
        from api.routers.models import get_active_model
        from fastapi import HTTPException
        svc = _make_svc(active_record=None)
        with pytest.raises(HTTPException) as exc_info:
            await get_active_model(current_user=ADMIN_USER, svc=svc)
        assert exc_info.value.status_code == 404


class TestGetModelVersion:
    @pytest.mark.asyncio
    async def test_returns_specific_version(self):
        from api.routers.models import get_model_version
        record = _make_record("v1.0")
        svc = _make_svc(get_version_record=record)
        result = await get_model_version(version="v1.0", current_user=ADMIN_USER, svc=svc)
        assert result.version == "v1.0"

    @pytest.mark.asyncio
    async def test_404_when_version_not_found(self):
        from api.routers.models import get_model_version
        from fastapi import HTTPException
        svc = _make_svc(get_version_record=None, get_version_side_effect=KeyError("v9.9"))
        with pytest.raises(HTTPException) as exc_info:
            await get_model_version(version="v9.9", current_user=ADMIN_USER, svc=svc)
        assert exc_info.value.status_code == 404


class TestRegisterModel:
    @pytest.mark.asyncio
    async def test_registers_new_version(self):
        from api.routers.models import register_model
        from src.schemas.model import ModelRegisterRequest
        record = _make_record("v2.0")
        svc = _make_svc(register_record=record)
        body = ModelRegisterRequest(version="v2.0", artifact_file="models/v2.0.pkl")
        result = await register_model(body=body, current_user=ADMIN_USER, svc=svc)
        assert result.version == "v2.0"

    @pytest.mark.asyncio
    async def test_409_on_duplicate(self):
        from api.routers.models import register_model
        from src.schemas.model import ModelRegisterRequest
        from fastapi import HTTPException
        svc = _make_svc(register_side_effect=ValueError("already registered"))
        body = ModelRegisterRequest(version="v1.0", artifact_file="models/v1.0.pkl")
        with pytest.raises(HTTPException) as exc_info:
            await register_model(body=body, current_user=ADMIN_USER, svc=svc)
        assert exc_info.value.status_code == 409


class TestActivateModel:
    @pytest.mark.asyncio
    async def test_activates_version(self):
        from api.routers.models import activate_model
        activated_at = datetime.now(timezone.utc)
        record = _make_record("v1.0", "active", activated_at)
        svc = _make_svc(activate_result=(record, "v0.9"))
        result = await activate_model(version="v1.0", current_user=ADMIN_USER, svc=svc)
        assert result.activated_version == "v1.0"
        assert result.previous_version == "v0.9"

    @pytest.mark.asyncio
    async def test_404_on_key_error(self):
        from api.routers.models import activate_model
        from fastapi import HTTPException
        svc = _make_svc(activate_side_effect=KeyError("v9.9"))
        with pytest.raises(HTTPException) as exc_info:
            await activate_model(version="v9.9", current_user=ADMIN_USER, svc=svc)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_409_on_value_error(self):
        from api.routers.models import activate_model
        from fastapi import HTTPException
        svc = _make_svc(activate_side_effect=ValueError("artifact missing"))
        with pytest.raises(HTTPException) as exc_info:
            await activate_model(version="v1.0", current_user=ADMIN_USER, svc=svc)
        assert exc_info.value.status_code == 409


class TestQuarantineModel:
    @pytest.mark.asyncio
    async def test_quarantines_version(self):
        from api.routers.models import quarantine_model
        record = _make_record("v1.0", "quarantined")
        svc = _make_svc(quarantine_record=record)
        result = await quarantine_model(version="v1.0", current_user=ADMIN_USER, svc=svc)
        assert result.status == "quarantined"

    @pytest.mark.asyncio
    async def test_404_on_key_error(self):
        from api.routers.models import quarantine_model
        from fastapi import HTTPException
        svc = _make_svc(quarantine_side_effect=KeyError("v9.9"))
        with pytest.raises(HTTPException) as exc_info:
            await quarantine_model(version="v9.9", current_user=ADMIN_USER, svc=svc)
        assert exc_info.value.status_code == 404
