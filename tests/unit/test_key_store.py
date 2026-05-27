"""
tests/unit/test_key_store.py — RS256KeyStore + KeyRotationStore (OPEN-01)

All tests generate ephemeral 2048-bit keys in-process.  No filesystem I/O,
no real env vars, no network, no database.  Runs with: pytest tests/unit/test_key_store.py
"""
from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest

from src.auth.key_store import KeyRotationStore, RS256KeyStore, _derive_key_id


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def store_a() -> RS256KeyStore:
    """Ephemeral 2048-bit store, used as 'current' key."""
    return RS256KeyStore.generate(key_size=2048, key_id="key-a")


@pytest.fixture(scope="module")
def store_b() -> RS256KeyStore:
    """Ephemeral 2048-bit store, used as 'retired' key."""
    return RS256KeyStore.generate(key_size=2048, key_id="key-b")


@pytest.fixture(scope="module")
def store_c() -> RS256KeyStore:
    """Third ephemeral store for deeper rotation pool tests."""
    return RS256KeyStore.generate(key_size=2048, key_id="key-c")


# ---------------------------------------------------------------------------
# RS256KeyStore.generate()
# ---------------------------------------------------------------------------

class TestRS256KeyStoreGenerate:
    def test_key_id_is_non_empty_string(self, store_a):
        assert isinstance(store_a.key_id, str)
        assert len(store_a.key_id) > 0

    def test_explicit_key_id_is_preserved(self):
        store = RS256KeyStore.generate(key_size=2048, key_id="my-explicit-id")
        assert store.key_id == "my-explicit-id"

    def test_derived_key_id_is_deterministic(self):
        """Two stores with the same private key must produce the same derived key_id."""
        from cryptography.hazmat.primitives import serialization
        s1 = RS256KeyStore.generate(key_size=2048)
        pem = s1.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        s2 = RS256KeyStore(pem)
        assert s1.key_id == s2.key_id

    def test_sign_token_returns_three_tuple(self, store_a):
        token, jti, ttl = store_a.sign_token({"sub": "alice", "role": "analyst"})
        assert isinstance(token, str) and token
        assert isinstance(jti, str) and jti
        assert ttl == 30 * 60

    def test_verify_token_decodes_correctly(self, store_a):
        token, jti, _ = store_a.sign_token({"sub": "alice", "role": "analyst"})
        payload = store_a.verify_token(token)
        assert payload["sub"] == "alice"
        assert payload["role"] == "analyst"
        assert payload["jti"] == jti
        assert payload["token_type"] == "access"

    def test_kid_embedded_in_token_header(self, store_a):
        token, _, _ = store_a.sign_token({"sub": "alice", "role": "analyst"})
        header = jwt.get_unverified_header(token)
        assert header["kid"] == store_a.key_id

    def test_custom_ttl_respected(self, store_a):
        _, _, ttl = store_a.sign_token(
            {"sub": "bob", "role": "operator"},
            expires_delta=timedelta(hours=2),
        )
        assert ttl == 7200

    def test_refresh_token_type_claim(self, store_a):
        token, _, _ = store_a.sign_token(
            {"sub": "alice", "role": "analyst"},
            token_type="refresh",
        )
        payload = store_a.verify_token(token)
        assert payload["token_type"] == "refresh"

    def test_cross_key_verification_fails(self, store_a, store_b):
        token, _, _ = store_a.sign_token({"sub": "alice", "role": "analyst"})
        with pytest.raises(jwt.InvalidSignatureError):
            store_b.verify_token(token)

    def test_missing_sub_raises_value_error(self, store_a):
        with pytest.raises(ValueError, match="sub"):
            store_a.sign_token({"role": "analyst"})

    def test_different_generate_calls_produce_different_key_ids(self):
        s1 = RS256KeyStore.generate(key_size=2048)
        s2 = RS256KeyStore.generate(key_size=2048)
        assert s1.key_id != s2.key_id


# ---------------------------------------------------------------------------
# RS256KeyStore.from_public_pem() — verify-only mode
# ---------------------------------------------------------------------------

class TestRS256KeyStorePublicOnly:
    @pytest.fixture()
    def pub_only(self, store_a) -> RS256KeyStore:
        from cryptography.hazmat.primitives import serialization
        pub_pem = store_a.public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        return RS256KeyStore.from_public_pem(pub_pem, key_id=store_a.key_id)

    def test_verify_succeeds_for_matching_token(self, store_a, pub_only):
        token, _, _ = store_a.sign_token({"sub": "alice", "role": "analyst"})
        payload = pub_only.verify_token(token)
        assert payload["sub"] == "alice"

    def test_sign_token_raises_runtime_error(self, pub_only):
        with pytest.raises(RuntimeError, match="public key"):
            pub_only.sign_token({"sub": "alice", "role": "analyst"})

    def test_private_key_property_raises_runtime_error(self, pub_only):
        with pytest.raises(RuntimeError, match="public key"):
            _ = pub_only.private_key

    def test_key_id_preserved(self, store_a, pub_only):
        assert pub_only.key_id == store_a.key_id


# ---------------------------------------------------------------------------
# RS256KeyStore.from_env()
# ---------------------------------------------------------------------------

class TestRS256KeyStoreFromEnv:
    @pytest.fixture()
    def pem(self, store_a) -> str:
        from cryptography.hazmat.primitives import serialization
        return store_a.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()

    def test_loads_and_signs(self, pem):
        with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": pem}):
            store = RS256KeyStore.from_env()
        token, _, _ = store.sign_token({"sub": "charlie", "role": "admin"})
        payload = store.verify_token(token)
        assert payload["sub"] == "charlie"

    def test_explicit_key_id_env_var(self, pem):
        with patch.dict(os.environ, {
            "RSA_PRIVATE_KEY_PEM": pem,
            "RSA_KEY_ID": "prod-key-2026-05",
        }):
            store = RS256KeyStore.from_env()
        assert store.key_id == "prod-key-2026-05"

    def test_normalises_escaped_newlines(self, pem):
        inline_pem = pem.replace("\n", "\\n")
        with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": inline_pem}):
            store = RS256KeyStore.from_env()
        token, _, _ = store.sign_token({"sub": "test", "role": "analyst"})
        assert store.verify_token(token)["sub"] == "test"

    def test_raises_runtime_error_when_env_var_absent(self):
        env = {k: v for k, v in os.environ.items() if k != "RSA_PRIVATE_KEY_PEM"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="RSA_PRIVATE_KEY_PEM"):
                RS256KeyStore.from_env()


# ---------------------------------------------------------------------------
# KeyRotationStore — construction
# ---------------------------------------------------------------------------

class TestKeyRotationStoreConstruction:
    def test_single_key_constructs(self, store_a):
        rotation = KeyRotationStore.from_stores(store_a)
        assert rotation.current is store_a
        assert len(rotation.all_keys) == 1

    def test_multi_key_constructs(self, store_a, store_b, store_c):
        rotation = KeyRotationStore.from_stores(store_a, store_b, store_c)
        assert rotation.current is store_a
        assert len(rotation.all_keys) == 3

    def test_key_id_reflects_current(self, store_a, store_b):
        rotation = KeyRotationStore.from_stores(store_a, store_b)
        assert rotation.key_id == store_a.key_id

    def test_empty_sequence_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            KeyRotationStore([])

    def test_duplicate_key_id_raises(self, store_a):
        with pytest.raises(ValueError, match="duplicate key_id"):
            KeyRotationStore([store_a, store_a])


# ---------------------------------------------------------------------------
# KeyRotationStore — signing
# ---------------------------------------------------------------------------

class TestKeyRotationStoreSigning:
    def test_sign_uses_current_key(self, store_a, store_b):
        rotation = KeyRotationStore.from_stores(store_a, store_b)
        token, _, _ = rotation.sign_token({"sub": "alice", "role": "analyst"})
        header = jwt.get_unverified_header(token)
        assert header["kid"] == store_a.key_id

    def test_sign_token_ttl_default(self, store_a):
        rotation = KeyRotationStore.from_stores(store_a)
        _, _, ttl = rotation.sign_token({"sub": "bob", "role": "operator"})
        assert ttl == 1800

    def test_sign_token_custom_ttl(self, store_a):
        rotation = KeyRotationStore.from_stores(store_a)
        _, _, ttl = rotation.sign_token(
            {"sub": "bob", "role": "operator"},
            expires_delta=timedelta(hours=1),
        )
        assert ttl == 3600


# ---------------------------------------------------------------------------
# KeyRotationStore — verification (the core OPEN-01 behaviour)
# ---------------------------------------------------------------------------

class TestKeyRotationStoreVerification:
    def test_verify_token_signed_by_current_key(self, store_a, store_b):
        rotation = KeyRotationStore.from_stores(store_a, store_b)
        token, jti, _ = rotation.sign_token({"sub": "alice", "role": "analyst"})
        payload = rotation.verify_token(token)
        assert payload["sub"] == "alice"
        assert payload["jti"] == jti

    def test_verify_token_signed_by_retired_key(self, store_a, store_b):
        """Token signed by the retired key must still verify during rotation window."""
        # Simulate a token issued before rotation: signed with store_b
        old_token, _, _ = store_b.sign_token({"sub": "bob", "role": "operator"})
        # Pool now has store_a as current, store_b as retired
        rotation = KeyRotationStore.from_stores(store_a, store_b)
        payload = rotation.verify_token(old_token)
        assert payload["sub"] == "bob"

    def test_verify_three_key_pool(self, store_a, store_b, store_c):
        """Tokens from any of three keys must verify."""
        rotation = KeyRotationStore.from_stores(store_a, store_b, store_c)
        for store, sub in [(store_a, "alice"), (store_b, "bob"), (store_c, "carol")]:
            token, _, _ = store.sign_token({"sub": sub, "role": "analyst"})
            payload = rotation.verify_token(token)
            assert payload["sub"] == sub

    def test_verify_unknown_kid_raises_invalid_key_error(self, store_a, store_b):
        """Token with a kid not in the pool must be rejected explicitly."""
        unknown_store = RS256KeyStore.generate(key_size=2048, key_id="unknown-kid")
        token, _, _ = unknown_store.sign_token({"sub": "attacker", "role": "analyst"})
        rotation = KeyRotationStore.from_stores(store_a, store_b)
        with pytest.raises(jwt.InvalidKeyError, match="not in the rotation pool"):
            rotation.verify_token(token)

    def test_verify_expired_token_raises(self, store_a):
        token, _, _ = store_a.sign_token(
            {"sub": "alice", "role": "analyst"},
            expires_delta=timedelta(seconds=-1),
        )
        rotation = KeyRotationStore.from_stores(store_a)
        with pytest.raises(jwt.ExpiredSignatureError):
            rotation.verify_token(token)

    def test_verify_rejects_hs256_token(self, store_a):
        """Algorithm confusion: HS256 token must be rejected even if the secret matches."""
        hs256_token = jwt.encode(
            {"sub": "attacker", "role": "admin", "exp": 9999999999, "iat": 0, "jti": "x"},
            "some-secret",  # noqa: S106 — intentional wrong key for algorithm confusion attack test
            algorithm="HS256",
            headers={"kid": store_a.key_id},
        )
        rotation = KeyRotationStore.from_stores(store_a)
        with pytest.raises((jwt.InvalidAlgorithmError, jwt.DecodeError, jwt.InvalidSignatureError)):
            rotation.verify_token(hs256_token)

    def test_verify_malformed_token_raises(self, store_a):
        rotation = KeyRotationStore.from_stores(store_a)
        with pytest.raises(jwt.DecodeError):
            rotation.verify_token("not.a.token")


# ---------------------------------------------------------------------------
# KeyRotationStore — JWKS output
# ---------------------------------------------------------------------------

class TestKeyRotationStoreJWKS:
    def test_single_key_jwks(self, store_a):
        rotation = KeyRotationStore.from_stores(store_a)
        jwks = rotation.as_jwks()
        assert "keys" in jwks
        assert len(jwks["keys"]) == 1
        assert jwks["keys"][0]["kid"] == store_a.key_id

    def test_multi_key_jwks_includes_all_keys(self, store_a, store_b, store_c):
        rotation = KeyRotationStore.from_stores(store_a, store_b, store_c)
        jwks = rotation.as_jwks()
        kids = {k["kid"] for k in jwks["keys"]}
        assert kids == {store_a.key_id, store_b.key_id, store_c.key_id}

    def test_jwks_key_order_current_first(self, store_a, store_b):
        rotation = KeyRotationStore.from_stores(store_a, store_b)
        jwks = rotation.as_jwks()
        assert jwks["keys"][0]["kid"] == store_a.key_id
        assert jwks["keys"][1]["kid"] == store_b.key_id

    def test_jwks_fields_are_correct(self, store_a):
        rotation = KeyRotationStore.from_stores(store_a)
        key = rotation.as_jwks()["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == "RS256"
        assert "n" in key and "e" in key


# ---------------------------------------------------------------------------
# KeyRotationStore.from_env()
# ---------------------------------------------------------------------------

class TestKeyRotationStoreFromEnv:
    @pytest.fixture()
    def pem_a(self, store_a) -> str:
        from cryptography.hazmat.primitives import serialization
        return store_a.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()

    @pytest.fixture()
    def pem_b(self, store_b) -> str:
        from cryptography.hazmat.primitives import serialization
        return store_b.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()

    @pytest.fixture()
    def pub_pem_b(self, store_b) -> str:
        from cryptography.hazmat.primitives import serialization
        return store_b.public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def test_single_key_from_env(self, pem_a):
        with patch.dict(os.environ, {"RSA_PRIVATE_KEY_PEM": pem_a}, clear=False):
            rotation = KeyRotationStore.from_env()
        assert len(rotation.all_keys) == 1

    def test_two_keys_private_pem_retired(self, pem_a, pem_b):
        env = {
            "RSA_PRIVATE_KEY_PEM": pem_a,
            "RSA_KEY_ID": "key-a",
            "RSA_OLD_PRIVATE_KEY_PEM_1": pem_b,
            "RSA_OLD_KEY_ID_1": "key-b",
        }
        with patch.dict(os.environ, env, clear=False):
            rotation = KeyRotationStore.from_env()
        assert len(rotation.all_keys) == 2
        assert rotation.current.key_id == "key-a"
        assert rotation.all_keys[1].key_id == "key-b"

    def test_retired_key_public_pem_only(self, pem_a, pub_pem_b, store_b):
        """Retired key supplied as public-only PEM — verify still works."""
        old_token, _, _ = store_b.sign_token({"sub": "bob", "role": "operator"})
        env = {
            "RSA_PRIVATE_KEY_PEM": pem_a,
            "RSA_KEY_ID": "key-a",
            "RSA_OLD_PUBLIC_KEY_PEM_1": pub_pem_b,
            "RSA_OLD_KEY_ID_1": "key-b",
        }
        with patch.dict(os.environ, env, clear=False):
            rotation = KeyRotationStore.from_env()
        payload = rotation.verify_token(old_token)
        assert payload["sub"] == "bob"

    def test_scanning_stops_at_first_missing_index(self, pem_a, pem_b):
        """Index 1 present, index 2 absent — only two keys total."""
        env = {
            "RSA_PRIVATE_KEY_PEM": pem_a,
            "RSA_OLD_PRIVATE_KEY_PEM_1": pem_b,
            # RSA_OLD_PRIVATE_KEY_PEM_2 intentionally absent
        }
        with patch.dict(os.environ, env, clear=False):
            rotation = KeyRotationStore.from_env()
        assert len(rotation.all_keys) == 2

    def test_missing_current_key_raises(self):
        env = {k: v for k, v in os.environ.items() if k != "RSA_PRIVATE_KEY_PEM"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="RSA_PRIVATE_KEY_PEM"):
                KeyRotationStore.from_env()
