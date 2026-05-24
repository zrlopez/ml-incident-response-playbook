"""
src/auth/password.py — Argon2 password hashing (ARCH-02 remediation)
======================================================================
Phase 2 remediation: replaces passlib[bcrypt] with argon2-cffi.

Findings addressed:
  ARCH-02  passlib 1.7.4 unmaintained (last release 2020); no security fixes.
           bcrypt is adequate but argon2id is the 2023 OWASP Password Storage
           Cheat Sheet recommendation for new systems.
           Argon2id is memory-hard, resistant to GPU/ASIC brute-force.

Migration strategy (zero-downtime rehash-on-login):
  1. Deploy this module alongside the existing passlib bcrypt store.
  2. On every successful login, call maybe_rehash(stored_hash, plaintext).
     If the stored hash is a bcrypt hash, it will be re-hashed with argon2id
     and the new hash written back to the user record.
  3. After all active users have logged in at least once (monitor via
     audit log: hash_algorithm field), remove the bcrypt fallback.
  4. At that point, set ALLOW_BCRYPT_FALLBACK=false in the env and redeploy.

Parameters (OWASP 2024 minimums for argon2id):
  memory_cost = 65536 KiB  (64 MB) — minimum for interactive logins
  time_cost   = 3 iterations
  parallelism = 4 threads

  These are deliberately conservative. Increase memory_cost to 128 MB for
  high-value accounts (admin) if hardware budget permits.

Thread safety:
  PasswordHasher instances are stateless after construction and are safe
  to share across async worker threads without locking.
"""
from __future__ import annotations

import os

import structlog
from argon2 import PasswordHasher, exceptions as argon2_exc
from argon2.low_level import Type as Argon2Type

try:
    # passlib bcrypt fallback — used during rehash-on-login migration only.
    # Import is guarded so this module works cleanly after passlib is removed.
    from passlib.context import CryptContext as _PasslibCryptContext
    _PASSLIB_CTX = _PasslibCryptContext(schemes=["bcrypt"], deprecated="auto")
    _PASSLIB_AVAILABLE = True
except ImportError:
    _PASSLIB_CTX = None  # type: ignore[assignment]
    _PASSLIB_AVAILABLE = False

log = structlog.get_logger(__name__)

# Controls whether bcrypt hashes from the old passlib store are accepted
# during the migration window. Set to "false" once all users are rehashed.
_ALLOW_BCRYPT_FALLBACK: bool = (
    os.getenv("ALLOW_BCRYPT_FALLBACK", "true").lower() not in ("false", "0", "no")
)

# OWASP 2024 argon2id parameters (interactive login profile)
_HASHER = PasswordHasher(
    memory_cost=65536,   # 64 MB in KiB
    time_cost=3,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Argon2Type.ID,  # argon2id — hybrid of argon2i and argon2d
)


def hash_password(plaintext: str) -> str:
    """
    Hash a plaintext password with argon2id.

    Returns an encoded string in the standard argon2 PHC format:
      $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>

    Never store the plaintext — discard it immediately after calling this.
    """
    return _HASHER.hash(plaintext)


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """
    Verify a plaintext password against a stored hash.

    Supports both argon2id (new) and bcrypt (migration fallback).
    Returns True on match, False on mismatch. Never raises on bad input.

    The caller should check needs_rehash() after a successful verify and
    re-hash + persist if True (see maybe_rehash below).
    """
    # Primary path: argon2id hash
    if stored_hash.startswith("$argon2"):
        try:
            _HASHER.verify(stored_hash, plaintext)
            return True
        except argon2_exc.VerifyMismatchError:
            return False
        except argon2_exc.VerificationError as exc:
            log.warning("password.argon2_verification_error", error=str(exc))
            return False
        except argon2_exc.InvalidHashError:
            log.error("password.invalid_argon2_hash")
            return False

    # Fallback path: bcrypt hash from passlib migration
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        if not _ALLOW_BCRYPT_FALLBACK:
            log.error(
                "password.bcrypt_fallback_disabled",
                hint="Set ALLOW_BCRYPT_FALLBACK=true or complete the argon2 migration.",
            )
            return False
        if not _PASSLIB_AVAILABLE:
            log.error(
                "password.passlib_not_available",
                hint="Install passlib[bcrypt] or complete the argon2 migration.",
            )
            return False
        try:
            return bool(_PASSLIB_CTX.verify(plaintext, stored_hash))
        except Exception as exc:
            log.warning("password.bcrypt_verification_error", error=str(exc))
            return False

    log.error("password.unrecognized_hash_format", prefix=stored_hash[:10])
    return False


def needs_rehash(stored_hash: str) -> bool:
    """
    Return True if the stored hash should be upgraded.

    Triggers upgrade when:
      - The hash uses bcrypt (old passlib format) and ALLOW_BCRYPT_FALLBACK=true
      - The hash uses argon2 but with outdated parameters (lower memory/time cost)
        as detected by argon2-cffi's built-in check_needs_rehash.
    """
    if stored_hash.startswith("$argon2"):
        return _HASHER.check_needs_rehash(stored_hash)
    # bcrypt hashes always need rehash to argon2id
    if stored_hash.startswith(("$2b$", "$2a$")):
        return True
    return False


def maybe_rehash(stored_hash: str, plaintext: str) -> str | None:
    """
    ARCH-02 MIGRATION: If the stored hash needs upgrading, return a new argon2id hash.
    Returns None if no rehash is needed.

    Usage in login handler:
        if verify_password(plaintext, user.hashed_password):
            new_hash = maybe_rehash(user.hashed_password, plaintext)
            if new_hash:
                await user_repo.update_password_hash(user.username, new_hash)
                log.info("auth.password_rehashed", username=user.username)

    The caller is responsible for persisting the returned hash.
    This function never persists anything itself.
    """
    if needs_rehash(stored_hash):
        log.info(
            "password.rehash_triggered",
            old_scheme="bcrypt" if stored_hash.startswith("$2") else "argon2_old_params",
        )
        return hash_password(plaintext)
    return None
