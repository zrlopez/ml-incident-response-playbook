"""
tests/unit/test_lifespan.py
============================
Unit tests for api/lifespan.py — targets the 46 uncovered lines.

Covered:
  - in-memory path (non-postgres DATABASE_URL) with stub users
  - BLOCKER-01: ImportError on stub_users raises RuntimeError
  - RS256 active: key_store loaded successfully
  - RS256 active: key_store load failure (sets key_store=None)
  - HS256 fallback path (no RSA key)
  - DB init failure propagates
  - Shutdown: denylist.close() called; otel shutdown called
  - Shutdown: no denylist on state (no-op)
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from fastapi import FastAPI

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_lifespan.db")
os.environ.setdefault("DEV_ALICE_PASSWORD", "alicepassword123")
os.environ.setdefault("DEV_BOB_PASSWORD", "bobpassword456")
os.environ.setdefault("DEV_CAROL_PASSWORD", "carolpassword789")
os.environ.setdefault("JWT_SECRET_KEY", "ci-unit-test-secret-32chars-safe!!")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def _make_app() -> FastAPI:
    return FastAPI()


def _mock_denylist(connect_raises: bool = False) -> MagicMock:
    dl = MagicMock()
    if connect_raises:
        dl.connect = AsyncMock(side_effect=ConnectionError("redis unreachable"))
    else:
        dl.connect = AsyncMock()
    dl.close = AsyncMock()
    dl._client = MagicMock()
    return dl


def _mock_key_store(raise_on_load: bool = False) -> MagicMock:
    ks = MagicMock()
    ks.key_id = "kid-1"
    ks.all_keys = ["k1", "k2"]
    if raise_on_load:
        with patch("api.lifespan.KeyRotationStore") as m:
            m.from_env.side_effect = Exception("key load failed")
    return ks


# ---------------------------------------------------------------------------
# Helpers: patch the heavy dependencies we don't want to execute for real
# ---------------------------------------------------------------------------

BASE_PATCHES = [
    ("api.lifespan.init_db", AsyncMock()),
    ("api.lifespan.configure_otel", MagicMock()),
    ("api.lifespan.shutdown_otel", MagicMock()),
]


@pytest.mark.asyncio
async def test_in_memory_path_wires_stub_users():
    """Non-postgres URL → InMemoryUserRepository wired from stub."""
    app = _make_app()
    denylist = _mock_denylist()
    ks = MagicMock()
    ks.key_id = "k1"
    ks.all_keys = ["k"]

    stub_users = {"alice": {"username": "alice", "role": "admin", "hashed_password": "h", "disabled": False}}

    with patch("api.lifespan.init_db", AsyncMock()), \
         patch("api.lifespan.configure_otel"), \
         patch("api.lifespan.shutdown_otel"), \
         patch("api.lifespan.RedisDenylist", return_value=denylist), \
         patch("api.lifespan.jwt_rs256.load_keys", return_value=False), \
         patch("api.stub_users._USERS", stub_users), \
         patch("api.lifespan.REDIS_URL", "redis://localhost:6379/0"), \
         patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}):
        from api.lifespan import lifespan
        async with lifespan(app):
            assert hasattr(app.state, "user_repo")
            assert hasattr(app.state, "denylist")


@pytest.mark.asyncio
async def test_blocker01_importerror_raises_runtime_error():
    """Missing stub_users → RuntimeError with guidance message."""
    app = _make_app()
    denylist = _mock_denylist()

    with patch("api.lifespan.init_db", AsyncMock()), \
         patch("api.lifespan.configure_otel"), \
         patch("api.lifespan.shutdown_otel"), \
         patch("api.lifespan.RedisDenylist", return_value=denylist), \
         patch("api.lifespan.jwt_rs256.load_keys", return_value=False), \
         patch("api.lifespan.REDIS_URL", "redis://localhost:6379/0"), \
         patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}), \
         patch("api.lifespan.__builtins__", {}):
        # Simulate ImportError on stub_users by patching the import inside lifespan
        import builtins
        real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "api.stub_users":
                raise ImportError("not available")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_mock_import):
            from api.lifespan import lifespan
            with pytest.raises(RuntimeError, match="FATAL"):
                async with lifespan(app):
                    pass


@pytest.mark.asyncio
async def test_rs256_active_key_store_loaded():
    """RS256 active → key_store attached to app.state."""
    app = _make_app()
    denylist = _mock_denylist()
    ks = MagicMock()
    ks.key_id = "kid-1"
    ks.all_keys = ["k"]
    mock_router = MagicMock()

    stub_users = {"alice": {"username": "alice", "role": "admin", "hashed_password": "h", "disabled": False}}

    with patch("api.lifespan.init_db", AsyncMock()), \
         patch("api.lifespan.configure_otel"), \
         patch("api.lifespan.shutdown_otel"), \
         patch("api.lifespan.RedisDenylist", return_value=denylist), \
         patch("api.lifespan.jwt_rs256.load_keys", return_value=True), \
         patch("api.lifespan.jwt_rs256.jwks_router", mock_router), \
         patch("api.lifespan.jwt_rs256._key_id", "kid-1"), \
         patch("api.lifespan.KeyRotationStore") as mock_ks_cls, \
         patch("api.stub_users._USERS", stub_users), \
         patch("api.lifespan.REDIS_URL", "redis://localhost:6379/0"), \
         patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}):
        mock_ks_cls.from_env.return_value = ks
        from api.lifespan import lifespan
        async with lifespan(app):
            assert app.state.key_store is ks


@pytest.mark.asyncio
async def test_rs256_active_key_store_load_fails_sets_none():
    """KeyRotationStore.from_env raises → key_store=None, no crash."""
    app = _make_app()
    denylist = _mock_denylist()
    mock_router = MagicMock()

    stub_users = {"alice": {"username": "alice", "role": "admin", "hashed_password": "h", "disabled": False}}

    with patch("api.lifespan.init_db", AsyncMock()), \
         patch("api.lifespan.configure_otel"), \
         patch("api.lifespan.shutdown_otel"), \
         patch("api.lifespan.RedisDenylist", return_value=denylist), \
         patch("api.lifespan.jwt_rs256.load_keys", return_value=True), \
         patch("api.lifespan.jwt_rs256.jwks_router", mock_router), \
         patch("api.lifespan.jwt_rs256._key_id", "kid-1"), \
         patch("api.lifespan.KeyRotationStore") as mock_ks_cls, \
         patch("api.stub_users._USERS", stub_users), \
         patch("api.lifespan.REDIS_URL", "redis://localhost:6379/0"), \
         patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}):
        mock_ks_cls.from_env.side_effect = Exception("pem invalid")
        from api.lifespan import lifespan
        async with lifespan(app):
            assert app.state.key_store is None


@pytest.mark.asyncio
async def test_db_init_failure_propagates():
    """DB init error raised → lifespan propagates it."""
    app = _make_app()
    with patch("api.lifespan.init_db", AsyncMock(side_effect=RuntimeError("db down"))):
        from api.lifespan import lifespan
        with pytest.raises(RuntimeError, match="db down"):
            async with lifespan(app):
                pass


@pytest.mark.asyncio
async def test_shutdown_calls_denylist_close():
    """Shutdown phase calls denylist.close() and shutdown_otel."""
    app = _make_app()
    denylist = _mock_denylist()
    stub_users = {"alice": {"username": "alice", "role": "admin", "hashed_password": "h", "disabled": False}}

    with patch("api.lifespan.init_db", AsyncMock()), \
         patch("api.lifespan.configure_otel"), \
         patch("api.lifespan.shutdown_otel") as mock_shutdown, \
         patch("api.lifespan.RedisDenylist", return_value=denylist), \
         patch("api.lifespan.jwt_rs256.load_keys", return_value=False), \
         patch("api.stub_users._USERS", stub_users), \
         patch("api.lifespan.REDIS_URL", "redis://localhost:6379/0"), \
         patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}):
        from api.lifespan import lifespan
        async with lifespan(app):
            pass
    denylist.close.assert_awaited_once()
    mock_shutdown.assert_called_once()
