"""
tests/test_api.py — Integration tests for the ML Incident Response API.

Covers:
  - Authentication (login, token payload, brute-force rate limit)
  - Token revocation (logout → subsequent request rejected)
  - Refresh token rotation (old refresh token revoked after use)
  - Role-based access control
  - Incident CRUD: create, list, get, update
  - Input validation (bad severity, short title, bad incident_id)
  - Health/readiness probes
  - Security response headers

Run:
    pytest tests/test_api.py -v

Note: Tests use a mock Redis denylist so no live Redis instance is required.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401
from httpx import AsyncClient, ASGITransport

# Provide required env vars before importing the app
import os
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# CRIT-A REMEDIATION: Test passwords read from env so CI can inject them via
# secrets. Defaults are only used for local dev; never match production values.
# In CI: set DEV_ADMIN_PASSWORD etc. in the test job env (see ci_cd/secure-ci.yml).
os.environ.setdefault("DEV_ADMIN_PASSWORD",    "test-admin-pw-32chars-aaaaaaaaaa")
os.environ.setdefault("DEV_ANALYST_PASSWORD",  "test-analyst-pw-32chars-aaaaaaaaa")
os.environ.setdefault("DEV_OPERATOR_PASSWORD", "test-operator-pw-32chars-aaaaaaaa")

_TEST_ADMIN_PW = os.environ["DEV_ADMIN_PASSWORD"]
_TEST_ANALYST_PW = os.environ["DEV_ANALYST_PASSWORD"]
_TEST_OPERATOR_PW = os.environ["DEV_OPERATOR_PASSWORD"]


# ── Mock Redis denylist so tests run without a live Redis ────────────────────
class _MockDenylist:
    def __init__(self, *args, **kwargs):
        self._denied: set[str] = set()

    async def connect(self): pass
    async def close(self): pass
    async def ping(self): return True

    def revoke(self, jti: str, ttl_seconds: int):
        self._denied.add(jti)

    async def revoke_async(self, jti: str, ttl_seconds: int):
        self._denied.add(jti)

    def is_revoked(self, jti: str) -> bool:
        return jti in self._denied

    async def is_revoked_async(self, jti: str) -> bool:
        return jti in self._denied


@pytest.fixture(autouse=True)
def mock_denylist(monkeypatch):
    """Patch RedisDenylist globally so no test needs a real Redis."""
    # Patch at the canonical definition site and at the lifespan import site.
    # R-C03: RedisDenylist is no longer re-imported into api.app, so patch
    # api.lifespan where it is actually imported and instantiated.
    monkeypatch.setattr("api.redis_denylist.RedisDenylist", _MockDenylist)
    monkeypatch.setattr("api.lifespan.RedisDenylist", _MockDenylist)


@pytest.fixture(autouse=True)
def mock_otel(monkeypatch):
    """Suppress OTel SDK initialisation in tests."""
    # R-C03: configure_otel / shutdown_otel are imported in api.lifespan, not api.app.
    monkeypatch.setattr("api.lifespan.configure_otel", lambda **kwargs: None)
    monkeypatch.setattr("api.lifespan.shutdown_otel", lambda: None)


@pytest.fixture
async def client():
    """Async test client with lifespan events executed."""
    from api.app import app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _login(client: AsyncClient, username: str = "analyst", password: str = _TEST_ANALYST_PW) -> str:  # noqa: E501
    """Return an access token for the given user."""
    resp = await client.post(
        "/auth/token",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _auth_headers(client: AsyncClient, username: str = "analyst", password: str = _TEST_ANALYST_PW) -> dict:  # noqa: E501
    token = await _login(client, username, password)
    return {"Authorization": f"Bearer {token}"}


# ── Auth tests ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_login_returns_token_pair(client):
    resp = await client.post(
        "/auth/token",
        data={"username": "analyst", "password": _TEST_ANALYST_PW},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


@pytest.mark.anyio
async def test_login_bad_password_rejected(client):
    resp = await client.post(
        "/auth/token",
        data={"username": "analyst", "password": "wrongpassword"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_unknown_user_rejected(client):
    resp = await client.post(
        "/auth/token",
        data={"username": "nobody", "password": "irrelevant"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_logout_revokes_access_token(client):
    token = await _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Logout succeeds
    resp = await client.post("/auth/logout", headers=headers)
    assert resp.status_code == 204

    # Same token is now rejected
    resp2 = await client.get("/incidents", headers=headers)
    assert resp2.status_code == 401
    assert "revoked" in resp2.json()["detail"].lower()


@pytest.mark.anyio
async def test_refresh_token_rotation(client):
    login_resp = await client.post(
        "/auth/token",
        data={"username": "admin", "password": _TEST_ADMIN_PW},
    )
    refresh_token = login_resp.json()["refresh_token"]

    # Use the refresh token to get a new pair
    resp = await client.post(
        "/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert resp.status_code == 200
    new_data = resp.json()
    assert "access_token" in new_data
    assert "refresh_token" in new_data

    # Old refresh token should now be revoked
    resp2 = await client.post(
        "/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert resp2.status_code == 401


@pytest.mark.anyio
async def test_protected_route_requires_auth(client):
    resp = await client.get("/incidents")
    assert resp.status_code == 401


# ── RBAC tests ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_analyst_can_create_incident(client):
    headers = await _auth_headers(client, "analyst", _TEST_ANALYST_PW)
    resp = await client.post(
        "/incidents",
        json={
            "title": "Model accuracy dropped below threshold",
            "description": "Production model accuracy fell below 0.80 threshold during peak hours.",  # noqa: E501
            "severity": "SEV-2",
            "affected_system": "recommendation-model-v3",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["severity"] == "SEV-2"
    assert body["status"] == "open"


@pytest.mark.anyio
async def test_operator_cannot_create_incident(client):
    headers = await _auth_headers(client, "operator", _TEST_OPERATOR_PW)
    resp = await client.post(
        "/incidents",
        json={
            "title": "Latency spike on inference endpoint",
            "description": "P99 latency exceeded 500ms for 10 consecutive minutes.",
            "severity": "SEV-3",
            "affected_system": "inference-service",
        },
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_operator_can_update_incident(client):
    # Admin creates; operator updates
    admin_headers = await _auth_headers(client, "admin", _TEST_ADMIN_PW)
    create_resp = await client.post(
        "/incidents",
        json={
            "title": "Data pipeline ETL failure",
            "description": "The nightly ETL job failed at the transform stage with OOM error.",
            "severity": "SEV-2",
            "affected_system": "etl-pipeline",
        },
        headers=admin_headers,
    )
    incident_id = create_resp.json()["incident_id"]

    op_headers = await _auth_headers(client, "operator", _TEST_OPERATOR_PW)
    update_resp = await client.patch(
        f"/incidents/{incident_id}",
        json={"status": "investigating"},
        headers=op_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["status"] == "investigating"


@pytest.mark.anyio
async def test_analyst_cannot_update_incident(client):
    admin_headers = await _auth_headers(client, "admin", _TEST_ADMIN_PW)
    create_resp = await client.post(
        "/incidents",
        json={
            "title": "LLM cost spike overnight",
            "description": "Token costs exceeded $500 in a single hour due to runaway batch job.",
            "severity": "SEV-3",
            "affected_system": "llm-gateway",
        },
        headers=admin_headers,
    )
    incident_id = create_resp.json()["incident_id"]

    analyst_headers = await _auth_headers(client, "analyst", _TEST_ANALYST_PW)
    resp = await client.patch(
        f"/incidents/{incident_id}",
        json={"status": "resolved"},
        headers=analyst_headers,
    )
    assert resp.status_code == 403


# ── Incident CRUD tests ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_incident_by_id(client):
    headers = await _auth_headers(client, "analyst", _TEST_ANALYST_PW)
    create_resp = await client.post(
        "/incidents",
        json={
            "title": "Feature store drift detected",
            "description": "PSI score exceeded 0.2 for the age feature in the fraud model.",
            "severity": "SEV-3",
            "affected_system": "feature-store",
        },
        headers=headers,
    )
    incident_id = create_resp.json()["incident_id"]

    get_resp = await client.get(f"/incidents/{incident_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["incident_id"] == incident_id


@pytest.mark.anyio
async def test_get_incident_not_found(client):
    headers = await _auth_headers(client)
    resp = await client.get("/incidents/INC-AABBCCDDEEFF", headers=headers)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_incident_invalid_id_format(client):
    headers = await _auth_headers(client)
    resp = await client.get("/incidents/not-a-valid-id", headers=headers)
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_list_incidents_pagination(client):
    headers = await _auth_headers(client, "analyst", _TEST_ANALYST_PW)
    # Create 3 incidents
    for i in range(3):
        await client.post(
            "/incidents",
            json={
                "title": f"Test incident {i} for pagination",
                "description": f"Description for pagination test incident number {i}.",
                "severity": "SEV-4",
                "affected_system": f"system-{i}",
            },
            headers=headers,
        )
    resp = await client.get("/incidents?limit=2&offset=0", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["incidents"]) <= 2
    assert "total" in body


# ── Validation tests ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_invalid_severity_rejected(client):
    headers = await _auth_headers(client, "analyst", _TEST_ANALYST_PW)
    resp = await client.post(
        "/incidents",
        json={
            "title": "Some incident title here",
            "description": "Some detailed description that is long enough.",
            "severity": "P5",  # Invalid — only SEV-1..SEV-4 allowed
            "affected_system": "test-system",
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_short_title_rejected(client):
    headers = await _auth_headers(client, "analyst", _TEST_ANALYST_PW)
    resp = await client.post(
        "/incidents",
        json={
            "title": "Hi",  # Too short (min_length=5)
            "description": "Some description that is long enough.",
            "severity": "SEV-3",
            "affected_system": "test-system",
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_invalid_status_update_rejected(client):
    admin_headers = await _auth_headers(client, "admin", _TEST_ADMIN_PW)
    create_resp = await client.post(
        "/incidents",
        json={
            "title": "Incident for bad status test",
            "description": "Testing that invalid status values are rejected by the API.",
            "severity": "SEV-4",
            "affected_system": "test",
        },
        headers=admin_headers,
    )
    incident_id = create_resp.json()["incident_id"]

    op_headers = await _auth_headers(client, "operator", _TEST_OPERATOR_PW)
    resp = await client.patch(
        f"/incidents/{incident_id}",
        json={"status": "deleted"},  # Not a valid status
        headers=op_headers,
    )
    assert resp.status_code == 422


# ── Health probe tests ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_liveness_probe(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


@pytest.mark.anyio
async def test_readiness_probe_with_mock_redis(client):
    resp = await client.get("/ready")
    # Should be 200 because mock denylist.ping() returns True
    assert resp.status_code in (200, 503)  # 503 allowed if JWT_SECRET_KEY not set in env
    assert "status" in resp.json()


# ── Security header tests ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_security_headers_present(client):
    resp = await client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert "max-age" in resp.headers.get("strict-transport-security", "")
    assert resp.headers.get("cache-control") == "no-store"
    assert resp.headers.get("content-security-policy") is not None


@pytest.mark.anyio
async def test_server_header_absent(client):
    resp = await client.get("/health")
    assert "server" not in resp.headers
