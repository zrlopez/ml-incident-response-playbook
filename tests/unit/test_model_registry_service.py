"""
tests/unit/test_model_registry_service.py
==========================================
Unit tests for ModelRegistryService (Phase 7).

All tests use a *fresh* ModelRegistryService instance constructed with a
monkeypatched model_registry singleton so they run entirely in-process
without touching the real joblib artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_health(
    *,
    model_loaded: bool = True,
    artifact_file: str = "isolation_forest_v1.joblib",
    loaded_at: Optional[float] = 1_700_000_000.0,
) -> dict:
    return {
        "model_loaded": model_loaded,
        "model_version": "1.0.0",
        "artifact_file": artifact_file,
        "artifact_exists": True,
        "loaded_at": loaded_at,
    }


@pytest.fixture()
def service():
    """Fresh ModelRegistryService with the singleton registry mocked out."""
    mock_registry = MagicMock()
    mock_registry.health.return_value = _make_health()

    with patch(
        "src.services.model_registry_service.model_registry",
        mock_registry,
    ):
        # Re-import after patch so __init__ picks up the mock
        from importlib import import_module, reload
        import src.services.model_registry_service as svc_mod
        reload(svc_mod)  # re-runs module-level singleton construction

        yield svc_mod.ModelRegistryService()


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

class TestBootstrap:
    def test_active_version_is_set_when_model_loaded(self, service):
        assert service.active_version == "1.0.0"

    def test_active_version_is_none_when_not_loaded(self):
        mock_registry = MagicMock()
        mock_registry.health.return_value = _make_health(model_loaded=False, loaded_at=None)

        with patch("src.services.model_registry_service.model_registry", mock_registry):
            from src.services.model_registry_service import ModelRegistryService
            svc = ModelRegistryService()

        assert svc.active_version is None

    def test_bootstrap_registers_exactly_one_version(self, service):
        versions = service.list_versions()
        assert len(versions) == 1
        assert versions[0]["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# list_versions / get_version / get_active
# ---------------------------------------------------------------------------

class TestReadOperations:
    def test_list_versions_returns_dicts(self, service):
        result = service.list_versions()
        assert isinstance(result, list)
        assert all(isinstance(v, dict) for v in result)

    def test_get_version_returns_record(self, service):
        record = service.get_version("1.0.0")
        assert record is not None
        assert record["version"] == "1.0.0"

    def test_get_version_missing_returns_none(self, service):
        assert service.get_version("99.99.99") is None

    def test_get_active_returns_record(self, service):
        active = service.get_active()
        assert active is not None
        assert active["status"] == "active"

    def test_get_active_none_when_no_active(self):
        mock_registry = MagicMock()
        mock_registry.health.return_value = _make_health(model_loaded=False, loaded_at=None)

        with patch("src.services.model_registry_service.model_registry", mock_registry):
            from src.services.model_registry_service import ModelRegistryService
            svc = ModelRegistryService()

        assert svc.get_active() is None


# ---------------------------------------------------------------------------
# register_version
# ---------------------------------------------------------------------------

class TestRegisterVersion:
    def test_register_new_version(self, service):
        record = service.register_version(
            version="2.0.0",
            artifact_file="isolation_forest_v2.joblib",
            metrics={"precision": 0.91, "recall": 0.88},
        )
        assert record["version"] == "2.0.0"
        assert record["status"] == "inactive"
        assert record["metrics"]["precision"] == pytest.approx(0.91)

    def test_register_duplicate_raises(self, service):
        with pytest.raises(ValueError, match="already registered"):
            service.register_version(
                version="1.0.0",
                artifact_file="isolation_forest_v1.joblib",
            )

    def test_registered_version_appears_in_list(self, service):
        service.register_version(version="1.1.0", artifact_file="isolation_forest_v1.1.0.joblib")
        versions = [v["version"] for v in service.list_versions()]
        assert "1.1.0" in versions


# ---------------------------------------------------------------------------
# activate_version
# ---------------------------------------------------------------------------

class TestActivateVersion:
    def test_activate_existing_version(self, service, tmp_path, monkeypatch):
        # Patch artifact_exists so the check passes without a real file
        import src.services.model_registry_service as svc_mod
        monkeypatch.setattr(
            svc_mod.ModelVersionRecord,
            "artifact_exists",
            lambda self: True,
        )
        record, previous = service.activate_version("1.0.0")
        assert record["status"] == "active"
        # 1.0.0 was already active, so previous may be "1.0.0" itself or None
        assert service.active_version == "1.0.0"

    def test_activate_demotes_previous(self, service, monkeypatch):
        import src.services.model_registry_service as svc_mod
        monkeypatch.setattr(
            svc_mod.ModelVersionRecord, "artifact_exists", lambda self: True
        )
        # Register a second version and activate it
        service.register_version(version="2.0.0", artifact_file="v2.joblib")
        service.activate_version("2.0.0")

        assert service.active_version == "2.0.0"
        v1_record = service.get_version("1.0.0")
        assert v1_record["status"] == "inactive"

    def test_activate_missing_version_raises_key_error(self, service):
        with pytest.raises(KeyError):
            service.activate_version("0.0.0")

    def test_activate_missing_artifact_raises_value_error(self, service, monkeypatch):
        import src.services.model_registry_service as svc_mod
        monkeypatch.setattr(
            svc_mod.ModelVersionRecord, "artifact_exists", lambda self: False
        )
        service.register_version(version="3.0.0", artifact_file="ghost.joblib")
        with pytest.raises(ValueError, match="not found on disk"):
            service.activate_version("3.0.0")


# ---------------------------------------------------------------------------
# quarantine_version
# ---------------------------------------------------------------------------

class TestQuarantineVersion:
    def test_quarantine_active_version_clears_active(self, service):
        record = service.quarantine_version("1.0.0")
        assert record["status"] == "quarantined"
        assert service.active_version is None

    def test_quarantine_missing_version_raises(self, service):
        with pytest.raises(KeyError):
            service.quarantine_version("99.0.0")

    def test_quarantine_inactive_does_not_affect_other_active(self, service, monkeypatch):
        import src.services.model_registry_service as svc_mod
        monkeypatch.setattr(
            svc_mod.ModelVersionRecord, "artifact_exists", lambda self: True
        )
        service.register_version(version="2.0.0", artifact_file="v2.joblib")
        service.activate_version("2.0.0")  # 1.0.0 demoted to inactive
        service.quarantine_version("1.0.0")  # quarantine inactive version
        # 2.0.0 should still be active
        assert service.active_version == "2.0.0"
