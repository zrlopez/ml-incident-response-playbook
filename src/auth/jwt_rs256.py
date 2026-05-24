"""
src/auth/jwt_rs256.py — RS256 JWT helpers (ARCH-01)
====================================================
Phase 4 remediation: upgrades JWT signing from HS256 (symmetric shared secret)
to RS256 (RSA asymmetric key pair), enabling:

  - Zero shared-secret risk: verifiers only need the public key
  - JWKS endpoint: downstream services verify tokens without trusting API internals
  - Key rotation: old public key stays valid during rotation window
  - Algorithm confusion protection: public key cannot be abused as an HMAC secret

Finding addressed:
  ARCH-01  HS256 symmetric JWT — all services sharing JWT_SECRET_KEY creates
           a single point of compromise. RS256 eliminates shared-secret risk.

Key management:
  Private key:  Loaded from RSA_PRIVATE_KEY_PEM env var (PEM string, no file).
                In production: inject from AWS Secrets Manager / Vault (ARCH-04).
  Public key:   Loaded from RSA_PUBLIC_KEY_PEM env var or derived from private key.
                Also served via GET /.well-known/jwks.json for downstream verifiers.

Integration with api/app.py:
  1. Set ENABLE_RS256=true in environment
  2. app.py lifespan calls jwt_rs256.load_keys() on startup
  3. create_access_token / create_refresh_token use RS256 path when keys loaded
  4. Mount jwks_router on app: app.include_router(jwks_router)

Key generation (one-time per environment):
  # Generate 4096-bit RSA key pair:
  openssl genrsa -out jwt_private.pem 4096
  openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem

  # Store in secrets manager (never in .env or git):
  # RSA_PRIVATE_KEY_PEM  ← contents of jwt_private.pem
  # RSA_PUBLIC_KEY_PEM   ← contents of jwt_public.pem (optional; derived if absent)

  # Local dev only (not production):
  # export RSA_PRIVATE_KEY_PEM=$(cat jwt_private.pem)
  # export RSA_PUBLIC_KEY_PEM=$(cat jwt_public.pem)

Rotation procedure:
  1. Generate new key pair (new_private.pem, new_public.pem)
  2. Update RSA_PRIVATE_KEY_PEM secret to new private key
  3. Add old public key to RSA_OLD_PUBLIC_KEY_PEM for a rotation window (default 24h)
     so tokens signed with the old key remain valid during the window
  4. After window: clear RSA_OLD_PUBLIC_KEY_PEM
  5. JWKS endpoint automatically reflects the active keys
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import APIRouter
import structlog

log = structlog.get_logger(__name__)

# ── Module state ────────────────────────────────────────────────────────────────
_private_key: RSAPrivateKey | None = None
_public_key: RSAPublicKey | None = None
_old_public_key: RSAPublicKey | None = None  # Retained during rotation window
_key_id: str = ""  # kid claim in JWKS; SHA-256 fingerprint of public key DER
_old_key_id: str = ""


# ── Key loading ───────────────────────────────────────────────────────────────

def _pem_to_key_id(public_key: RSAPublicKey) -> str:
    """Derive a stable key ID from the SHA-256 fingerprint of the DER-encoded public key."""
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()[:16]


def load_keys() -> bool:
    """
    Load RSA key pair from environment variables.
    Returns True if RS256 is available, False if HS256 fallback should be used.

    Call this once in the application lifespan startup.
    """
    global _private_key, _public_key, _old_public_key, _key_id, _old_key_id

    private_pem = os.environ.get("RSA_PRIVATE_KEY_PEM", "").strip()
    if not private_pem:
        log.warning(
            "jwt_rs256.keys_not_loaded",
            reason="RSA_PRIVATE_KEY_PEM not set",
            fallback="HS256 active — set RSA_PRIVATE_KEY_PEM to enable RS256",
        )
        return False

    try:
        _private_key = serialization.load_pem_private_key(
            private_pem.encode(), password=None
        )
        _public_key = _private_key.public_key()
        _key_id = _pem_to_key_id(_public_key)
        log.info("jwt_rs256.private_key_loaded", key_id=_key_id)
    except Exception as exc:
        log.error("jwt_rs256.private_key_load_failed", error=str(exc))
        raise RuntimeError(f"Failed to load RSA_PRIVATE_KEY_PEM: {exc}") from exc

    # Optional: load public key override (useful when public key is distributed separately)
    public_pem = os.environ.get("RSA_PUBLIC_KEY_PEM", "").strip()
    if public_pem:
        try:
            _public_key = serialization.load_pem_public_key(public_pem.encode())
            _key_id = _pem_to_key_id(_public_key)
        except Exception as exc:
            log.error("jwt_rs256.public_key_load_failed", error=str(exc))
            raise RuntimeError(f"Failed to load RSA_PUBLIC_KEY_PEM: {exc}") from exc

    # Optional: load old public key for rotation window
    old_public_pem = os.environ.get("RSA_OLD_PUBLIC_KEY_PEM", "").strip()
    if old_public_pem:
        try:
            _old_public_key = serialization.load_pem_public_key(old_public_pem.encode())
            _old_key_id = _pem_to_key_id(_old_public_key)
            log.info("jwt_rs256.old_key_loaded_rotation_window", old_key_id=_old_key_id)
        except Exception as exc:
            log.warning("jwt_rs256.old_key_load_failed", error=str(exc))

    return True


def rs256_available() -> bool:
    """True if RS256 keys are loaded and ready."""
    return _private_key is not None and _public_key is not None


# ── Token creation ─────────────────────────────────────────────────────────────

def sign_token(
    payload: dict[str, Any],
    expires_delta: timedelta,
    token_type: str = "access",
) -> tuple[str, str, int]:
    """
    Sign a JWT with RS256. Returns (encoded_token, jti, ttl_seconds).

    The payload is augmented with standard claims:
      iat, exp, jti, token_type, kid (key ID for JWKS lookup)
    """
    if not rs256_available():
        raise RuntimeError(
            "RS256 keys not loaded. Call load_keys() in application lifespan."
        )

    import uuid as _uuid
    now = datetime.now(timezone.utc)
    jti = str(_uuid.uuid4())
    ttl = int(expires_delta.total_seconds())

    claims = {
        **payload,
        "iat": now,
        "exp": now + expires_delta,
        "jti": jti,
        "token_type": token_type,
        "kid": _key_id,
    }

    encoded = jwt.encode(
        claims,
        _private_key,  # type: ignore[arg-type]
        algorithm="RS256",
        headers={"kid": _key_id},
    )
    return encoded, jti, ttl


def verify_token(token: str) -> dict[str, Any]:
    """
    Verify an RS256 JWT. Tries the current public key first, then the old key
    (rotation window). Raises jwt.PyJWTError on any verification failure.

    Algorithm confusion protection: only RS256 is accepted. Passing an HS256
    token will raise DecodeError regardless of the payload.
    """
    if not rs256_available():
        raise RuntimeError("RS256 keys not loaded.")

    # Extract kid from header to select verification key
    unverified_header = jwt.get_unverified_header(token)
    token_kid = unverified_header.get("kid", "")

    verification_key: RSAPublicKey | None = None
    if token_kid == _key_id:
        verification_key = _public_key
    elif _old_public_key and token_kid == _old_key_id:
        verification_key = _old_public_key
        log.info("jwt_rs256.verified_with_old_key", kid=token_kid)
    else:
        # No kid match — try current key anyway (backward compat for tokens without kid)
        verification_key = _public_key

    return jwt.decode(
        token,
        verification_key,  # type: ignore[arg-type]
        algorithms=["RS256"],  # Explicitly reject HS256 / none
        options={"require": ["exp", "iat", "jti", "sub"]},
    )


# ── JWKS endpoint ────────────────────────────────────────────────────────────────

def _rsa_public_key_to_jwk(public_key: RSAPublicKey, kid: str) -> dict[str, str]:
    """
    Convert an RSA public key to a JWK (JSON Web Key) dict.
    Uses the standard Base64url-encoded n (modulus) and e (exponent) representation.
    """
    pub_numbers = public_key.public_key().public_numbers() if hasattr(public_key, 'public_key') else public_key.public_numbers()  # type: ignore

    def _int_to_base64url(n: int) -> str:
        byte_length = math.ceil(n.bit_length() / 8)
        return base64.urlsafe_b64encode(
            n.to_bytes(byte_length, byteorder="big")
        ).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _int_to_base64url(pub_numbers.n),
        "e": _int_to_base64url(pub_numbers.e),
    }


jwks_router = APIRouter(tags=["Security / JWKS"])


@jwks_router.get(
    "/.well-known/jwks.json",
    summary="JSON Web Key Set — RS256 public keys for token verification",
    include_in_schema=True,
    response_description="JWKS document containing active and rotation-window public keys",
)
async def jwks_endpoint() -> dict:
    """
    Serve the JSON Web Key Set (JWKS) for RS256 JWT verification.

    Downstream services (API gateways, microservices, frontend auth middleware)
    should fetch and cache this endpoint to verify access tokens without
    calling back to the auth service.

    Caching guidance:
      - Cache-Control: max-age=3600 (1 hour)
      - Re-fetch on jwt.InvalidKeyError or kid mismatch
      - During key rotation: both old and new keys appear for 24 hours
    """
    if not rs256_available():
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(
            status_code=503,
            detail="RS256 keys not loaded. Set RSA_PRIVATE_KEY_PEM to enable.",
        )

    keys = [_rsa_public_key_to_jwk(_public_key, _key_id)]  # type: ignore
    if _old_public_key:
        keys.append(_rsa_public_key_to_jwk(_old_public_key, _old_key_id))

    return {"keys": keys}


# ── Dev key generation utility ────────────────────────────────────────────────────

def generate_dev_keypair() -> tuple[str, str]:
    """
    Generate a fresh 4096-bit RSA key pair for local development.
    Returns (private_pem, public_pem) as strings.

    Usage:
        from src.auth.jwt_rs256 import generate_dev_keypair
        priv, pub = generate_dev_keypair()
        print(priv)   # Set as RSA_PRIVATE_KEY_PEM
        print(pub)    # Set as RSA_PUBLIC_KEY_PEM (optional)

    DO NOT use dev-generated keys in production.
    Production keys must be generated, stored, and rotated via a secrets manager.
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


if __name__ == "__main__":
    # python -m src.auth.jwt_rs256  → prints a dev key pair
    priv, pub = generate_dev_keypair()
    print("# ── RSA_PRIVATE_KEY_PEM (keep secret) ──")
    print(priv)
    print("# ── RSA_PUBLIC_KEY_PEM (safe to distribute) ──")
    print(pub)
