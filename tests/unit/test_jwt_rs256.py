"""
tests/unit/test_jwt_rs256.py
=============================
Unit tests for src/auth/jwt_rs256.py — RS256 JWT signing, verification,
key loading, JWKS endpoint, and dev keypair utility.

Covers missing lines (CI-67):
  75-79   — load_keys(): no PEM env var → return False (HS256 fallback)
  90-132  — load_keys() success; optional public key override;
            old key rotation window; private key load failure
  151-176 — sign_token() full path
  186-203 — verify_token(): current key, old key fallback, no-kid fallback
  217-225 — verify_token(): RS256 keys not loaded guard
  255-266 — jwks_endpoint(): RS256 available (current + old key)
  284-297 — jwks_endpoint(): RS256 not available → 503
  301-305 — generate_dev_keypair()

All tests use a 2048-bit ephemeral key generated in a session fixture
(mirrors the CI pattern; 2048 is sufficient for test speed).
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Generator
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def rsa_pem_pair() -> tuple[str, str]:
    """Generate a 2048-bit RSA key pair once for the entire test session."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture(autouse=True)
def _reset_module_state() -> Generator[None, None, None]:
    """Reset jwt_rs256 module-level key state before/after each test."""
    import src.auth.jwt_rs256 as m
    m._private_key = None
    m._public_key = None
    m._old_public_key = None
    m._key_id = ""
    m._old_key_id = ""
    yield
    m._private_key = None
    m._public_key = None
    m._old_public_key = None
    m._key_id = ""
    m._old_key_id = ""


# ---------------------------------------------------------------------------
# load_keys()
# ---------------------------------------------------------------------------

def test_load_keys_no_env_returns_false() -> None:
    """Lines 75-79: missing RSA_PRIVATE_KEY_PEM → returns False."""
    import src.auth.jwt_rs256 as m
    env = {k: v for k, v in os.environ.items() if k != "RSA_PRIVATE_KEY_PEM"}
    with patch.dict(os.environ, env, clear=True):
        result = m.load_keys()
    assert result is False
    assert not m.rs256_available()


def test_load_keys_success(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 90-105: valid PEM → returns True, keys loaded."""
    import src.auth.jwt_rs256 as m
    private_pem, _ = rsa_pem_pair
    with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": private_pem}, clear=False):
        result = m.load_keys()
    assert result is True
    assert m.rs256_available()
    assert m._key_id != ""


def test_load_keys_invalid_pem_raises() -> None:
    """load_keys() with corrupt PEM raises RuntimeError."""
    import src.auth.jwt_rs256 as m
    with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": "NOT_VALID_PEM"}, clear=False):
        with pytest.raises(RuntimeError, match="Failed to load RSA_PRIVATE_KEY_PEM"):
            m.load_keys()


def test_load_keys_with_public_key_override(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 108-115: RSA_PUBLIC_KEY_PEM override accepted."""
    import src.auth.jwt_rs256 as m
    private_pem, public_pem = rsa_pem_pair
    env = {
        "RSA_PRIVATE_KEY_PEM": private_pem,
        "RSA_PUBLIC_KEY_PEM": public_pem,
    }
    with patch.dict(os.environ, env, clear=False):
        result = m.load_keys()
    assert result is True
    assert m.rs256_available()


def test_load_keys_with_old_key_rotation_window(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 118-128: RSA_OLD_PUBLIC_KEY_PEM loads old rotation key."""
    import src.auth.jwt_rs256 as m
    private_pem, public_pem = rsa_pem_pair
    env = {
        "RSA_PRIVATE_KEY_PEM": private_pem,
        "RSA_OLD_PUBLIC_KEY_PEM": public_pem,
    }
    with patch.dict(os.environ, env, clear=False):
        result = m.load_keys()
    assert result is True
    assert m._old_public_key is not None
    assert m._old_key_id != ""


def test_load_keys_invalid_old_key_warns_not_raises(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 128-132: invalid RSA_OLD_PUBLIC_KEY_PEM logs warning but does not raise."""
    import src.auth.jwt_rs256 as m
    private_pem, _ = rsa_pem_pair
    env = {
        "RSA_PRIVATE_KEY_PEM": private_pem,
        "RSA_OLD_PUBLIC_KEY_PEM": "INVALID_OLD_KEY",
    }
    with patch.dict(os.environ, env, clear=False):
        result = m.load_keys()  # should not raise
    assert result is True
    assert m._old_public_key is None


# ---------------------------------------------------------------------------
# sign_token()
# ---------------------------------------------------------------------------

def test_sign_token_produces_rs256_jwt(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 151-176: sign_token() produces a decodable RS256 JWT."""
    import src.auth.jwt_rs256 as m
    import jwt as pyjwt
    private_pem, _ = rsa_pem_pair
    with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": private_pem}, clear=False):
        m.load_keys()

    token, jti, ttl = m.sign_token(
        {"sub": "test_user", "role": "admin"},
        timedelta(minutes=15),
        token_type="access",
    )
    assert isinstance(token, str)
    assert isinstance(jti, str) and len(jti) == 36  # UUID4
    assert ttl == 900

    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["kid"] == m._key_id


def test_sign_token_no_keys_raises() -> None:
    """sign_token() without loaded keys raises RuntimeError."""
    import src.auth.jwt_rs256 as m
    assert not m.rs256_available()
    with pytest.raises(RuntimeError, match="RS256 keys not loaded"):
        m.sign_token({"sub": "x"}, timedelta(minutes=5))


# ---------------------------------------------------------------------------
# verify_token()
# ---------------------------------------------------------------------------

def test_verify_token_current_key(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 186-203: verify_token() succeeds with current key."""
    import src.auth.jwt_rs256 as m
    private_pem, _ = rsa_pem_pair
    with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": private_pem}, clear=False):
        m.load_keys()

    token, jti, _ = m.sign_token({"sub": "alice", "role": "user"}, timedelta(minutes=5))
    claims = m.verify_token(token)
    assert claims["sub"] == "alice"
    assert claims["jti"] == jti


def test_verify_token_old_key_rotation_window(rsa_pem_pair: tuple[str, str]) -> None:
    """Verify with old key during rotation window (lines 198-202)."""
    import src.auth.jwt_rs256 as m
    private_pem, _ = rsa_pem_pair

    with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": private_pem}, clear=False):
        m.load_keys()

    token, _, _ = m.sign_token({"sub": "bob", "role": "analyst"}, timedelta(minutes=5))

    # Simulate rotation: move current → old, load a new current key
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    m._old_public_key = m._public_key
    m._old_key_id = m._key_id
    m._private_key = new_key  # type: ignore[assignment]
    m._public_key = new_key.public_key()  # type: ignore[assignment]
    m._key_id = m._pem_to_key_id(m._public_key)  # type: ignore[arg-type]

    claims = m.verify_token(token)
    assert claims["sub"] == "bob"


def test_verify_token_no_kid_fallback(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 202-203: token without kid header falls back to current public key."""
    import src.auth.jwt_rs256 as m
    import jwt as pyjwt
    from datetime import datetime, timezone
    import uuid

    private_pem, _ = rsa_pem_pair
    with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": private_pem}, clear=False):
        m.load_keys()

    now = datetime.now(timezone.utc)
    raw_claims = {
        "sub": "carol",
        "role": "user",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": str(uuid.uuid4()),
    }
    # Encode without kid header to exercise the no-match fallback branch
    no_kid_token = pyjwt.encode(raw_claims, m._private_key, algorithm="RS256")  # type: ignore
    claims = m.verify_token(no_kid_token)
    assert claims["sub"] == "carol"


def test_verify_token_rs256_not_loaded_raises() -> None:
    """Lines 217-225: verify_token() without keys raises RuntimeError."""
    import src.auth.jwt_rs256 as m
    with pytest.raises(RuntimeError, match="RS256 keys not loaded"):
        m.verify_token("not.a.token")


# ---------------------------------------------------------------------------
# jwks_endpoint()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jwks_endpoint_rs256_available(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 255-266: JWKS returns current key."""
    import src.auth.jwt_rs256 as m
    private_pem, _ = rsa_pem_pair
    with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": private_pem}, clear=False):
        m.load_keys()

    result = await m.jwks_endpoint()
    assert "keys" in result
    assert len(result["keys"]) >= 1
    key = result["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert "n" in key and "e" in key


@pytest.mark.asyncio
async def test_jwks_endpoint_with_old_key(rsa_pem_pair: tuple[str, str]) -> None:
    """Lines 262-266: JWKS includes old key when rotation window active."""
    import src.auth.jwt_rs256 as m
    private_pem, public_pem = rsa_pem_pair
    env = {
        "RSA_PRIVATE_KEY_PEM": private_pem,
        "RSA_OLD_PUBLIC_KEY_PEM": public_pem,
    }
    with patch.dict(os.environ, env, clear=False):
        m.load_keys()

    result = await m.jwks_endpoint()
    assert len(result["keys"]) == 2


@pytest.mark.asyncio
async def test_jwks_endpoint_not_available_raises() -> None:
    """Lines 284-297: JWKS raises 503 when RS256 keys not loaded."""
    import src.auth.jwt_rs256 as m
    from fastapi import HTTPException
    assert not m.rs256_available()
    with pytest.raises(HTTPException) as exc_info:
        await m.jwks_endpoint()
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# generate_dev_keypair()
# ---------------------------------------------------------------------------

def test_generate_dev_keypair_returns_valid_pem() -> None:
    """Lines 301-305: generate_dev_keypair() returns valid loadable PEM strings."""
    import src.auth.jwt_rs256 as m
    private_pem, public_pem = m.generate_dev_keypair()
    assert "PRIVATE KEY" in private_pem
    assert "PUBLIC KEY" in public_pem
    from cryptography.hazmat.primitives import serialization as ser
    priv = ser.load_pem_private_key(private_pem.encode(), password=None)
    pub = ser.load_pem_public_key(public_pem.encode())
    assert priv is not None
    assert pub is not None
