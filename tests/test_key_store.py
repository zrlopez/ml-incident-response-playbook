"""
Phase-8 test coverage: RS256KeyStore.

All tests generate ephemeral keys in-process — no filesystem or real env vars
are required except for TestRS256KeyStoreFromEnv, which patches os.environ.
"""
from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest

from src.auth.key_store import RS256KeyStore


# ---------------------------------------------------------------------------
# RS256KeyStore.generate()
# ---------------------------------------------------------------------------

class TestRS256KeyStoreGenerate:
    def setup_method(self):
        self.store = RS256KeyStore.generate(key_size=2048)

    def test_key_id_is_set(self):
        assert isinstance(self.store.key_id, str)
        assert len(self.store.key_id) > 0

    def test_sign_token_returns_three_tuple(self):
        token, jti, ttl = self.store.sign_token({"sub": "alice", "role": "analyst"})
        assert isinstance(token, str)
        assert isinstance(jti, str)
        assert isinstance(ttl, int)
        assert ttl == 30 * 60  # default 30-minute window

    def test_verify_token_decodes_successfully(self):
        token, jti, _ = self.store.sign_token({"sub": "alice", "role": "analyst"})
        payload = self.store.verify_token(token)
        assert payload["sub"] == "alice"
        assert payload["role"] == "analyst"
        assert payload["jti"] == jti
        assert payload["token_type"] == "access"

    def test_custom_ttl_is_respected(self):
        _, _, ttl = self.store.sign_token(
            {"sub": "bob", "role": "operator"},
            expires_delta=timedelta(hours=2),
        )
        assert ttl == 7200

    def test_refresh_token_type_claim(self):
        token, _, _ = self.store.sign_token(
            {"sub": "alice", "role": "analyst"},
            token_type="refresh",
        )
        payload = self.store.verify_token(token)
        assert payload["token_type"] == "refresh"

    def test_cross_key_verification_fails(self):
        other_store = RS256KeyStore.generate(key_size=2048)
        token, _, _ = self.store.sign_token({"sub": "alice", "role": "analyst"})
        with pytest.raises(jwt.InvalidSignatureError):
            other_store.verify_token(token)

    def test_sign_token_missing_sub_raises_value_error(self):
        with pytest.raises(ValueError, match="sub"):
            self.store.sign_token({"role": "analyst"})


# ---------------------------------------------------------------------------
# RS256KeyStore.from_env()
# ---------------------------------------------------------------------------

class TestRS256KeyStoreFromEnv:
    def setup_method(self):
        # Generate a PEM we can inject via env
        self._ephemeral = RS256KeyStore.generate(key_size=2048)
        from cryptography.hazmat.primitives import serialization
        self._pem = self._ephemeral.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()

    def test_from_env_loads_key_and_signs(self):
        with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": self._pem}):
            store = RS256KeyStore.from_env()
        token, jti, _ = store.sign_token({"sub": "charlie", "role": "admin"})
        payload = store.verify_token(token)
        assert payload["sub"] == "charlie"

    def test_from_env_uses_rsa_key_id_env_var(self):
        with patch.dict(os.environ, {
            "RSA_PRIVATE_KEY_PEM": self._pem,
            "RSA_KEY_ID": "prod-key-2026-05",
        }):
            store = RS256KeyStore.from_env()
        assert store.key_id == "prod-key-2026-05"

    def test_from_env_normalises_escaped_newlines(self):
        # Kubernetes ConfigMap stores the PEM with literal \n escapes
        inline_pem = self._pem.replace("\n", "\\n")
        with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": inline_pem}):
            store = RS256KeyStore.from_env()
        token, _, _ = store.sign_token({"sub": "test", "role": "analyst"})
        assert store.verify_token(token)["sub"] == "test"


class TestRS256KeyStoreFromEnvMissing:
    def test_raises_runtime_error_when_env_var_absent(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the env var is definitely absent
            os.environ.pop("RSA_PRIVATE_KEY_PEM", None)
            with pytest.raises(RuntimeError, match="RSA_PRIVATE_KEY_PEM"):
                RS256KeyStore.from_env()


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------

class TestRS256KeyStoreExpiredToken:
    def test_expired_token_raises_expired_signature_error(self):
        store = RS256KeyStore.generate(key_size=2048)
        token, _, _ = store.sign_token(
            {"sub": "alice", "role": "analyst"},
            expires_delta=timedelta(seconds=-1),  # already expired
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            store.verify_token(token)


# ---------------------------------------------------------------------------
# JWKS
# ---------------------------------------------------------------------------

class TestRS256KeyStoreAsJwk:
    def test_as_jwk_has_required_fields(self):
        store = RS256KeyStore.generate(key_size=2048)
        jwk = store.as_jwk()
        for field in ("kty", "use", "alg", "kid", "n", "e"):
            assert field in jwk, f"Missing JWK field: {field}"
        assert jwk["kty"] == "RSA"
        assert jwk["alg"] == "RS256"
        assert jwk["kid"] == store.key_id
