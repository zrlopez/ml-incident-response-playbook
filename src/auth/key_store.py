"""
RS256KeyStore — canonical RSA key-pair container (Phase 6 / API-KEY-01).

Design rationale
----------------
The jwt_rs256 module uses module-level globals for the loaded key pair. That is
convenient for a single process, but module-level state is invisible to FastAPI's
dependency injection system and hard to swap in unit tests.

RS256KeyStore wraps the same keys in an explicit object that is attached to
app.state.key_store in the lifespan startup hook. Routes and tests that need to
sign or verify tokens can receive the store via a FastAPI dependency, making
replacement in tests trivial (just assign app.state.key_store = FakeKeyStore()).

jwt_rs256 module-level globals are retained for backward compatibility with the
existing route code; RS256KeyStore is the canonical injection point for all new
routes and test fixtures going forward.

Usage
-----
    store = RS256KeyStore.from_env()
    token, jti, ttl = store.sign_token({"sub": "alice", "role": "analyst"})
    payload = store.verify_token(token)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.backends import default_backend
import jwt


class RS256KeyStore:
    """
    Immutable container for an RSA-2048+ key pair used to sign and verify JWTs.

    Attributes:
        key_id  (str): Key ID embedded in the token header ("kid" claim).
        private_key:   cryptography RSAPrivateKey object.
        public_key:    cryptography RSAPublicKey object.
    """

    def __init__(
        self,
        private_key_pem: str | bytes,
        key_id: Optional[str] = None,
    ) -> None:
        """
        Load an RSA private key from PEM-encoded bytes or a string.

        Args:
            private_key_pem: PEM-encoded RSA private key. May include or omit
                             a passphrase; unencrypted keys are expected in all
                             non-HSM deployments.
            key_id:          Optional key identifier injected into the JWT header
                             as the "kid" claim. Defaults to a deterministic UUID
                             derived from the public key fingerprint.

        Raises:
            ValueError: If the PEM cannot be loaded as a valid RSA private key.
        """
        if isinstance(private_key_pem, str):
            private_key_pem = private_key_pem.encode()
        try:
            _loaded = serialization.load_pem_private_key(
                private_key_pem,
                password=None,
                backend=default_backend(),
            )
        except Exception as exc:
            raise ValueError(f"RS256KeyStore: failed to load private key PEM: {exc}") from exc

        if not isinstance(_loaded, RSAPrivateKey):
            raise ValueError(
                f"RS256KeyStore: expected RSA private key, got {type(_loaded).__name__}"
            )
        self._private_key: RSAPrivateKey = _loaded
        _pub = self._private_key.public_key()
        if not isinstance(_pub, RSAPublicKey):
            raise ValueError(
                f"RS256KeyStore: expected RSA public key, got {type(_pub).__name__}"
            )
        self._public_key: RSAPublicKey = _pub
        self.key_id: str = key_id or str(uuid.uuid5(
            uuid.NAMESPACE_DNS,
            self._public_key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode(),
        ))

    # ------------------------------------------------------------------ factories

    @classmethod
    def from_env(
        cls,
        pem_env_var: str = "RSA_PRIVATE_KEY_PEM",
        key_id_env_var: str = "RSA_KEY_ID",
    ) -> "RS256KeyStore":
        """
        Load the key store from environment variables.

        Args:
            pem_env_var:    Name of the env var holding the PEM. The value may
                            use literal \\n escapes (common in Docker / Kubernetes
                            secrets) which are normalised to real newlines.
            key_id_env_var: Name of the optional key-ID env var.

        Raises:
            RuntimeError: If pem_env_var is unset or empty.
            ValueError:   If the PEM is malformed.
        """
        raw_pem = os.environ.get(pem_env_var, "").strip()
        if not raw_pem:
            raise RuntimeError(
                f"RS256KeyStore.from_env(): {pem_env_var} is not set. "
                "Generate a key with: openssl genrsa -out rsa_private.pem 2048"
            )
        # Normalise escaped newlines that appear when the PEM is stored inline
        # in .env files or Kubernetes ConfigMaps.
        pem = raw_pem.replace("\\n", "\n")
        key_id = os.environ.get(key_id_env_var) or None
        return cls(private_key_pem=pem, key_id=key_id)

    @classmethod
    def generate(cls, key_size: int = 2048, key_id: Optional[str] = None) -> "RS256KeyStore":
        """
        Generate a fresh RSA key pair for testing / local development.

        WARNING: Keys generated this way are ephemeral (in-memory only).
        Tokens signed with them become unverifiable after the process restarts.
        Never use this in production.
        """
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend(),
        )
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        return cls(private_key_pem=pem, key_id=key_id)

    # ------------------------------------------------------------------ properties

    @property
    def private_key(self) -> RSAPrivateKey:
        return self._private_key

    @property
    def public_key(self) -> RSAPublicKey:
        return self._public_key

    # ------------------------------------------------------------------ sign / verify

    def sign_token(
        self,
        data: Dict[str, Any],
        *,
        expires_delta: timedelta = timedelta(minutes=30),
        token_type: str = "access",
    ) -> Tuple[str, str, int]:
        """
        Sign a JWT with RS256.

        Args:
            data:          Payload claims. 'sub' is required.
            expires_delta: Token lifetime. Defaults to 30 minutes.
            token_type:    "access" or "refresh". Stored as the token_type claim.

        Returns:
            Tuple of (encoded_jwt, jti, ttl_seconds).

        Raises:
            ValueError: If 'sub' is absent from data.
        """
        if "sub" not in data:
            raise ValueError("Token payload must include 'sub' claim")
        payload = data.copy()
        jti = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        payload.update({
            "exp": now + expires_delta,
            "iat": now,
            "jti": jti,
            "token_type": token_type,
        })
        token = jwt.encode(
            payload,
            self._private_key,
            algorithm="RS256",
            headers={"kid": self.key_id},
        )
        return token, jti, int(expires_delta.total_seconds())

    def verify_token(self, token: str) -> Dict[str, Any]:
        """
        Verify and decode a JWT signed with this store's private key.

        Args:
            token: Encoded JWT string.

        Returns:
            Decoded payload dict.

        Raises:
            jwt.ExpiredSignatureError: Token has expired.
            jwt.InvalidTokenError:     Token is malformed or signature invalid.
        """
        return jwt.decode(
            token,
            self._public_key,
            algorithms=["RS256"],
            options={"require": ["exp", "iat", "jti", "sub"]},
        )

    # ------------------------------------------------------------------ JWKS

    def as_jwk(self) -> Dict[str, Any]:
        """
        Return the public key as a JSON Web Key dict for /.well-known/jwks.json.
        """
        import base64
        pub: RSAPublicKey = self._public_key
        pub_numbers = pub.public_numbers()
        def _b64(n: int) -> str:
            byte_length = (n.bit_length() + 7) // 8
            return base64.urlsafe_b64encode(
                n.to_bytes(byte_length, "big")
            ).rstrip(b"=").decode()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.key_id,
            "n": _b64(pub_numbers.n),
            "e": _b64(pub_numbers.e),
        }
