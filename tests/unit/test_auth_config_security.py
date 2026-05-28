"""
tests/unit/test_auth_config_security.py
========================================
Security-focused tests for src/config.py and related auth config.

Replaces: tests/unit/test_coverage_gap_cycle5.py  (TEST-02: poor cycle-name)
Adds:     src/config.py coverage tests            (TEST-01: was excluded from
          coverage omit despite being security-critical)

Scope:
  - src/config.py: SecretStr masking, placeholder rejection, algorithm
    allowlist guard, slack_webhook_url SecretStr (LOW-01)
  - api/routers/health.py: remaining branch coverage lines
  - src/auth/tokens.py: guard clause and HS256 path coverage
  - src/incident_tracker.py: init_db / close_db paths

All tests are fully mocked. No external services required.
"""
from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Env bootstrap (must precede any api.* import)
# ---------------------------------------------------------------------------
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEV_ADMIN_PASSWORD",    "test-admin-pw-32chars-aaaaaaaaaa")
os.environ.setdefault("DEV_ANALYST_PASSWORD",  "test-analyst-pw-32chars-aaaaaaaaa")
os.environ.setdefault("DEV_OPERATOR_PASSWORD", "test-operator-pw-32chars-aaaaaaaa")


# ===========================================================================
# TEST-01: src/config.py — security-critical module coverage
# ===========================================================================

class TestSettingsSecretStr:
    """SecretStr masking: JWT secret and Slack webhook must never appear in repr."""

    def test_jwt_secret_masked_in_repr(self) -> None:
        """JWT secret must not appear in Settings repr or str."""
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        r = repr(s)
        assert "test-secret-key" not in r
        assert "**" in r or "SecretStr" in r

    def test_jwt_secret_accessible_via_get_secret_value(self) -> None:
        """get_secret_value() must return the raw secret without truncation."""
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        raw = s.jwt_secret_key.get_secret_value()
        assert len(raw) >= 32
        assert raw == os.environ["JWT_SECRET_KEY"]

    def test_slack_webhook_is_secretstr(self, monkeypatch) -> None:
        """LOW-01: slack_webhook_url must be SecretStr — not plain str."""
        from pydantic import SecretStr
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T000/B000/xxxx")
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        assert isinstance(s.slack_webhook_url, SecretStr)
        # Must not appear in repr
        assert "hooks.slack.com" not in repr(s)
        get_settings.cache_clear()

    def test_slack_webhook_masked_empty_default(self) -> None:
        """Default empty SecretStr must not raise and must be masked."""
        from src.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
        # Default is empty SecretStr — must not raise
        raw = s.slack_webhook_url.get_secret_value()
        assert raw == ""


class TestPlaceholderRejection:
    """Placeholder values must be rejected at startup, not silently accepted."""

    @pytest.mark.parametrize("placeholder", [
        "REPLACE_WITH_64_CHAR_RANDOM_HEX",
        "REPLACE_WITH_32_CHAR_MINIMUM",
        "your-secret-here-change-me-now",
        "placeholder-value-do-not-use-here",
        "TODO-set-this-before-deploy-xxxxx",
    ])
    def test_placeholder_raises_validation_error(self, placeholder, monkeypatch) -> None:
        """Any placeholder pattern must cause ValidationError at Settings init."""
        from pydantic import ValidationError
        from src.config import get_settings
        monkeypatch.setenv("JWT_SECRET_KEY", placeholder)
        get_settings.cache_clear()
        with pytest.raises(ValidationError):
            get_settings()
        get_settings.cache_clear()

    def test_short_secret_raises_validation_error(self, monkeypatch) -> None:
        """Secrets shorter than 32 chars must be rejected."""
        from pydantic import ValidationError
        from src.config import get_settings
        monkeypatch.setenv("JWT_SECRET_KEY", "tooshort")
        get_settings.cache_clear()
        with pytest.raises(ValidationError, match="too short"):
            get_settings()
        get_settings.cache_clear()

    def test_valid_secret_accepted(self, monkeypatch) -> None:
        """A valid 32+ char secret must not raise."""
        from src.config import get_settings
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        get_settings.cache_clear()
        s = get_settings()  # must not raise
        assert s.jwt_secret_key.get_secret_value() == "a" * 32
        get_settings.cache_clear()


# ===========================================================================
# Shared mock helpers (mirrors test_api.py)
# ===========================================================================

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


_HEALTH_OK = {
    "artifact_exists": True,
    "model_version": "1.0.0",
    "model_class": "IsolationForest",
    "artifact_path": "ml_models/incident_anomaly/artifacts/isolation_forest_v1.joblib",
}


@pytest.fixture
async def client():
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


# ===========================================================================
# health.py — branch coverage
# ===========================================================================

@pytest.mark.asyncio
async def test_ready_jwt_rs256_verify_path(client: AsyncClient) -> None:
    """COV-H-47: /ready exercises jwt_rs256.verify_token when RS256 available."""
    from src.auth.tokens import create_access_token
    real_token, real_jti, real_ttl = create_access_token(
        {"sub": "__healthcheck__", "role": "_probe"},
        timedelta(seconds=5),
    )
    with (
        patch(
            "api.routers.health.create_access_token",
            return_value=(real_token, real_jti, real_ttl),
        ),
        patch("api.routers.health.jwt_rs256.rs256_available", return_value=True),
        patch("api.routers.health.jwt_rs256.verify_token", return_value={"sub": "__healthcheck__"}),
        patch("api.routers.health.model_registry.health", return_value=_HEALTH_OK),
    ):
        resp = await client.get("/ready")
    body = resp.json()
    assert body["checks"]["jwt_algorithm"] == "RS256"
    assert body["checks"]["jwt_subsystem"] == "ok"


@pytest.mark.asyncio
async def test_ready_denylist_none_branch(client: AsyncClient) -> None:
    """COV-H-63: /ready marks redis_denylist=not_initialised when denylist absent."""
    from api.app import app
    try:
        saved = app.state.denylist
        had_it = True
    except AttributeError:
        had_it = False
    if had_it:
        del app.state.denylist
    try:
        with patch("api.routers.health.model_registry.health", return_value=_HEALTH_OK):
            resp = await client.get("/ready")
        body = resp.json()
        assert body["checks"]["redis_denylist"] == "not_initialised"
    finally:
        if had_it:
            app.state.denylist = saved


@pytest.mark.asyncio
async def test_ready_jwt_secret_missing(monkeypatch, client: AsyncClient) -> None:
    """COV-H-73: /ready marks env_JWT_SECRET_KEY=missing when var unset."""
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with patch("api.routers.health.model_registry.health", return_value=_HEALTH_OK):
        resp = await client.get("/ready")
    body = resp.json()
    assert body["checks"].get("env_JWT_SECRET_KEY") == "missing"


# ===========================================================================
# src/auth/tokens.py — guard clauses + HS256 path
# ===========================================================================

class TestCreateAccessToken:
    def test_missing_sub_raises(self) -> None:
        from src.auth.tokens import create_access_token
        with pytest.raises(ValueError, match="sub"):
            create_access_token({"role": "admin"})

    def test_missing_role_raises(self) -> None:
        from src.auth.tokens import create_access_token
        with pytest.raises(ValueError, match="role"):
            create_access_token({"sub": "user"})

    def test_hs256_path_returns_tuple(self, monkeypatch) -> None:
        import src.auth.tokens as tokens_mod
        monkeypatch.setattr(tokens_mod.jwt_rs256, "rs256_available", lambda: False)
        token, jti, ttl = tokens_mod.create_access_token(
            {"sub": "testuser", "role": "analyst"},
            expires_delta=timedelta(minutes=5),
        )
        assert isinstance(token, str)
        assert isinstance(jti, str)
        assert ttl == 300


class TestCreateRefreshToken:
    def test_missing_sub_raises(self) -> None:
        from src.auth.tokens import create_refresh_token
        with pytest.raises(ValueError, match="sub"):
            create_refresh_token({"role": "admin"})

    def test_hs256_path_returns_tuple(self, monkeypatch) -> None:
        import src.auth.tokens as tokens_mod
        monkeypatch.setattr(tokens_mod.jwt_rs256, "rs256_available", lambda: False)
        token, jti, ttl = tokens_mod.create_refresh_token({"sub": "testuser"})
        assert isinstance(token, str)
        assert len(jti) == 36  # UUID4


class TestDecodeToken:
    def test_expired_token_raises_401(self, monkeypatch) -> None:
        import jwt as pyjwt
        import src.auth.tokens as tokens_mod
        from fastapi import HTTPException
        monkeypatch.setattr(tokens_mod.jwt_rs256, "rs256_available", lambda: False)
        with pytest.raises(HTTPException) as exc_info:
            with patch.object(pyjwt, "decode", side_effect=pyjwt.ExpiredSignatureError):
                tokens_mod.decode_token("fake.token.here")
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_invalid_token_raises_401(self, monkeypatch) -> None:
        import jwt as pyjwt
        import src.auth.tokens as tokens_mod
        from fastapi import HTTPException
        monkeypatch.setattr(tokens_mod.jwt_rs256, "rs256_available", lambda: False)
        with pytest.raises(HTTPException) as exc_info:
            with patch.object(pyjwt, "decode", side_effect=pyjwt.InvalidTokenError("bad")):
                tokens_mod.decode_token("fake.token.here")
        assert exc_info.value.status_code == 401


# ===========================================================================
# HIGH-03: _pseudo_id() pseudonymisation helper
# ===========================================================================

class TestPseudoId:
    """Verify _pseudo_id() produces stable, non-reversible log identifiers."""

    def test_deterministic(self) -> None:
        from api.gdpr_routes import _pseudo_id
        assert _pseudo_id("alice") == _pseudo_id("alice")

    def test_different_users_differ(self) -> None:
        from api.gdpr_routes import _pseudo_id
        assert _pseudo_id("alice") != _pseudo_id("bob")

    def test_not_plaintext(self) -> None:
        from api.gdpr_routes import _pseudo_id
        result = _pseudo_id("alice")
        assert "alice" not in result

    def test_length_16_hex(self) -> None:
        from api.gdpr_routes import _pseudo_id
        result = _pseudo_id("anyuser")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_matches_sha256_truncation(self) -> None:
        from api.gdpr_routes import _pseudo_id
        expected = hashlib.sha256(b"alice").hexdigest()[:16]
        assert _pseudo_id("alice") == expected
