"""
api/stub_users.py
=================
Development / test in-memory user store.

R-GOD Step 2: Extracted from api/app.py.  Contains:
  - _STUB_ALLOWED_ENVIRONMENTS guard  (ARCH-07)
  - _require_dev_password()           (no fallback credentials)
  - _DEV_*_PW env reads
  - _USERS dict

Invariants:
  - Raises RuntimeError at import time if ENVIRONMENT is not development or test.
  - Raises RuntimeError at import time if any DEV_*_PASSWORD env var is absent
    or shorter than 12 characters.
  - Safe to import in tests; no FastAPI, no DB, no Redis dependencies.
  - Must be imported before app construction so the env guard fires early.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from src.auth.password import hash_password
from api.config import ENVIRONMENT

# ── ARCH-07: Explicit allowlist guard ────────────────────────────────────────
_STUB_ALLOWED_ENVIRONMENTS = {"development", "test"}
if ENVIRONMENT not in _STUB_ALLOWED_ENVIRONMENTS:
    raise RuntimeError(
        "\n"
        f"  FATAL: In-memory _USERS stub is not permitted in ENVIRONMENT='{ENVIRONMENT}'.\n"
        "  The stub is only safe in: development, test.\n"
        "\n"
        "  If this is a production/staging deployment:\n"
        "    Action: Wire PostgresUserRepository in api/user_repository.py\n"
        "    and set DATABASE_URL=postgresql+asyncpg://... before starting.\n"
        "\n"
        "  If this is a local environment incorrectly named:\n"
        "    Fix: Set ENVIRONMENT=development in your .env file.\n"
    )


def _require_dev_password(env_var: str) -> str:
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise RuntimeError(
            f"\n"
            f"  FATAL: {env_var} is not set.\n"
            f"  No fallback password is allowed — predictable dev credentials\n"
            f"  are a credential stuffing vector on misconfigured environments.\n"
            f"\n"
            f"  Fix: Add to your .env file:\n"
            f"    {env_var}=$(openssl rand -hex 16)\n"
        )
    if len(value) < 12:
        raise RuntimeError(
            f"{env_var} is too short (< 12 chars). "
            f"Generate a secure value: openssl rand -hex 16"
        )
    return value


_DEV_ADMIN_PW    = _require_dev_password("DEV_ADMIN_PASSWORD")    # noqa: E221
_DEV_ANALYST_PW  = _require_dev_password("DEV_ANALYST_PASSWORD")   # noqa: E221
_DEV_OPERATOR_PW = _require_dev_password("DEV_OPERATOR_PASSWORD")  # noqa: E221

_USERS: Dict[str, Dict[str, Any]] = {
    "admin": {
        "username": "admin",
        "hashed_password": hash_password(_DEV_ADMIN_PW),
        "role": "admin",
        "disabled": False,
    },
    "analyst": {
        "username": "analyst",
        "hashed_password": hash_password(_DEV_ANALYST_PW),
        "role": "analyst",
        "disabled": False,
    },
    "operator": {
        "username": "operator",
        "hashed_password": hash_password(_DEV_OPERATOR_PW),
        "role": "operator",
        "disabled": False,
    },
}
