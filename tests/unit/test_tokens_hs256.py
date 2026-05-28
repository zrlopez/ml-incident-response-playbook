"""
tests/unit/test_tokens_hs256.py
================================
Coverage for the HS256 fallback branch in src/auth/tokens.py.

RS256 is the default when RSA keys are loaded; these tests patch
jwt_rs256.rs256_available() to False to exercise the HS256 code paths
that were previously uncovered (lines 46, 49, 63, 66, 79, 86-94).
"""
from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException

from src.auth.tokens import create_access_token, create_refresh_token, decode_token

_RS256_OFF = "src.auth.tokens.jwt_rs256.rs256_available"


@pytest.fixture()
def hs256_mode():
    """Patch rs256_available to False for the duration of each test."""
    with patch(_RS256_OFF, return_value=False):
        yield


def test_create_access_token_hs256(hs256_mode):
    token, jti, ttl = create_access_token({"sub": "user1", "role": "analyst"})
    assert isinstance(token, str)
    assert isinstance(jti, str) and len(jti) == 36  # UUID4
    assert ttl > 0


def test_create_access_token_missing_role_raises(hs256_mode):
    with pytest.raises(ValueError, match="role"):
        create_access_token({"sub": "user1"})


def test_create_access_token_missing_sub_raises(hs256_mode):
    with pytest.raises(ValueError, match="sub"):
        create_access_token({"role": "analyst"})


def test_create_refresh_token_hs256(hs256_mode):
    token, jti, ttl = create_refresh_token({"sub": "user1", "role": "analyst"})
    assert isinstance(token, str)
    assert ttl > 0


def test_create_refresh_token_missing_sub_raises(hs256_mode):
    with pytest.raises(ValueError, match="sub"):
        create_refresh_token({})


def test_decode_token_hs256_roundtrip(hs256_mode):
    token, jti, _ = create_access_token({"sub": "user1", "role": "analyst"})
    payload = decode_token(token)
    assert payload["sub"] == "user1"
    assert payload["jti"] == jti


def test_decode_token_expired_raises_401(hs256_mode):
    from datetime import timedelta
    token, _, _ = create_access_token(
        {"sub": "user1", "role": "analyst"},
        expires_delta=timedelta(seconds=-1),
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_token(token)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_decode_token_invalid_raises_401(hs256_mode):
    with pytest.raises(HTTPException) as exc_info:
        decode_token("not.a.valid.token")
    assert exc_info.value.status_code == 401
