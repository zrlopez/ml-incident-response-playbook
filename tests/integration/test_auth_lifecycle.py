"""
tests/integration/test_auth_lifecycle.py
=========================================
Auth lifecycle integration tests for src/auth/jwt_rs256.py.

Scope (three properties asserted by the module):
  1. Happy path       -- load_keys -> sign_token -> verify_token -> protected
                         endpoint returns 200 with a real RS256 Bearer token;
                         sub and role claims survive the round-trip.
  2. Algorithm confusion rejection -- verify_token must raise on an HS256-signed
                         token regardless of payload validity. RS256-only
                         enforcement is a stated security property of the module.
  3. kid mismatch fallback -- when a token carries an unrecognised kid,
                         verify_token falls back to the current public key
                         rather than rejecting outright. Pinned so a refactor
                         cannot silently change this behaviour.

Explicitly NOT tested:
  - jti revocation via Redis/DB denylist
    [GAP: jti is issued but never checked against a revocation store --
     no Redis/DB blocklist exists. Nothing to assert against yet.]
  - Token refresh exchange
    [GAP: no exchange endpoint is implemented.]

Fixture design:
  rs256_keys   -- generates an ephemeral 2048-bit RSA key pair via
                  generate_dev_keypair(), loads it into module state via
                  load_keys(), then restores original module state on teardown.
                  Self-contained: does not touch RSA_PRIVATE_KEY_PEM in CI env.
  app_client   -- FastAPI ASGI client with real get_current_user (JWT path
                  active). Depends on rs256_keys so key state is always ready.

Source authority: src/auth/jwt_rs256.py
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import AsyncGenerator

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

import src.auth.jwt_rs256 as jwt_mod
from src.auth.jwt_rs256 import (
    generate_dev_keypair,
    load_keys,
    sign_token,
    verify_token,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def rs256_keys():
    """
    Generate an ephemeral 2048-bit RSA key pair, inject it into the jwt_rs256
    module state via load_keys(), and restore original module state on teardown.

    Uses 2048-bit keys (not 4096) for test speed; key size does not affect
    the properties being asserted.
    """
    # Capture original module state
    orig = (
        jwt_mod._private_key,
        jwt_mod._public_key,
        jwt_mod._old_public_key,
        jwt_mod._key_id,
        jwt_mod._old_key_id,
    )

    # Generate ephemeral 2048-bit key pair
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    # Inject via env var so load_keys() follows its own code path
    prev_pem = os.environ.get("RSA_PRIVATE_KEY_PEM", "")
    prev_pub = os.environ.get("RSA_PUBLIC_KEY_PEM", "")
    os.environ["RSA_PRIVATE_KEY_PEM"] = private_pem
    os.environ.pop("RSA_PUBLIC_KEY_PEM", None)
    os.environ.pop("RSA_OLD_PUBLIC_KEY_PEM", None)

    load_keys()
    yield

    # Restore original module state
    jwt_mod._private_key, jwt_mod._public_key, jwt_mod._old_public_key, \
        jwt_mod._key_id, jwt_mod._old_key_id = orig

    # Restore env
    if prev_pem:
        os.environ["RSA_PRIVATE_KEY_PEM"] = prev_pem
    else:
        os.environ.pop("RSA_PRIVATE_KEY_PEM", None)
    if prev_pub:
        os.environ["RSA_PUBLIC_KEY_PEM"] = prev_pub


@pytest_asyncio.fixture()
async def app_client(rs256_keys, sqlite_engine) -> AsyncGenerator[AsyncClient, None]:
    """
    FastAPI ASGI test client with the real JWT verification path active.

    - get_current_user is NOT overridden; the real dependency runs and
      decodes the Bearer token via verify_token.
    - get_session is overridden with an in-memory SQLite session so no
      Postgres is required for the auth-path HTTP assertion.
    """
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-minimum-32-chars-xxxxxxxxxxxx")

    from api.app import app
    from src.incident_tracker import get_session
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://testserver",
    ) as client:
        yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_token(sub: str = "test-admin", role: str = "admin", ttl_seconds: int = 300) -> str:
    """Issue a valid RS256 token using the currently loaded ephemeral key."""
    token, _jti, _ttl = sign_token(
        payload={"sub": sub, "role": role},
        expires_delta=timedelta(seconds=ttl_seconds),
    )
    return token


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_sign_and_verify_round_trip(rs256_keys):
    """
    sign_token -> verify_token: claims survive the RS256 round-trip intact.
    Asserts that sub and role are present in the decoded payload.
    """
    token = _make_token(sub="analyst-01", role="analyst")
    claims = verify_token(token)

    assert claims["sub"] == "analyst-01"
    assert claims["role"] == "analyst"
    assert claims["token_type"] == "access"
    assert "jti" in claims
    assert "exp" in claims
    assert "iat" in claims


@pytest.mark.integration
async def test_protected_endpoint_accepts_valid_token(app_client):
    """
    A valid RS256 Bearer token issued by sign_token must allow access to a
    protected endpoint and return HTTP 200 (or 201 for POST /incidents/).

    This is the full HTTP-layer assertion of the auth happy path:
    client -> ASGI -> get_current_user -> verify_token -> handler.
    """
    token = _make_token()

    resp = await app_client.post(
        "/incidents/",
        json={
            "title": "Auth lifecycle: latency regression",
            "severity": "SEV-3",
            "category": "latency",
            "owner": "ml-oncall",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, (
        f"Expected 201 with valid RS256 token, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["status"] == "open"
    assert "id" in body


# ---------------------------------------------------------------------------
# 2. Algorithm confusion rejection
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_hs256_token_is_rejected(rs256_keys):
    """
    verify_token must raise jwt.exceptions.DecodeError when presented with an
    HS256-signed token.

    Algorithm confusion protection: only RS256 is accepted. An attacker cannot
    forge a token by reusing the public key as an HMAC secret.

    Source: verify_token passes algorithms=["RS256"] to jwt.decode, which
    causes PyJWT to reject any token whose header declares a different algorithm.
    """
    hs256_token = jwt.encode(
        {"sub": "attacker", "role": "admin", "exp": 9999999999,
         "iat": 1000000000, "jti": "fake-jti"},
        "some-hs256-secret",
        algorithm="HS256",
    )

    with pytest.raises(jwt.exceptions.DecodeError):
        verify_token(hs256_token)


@pytest.mark.integration
def test_none_algorithm_token_is_rejected(rs256_keys):
    """
    verify_token must raise on a token signed with the 'none' algorithm.
    PyJWT rejects 'none' when algorithms=["RS256"] is enforced.
    """
    # Manually construct an unsigned JWT (none algorithm)
    import base64, json as _json
    header = base64.urlsafe_b64encode(
        _json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(
        _json.dumps({
            "sub": "attacker", "role": "admin",
            "exp": 9999999999, "iat": 1000000000, "jti": "fake-jti",
        }).encode()
    ).rstrip(b"=").decode()
    none_token = f"{header}.{payload_b64}."

    with pytest.raises((jwt.exceptions.DecodeError, jwt.exceptions.InvalidAlgorithmError)):
        verify_token(none_token)


# ---------------------------------------------------------------------------
# 3. kid mismatch fallback
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_unknown_kid_falls_back_to_current_key(rs256_keys):
    """
    When a token carries a kid that does not match any known key ID,
    verify_token falls back to the current public key instead of rejecting.

    This behaviour is documented in jwt_rs256.py:
      'No kid match -- try current key anyway (backward compat for tokens
       without kid)'

    Pinned here so a future refactor cannot silently change the fallback
    to an outright rejection, which would break clients that omit kid.

    Mechanism: sign a token with the current key, then patch _key_id to a
    different value so the token's embedded kid no longer matches. The
    token's signature is still valid against the current _public_key, so
    verify_token must succeed via the fallback path.
    """
    token = _make_token()

    # Patch _key_id so the token's embedded kid becomes 'unknown'
    original_key_id = jwt_mod._key_id
    jwt_mod._key_id = "000000000000ffff"  # unrecognised kid

    try:
        claims = verify_token(token)
        assert claims["sub"] == "test-admin"
    finally:
        jwt_mod._key_id = original_key_id
