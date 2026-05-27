"""
tests/unit/test_inference_router.py
====================================
Coverage tests for the inference router and readiness probe ML gate.

Covers (COV-INF-01 – COV-RDY-05):
  - POST /api/v1/inference/anomaly: happy path, 503 on missing artifact,
    503 on predict() exception
  - GET /api/v1/inference/anomaly/health: happy path
  - GET /ready: ML gate ok, artifact-missing 503, registry-exception 503
  - GET /ready: JWT error branch, Redis ping-exception degraded branch

All tests are fully mocked — no model artifact, no Redis, no Postgres required.
Mirrors the fixture pattern established in tests/unit/test_api.py.

Attribution note:
    All feature vectors are procedurally constructed constants.
    No real incident data, no external datasets.
    Model under test: scikit-learn IsolationForest (BSD-3-Clause).
    See MODEL_CARD.md for full license and BibTeX citation.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen before any api.* import
# ---------------------------------------------------------------------------
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEV_ADMIN_PASSWORD",    "test-admin-pw-32chars-aaaaaaaaaa")
os.environ.setdefault("DEV_ANALYST_PASSWORD",  "test-analyst-pw-32chars-aaaaaaaaa")
os.environ.setdefault("DEV_OPERATOR_PASSWORD", "test-operator-pw-32chars-aaaaaaaa")

_TEST_ADMIN_PW = os.environ["DEV_ADMIN_PASSWORD"]

# ---------------------------------------------------------------------------
# Shared mock helpers (mirrors test_api.py pattern)
# ---------------------------------------------------------------------------

class _MockDenylist:
    def __init__(self, *args, **kwargs):
        self._denied: set[str] = set()
        self._client = None

    async def connect(self) -> None: pass
    async def close(self) -> None: pass
    async def ping(self) -> bool: return True
    async def revoke(self, jti: str, ttl_seconds: int) -> None: self._denied.add(jti)
    async def revoke_async(self, jti: str, ttl_seconds: int) -> None: self._denied.add(jti)
    async def is_revoked(self, jti: str) -> bool: return jti in self._denied
    async def is_revoked_async(self, jti: str) -> bool: return jti in self._denied


class _MockUserRecord:
    def __init__(self, username: str, ud: dict) -> None:
        self.username = username
        self.hashed_password = ud["hashed_password"]
        self.role = ud["role"]
        self.disabled = ud.get("disabled", False)
        self.hash_algorithm = ud.get("hash_algorithm", "argon2id")

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "role": self.role,
            "disabled": self.disabled,
            "hashed_password": self.hashed_password,
            "hash_algorithm": self.hash_algorithm,
        }


class _MockUserRepo:
    def __init__(self, users: dict) -> None:
        self._store = dict(users)

    async def get_by_username(self, username: str):
        data = self._store.get(username)
        if data is None:
            return None
        return _MockUserRecord(username=username, ud=data)

    async def authenticate(self, username: str, plaintext_password: str):
        from src.auth.password import verify_password
        record = await self.get_by_username(username)
        if record is None or record.disabled:
            return None
        if not verify_password(plaintext_password, record.hashed_password):
            return None
        return record

    async def update_password_hash(
        self, username: str, new_hash: str, algorithm: str = "argon2id"
    ) -> None:
        if username in self._store:
            self._store[username]["hashed_password"] = new_hash
            self._store[username]["hash_algorithm"] = algorithm


# ---------------------------------------------------------------------------
# Autouse fixtures — apply to every test in this module
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_denylist(monkeypatch):
    monkeypatch.setattr("api.redis_denylist.RedisDenylist", _MockDenylist)
    monkeypatch.setattr("api.lifespan.RedisDenylist", _MockDenylist)


@pytest.fixture(autouse=True)
def _patch_user_repo(monkeypatch):
    monkeypatch.setattr("src.users.repository.InMemoryUserRepository", _MockUserRepo)


@pytest.fixture(autouse=True)
def _patch_otel(monkeypatch):
    monkeypatch.setattr("api.lifespan.configure_otel", lambda **kwargs: None)
    monkeypatch.setattr("api.lifespan.shutdown_otel", lambda: None)


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    from api.config import limiter
    monkeypatch.setattr(limiter, "_check_request_limit", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# Test client fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    """Async HTTPX client wired to the live module-level app object."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.incident_tracker import Base
    from api.app import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac

    await engine.dispose()


async def _login(client: AsyncClient) -> str:
    """Return a valid Bearer token for admin."""
    resp = await client.post(
        "/auth/token",
        data={"username": "admin", "password": _TEST_ADMIN_PW},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Shared mock data — all synthetic constants, no real incident data
# ---------------------------------------------------------------------------

# Feature vector: boundary-representative values within AnomalyRequest constraints.
_VALID_BODY: dict = {
    "severity_numeric": 2,
    "alert_count": 50,
    "time_to_detect_minutes": 15.0,
    "affected_services": 3,
    "on_call_escalations": 1,
    "duplicate_alert_ratio": 0.2,
    "blast_radius_pct": 30.0,
}

_HEALTH_OK: dict = {
    "artifact_exists": True,
    "model_version": "1.0.0",
    "model_class": "IsolationForest",
    "artifact_path": "ml_models/incident_anomaly/artifacts/isolation_forest_v1.joblib",
}
_HEALTH_MISSING: dict = {**_HEALTH_OK, "artifact_exists": False}

_PREDICT_RESULT: dict = {
    "anomaly_score": -0.25,
    "is_anomalous": True,
    "confidence": 0.72,
    "inference_latency_ms": 2.1,
}


# ---------------------------------------------------------------------------
# COV-INF-01: POST /api/v1/inference/anomaly — happy path (lines 75–108)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inference_anomaly_happy_path(client: AsyncClient) -> None:
    """COV-INF-01: Valid payload + present artifact returns 200 AnomalyResponse."""
    token = await _login(client)
    with (
        patch("api.routers.inference.model_registry.health", return_value=_HEALTH_OK),
        patch("api.routers.inference.model_registry.predict", return_value=_PREDICT_RESULT),
    ):
        resp = await client.post(
            "/api/v1/inference/anomaly",
            json=_VALID_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("anomaly_score", "is_anomalous", "confidence", "model_version", "inference_latency_ms"):
        assert key in body, f"missing key: {key}"


# ---------------------------------------------------------------------------
# COV-INF-02: POST /anomaly — 503 when artifact missing (lines 65–73)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inference_anomaly_503_artifact_missing(client: AsyncClient) -> None:
    """COV-INF-02: 503 returned when health reports artifact_exists=False."""
    token = await _login(client)
    with patch("api.routers.inference.model_registry.health", return_value=_HEALTH_MISSING):
        resp = await client.post(
            "/api/v1/inference/anomaly",
            json=_VALID_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 503
    assert "artifact" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# COV-INF-03: POST /anomaly — 503 when predict() raises (lines 87–92)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inference_anomaly_503_predict_raises(client: AsyncClient) -> None:
    """COV-INF-03: 503 returned when model_registry.predict() raises."""
    token = await _login(client)
    with (
        patch("api.routers.inference.model_registry.health", return_value=_HEALTH_OK),
        patch(
            "api.routers.inference.model_registry.predict",
            side_effect=RuntimeError("simulated model crash"),
        ),
    ):
        resp = await client.post(
            "/api/v1/inference/anomaly",
            json=_VALID_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 503
    assert "inference failed" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# COV-INF-04: GET /api/v1/inference/anomaly/health (line 121)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inference_health_endpoint(client: AsyncClient) -> None:
    """COV-INF-04: /anomaly/health returns model_registry.health() payload."""
    token = await _login(client)
    with patch("api.routers.inference.model_registry.health", return_value=_HEALTH_OK):
        resp = await client.get(
            "/api/v1/inference/anomaly/health",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_exists"] is True
    assert body["model_version"] == "1.0.0"


# ---------------------------------------------------------------------------
# COV-RDY-01: GET /ready — ML gate reports ok (line 80)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ready_ml_gate_ok(client: AsyncClient) -> None:
    """COV-RDY-01: /ready includes ml_anomaly_model=ok(v...) when artifact exists."""
    with patch("api.routers.health.model_registry.health", return_value=_HEALTH_OK):
        resp = await client.get("/ready")
    body = resp.json()
    assert "ml_anomaly_model" in body["checks"]
    assert body["checks"]["ml_anomaly_model"].startswith("ok")


# ---------------------------------------------------------------------------
# COV-RDY-02: GET /ready — ML gate 503 on missing artifact (lines 82–83)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ready_ml_gate_artifact_missing(client: AsyncClient) -> None:
    """COV-RDY-02: /ready returns 503 and error check when artifact absent."""
    with patch("api.routers.health.model_registry.health", return_value=_HEALTH_MISSING):
        resp = await client.get("/ready")
    assert resp.status_code == 503
    assert "error" in resp.json()["checks"]["ml_anomaly_model"]


# ---------------------------------------------------------------------------
# COV-RDY-03: GET /ready — ML gate 503 when registry raises (lines 84–86)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ready_ml_gate_registry_exception(client: AsyncClient) -> None:
    """COV-RDY-03: /ready returns 503 when model_registry.health() raises."""
    with patch(
        "api.routers.health.model_registry.health",
        side_effect=RuntimeError("registry exploded"),
    ):
        resp = await client.get("/ready")
    body = resp.json()
    assert resp.status_code == 503
    assert "error" in body["checks"]["ml_anomaly_model"]


# ---------------------------------------------------------------------------
# COV-RDY-04: GET /ready — JWT error branch (lines 52–54)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ready_jwt_error_branch(client: AsyncClient) -> None:
    """COV-RDY-04: /ready degrades gracefully when create_access_token raises."""
    with (
        patch(
            "api.routers.health.create_access_token",
            side_effect=ValueError("key error"),
        ),
        patch("api.routers.health.model_registry.health", return_value=_HEALTH_OK),
    ):
        resp = await client.get("/ready")
    body = resp.json()
    assert "error" in body["checks"]["jwt_subsystem"]


# ---------------------------------------------------------------------------
# COV-RDY-05: GET /ready — Redis ping exception → degraded (lines 65–68)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ready_redis_ping_exception_degraded(client: AsyncClient) -> None:
    """COV-RDY-05: /ready marks redis_denylist_degraded when ping() raises."""
    from api.app import app

    broken_dl = _MockDenylist()
    broken_dl.ping = AsyncMock(side_effect=ConnectionError("redis down"))  # type: ignore[method-assign]
    # Set directly on app.state; lifespan may not have run in test context.
    app.state.denylist = broken_dl

    with patch("api.routers.health.model_registry.health", return_value=_HEALTH_OK):
        resp = await client.get("/ready")
    body = resp.json()
    assert body["checks"].get("redis_denylist_degraded") == "true"
    # Cleanup so other tests aren't affected by the broken denylist
    del app.state.denylist
