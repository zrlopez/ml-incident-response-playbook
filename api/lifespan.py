"""
api/lifespan.py
===============
FastAPI application lifespan — startup and shutdown wiring.

R-GOD Step 6: Extracted from api/app.py.  Owns:
  - DB connectivity check + Alembic migration state verification
  - PostgresUserRepository / InMemoryUserRepository wiring to app.state
  - RedisDenylist initialisation and app.state attachment
  - RS256KeyStore loading and JWKS router registration
  - OpenTelemetry bootstrap
  - Graceful shutdown (denylist close, OTel shutdown)

R-C04 NOTE: _build_engine() is called inside this context manager (via init_db()),
not at module import time.  Once app.py imports lifespan from here instead of
defining it inline, test fixtures that import token helpers or dependencies will
no longer trigger engine construction at collection time.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI

from api.config import ENVIRONMENT, REDIS_URL
from api.redis_denylist import RedisDenylist
from api.dependencies import _denylist as _dep_denylist  # noqa: F401 — re-exported for mutation
import api.dependencies as _deps
from src.users.repository import PostgresUserRepository
from src.auth import jwt_rs256
from src.auth.key_store import KeyRotationStore
from observability.otel_setup import configure_otel, shutdown_otel

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("api.startup", environment=ENVIRONMENT, algorithm=os.getenv("JWT_ALGORITHM", "HS256"))

    # CR-1: Verify DB connectivity + Alembic migration state
    try:
        from src.incident_tracker import init_db
        await init_db()
    except Exception as _db_exc:
        log.error("api.startup.db_check_failed", error=str(_db_exc))
        raise

    # ARCH-03: Wire PostgresUserRepository when DATABASE_URL is a real Postgres URL
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("postgresql"):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        _pg_engine = create_async_engine(database_url, pool_pre_ping=True)
        _pg_session_factory = async_sessionmaker(_pg_engine, expire_on_commit=False)
        _deps._user_repo = PostgresUserRepository(session_factory=_pg_session_factory)
        app.state.user_repo = _deps._user_repo
        log.info("user_repo.postgres_wired", environment=ENVIRONMENT)
    else:
        from src.users.repository import InMemoryUserRepository
        from api.stub_users import _USERS
        _deps._user_repo = InMemoryUserRepository(users=_USERS)
        app.state.user_repo = _deps._user_repo
        log.warning(
            "user_repo.in_memory_fallback",
            hint="Set DATABASE_URL=postgresql+asyncpg://... to use PostgresUserRepository",
        )

    # Initialise Redis-backed JWT denylist (R-03)
    _deps._denylist = RedisDenylist(redis_url=REDIS_URL)
    await _deps._denylist.connect()
    app.state.redis = _deps._denylist._client
    log.info("denylist.connected", redis_url=REDIS_URL)

    # API-KEY-01: Load RS256KeyStore and attach to app.state
    _rs256_active = jwt_rs256.load_keys()
    if _rs256_active:
        try:
            app.state.key_store = KeyRotationStore.from_env()
            log.info(
                "jwt.key_store_loaded",
                key_id=app.state.key_store.key_id,
                pool_size=len(app.state.key_store.all_keys),
            )
        except Exception as _ks_exc:
            log.warning("jwt.key_store_load_failed", error=str(_ks_exc))
            app.state.key_store = None
        app.include_router(jwt_rs256.jwks_router)
        log.info("jwt.rs256_active", key_id=jwt_rs256._key_id)
    else:
        app.state.key_store = None
        log.warning(
            "jwt.hs256_fallback_active",
            hint="Set RSA_PRIVATE_KEY_PEM to upgrade to RS256 (ARCH-01)",
        )

    # Bootstrap OpenTelemetry tracing (R-20)
    configure_otel(
        service_name=os.getenv("OTEL_SERVICE_NAME", "ml-incident-api"),
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        environment=ENVIRONMENT,
    )
    log.info("otel.configured")

    yield

    # ── Shutdown ────────────────────────────────────────────────────────────
    if _deps._denylist is not None:
        await _deps._denylist.close()
    shutdown_otel()
    log.info("api.shutdown")
