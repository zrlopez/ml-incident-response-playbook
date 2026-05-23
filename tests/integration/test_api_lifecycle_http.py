"""
API-layer integration tests: incident lifecycle endpoints.

Verifies that the HTTP layer correctly surfaces domain-level state machine
enforcement. These tests hit the FastAPI application via httpx.AsyncClient
using the ASGI test transport (no real network sockets).

Coverage targets:
  - POST /incidents/ -> 201 with OPEN status
  - PATCH /incidents/{id}/status -> 200 for valid transitions
  - PATCH /incidents/{id}/status -> 409 Conflict for blocked transitions
  - PATCH /incidents/{id}/status -> 404 for unknown incident ID
  - Response body on 409 contains 'invalid_transition' error code
  - Response body on 409 contains actionable hint text

These tests use the sqlite_session fixture from conftest.py so they run
without an external database.
"""
import pytest
import pytest_asyncio

from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def app_client(sqlite_engine):
    """
    FastAPI test client backed by in-memory SQLite.
    Overrides the database dependency to use the test engine.
    """
    from api.app import app
    from src.incident_tracker import get_session
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": "Bearer test-token-override"},
    ) as client:
        yield client

    app.dependency_overrides.clear()


# ── Helpers ─────────────────────────────────────────────────────────────────────

async def create_incident(client: AsyncClient) -> str:
    """Create a fresh OPEN incident and return its ID."""
    resp = await client.post("/incidents/", json={
        "title": "API test: model latency spike",
        "severity": "SEV2",
        "category": "latency",
        "owner": "oncall-ml",
    })
    assert resp.status_code == 201, f"Create failed: {resp.text}"
    return resp.json()["id"]


async def patch_status(client: AsyncClient, incident_id: str, new_status: str):
    return await client.patch(
        f"/incidents/{incident_id}/status",
        json={"status": new_status},
    )


# ── Happy path HTTP tests ─────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_create_incident_returns_201(app_client):
    resp = await app_client.post("/incidents/", json={
        "title": "CPU spike on serving cluster",
        "severity": "SEV1",
        "category": "compute",
        "owner": "platform-oncall",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "open"
    assert "id" in body


@pytest.mark.integration
async def test_valid_status_transition_returns_200(app_client):
    inc_id = await create_incident(app_client)
    resp = await patch_status(app_client, inc_id, "investigating")
    assert resp.status_code == 200
    assert resp.json()["status"] == "investigating"


@pytest.mark.integration
async def test_full_lifecycle_via_http(app_client):
    inc_id = await create_incident(app_client)

    for status in ["investigating", "mitigating", "resolved", "closed"]:
        resp = await patch_status(app_client, inc_id, status)
        assert resp.status_code == 200, f"Failed at {status}: {resp.text}"
        assert resp.json()["status"] == status


# ── HTTP 409 enforcement (blocked transitions) ───────────────────────────────────

@pytest.mark.integration
async def test_blocked_transition_returns_409(app_client):
    """OPEN -> RESOLVED is a blocked transition; must return 409."""
    inc_id = await create_incident(app_client)
    resp = await patch_status(app_client, inc_id, "resolved")
    assert resp.status_code == 409


@pytest.mark.integration
async def test_409_response_body_contains_error_code(app_client):
    inc_id = await create_incident(app_client)
    resp = await patch_status(app_client, inc_id, "resolved")
    body = resp.json()
    assert body["error"] == "invalid_transition"


@pytest.mark.integration
async def test_409_response_body_contains_hint(app_client):
    inc_id = await create_incident(app_client)
    resp = await patch_status(app_client, inc_id, "resolved")
    body = resp.json()
    assert "hint" in body
    assert len(body["hint"]) > 0


@pytest.mark.integration
async def test_409_response_body_contains_detail(app_client):
    inc_id = await create_incident(app_client)
    resp = await patch_status(app_client, inc_id, "resolved")
    body = resp.json()
    assert "detail" in body


@pytest.mark.integration
async def test_closed_is_terminal_via_http(app_client):
    inc_id = await create_incident(app_client)
    for status in ["investigating", "resolved", "closed"]:
        await patch_status(app_client, inc_id, status)

    for bad_status in ["open", "investigating", "mitigating", "resolved"]:
        resp = await patch_status(app_client, inc_id, bad_status)
        assert resp.status_code == 409, (
            f"Expected 409 for closed->{bad_status}, got {resp.status_code}"
        )


# ── HTTP 404 (unknown incident) ───────────────────────────────────────────────────────

@pytest.mark.integration
async def test_patch_unknown_incident_returns_404(app_client):
    resp = await patch_status(
        app_client,
        "00000000-0000-0000-0000-000000000000",
        "investigating",
    )
    assert resp.status_code == 404
