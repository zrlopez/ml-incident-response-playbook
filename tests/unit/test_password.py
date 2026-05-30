"""
tests/unit/test_password.py
============================
Unit tests for src/auth/password.py

Covers:
  - hash_password: output format, uniqueness (random salt)
  - verify_password: argon2id happy path, wrong password, VerificationError,
    InvalidHashError, bcrypt fallback (allowed / disabled / passlib absent),
    unrecognized hash format
  - needs_rehash: argon2 current params, argon2 stale params, bcrypt, unknown
  - maybe_rehash: no-op when current, returns new hash when stale

Note on patching strategy:
  argon2-cffi's PasswordHasher is a C extension type — its methods are
  read-only slots and cannot be patched with patch.object(instance, method).
  Tests that need to inject exceptions replace the entire `_HASHER` module
  attribute with a MagicMock instead.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from argon2 import exceptions as argon2_exc

import src.auth.password as pwd_module
from src.auth.password import (
    hash_password,
    maybe_rehash,
    needs_rehash,
    verify_password,
)


# ---------------------------------------------------------------------------
# hash_password
# ---------------------------------------------------------------------------


def test_hash_password_returns_argon2id_prefix() -> None:
    result = hash_password("hunter2")
    assert result.startswith("$argon2id")


def test_hash_password_unique_salts() -> None:
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2


# ---------------------------------------------------------------------------
# verify_password — argon2id paths
# ---------------------------------------------------------------------------


def test_verify_password_correct() -> None:
    h = hash_password("correct")
    assert verify_password("correct", h) is True


def test_verify_password_wrong() -> None:
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_verify_password_argon2_verification_error() -> None:
    """VerificationError (not mismatch) — patching entire _HASHER object."""
    mock_hasher = MagicMock()
    mock_hasher.verify.side_effect = argon2_exc.VerificationError("bad")
    fake_hash = "$argon2id$v=19$m=65536,t=3,p=4$fakesalt$fakehash"
    with patch.object(pwd_module, "_HASHER", mock_hasher):
        assert verify_password("any", fake_hash) is False


def test_verify_password_invalid_hash_error() -> None:
    """InvalidHashError — patching entire _HASHER object."""
    mock_hasher = MagicMock()
    mock_hasher.verify.side_effect = argon2_exc.InvalidHashError()
    fake_hash = "$argon2id$corrupted"
    with patch.object(pwd_module, "_HASHER", mock_hasher):
        assert verify_password("any", fake_hash) is False


# ---------------------------------------------------------------------------
# verify_password — bcrypt fallback paths
# ---------------------------------------------------------------------------


def test_verify_password_bcrypt_fallback_allowed() -> None:
    """bcrypt hash verified via passlib when fallback is enabled."""
    fake_bcrypt = "$2b$12$" + "x" * 53
    mock_ctx = MagicMock()
    mock_ctx.verify.return_value = True
    with (
        patch.object(pwd_module, "_ALLOW_BCRYPT_FALLBACK", True),
        patch.object(pwd_module, "_PASSLIB_AVAILABLE", True),
        patch.object(pwd_module, "_PASSLIB_CTX", mock_ctx),
    ):
        assert verify_password("pw", fake_bcrypt) is True


def test_verify_password_bcrypt_fallback_disabled() -> None:
    """Returns False immediately when ALLOW_BCRYPT_FALLBACK is False."""
    fake_bcrypt = "$2b$12$" + "x" * 53
    with patch.object(pwd_module, "_ALLOW_BCRYPT_FALLBACK", False):
        assert verify_password("pw", fake_bcrypt) is False


def test_verify_password_passlib_not_available() -> None:
    """Returns False when passlib is absent during bcrypt fallback."""
    fake_bcrypt = "$2b$12$" + "x" * 53
    with (
        patch.object(pwd_module, "_ALLOW_BCRYPT_FALLBACK", True),
        patch.object(pwd_module, "_PASSLIB_AVAILABLE", False),
    ):
        assert verify_password("pw", fake_bcrypt) is False


def test_verify_password_bcrypt_exception() -> None:
    """Passlib raises unexpectedly — returns False, logs warning."""
    fake_bcrypt = "$2b$12$" + "x" * 53
    mock_ctx = MagicMock()
    mock_ctx.verify.side_effect = RuntimeError("passlib exploded")
    with (
        patch.object(pwd_module, "_ALLOW_BCRYPT_FALLBACK", True),
        patch.object(pwd_module, "_PASSLIB_AVAILABLE", True),
        patch.object(pwd_module, "_PASSLIB_CTX", mock_ctx),
    ):
        assert verify_password("pw", fake_bcrypt) is False


def test_verify_password_unrecognized_format() -> None:
    """Hash with unrecognized prefix logs error and returns False."""
    assert verify_password("pw", "md5:abc123") is False


# ---------------------------------------------------------------------------
# needs_rehash
# ---------------------------------------------------------------------------


def test_needs_rehash_current_argon2_returns_false() -> None:
    h = hash_password("pw")
    assert needs_rehash(h) is False


def test_needs_rehash_stale_argon2_returns_true() -> None:
    """Simulate argon2 hash with outdated params — patching entire _HASHER."""
    mock_hasher = MagicMock()
    mock_hasher.check_needs_rehash.return_value = True
    stale = "$argon2id$v=19$m=1024,t=1,p=1$fakesalt$fakehash"
    with patch.object(pwd_module, "_HASHER", mock_hasher):
        assert needs_rehash(stale) is True


def test_needs_rehash_bcrypt_always_true() -> None:
    fake_bcrypt = "$2b$12$" + "x" * 53
    assert needs_rehash(fake_bcrypt) is True


def test_needs_rehash_unknown_format_returns_false() -> None:
    assert needs_rehash("plaintext_not_a_hash") is False


# ---------------------------------------------------------------------------
# maybe_rehash
# ---------------------------------------------------------------------------


def test_maybe_rehash_no_op_when_current() -> None:
    h = hash_password("pw")
    assert maybe_rehash(h, "pw") is None


def test_maybe_rehash_returns_new_hash_when_stale() -> None:
    """When needs_rehash returns True, a new argon2id hash is returned."""
    with patch("src.auth.password.needs_rehash", return_value=True):
        result = maybe_rehash("$2b$12$" + "x" * 53, "pw")
    assert result is not None
    assert result.startswith("$argon2id")
