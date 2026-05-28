"""
tests/integration/test_container_smoke.py
==========================================
E2E container smoke test  (TEST-04)

Spins up the production Docker image via testcontainers-python, waits for
the /health liveness probe to return 200, then asserts the minimal API
contract holds in an isolated container environment.

Skip conditions (CI-safe):
  - Docker daemon not reachable on the host.
  - SKIP_CONTAINER_TESTS=1 environment variable set.
  - Image ml-incident-api:smoke-test not pre-built (see below).

How to run locally:
    # Build image once:
    docker build -t ml-incident-api:smoke-test .

    # Run smoke tests:
    JWT_SECRET_KEY='...' DATABASE_URL='...' pytest tests/integration/test_container_smoke.py -v

CI integration:
    The secured_ci.yml `e2e-smoke` job builds the image and sets the
    required env vars before invoking pytest with this file.

Attribution:
    testcontainers-python (Apache-2.0) — https://github.com/testcontainers/testcontainers-python
    httpx (BSD-3-Clause)               — https://www.python-httpx.org
"""
from __future__ import annotations

import os
import time

import pytest

# ---------------------------------------------------------------------------
# Guard: skip entire module if Docker unavailable or explicitly suppressed
# ---------------------------------------------------------------------------

_SKIP_ENV = os.environ.get("SKIP_CONTAINER_TESTS", "").strip() in ("1", "true", "yes")


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import]
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    _SKIP_ENV or not _docker_available(),
    reason="Docker unavailable or SKIP_CONTAINER_TESTS=1",
)

# ---------------------------------------------------------------------------
# Imports (only reached when Docker is available)
# ---------------------------------------------------------------------------

try:
    import httpx
    from testcontainers.core.container import DockerContainer  # type: ignore[import]
except ImportError as exc:  # pragma: no cover
    pytest.skip(f"testcontainers or httpx not installed: {exc}", allow_module_level=True)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_IMAGE = os.environ.get("SMOKE_IMAGE", "ml-incident-api:smoke-test")
_PORT = 8000
_STARTUP_TIMEOUT = 30  # seconds

# Minimal required env vars — use safe non-secret values for smoke testing
_CONTAINER_ENV = {
    "ENVIRONMENT": "test",
    "APP_ENV": "test",
    "JWT_SECRET_KEY": os.environ.get(
        "JWT_SECRET_KEY", "smoke-test-secret-key-32chars-safe"
    ),
    "DATABASE_URL": os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:////tmp/smoke_test.db"
    ),
    "REDIS_URL": os.environ.get("REDIS_URL", ""),
    "LOG_LEVEL": "WARNING",  # reduce noise during smoke run
}


@pytest.fixture(scope="module")
def api_container():
    """Start the API container and yield its base URL. Tears down on exit."""
    container = DockerContainer(_IMAGE)
    for key, val in _CONTAINER_ENV.items():
        if val:  # skip empty strings (e.g. REDIS_URL when not available)
            container = container.with_env(key, val)
    container = container.with_exposed_ports(_PORT)

    container.start()

    # Resolve host:port from container
    host = container.get_container_host_ip()
    port = container.get_exposed_port(_PORT)
    base_url = f"http://{host}:{port}"

    # Wait for liveness probe
    deadline = time.time() + _STARTUP_TIMEOUT
    last_exc: Exception = RuntimeError("container never started")
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                break
        except Exception as exc:
            last_exc = exc
        time.sleep(0.5)
    else:
        container.stop()
        pytest.fail(f"Container /health never returned 200 within {_STARTUP_TIMEOUT}s: {last_exc}")

    yield base_url
    container.stop()


# ---------------------------------------------------------------------------
# Smoke assertions
# ---------------------------------------------------------------------------

class TestContainerSmoke:
    def test_health_returns_200(self, api_container: str) -> None:
        """Liveness probe must return HTTP 200."""
        r = httpx.get(f"{api_container}/health", timeout=5.0)
        assert r.status_code == 200

    def test_health_body_has_status_ok(self, api_container: str) -> None:
        """Health body must include status=ok (not degraded)."""
        r = httpx.get(f"{api_container}/health", timeout=5.0)
        body = r.json()
        assert body.get("status") == "ok"

    def test_ready_endpoint_reachable(self, api_container: str) -> None:
        """/ready readiness probe must respond (200 or 503 are both valid — not 404/500)."""
        r = httpx.get(f"{api_container}/ready", timeout=5.0)
        assert r.status_code in (200, 503), f"Unexpected status: {r.status_code}"

    def test_openapi_schema_available(self, api_container: str) -> None:
        """OpenAPI schema must be accessible at /openapi.json."""
        r = httpx.get(f"{api_container}/openapi.json", timeout=5.0)
        assert r.status_code == 200
        schema = r.json()
        assert "openapi" in schema
        assert "paths" in schema

    def test_unauthenticated_protected_route_returns_401(self, api_container: str) -> None:
        """Any protected route must reject unauthenticated requests with 401, not 500."""
        r = httpx.get(f"{api_container}/incidents", timeout=5.0)
        assert r.status_code == 401, (
            f"Expected 401 for unauthenticated /incidents, got {r.status_code}"
        )

    def test_inference_endpoint_requires_auth(self, api_container: str) -> None:
        """ML inference endpoint must reject unauthenticated POST with 401."""
        r = httpx.post(
            f"{api_container}/inference/predict",
            json={
                "severity_numeric": 1,
                "alert_count": 10,
                "time_to_detect_minutes": 5.0,
                "affected_services": 2,
                "on_call_escalations": 1,
                "duplicate_alert_ratio": 0.1,
                "blast_radius_pct": 20.0,
            },
            timeout=5.0,
        )
        assert r.status_code == 401, (
            f"Expected 401 for unauthenticated /inference/predict, got {r.status_code}"
        )

    def test_no_server_version_header_leaked(self, api_container: str) -> None:
        """Server header must not expose framework/version information."""
        r = httpx.get(f"{api_container}/health", timeout=5.0)
        server_header = r.headers.get("server", "").lower()
        for banned in ("uvicorn", "fastapi", "python", "starlette"):
            assert banned not in server_header, (
                f"Server header leaks '{banned}': {server_header!r}"
            )
