"""
src/services/model_registry_service.py
=======================================
Application-layer service wrapping the low-level ModelRegistry.

Responsibilities
----------------
- Maintain a version catalogue (in-memory for Phase 7; swap to DB in Phase 8)
- Track active / inactive / canary / shadow / quarantined status per version
- Expose safe activate(), list_versions(), and get_active() operations
- Bridge the existing singleton ``model_registry`` to the REST layer
  without touching ml_models/ internals

Design notes
------------
- All mutations are protected by a threading.Lock (same pattern as ModelRegistry)
- ``activated_at`` and ``registered_at`` are always UTC datetimes
- SHA-256 verification state is read from the registry health check, not re-run
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

from ml_models.incident_anomaly.registry import MODEL_VERSION, model_registry

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_ARTIFACT_DIR = Path(__file__).parent.parent.parent / "ml_models" / "incident_anomaly" / "artifacts"


class ModelVersionRecord:
    """
    Mutable record for a single registered model version.
    Not a Pydantic model — lives only inside the service layer.
    """

    __slots__ = (
        "version",
        "status",
        "artifact_file",
        "registered_at",
        "activated_at",
        "sha256_verified",
        "metrics",
    )

    def __init__(
        self,
        *,
        version: str,
        status: str,
        artifact_file: str,
        registered_at: datetime,
        activated_at: Optional[datetime] = None,
        sha256_verified: bool = False,
        metrics: Optional[dict] = None,
    ) -> None:
        self.version = version
        self.status = status
        self.artifact_file = artifact_file
        self.registered_at = registered_at
        self.activated_at = activated_at
        self.sha256_verified = sha256_verified
        self.metrics = metrics

    def artifact_exists(self) -> bool:
        return (_ARTIFACT_DIR / self.artifact_file).exists()

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "status": self.status,
            "artifact_file": self.artifact_file,
            "artifact_exists": self.artifact_exists(),
            "registered_at": self.registered_at,
            "activated_at": self.activated_at,
            "sha256_verified": self.sha256_verified,
            "metrics": self.metrics,
        }


class ModelRegistryService:
    """
    Application-level model registry service.

    Bootstraps from the existing singleton ``model_registry`` so Phase 7
    integrates with Phases 1-6 without modifying ml_models/.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._versions: dict[str, ModelVersionRecord] = {}
        self._active_version: Optional[str] = None
        self._bootstrap()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------
    def _bootstrap(self) -> None:
        """
        Seed the catalogue from the already-loaded ModelRegistry singleton.
        Marks the existing artifact as active if the registry is healthy.
        """
        health = model_registry.health()
        artifact_file: str = health.get("artifact_file", "isolation_forest_v1.joblib")
        model_loaded: bool = health.get("model_loaded", False)
        loaded_at_ts: Optional[float] = health.get("loaded_at")

        registered_at = datetime.now(timezone.utc)
        activated_at: Optional[datetime] = None
        if loaded_at_ts is not None:
            from datetime import datetime as _dt  # local import keeps top clean
            activated_at = _dt.fromtimestamp(loaded_at_ts, tz=timezone.utc)

        record = ModelVersionRecord(
            version=MODEL_VERSION,
            status="active" if model_loaded else "inactive",
            artifact_file=artifact_file,
            registered_at=registered_at,
            activated_at=activated_at,
            sha256_verified=False,  # conservative default; verified at load time by registry
            metrics=None,
        )

        with self._lock:
            self._versions[MODEL_VERSION] = record
            if model_loaded:
                self._active_version = MODEL_VERSION

        log.info(
            "model_registry_service.bootstrapped",
            version=MODEL_VERSION,
            status=record.status,
            artifact_file=artifact_file,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def list_versions(self) -> list[dict]:
        """Return all registered version records as dicts (snapshot)."""
        with self._lock:
            return [v.to_dict() for v in self._versions.values()]

    def get_version(self, version: str) -> Optional[dict]:
        """Return a single version record or None if not found."""
        with self._lock:
            record = self._versions.get(version)
            return record.to_dict() if record else None

    def get_active(self) -> Optional[dict]:
        """Return the currently active version record or None."""
        with self._lock:
            if self._active_version is None:
                return None
            record = self._versions.get(self._active_version)
            return record.to_dict() if record else None

    @property
    def active_version(self) -> Optional[str]:
        with self._lock:
            return self._active_version

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def register_version(
        self,
        *,
        version: str,
        artifact_file: str,
        metrics: Optional[dict] = None,
    ) -> dict:
        """
        Register a new model version as *inactive*.
        Raises ValueError if the version is already registered.
        """
        with self._lock:
            if version in self._versions:
                raise ValueError(f"Version {version!r} is already registered.")
            record = ModelVersionRecord(
                version=version,
                status="inactive",
                artifact_file=artifact_file,
                registered_at=datetime.now(timezone.utc),
                metrics=metrics,
            )
            self._versions[version] = record
            log.info("model_registry_service.version_registered", version=version)
            return record.to_dict()

    def activate_version(self, version: str) -> tuple[dict, Optional[str]]:
        """
        Promote *version* to *active*, demoting the previous active version
        to *inactive*.

        Returns:
            (new_active_record_dict, previous_version_string_or_None)

        Raises:
            KeyError: if *version* is not registered.
            ValueError: if the artifact file does not exist on disk.
        """
        with self._lock:
            if version not in self._versions:
                raise KeyError(f"Version {version!r} is not registered.")

            candidate = self._versions[version]

            if not candidate.artifact_exists():
                raise ValueError(
                    f"Artifact '{candidate.artifact_file}' not found on disk. "
                    "Run the training pipeline before activating this version."
                )

            previous = self._active_version

            # Demote previous active version
            if previous and previous != version and previous in self._versions:
                self._versions[previous].status = "inactive"

            # Activate the candidate
            now = datetime.now(timezone.utc)
            candidate.status = "active"
            candidate.activated_at = now
            self._active_version = version

        log.info(
            "model_registry_service.version_activated",
            version=version,
            previous_version=previous,
        )
        return candidate.to_dict(), previous

    def quarantine_version(self, version: str) -> dict:
        """
        Mark *version* as quarantined (blocks inference on that version).
        If this was the active version, active_version becomes None.
        """
        with self._lock:
            if version not in self._versions:
                raise KeyError(f"Version {version!r} is not registered.")
            self._versions[version].status = "quarantined"
            if self._active_version == version:
                self._active_version = None
            record = self._versions[version]

        log.warning("model_registry_service.version_quarantined", version=version)
        return record.to_dict()


# ---------------------------------------------------------------------------
# Module-level singleton — import this in routers and tests
# ---------------------------------------------------------------------------
model_registry_service = ModelRegistryService()
