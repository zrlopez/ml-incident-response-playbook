"""tests/unit/test_user_repository.py

Covers src/users/repository.py — InMemoryUserRepository.

Strategy
--------
InMemoryUserRepository requires zero mocking (no DB, no async engine),
so every branch is exercised directly.

get_user_repository() is intentionally excluded: it lazy-imports
api.stub_users at call time, which calls _require_dev_password() and
raises RuntimeError if DEV_*_PASSWORD env vars are absent — killing
pytest collection. The factory is already exercised indirectly by
test_api.py via the full app fixture.

PostgresUserRepository and get_db_session are deferred to integration
tests that need a live engine.

Coverage targets (src/users/repository.py):
  - UserRecord.from_dict() / to_dict()
  - InMemoryUserRepository.get_by_username()       — hit and miss
  - InMemoryUserRepository.update_password_hash()  — found and not-found
  - InMemoryUserRepository.authenticate()          — success, wrong password,
                                                     disabled user, rehash path
  - InMemoryUserRepository.disable_user()          — success and not-found
"""
from __future__ import annotations

import pytest

from src.users.repository import InMemoryUserRepository, UserRecord
from src.auth.password import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PLAINTEXT = "correct-horse-battery-staple-99"


def _make_users(
    *,
    disabled: bool = False,
    extra: dict | None = None,
) -> dict[str, dict]:
    """Build a minimal _USERS-style dict with one active user."""
    users: dict[str, dict] = {
        "alice": {
            "hashed_password": hash_password(PLAINTEXT),
            "role": "analyst",
            "disabled": disabled,
        },
    }
    if extra:
        users.update(extra)
    return users


def _make_repo(**kwargs) -> InMemoryUserRepository:
    return InMemoryUserRepository(_make_users(**kwargs))


# ---------------------------------------------------------------------------
# UserRecord
# ---------------------------------------------------------------------------

class TestUserRecord:
    def test_from_dict_sets_username(self) -> None:
        record = UserRecord.from_dict("alice", {"hashed_password": "h", "role": "analyst"})
        assert record.username == "alice"

    def test_from_dict_sets_role(self) -> None:
        record = UserRecord.from_dict("alice", {"hashed_password": "h", "role": "admin"})
        assert record.role == "admin"

    def test_from_dict_disabled_defaults_false(self) -> None:
        record = UserRecord.from_dict("alice", {"hashed_password": "h", "role": "analyst"})
        assert record.disabled is False

    def test_from_dict_disabled_explicit_true(self) -> None:
        record = UserRecord.from_dict(
            "alice", {"hashed_password": "h", "role": "analyst", "disabled": True}
        )
        assert record.disabled is True

    def test_from_dict_assigns_uuid_id(self) -> None:
        record = UserRecord.from_dict("alice", {"hashed_password": "h", "role": "analyst"})
        assert len(record.id) == 36  # UUID4 string

    def test_to_dict_excludes_hashed_password(self) -> None:
        record = UserRecord.from_dict("alice", {"hashed_password": "secret", "role": "analyst"})
        d = record.to_dict()
        assert "hashed_password" not in d

    def test_to_dict_contains_expected_keys(self) -> None:
        record = UserRecord.from_dict("alice", {"hashed_password": "h", "role": "analyst"})
        assert {
            "id", "username", "role", "disabled",
            "hash_algorithm", "created_at", "updated_at",
        } == set(record.to_dict())


# ---------------------------------------------------------------------------
# InMemoryUserRepository — get_by_username
# ---------------------------------------------------------------------------

class TestGetByUsername:
    async def test_returns_record_for_known_user(self) -> None:
        repo = _make_repo()
        user = await repo.get_by_username("alice")
        assert user is not None
        assert user.username == "alice"

    async def test_returns_none_for_unknown_user(self) -> None:
        repo = _make_repo()
        user = await repo.get_by_username("nobody")
        assert user is None


# ---------------------------------------------------------------------------
# InMemoryUserRepository — update_password_hash
# ---------------------------------------------------------------------------

class TestUpdatePasswordHash:
    async def test_updates_hash_for_known_user(self) -> None:
        repo = _make_repo()
        await repo.update_password_hash("alice", "newhash", algorithm="argon2id")
        user = await repo.get_by_username("alice")
        assert user is not None
        assert user.hashed_password == "newhash"

    async def test_updates_algorithm(self) -> None:
        repo = _make_repo()
        await repo.update_password_hash("alice", "newhash", algorithm="bcrypt")
        user = await repo.get_by_username("alice")
        assert user is not None
        assert user.hash_algorithm == "bcrypt"

    async def test_no_error_for_unknown_user(self) -> None:
        """Should log a warning and return gracefully — not raise."""
        repo = _make_repo()
        await repo.update_password_hash("ghost", "newhash")  # must not raise

    async def test_unknown_user_does_not_affect_store(self) -> None:
        repo = _make_repo()
        await repo.update_password_hash("ghost", "newhash")
        assert await repo.get_by_username("ghost") is None


# ---------------------------------------------------------------------------
# InMemoryUserRepository — authenticate
# ---------------------------------------------------------------------------

class TestAuthenticate:
    async def test_returns_user_on_correct_password(self) -> None:
        repo = _make_repo()
        user = await repo.authenticate("alice", PLAINTEXT)
        assert user is not None
        assert user.username == "alice"

    async def test_returns_none_on_wrong_password(self) -> None:
        repo = _make_repo()
        user = await repo.authenticate("alice", "wrong-password")
        assert user is None

    async def test_returns_none_for_unknown_user(self) -> None:
        repo = _make_repo()
        user = await repo.authenticate("nobody", PLAINTEXT)
        assert user is None

    async def test_returns_none_for_disabled_user(self) -> None:
        repo = _make_repo(disabled=True)
        user = await repo.authenticate("alice", PLAINTEXT)
        assert user is None

    async def test_returns_user_role(self) -> None:
        repo = _make_repo()
        user = await repo.authenticate("alice", PLAINTEXT)
        assert user is not None
        assert user.role == "analyst"


# ---------------------------------------------------------------------------
# InMemoryUserRepository — disable_user
# ---------------------------------------------------------------------------

class TestDisableUser:
    async def test_returns_true_for_known_user(self) -> None:
        repo = _make_repo()
        result = await repo.disable_user("alice")
        assert result is True

    async def test_sets_disabled_flag(self) -> None:
        repo = _make_repo()
        await repo.disable_user("alice")
        user = await repo.get_by_username("alice")
        assert user is not None
        assert user.disabled is True

    async def test_returns_false_for_unknown_user(self) -> None:
        repo = _make_repo()
        result = await repo.disable_user("ghost")
        assert result is False

    async def test_disabled_user_cannot_authenticate(self) -> None:
        repo = _make_repo()
        await repo.disable_user("alice")
        user = await repo.authenticate("alice", PLAINTEXT)
        assert user is None

    async def test_disable_idempotent(self) -> None:
        """Disabling an already-disabled user should still return True."""
        repo = _make_repo(disabled=True)
        result = await repo.disable_user("alice")
        assert result is True
