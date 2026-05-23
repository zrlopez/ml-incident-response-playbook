"""
tests/conftest.py — Shared pytest fixtures and anyio configuration.

The @pytest.mark.anyio decorator in test_api.py requires anyio's pytest
plugin to be active. The `anyio_backend` fixture declared here sets the
async backend to asyncio (compatible with FastAPI's ASGI runner) rather
than trio, which avoids backend-mismatch errors when SQLAlchemy's async
session uses asyncio internals.
"""
import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    """Use asyncio as the anyio backend for all async tests in this session."""
    return "asyncio"
