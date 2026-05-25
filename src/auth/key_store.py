"""
RS256KeyStore — immutable RSA key state container.

RS256-01 Rationale:
  The original jwt_rs256.py used module-level globals (_private_key, _public_key,
  _key_id, _old_public_key, _old_key_id) to hold RSA key material loaded from
  environment variables at import time.

  This pattern has a critical failure mode under multi-process deployment
  (Gunicorn with multiple Uvicorn workers, Kubernetes Deployment with multiple
  replicas):

    - Each worker process imports jwt_rs256 independently.
    - If key material is injected via a secrets manager that rotates between
      worker startups, different workers can hold different key state.
    - More subtly, module globals cannot be replaced cleanly during testing
      without patching the module's namespace, which creates fragile test
      coupling to the internal implementation rather than the public interface.

  RS256KeyStore solves both problems:
    1. Key state is a dataclass instance constructed once in the FastAPI lifespan
       handler and attached to app.state.key_store.
    2. All token functions accept key_store as an argument (or via FastAPI Depends()),
       making the dependency explicit and injectable in tests without monkeypatching.
    3. The dataclass is frozen (immutable after construction), preventing accidental
       mutation of live key material in route handlers.

Usage:
    # In lifespan startup:
    app.state.key_store = RS256KeyStore.from_env()

    # In FastAPI dependency:
    def get_key_store(request: Request) -> RS256KeyStore:
        return request.app.state.key_store

    # In token route:
    @app.post("/auth/token")
    async def login(
        key_store: Annotated[RS256KeyStore, Depends(get_key_store)],
        ...
    ):
        token = key_store.sign_access_token(subject, role, expires_delta)

Key rotation:
    Set RSA_PRIVATE_KEY_PEM_OLD and RSA_KEY_ID_OLD in the environment alongside
    the current key pair to enable zero-downtime rotation. The old public key is
    retained for verification-only (no new tokens are signed with it), allowing
    tokens issued before rotation to remain valid until they expire naturally.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey


_ALGORITHM = "RS256"


@dataclass(frozen=True)
class RS256KeyStore:
    """
    Immutable container for RS256 signing and verification key material.

    Constructed once at application startup via RS256KeyStore.from_env().
    Attached to app.state.key_store for injection into route handlers.

    Attributes:
        private_key:   Current RSA private key used to sign new tokens.
        public_key:    Current RSA public key used to verify current tokens.
        key_id:        Key ID (kid) embedded in JWT headers; used to select
                       the correct verification key from the JWKS endpoint.
        old_public_key: Previous public key retained during key rotation to
                        validate tokens issued before the rotation event.
                        None when no rotation is in progress.
        old_key_id:    Key ID of the old public key. Empty string when absent.
    """

    private_key: RSAPrivateKey
    public_key: RSAPublicKey
    key_id: str
    old_public_key: Optional[RSAPublicKey] = field(default=None)
    old_key_id: str = field(default="")

    # ── Construction ────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "RS256KeyStore":
        """
        Load key material from environment variables.

        Required environment variables:
            RSA_PRIVATE_KEY_PEM  — PEM-encoded RSA private key (PKCS#8 or PKCS#1).
                                   In production: inject from AWS Secrets Manager /
                                   HashiCorp Vault rather than a static env var.
            RSA_PUBLIC_KEY_PEM   — PEM-encoded RSA public key.
            RSA_KEY_ID           — Opaque string identifying this key pair in JWKS.
                                   Use a short hash or UUID. Example: "prod-2026-05".

        Optional environment variables (key rotation):
            RSA_PRIVATE_KEY_PEM_OLD — Previous private key PEM (no longer used for
                                      signing; retained only so the variable name is
                                      documented). Not loaded into key store.
            RSA_PUBLIC_KEY_PEM_OLD  — Previous public key PEM for verifying old tokens.
            RSA_KEY_ID_OLD          — Key ID of the old public key.

        Raises:
            RuntimeError: If any required environment variable is absent.
            ValueError:   If any PEM value cannot be parsed as a valid RSA key.
        """
        private_pem = cls._require_env("RSA_PRIVATE_KEY_PEM")
        public_pem = cls._require_env("RSA_PUBLIC_KEY_PEM")
        key_id = cls._require_env("RSA_KEY_ID")

        private_key = serialization.load_pem_private_key(
            private_pem.encode(), password=None
        )
        public_key = serialization.load_pem_public_key(public_pem.encode())

        # Optional rotation keys — fail softly if absent
        old_public_key = None
        old_key_id = ""
        old_public_pem = os.environ.get("RSA_PUBLIC_KEY_PEM_OLD", "").strip()
        if old_public_pem:
            old_public_key = serialization.load_pem_public_key(old_public_pem.encode())
            old_key_id = os.environ.get("RSA_KEY_ID_OLD", "old").strip()

        return cls(
            private_key=private_key,
            public_key=public_key,
            key_id=key_id,
            old_public_key=old_public_key,
            old_key_id=old_key_id,
        )

    @staticmethod
    def _require_env(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise RuntimeError(
                f"FATAL: {name} is not set. "
                f"RS256KeyStore requires all three RSA_*_KEY_PEM and RSA_KEY_ID "
                f"environment variables. In development, run: make keys"
            )
        return value

    # ── Token operations ─────────────────────────────────────────────────────────

    def sign_access_token(
        self,
        subject: str,
        role: str,
        expires_delta: timedelta,
        trace_id: str = "",
    ) -> str:
        """
        Issue a signed RS256 access token.

        Args:
            subject:       Username / user identifier (becomes JWT 'sub' claim).
            role:          RBAC role string (embedded as 'role' claim).
            expires_delta: Token lifetime. Caller controls duration to allow
                           different lifetimes for access vs. refresh tokens.
            trace_id:      Optional distributed trace ID embedded as 'tid' claim
                           for correlation with request logs.

        Returns:
            Compact JWS string (header.payload.signature).
        """
        now = datetime.now(timezone.utc)
        payload = {
            "sub": subject,
            "role": role,
            "iat": now,
            "exp": now + expires_delta,
            "jti": os.urandom(16).hex(),  # Unique token ID for denylist lookups
        }
        if trace_id:
            payload["tid"] = trace_id

        headers = {"kid": self.key_id}
        return jwt.encode(
            payload,
            self.private_key,
            algorithm=_ALGORITHM,
            headers=headers,
        )

    def verify_token(self, token: str) -> dict:
        """
        Verify and decode a JWT, trying current key then rotation key.

        Algorithm confusion protection: only RS256 is accepted.
        The 'algorithms' list is explicit and does not include 'none' or
        any symmetric algorithm.

        Raises:
            jwt.ExpiredSignatureError:  Token is past its 'exp' claim.
            jwt.InvalidTokenError:      Token is malformed or signature invalid.
        """
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid", "")

        # Select verification key based on kid claim
        if kid == self.key_id:
            verify_key = self.public_key
        elif self.old_public_key and kid == self.old_key_id:
            verify_key = self.old_public_key
        else:
            # Unknown kid — attempt current key (handles missing kid header)
            verify_key = self.public_key

        return jwt.decode(
            token,
            verify_key,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "exp", "iat", "jti", "role"]},
        )

    def public_jwks(self) -> dict:
        """
        Return a JWKS-formatted dict of the current (and optional old) public key.

        Suitable for direct use as the /auth/jwks.json endpoint response body.
        Consumers (API gateways, downstream services) use this to verify tokens
        without needing access to the private key.
        """
        keys = [self._public_key_to_jwk(self.public_key, self.key_id)]
        if self.old_public_key and self.old_key_id:
            keys.append(self._public_key_to_jwk(self.old_public_key, self.old_key_id))
        return {"keys": keys}

    @staticmethod
    def _public_key_to_jwk(key: RSAPublicKey, kid: str) -> dict:
        """Convert an RSA public key to a minimal RFC 7517 JWK dict."""
        import base64
        pub_numbers = key.public_key().public_numbers() if hasattr(key, 'public_key') else key.public_numbers()
        def _to_base64url(n: int) -> str:
            length = (n.bit_length() + 7) // 8
            return base64.urlsafe_b64encode(
                n.to_bytes(length, byteorder="big")
            ).rstrip(b"=").decode()

        return {
            "kty": "RSA",
            "use": "sig",
            "alg": _ALGORITHM,
            "kid": kid,
            "n": _to_base64url(pub_numbers.n),
            "e": _to_base64url(pub_numbers.e),
        }
