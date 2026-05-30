"""
tests/unit/test_api_lifespan.py

stub_users reads DEV_*_PASSWORD at MODULE IMPORT TIME — set env vars here
before anything that might trigger that import.
"""
from __future__ import annotations

import os

for _var, _val in [
    ("DEV_ADMIN_PASSWORD",    "test-admin-pw-ok"),
    ("DEV_ANALYST_PASSWORD",  "test-analyst-pw"),
    ("DEV_OPERATOR_PASSWORD", "test-operator-pw"),
    ("ENVIRONMENT",           "test"),
]:
    os.environ.setdefault(_var, _val)

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402
import pytest  # noqa: E402

# Build a _STUB_USERS that satisfies UserRecord.from_dict (needs hashed_password)
_HASHED_PW = "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_STUB_USERS = {
    "admin": {
        "username": "admin",
        "hashed_password": _HASHED_PW,
        "role": "admin",
        "disabled": False,
    }
}


def _mock_denylist():
    dl = AsyncMock()
    dl._client = MagicMock()
    dl.connect = AsyncMock()
    dl.close   = AsyncMock()
    return dl


def _base_patches(dl):
    import api.stub_users as stub_mod
    return [
        patch("api.lifespan.RedisDenylist",        return_value=dl),
        patch("src.incident_tracker.init_db",      new=AsyncMock()),
        patch("api.lifespan.configure_otel"),
        patch("api.lifespan.shutdown_otel"),
        patch("api.lifespan.jwt_rs256.load_keys",  return_value=False),
        patch.object(stub_mod, "_USERS",            _STUB_USERS),
    ]


def _make_app():
    from fastapi import FastAPI
    app = FastAPI()
    app.state.user_repo = None
    app.state.denylist  = None
    app.state.redis     = None
    app.state.key_store = None
    return app


class TestLifespanStartup:

    @pytest.mark.anyio
    async def test_startup_wires_in_memory_user_repo(self):
        from src.users.repository import InMemoryUserRepository
        dl  = _mock_denylist()
        app = _make_app()
        p   = _base_patches(dl)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            from api.lifespan import lifespan
            async with lifespan(app):
                assert isinstance(app.state.user_repo, InMemoryUserRepository)

    @pytest.mark.anyio
    async def test_startup_attaches_denylist_and_redis(self):
        dl  = _mock_denylist()
        app = _make_app()
        p   = _base_patches(dl)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            from api.lifespan import lifespan
            async with lifespan(app):
                assert app.state.denylist is dl
                assert app.state.redis    is dl._client

    @pytest.mark.anyio
    async def test_startup_db_failure_propagates(self):
        import api.stub_users as stub_mod
        app = _make_app()
        with (
            patch("src.incident_tracker.init_db",    new=AsyncMock(side_effect=RuntimeError("db down"))),
            patch("api.lifespan.configure_otel"),
            patch("api.lifespan.shutdown_otel"),
            patch("api.lifespan.jwt_rs256.load_keys", return_value=False),
            patch.object(stub_mod, "_USERS",           _STUB_USERS),
        ):
            from api.lifespan import lifespan
            with pytest.raises(RuntimeError, match="db down"):
                async with lifespan(app):
                    pass

    @pytest.mark.anyio
    async def test_shutdown_closes_denylist(self):
        dl  = _mock_denylist()
        app = _make_app()
        p   = _base_patches(dl)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            from api.lifespan import lifespan
            async with lifespan(app):
                pass
        dl.close.assert_awaited_once()
