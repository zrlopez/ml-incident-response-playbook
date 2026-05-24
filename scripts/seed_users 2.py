#!/usr/bin/env python3
"""
scripts/seed_users.py — Database user seeding for ARCH-03 migration
====================================================================
Creates the initial set of application users in the DB with argon2id
hashed passwords. Run ONCE after `alembic upgrade head` on a fresh environment.

Never run against a database that already has users — use --force to override.
Passwords are read from environment variables; never passed as CLI arguments.

Usage:
    # Dry run (validate config, print what would be seeded):
    python scripts/seed_users.py --dry-run

    # Seed with env-based passwords:
    DEV_ADMIN_PASSWORD=<strong> DEV_ANALYST_PASSWORD=<strong> \\
    DEV_OPERATOR_PASSWORD=<strong> DATABASE_URL=postgresql+asyncpg://... \\
    python scripts/seed_users.py

    # Force re-seed (overwrites existing users):
    python scripts/seed_users.py --force

Environment variables required:
    DATABASE_URL        Connection string for the target database
    DEV_ADMIN_PASSWORD  Admin user password (min 16 chars)
    DEV_ANALYST_PASSWORD
    DEV_OPERATOR_PASSWORD
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add project root to path so imports work without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.auth.password import hash_password
from src.users.repository import UserRecord
from src.incident_tracker import Base

log = structlog.get_logger("seed_users")

_MIN_PASSWORD_LEN = 16

_SEED_USERS = [
    {"username": "admin",    "env_var": "DEV_ADMIN_PASSWORD",    "role": "admin"},
    {"username": "analyst",  "env_var": "DEV_ANALYST_PASSWORD",  "role": "analyst"},
    {"username": "operator", "env_var": "DEV_OPERATOR_PASSWORD", "role": "operator"},
]


def _get_password(env_var: str) -> str:
    value = os.environ.get(env_var, "").strip()
    if not value:
        log.error("seed.missing_env_var", env_var=env_var)
        sys.exit(1)
    if len(value) < _MIN_PASSWORD_LEN:
        log.error(
            "seed.password_too_short",
            env_var=env_var,
            length=len(value),
            minimum=_MIN_PASSWORD_LEN,
        )
        sys.exit(1)
    return value


async def seed(database_url: str, force: bool = False, dry_run: bool = False) -> None:
    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    users_to_create = []
    for user_def in _SEED_USERS:
        plaintext = _get_password(user_def["env_var"])
        users_to_create.append(
            {
                "username": user_def["username"],
                "role": user_def["role"],
                "hashed_password": hash_password(plaintext),
                "hash_algorithm": "argon2id",
            }
        )
        log.info(
            "seed.prepared",
            username=user_def["username"],
            role=user_def["role"],
            dry_run=dry_run,
        )

    if dry_run:
        log.info("seed.dry_run_complete", count=len(users_to_create))
        await engine.dispose()
        return

    async with session_factory() as session:
        async with session.begin():
            # Check for existing users
            from sqlalchemy import select
            existing = (
                await session.execute(select(UserRecord.username))
            ).scalars().all()

            if existing and not force:
                log.error(
                    "seed.users_already_exist",
                    existing=list(existing),
                    hint="Use --force to overwrite existing users.",
                )
                await engine.dispose()
                sys.exit(1)

            if existing and force:
                await session.execute(text("DELETE FROM users"))
                log.warning("seed.wiped_existing_users", count=len(existing))

            for user_data in users_to_create:
                session.add(UserRecord(
                    username=user_data["username"],
                    hashed_password=user_data["hashed_password"],
                    role=user_data["role"],
                    hash_algorithm=user_data["hash_algorithm"],
                    disabled=False,
                ))

    log.info("seed.complete", count=len(users_to_create))
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed initial users into the database.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and print actions without writing to DB.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete all existing users and re-seed. Use with caution.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./incidents.db")
    log.info("seed.starting", database_url=database_url.split("@")[-1], dry_run=args.dry_run)

    asyncio.run(seed(database_url=database_url, force=args.force, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
