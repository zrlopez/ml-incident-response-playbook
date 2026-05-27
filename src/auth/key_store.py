"""
src/auth/key_store.py — RS256 key management with multi-key rotation (OPEN-01)
  R-A02  key_store.generate() now enforces 2048-bit minimum key size (2026-05-26)
===============================================================================

Design
------
Two public classes:

  RS256KeyStore
    Immutable container for a single RSA key pair.  Signs JWTs with its private
    key and verifies tokens whose ``kid`` matches its own ``key_id``.  Used both
    standalone (single-key deployments, tests) and as the building block for
    KeyRotationStore.

  KeyRotationStore
    Wraps an ordered list of RS256KeyStore instances:
      - index 0  → current signing key (all new tokens are signed with this)
      - index 1+ → retired keys still valid during the rotation window

    ``verify_token()`` iterates the list in order, selecting the store whose
    ``key_id`` matches the token's ``kid`` header claim.  A token signed by any
    key in the store is accepted, which allows zero-downtime key rotation:

      1. Generate new key pair → becomes index 0.
      2. Demote old key pair   → becomes index 1 (rotation window, e.g. 24 h).
      3. After window expires  → remove index 1.

    ``as_jwks()`` returns the full JWKS document (all public keys), so downstream
    services that cache JWKS will seamlessly pick up the new signing key.

Environment variables (production)
-----------------------------------
  RSA_PRIVATE_KEY_PEM          Current signing key (required).
  RSA_KEY_ID                   Optional explicit key ID for current key.
  RSA_OLD_PRIVATE_KEY_PEM_1    First retired key PEM (optional).
  RSA_OLD_KEY_ID_1             Optional explicit key ID for retired key 1.
  RSA_OLD_PRIVATE_KEY_PEM_2    Second retired key PEM (optional).
  RSA_OLD_KEY_ID_2             Optional explicit key ID for retired key 2.
  (... pattern continues for N retired keys)

  In public-key-only mode retired keys may be supplied as public PEMs via
  RSA_OLD_PUBLIC_KEY_PEM_1 etc. — the KeyRotationStore.from_env() method
  accepts either form.

Usage
-----
  # Single-key (existing behaviour preserved):
  store = RS256KeyStore.from_env()
  token, jti, ttl = store.sign_token({"sub": "alice", "role": "analyst"})
  payload = store.verify_token(token)

  # Multi-key rotation:
  rotation = KeyRotationStore.from_env()
  token, jti, ttl = rotation.sign_token({"sub": "alice", "role": "analyst"})
  payload = rotation.verify_token(token)   # works for old & new keys
  jwks    = rotation.as_jwks()             # {"keys": [new_jwk, old_jwk, ...]}

  # In-process key generation (dev / CI):
  store = RS256KeyStore.generate(key_size=2048)
"""
from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
import jwt
import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# RS256KeyStore — single key pair
# ---------------------------------------------------------------------------

class RS256KeyStore:
    """
    Immutable container for an RSA key pair used to sign and verify JWTs.

    Attributes
    ----------
    key_id : str
        Key ID embedded in the JWT ``kid`` header and JWKS ``kid`` field.
    private_key : RSAPrivateKey
        The loaded private key (``None`` only in public-key-only mode — see
        ``from_public_pem``).
    public_key : RSAPublicKey
        The loaded public key.
    """

    def __init__(
        self,
        private_key_pem: str | bytes,
        key_id: Optional[str] = None,
    ) -> None:
        """
        Load an RSA private key from PEM bytes or a string.

        Parameters
        ----------
        private_key_pem:
            PEM-encoded RSA private key.  Unencrypted keys are expected in all
            non-HSM deployments.  Literal ``\\n`` escapes (common when the PEM
            is stored as a single-line env var) are normalised automatically.
        key_id:
            Optional explicit key identifier.  Defaults to a deterministic
            UUID derived from the public key's SubjectPublicKeyInfo fingerprint.

        Raises
        ------
        ValueError
            If the PEM cannot be loaded or is not an RSA private key.
        """
        if isinstance(private_key_pem, str):
            private_key_pem = private_key_pem.replace("\\n", "\n").encode()
        try:
            _loaded = serialization.load_pem_private_key(
                private_key_pem,
                password=None,
                backend=default_backend(),
            )
        except Exception as exc:
            raise ValueError(
                f"RS256KeyStore: failed to load private key PEM: {exc}"
            ) from exc

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
        self.key_id: str = key_id or _derive_key_id(self._public_key)

    # ------------------------------------------------------------------
    # Public-key-only mode (for retired keys where private PEM is gone)
    # ------------------------------------------------------------------

    @classmethod
    def from_public_pem(
        cls,
        public_key_pem: str | bytes,
        key_id: Optional[str] = None,
    ) -> "RS256KeyStore":
        """
        Create a verify-only store from a public key PEM.

        ``sign_token()`` will raise ``RuntimeError`` on an instance created this
        way — it is intended exclusively for holding retired keys in a
        ``KeyRotationStore`` when the private key has already been deleted.

        Parameters
        ----------
        public_key_pem:
            PEM-encoded RSA public key.
        key_id:
            Optional explicit key ID.
        """
        if isinstance(public_key_pem, str):
            public_key_pem = public_key_pem.replace("\\n", "\n").encode()
        try:
            _pub = serialization.load_pem_public_key(
                public_key_pem,
                backend=default_backend(),
            )
        except Exception as exc:
            raise ValueError(
                f"RS256KeyStore.from_public_pem: failed to load public key PEM: {exc}"
            ) from exc
        if not isinstance(_pub, RSAPublicKey):
            raise ValueError(
                f"RS256KeyStore.from_public_pem: expected RSA public key, "
                f"got {type(_pub).__name__}"
            )
        instance = object.__new__(cls)
        instance._private_key = None  # type: ignore[assignment]
        instance._public_key = _pub
        instance.key_id = key_id or _derive_key_id(_pub)
        return instance

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        pem_env_var: str = "RSA_PRIVATE_KEY_PEM",
        key_id_env_var: str = "RSA_KEY_ID",
    ) -> "RS256KeyStore":
        """
        Load the key store from environment variables.

        Parameters
        ----------
        pem_env_var:
            Name of the env var holding the PEM.  Literal ``\\n`` escapes are
            normalised (common in Docker / Kubernetes secrets).
        key_id_env_var:
            Name of the optional explicit key-ID env var.

        Raises
        ------
        RuntimeError
            If ``pem_env_var`` is unset or empty.
        ValueError
            If the PEM is malformed.
        """
        raw_pem = os.environ.get(pem_env_var, "").strip()
        if not raw_pem:
            raise RuntimeError(
                f"RS256KeyStore.from_env(): {pem_env_var} is not set. "
                "Generate a key with: openssl genrsa -out rsa_private.pem 2048"
            )
        key_id = os.environ.get(key_id_env_var, "").strip() or None
        return cls(raw_pem, key_id=key_id)

    @classmethod
    def generate(cls, key_size: int = 4096, key_id: Optional[str] = None) -> "RS256KeyStore":
        """
        Generate a fresh RSA key pair in-process.

        Intended for local development and unit tests only.  Production keys
        must be generated externally, stored in a secrets manager, and injected
        via environment variables.

        Parameters
        ----------
        key_size:
            RSA key size in bits.  Minimum 2048 (enforced); 4096 recommended
            for production.  Values below 2048 are rejected to prevent weak-key
            generation even in test contexts.
        key_id:
            Optional explicit key ID.  Defaults to a fingerprint-derived UUID.

        Returns
        -------
        RS256KeyStore
            A fully-loaded store ready for signing and verification.

        Raises
        ------
        ValueError
            If ``key_size`` is below the 2048-bit minimum.
        """
        # R-A02: Enforce minimum key size — sub-2048 keys are cryptographically
        # weak and must be rejected even in test/dev contexts.
        _MINIMUM_KEY_SIZE = 2048
        if key_size < _MINIMUM_KEY_SIZE:
            raise ValueError(
                f"RS256KeyStore.generate(): key_size={key_size} is below the minimum "
                f"of {_MINIMUM_KEY_SIZE} bits. RSA keys smaller than 2048 bits are "
                "cryptographically weak and are not permitted."
            )
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend(),
        )
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cls(pem, key_id=key_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def private_key(self) -> RSAPrivateKey:
        """The RSA private key object (raises if store is public-key-only)."""
        if self._private_key is None:
            raise RuntimeError(
                "RS256KeyStore: this instance was created from a public key PEM "
                "and cannot sign tokens."
            )
        return self._private_key

    @property
    def public_key(self) -> RSAPublicKey:
        """The RSA public key object."""
        return self._public_key

    # ------------------------------------------------------------------
    # Core JWT operations
    # ------------------------------------------------------------------

    def sign_token(
        self,
        data: Dict[str, Any],
        expires_delta: timedelta = timedelta(minutes=30),
        token_type: str = "access",
    ) -> Tuple[str, str, int]:
        """
        Sign a JWT with RS256.

        Parameters
        ----------
        data:
            Payload claims.  ``sub`` is required.
        expires_delta:
            Token lifetime.  Defaults to 30 minutes.
        token_type:
            ``"access"`` or ``"refresh"``.  Stored as the ``token_type`` claim.

        Returns
        -------
        tuple[str, str, int]
            ``(encoded_jwt, jti, ttl_seconds)``

        Raises
        ------
        ValueError
            If ``sub`` is absent from ``data``.
        RuntimeError
            If this store was constructed from a public-key-only PEM.
        """
        if "sub" not in data:
            raise ValueError("Token payload must include 'sub' claim")
        _ = self.private_key  # raises RuntimeError for public-key-only instances
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
        Verify and decode a JWT whose ``kid`` matches this store's ``key_id``.

        This method does **not** perform ``kid`` matching — it always attempts
        verification with this store's public key.  Use ``KeyRotationStore`` for
        multi-key ``kid``-dispatched verification.

        Raises
        ------
        jwt.ExpiredSignatureError
            Token has expired.
        jwt.InvalidTokenError
            Token is malformed or signature is invalid.
        """
        return jwt.decode(
            token,
            self._public_key,
            algorithms=["RS256"],
            options={"require": ["exp", "iat", "jti", "sub"]},
        )

    # ------------------------------------------------------------------
    # JWKS
    # ------------------------------------------------------------------

    def as_jwk(self) -> Dict[str, Any]:
        """
        Return the public key as a JSON Web Key dict.

        Suitable for inclusion in a ``/.well-known/jwks.json`` response.
        """
        pub_numbers = self._public_key.public_numbers()

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


# ---------------------------------------------------------------------------
# KeyRotationStore — multi-key signing + verification
# ---------------------------------------------------------------------------

class KeyRotationStore:
    """
    Multi-key rotation wrapper around a sequence of ``RS256KeyStore`` instances.

    The store at index 0 is the **current** signing key.  All other stores are
    **retired** keys that are still accepted during their rotation window.

    Parameters
    ----------
    keys:
        Ordered sequence of ``RS256KeyStore`` objects.  Must contain at least
        one entry.  The first entry is used for signing.

    Raises
    ------
    ValueError
        If ``keys`` is empty or contains duplicate ``key_id`` values.
    """

    def __init__(self, keys: Sequence[RS256KeyStore]) -> None:
        if not keys:
            raise ValueError("KeyRotationStore requires at least one RS256KeyStore.")
        ids = [k.key_id for k in keys]
        seen: set[str] = set()
        for kid in ids:
            if kid in seen:
                raise ValueError(
                    f"KeyRotationStore: duplicate key_id detected: {kid!r}. "
                    "Each key in the rotation set must have a unique key_id."
                )
            seen.add(kid)
        self._keys: List[RS256KeyStore] = list(keys)
        self._index: Dict[str, RS256KeyStore] = {k.key_id: k for k in self._keys}
        log.info(
            "key_rotation_store.initialised",
            active_kid=self.current.key_id,
            rotation_pool_size=len(self._keys),
            retired_kids=[k.key_id for k in self._keys[1:]],
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "KeyRotationStore":
        """
        Build a ``KeyRotationStore`` from environment variables.

        Loads the current signing key from ``RSA_PRIVATE_KEY_PEM`` (and
        optionally ``RSA_KEY_ID``), then discovers retired keys by scanning for
        numbered env var pairs:

          RSA_OLD_PRIVATE_KEY_PEM_1 / RSA_OLD_KEY_ID_1   (private PEM preferred)
          RSA_OLD_PUBLIC_KEY_PEM_1  / RSA_OLD_KEY_ID_1   (public PEM fallback)
          RSA_OLD_PRIVATE_KEY_PEM_2 / RSA_OLD_KEY_ID_2
          ...

        Scanning stops at the first missing index.  An absent ``_KEY_ID_N``
        means the key ID is derived automatically from the key fingerprint.

        Raises
        ------
        RuntimeError
            If ``RSA_PRIVATE_KEY_PEM`` is unset.
        """
        current = RS256KeyStore.from_env(
            pem_env_var="RSA_PRIVATE_KEY_PEM",
            key_id_env_var="RSA_KEY_ID",
        )
        stores: List[RS256KeyStore] = [current]

        idx = 1
        while True:
            priv_pem = os.environ.get(f"RSA_OLD_PRIVATE_KEY_PEM_{idx}", "").strip()
            pub_pem = os.environ.get(f"RSA_OLD_PUBLIC_KEY_PEM_{idx}", "").strip()
            kid = os.environ.get(f"RSA_OLD_KEY_ID_{idx}", "").strip() or None

            if not priv_pem and not pub_pem:
                # No more retired keys defined.
                break

            try:
                if priv_pem:
                    retired = RS256KeyStore(priv_pem, key_id=kid)
                else:
                    retired = RS256KeyStore.from_public_pem(pub_pem, key_id=kid)
                stores.append(retired)
                log.info(
                    "key_rotation_store.retired_key_loaded",
                    index=idx,
                    kid=retired.key_id,
                    mode="private" if priv_pem else "public-only",
                )
            except (ValueError, RuntimeError) as exc:
                log.warning(
                    "key_rotation_store.retired_key_load_failed",
                    index=idx,
                    error=str(exc),
                )
            idx += 1

        return cls(stores)

    @classmethod
    def from_stores(cls, *stores: RS256KeyStore) -> "KeyRotationStore":
        """Convenience constructor for tests — accepts positional stores."""
        return cls(list(stores))

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def current(self) -> RS256KeyStore:
        """The active signing key (index 0)."""
        return self._keys[0]

    @property
    def all_keys(self) -> List[RS256KeyStore]:
        """All keys including retired ones, in rotation order."""
        return list(self._keys)

    @property
    def key_id(self) -> str:
        """Key ID of the current signing key (mirrors RS256KeyStore.key_id)."""
        return self.current.key_id

    # ------------------------------------------------------------------
    # JWT operations
    # ------------------------------------------------------------------

    def sign_token(
        self,
        data: Dict[str, Any],
        expires_delta: timedelta = timedelta(minutes=30),
        token_type: str = "access",
    ) -> Tuple[str, str, int]:
        """
        Sign a JWT using the **current** key (index 0).

        Delegates to ``self.current.sign_token()`` — see that method's
        docstring for parameter and return value documentation.
        """
        return self.current.sign_token(
            data,
            expires_delta=expires_delta,
            token_type=token_type,
        )

    def verify_token(self, token: str) -> Dict[str, Any]:
        """
        Verify a JWT against the rotation pool, dispatching by ``kid``.

        Algorithm
        ---------
        1. Decode the JWT header without signature verification to extract
           the ``kid`` claim.
        2. Look up the corresponding ``RS256KeyStore`` in the rotation index.
        3. If found, verify with that store's public key.
        4. If the ``kid`` is absent or unrecognised, fall back to the current
           key (preserves compatibility with tokens issued before ``kid`` was
           added to the header).

        Only ``RS256`` is accepted — tokens with ``alg: HS256`` or ``alg: none``
        are rejected outright.

        Raises
        ------
        jwt.exceptions.InvalidKeyError
            If the ``kid`` is present but does not match any key in the pool.
        jwt.ExpiredSignatureError
            If the token has expired.
        jwt.InvalidTokenError
            If the token is malformed or the signature is invalid.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.DecodeError as exc:
            raise jwt.DecodeError(f"Cannot decode JWT header: {exc}") from exc

        token_kid: str = header.get("kid", "")
        alg: str = header.get("alg", "")

        if alg and alg != "RS256":
            raise jwt.InvalidAlgorithmError(
                f"KeyRotationStore: token uses algorithm {alg!r}; only RS256 is accepted."
            )

        if token_kid and token_kid not in self._index:
            raise jwt.InvalidKeyError(
                f"KeyRotationStore: kid {token_kid!r} is not in the rotation pool "
                f"(known kids: {list(self._index.keys())!r})."
            )

        store = self._index.get(token_kid, self.current)

        if token_kid and store is not self.current:
            log.info(
                "key_rotation_store.verified_with_retired_key",
                kid=token_kid,
                current_kid=self.current.key_id,
            )

        return store.verify_token(token)

    # ------------------------------------------------------------------
    # JWKS
    # ------------------------------------------------------------------

    def as_jwks(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Return the full JWKS document for ``/.well-known/jwks.json``.

        All keys in the rotation pool are included so downstream verifiers that
        cache JWKS will accept both old and new tokens during the rotation
        window.

        Returns
        -------
        dict
            ``{"keys": [<current_jwk>, <retired_jwk_1>, ...]}``
        """
        return {"keys": [k.as_jwk() for k in self._keys]}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _derive_key_id(public_key: RSAPublicKey) -> str:
    """
    Derive a stable, deterministic key ID from the public key's
    SubjectPublicKeyInfo fingerprint using UUID v5 (SHA-1 namespace).

    Using UUID v5 (rather than a raw SHA-256 hex) keeps the ``kid`` value
    short enough to be safe for HTTP headers and log fields while still being
    collision-resistant for any realistic number of keys.
    """
    spki = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, spki))
