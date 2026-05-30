"""
Unit tests for src/users/repository.py.

Covers:
  - UserRecord.from_dict / to_dict
  - InMemoryUserRepository: get_by_username, authenticate, update_password_hash, disable_user
  - PostgresUserRepository via mocked async session factory

All tests use the sqlite_engine fixture (from conftest.py) to ensure
SQLAlchemy mappers are configured before UserRecord.from_dict is called.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.password import hash_password
from src.users.repository import (
    InMemoryUserRepository,
    PostgresUserRepository,
    UserRecord,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_users(password: str = "Secret123!") -> dict[str, dict]:
    hashed = hash_password(password)
    return {
        "alice": {"hashed_password": hashed, "role": "admin"},
        "bob": {"hashed_password": hashed, "role": "analyst", "disabled": True},
    }


def _make_record(username: str = "alice", role: str = "admin", disabled: bool = False) -> UserRecord:
    hashed = hash_password("Secret123!")
    return UserRecord.from_dict(username, {"hashed_password": hashed, "role": role, "disabled": disabled})


@pytest.fixture()
def repo(sqlite_engine) -> InMemoryUserRepository:  # sqlite_engine ensures mappers are configured
    return InMemoryUserRepository(_make_users())


# ── UserRecord ────────────────────────────────────────────────────────────────

class TestUserRecord:
    @pytest.mark.anyio
    async def test_from_dict_sets_fields(self, sqlite_engine):
        hashed = hash_password("pw")
        rec = UserRecord.from_dict("testuser", {"hashed_password": hashed, "role": "admin"})
        assert rec.username == "testuser"
        assert rec.role == "admin"
        assert rec.hashed_password == hashed
        assert rec.disabled is False
        assert rec.hash_algorithm == "argon2id"

    @pytest.mark.anyio
    async def test_from_dict_disabled_flag(self, sqlite_engine):
        hashed = hash_password("pw")
        rec = UserRecord.from_dict("u", {"hashed_password": hashed, "role": "analyst", "disabled": True})
        assert rec.disabled is True

    @pytest.mark.anyio
    async def test_to_dict_excludes_password(self, sqlite_engine):
        hashed = hash_password("pw")
        rec = UserRecord.from_dict("u", {"hashed_password": hashed, "role": "analyst"})
        d = rec.to_dict()
        assert "hashed_password" not in d
        assert d["username"] == "u"
        assert d["role"] == "analyst"
        assert "id" in d
        assert "created_at" in d
        assert "updated_at" in d

    @pytest.mark.anyio
    async def test_to_dict_id_is_uuid_string(self, sqlite_engine):
        import uuid
        hashed = hash_password("pw")
        rec = UserRecord.from_dict("u", {"hashed_password": hashed, "role": "analyst"})
        uuid.UUID(rec.to_dict()["id"])  # raises if not a valid UUID


# ── InMemoryUserRepository ────────────────────────────────────────────────────

class TestInMemoryUserRepository:

    @pytest.mark.anyio
    async def test_get_by_username_found(self, repo):
        user = await repo.get_by_username("alice")
        assert user is not None
        assert user.username == "alice"

    @pytest.mark.anyio
    async def test_get_by_username_missing(self, repo):
        user = await repo.get_by_username("nobody")
        assert user is None

    @pytest.mark.anyio
    async def test_authenticate_success(self, repo):
        user = await repo.authenticate("alice", "Secret123!")
        assert user is not None
        assert user.username == "alice"

    @pytest.mark.anyio
    async def test_authenticate_wrong_password(self, repo):
        user = await repo.authenticate("alice", "WrongPass!")
        assert user is None

    @pytest.mark.anyio
    async def test_authenticate_disabled_user(self, repo):
        user = await repo.authenticate("bob", "Secret123!")
        assert user is None

    @pytest.mark.anyio
    async def test_authenticate_missing_user(self, repo):
        user = await repo.authenticate("ghost", "Secret123!")
        assert user is None

    @pytest.mark.anyio
    async def test_update_password_hash(self, repo):
        new_hash = hash_password("NewPass456!")
        await repo.update_password_hash("alice", new_hash, algorithm="argon2id")
        user = await repo.get_by_username("alice")
        assert user.hashed_password == new_hash
        assert user.hash_algorithm == "argon2id"

    @pytest.mark.anyio
    async def test_update_password_hash_missing_user_is_noop(self, repo):
        await repo.update_password_hash("ghost", "somehash")  # must not raise

    @pytest.mark.anyio
    async def test_disable_user_found(self, repo):
        result = await repo.disable_user("alice")
        assert result is True
        user = await repo.get_by_username("alice")
        assert user.disabled is True

    @pytest.mark.anyio
    async def test_disable_user_not_found(self, repo):
        result = await repo.disable_user("nobody")
        assert result is False

    @pytest.mark.anyio
    async def test_disable_already_disabled_user(self, repo):
        result = await repo.disable_user("bob")
        assert result is True
        user = await repo.get_by_username("bob")
        assert user.disabled is True

    @pytest.mark.anyio
    async def test_authenticate_triggers_rehash(self, repo):
        new_hash = hash_password("Secret123!")
        with patch("src.users.repository.maybe_rehash", return_value=new_hash):
            user = await repo.authenticate("alice", "Secret123!")
        assert user is not None
        stored = await repo.get_by_username("alice")
        assert stored.hashed_password == new_hash

    @pytest.mark.anyio
    async def test_authenticate_no_rehash_needed(self, repo):
        original_hash = (await repo.get_by_username("alice")).hashed_password
        with patch("src.users.repository.maybe_rehash", return_value=None):
            user = await repo.authenticate("alice", "Secret123!")
        assert user is not None
        stored = await repo.get_by_username("alice")
        assert stored.hashed_password == original_hash


# ── PostgresUserRepository (mocked session) ───────────────────────────────────

class TestPostgresUserRepository:

    def _make_repo(self, scalar_result=None, rowcount=1):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = scalar_result
        mock_result.fetchone.return_value = MagicMock() if rowcount else None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin)

        mock_factory = MagicMock(return_value=mock_session)
        return PostgresUserRepository(mock_factory)

    @pytest.mark.anyio
    async def test_get_by_username_returns_record(self, sqlite_engine):
        fake_user = _make_record()
        repo = self._make_repo(scalar_result=fake_user)
        result = await repo.get_by_username("alice")
        assert result is fake_user

    @pytest.mark.anyio
    async def test_get_by_username_returns_none(self, sqlite_engine):
        repo = self._make_repo(scalar_result=None)
        result = await repo.get_by_username("nobody")
        assert result is None

    @pytest.mark.anyio
    async def test_update_password_hash(self, sqlite_engine):
        repo = self._make_repo()
        await repo.update_password_hash("alice", "newhash", algorithm="argon2id")

    @pytest.mark.anyio
    async def test_disable_user_found(self, sqlite_engine):
        repo = self._make_repo(rowcount=1)
        result = await repo.disable_user("alice")
        assert result is True

    @pytest.mark.anyio
    async def test_disable_user_not_found(self, sqlite_engine):
        repo = self._make_repo(rowcount=0)
        result = await repo.disable_user("nobody")
        assert result is False

    @pytest.mark.anyio
    async def test_authenticate_success(self, sqlite_engine):
        fake_user = _make_record()
        repo = self._make_repo(scalar_result=fake_user)
        with patch("src.users.repository.maybe_rehash", return_value=None):
            result = await repo.authenticate("alice", "Secret123!")
        assert result is fake_user

    @pytest.mark.anyio
    async def test_authenticate_wrong_password(self, sqlite_engine):
        fake_user = _make_record()
        repo = self._make_repo(scalar_result=fake_user)
        result = await repo.authenticate("alice", "WrongPass!")
        assert result is None

    @pytest.mark.anyio
    async def test_authenticate_disabled_user(self, sqlite_engine):
        fake_user = _make_record(disabled=True)
        repo = self._make_repo(scalar_result=fake_user)
        result = await repo.authenticate("alice", "Secret123!")
        assert result is None

    @pytest.mark.anyio
    async def test_authenticate_user_not_found(self, sqlite_engine):
        repo = self._make_repo(scalar_result=None)
        result = await repo.authenticate("ghost", "Secret123!")
        assert result is None

    @pytest.mark.anyio
    async def test_authenticate_triggers_rehash(self, sqlite_engine):
        fake_user = _make_record()
        repo = self._make_repo(scalar_result=fake_user)
        new_hash = hash_password("Secret123!")
        with patch("src.users.repository.maybe_rehash", return_value=new_hash):
            result = await repo.authenticate("alice", "Secret123!")
        assert result is not None
        assert result.hashed_password == new_hash
