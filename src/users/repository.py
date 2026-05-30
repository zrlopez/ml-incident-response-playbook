"""
=======================================================================
with a proper async SQLAlchemy repository backed by PostgreSQL (or SQLite
for local/test use via DATABASE_URL).

Findings addressed:
           - passwords reset on every restart (dev UX problem)
           - cannot support horizontal scaling (multiple uvicorn workers)
           - no audit trail for user mutations
           - ARCH-02 rehash-on-login requires a writable persistence layer

Architecture:
  - UserRecord: SQLAlchemy ORM model, shares the same DeclarativeBase as
    Incident so Alembic owns schema changes for both.
  - AbstractUserRepository: Protocol/ABC contract for dependency injection;
    lets tests inject InMemoryUserRepository without a live DB.
  - PostgresUserRepository: production implementation; all writes are
    committed transactionally and audit-logged via structlog.
  - InMemoryUserRepository: test-only drop-in. Loaded only when
    ENVIRONMENT=test or ENVIRONMENT=development.

Security properties:
  - Passwords NEVER stored in plaintext. Only argon2id hashes are written.
  - Hash column is named `hashed_password`; never returned in API responses.
  - update_password_hash() is the only mutation on the password column.
    Used exclusively by the rehash-on-login path (ARCH-02 migration).
  - get_by_username() returns None on miss (timing-safe: no 404 vs 401
    distinction is exposed to the caller).
  - All writes are audit-logged with username (no hash) for traceability.

Migration to production:
  1. Generate Alembic migration:
       alembic revision --autogenerate -m "add users table"
       alembic upgrade head
  2. Seed initial users via the management CLI (scripts/seed_users.py).
  3. Remove _USERS dict and _require_dev_password() from api/app.py.
  4. Inject PostgresUserRepository via lifespan context.

Refer to REMEDIATION_LOG.md § ARCH-03 for status.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Optional, cast

import structlog
from sqlalchemy import Boolean, DateTime, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database import Base  # shared DeclarativeBase — Alembic sees both
from src.auth.password import verify_password, maybe_rehash
from src.config import get_settings

log = structlog.get_logger(__name__)

# ── ORM model ──────────────────────────────────────────────────────────────────────────────
class UserRecord(Base):
    """
    Persisted user record.

    Schema changes MUST be managed via Alembic migrations.
    Do not add, rename, or drop columns without a migration file.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # hashed_password stores argon2id hash (or bcrypt during migration window).
    # NEVER expose this field in API responses.
    hashed_password: Mapped[str] = mapped_column(String(1024), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="analyst")
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # hash_algorithm tracks migration progress: 'argon2id' | 'bcrypt'
    hash_algorithm: Mapped[str] = mapped_column(String(20), nullable=False, default="argon2id")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def from_dict(cls, username: str, data: dict[str, object]) -> "UserRecord":
        """
        Construct a transient (un-persisted) UserRecord from a plain dict.

        Used by InMemoryUserRepository to build in-memory records that satisfy
        the AbstractUserRepository interface without a database session.

        SQLAlchemy's instrumented __init__ does NOT require a session — it only
        needs one when the object is added to a session. Using cls(...) directly
        is therefore correct and avoids the _sa_instance_state bug that
        __new__ caused (instrumentation was never attached on attribute writes).
        """
        return cls(
            id=str(uuid.uuid4()),
            username=username,
            hashed_password=str(data["hashed_password"]),
            role=str(data["role"]),
            disabled=bool(data.get("disabled", False)),
            hash_algorithm="argon2id",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    def to_dict(self) -> dict:
        """Safe representation — hashed_password intentionally excluded."""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "disabled": self.disabled,
            "hash_algorithm": self.hash_algorithm,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

# ── Repository contract ─────────────────────────────────────────────────────────────────────
class AbstractUserRepository(ABC):
    """Dependency-injection contract for user persistence."""

    @abstractmethod
    async def get_by_username(self, username: str) -> Optional[UserRecord]:
        """Return UserRecord or None (never raises on miss)."""
        ...

    @abstractmethod
    async def update_password_hash(
        self, username: str, new_hash: str, algorithm: str = "argon2id"
    ) -> None:
        """Persist a rehashed password (ARCH-02 migration). Audit-logged."""
        ...

    @abstractmethod
    async def authenticate(
        self, username: str, plaintext_password: str
    ) -> Optional[UserRecord]:
        """
        Verify credentials and return UserRecord on success, None on failure.
        Triggers rehash-on-login (ARCH-02) if the stored hash needs upgrading.
        """
        ...

    @abstractmethod
    async def disable_user(self, username: str) -> bool:
        """
        Soft-delete a user account by setting disabled=True.

        Used by GDPR Art. 17 erasure endpoint. Soft delete preserves the
        account row for audit-trail integrity (GDPR Art. 5(1)(e)) while
        preventing further authentication.

        Returns True if the user was found and disabled, False if not found.
        Hard deletion after the 30-day retention period is handled by a
        separate background job (see docs/dpo_runbook.md).
        """
        ...

# ── PostgreSQL implementation ────────────────────────────────────────────────────────────────
class PostgresUserRepository(AbstractUserRepository):
    """
    Production user store backed by PostgreSQL (or SQLite for dev/test).

    Inject via FastAPI lifespan:

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            settings = get_settings()
            engine = create_async_engine(settings.database_url)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            app.state.user_repo = PostgresUserRepository(session_factory)
            yield
            await engine.dispose()
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_by_username(self, username: str) -> Optional[UserRecord]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserRecord).where(UserRecord.username == username)
            )
            return cast(Optional[UserRecord], result.scalar_one_or_none())

    async def update_password_hash(
        self, username: str, new_hash: str, algorithm: str = "argon2id"
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(UserRecord)
                    .where(UserRecord.username == username)
                    .values(
                        hashed_password=new_hash,
                        hash_algorithm=algorithm,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
        log.info("user.password_hash_updated", username=username, algorithm=algorithm)

    async def authenticate(
        self, username: str, plaintext_password: str
    ) -> Optional[UserRecord]:
        user = await self.get_by_username(username)
        if user is None or user.disabled:
            return None

        if not verify_password(plaintext_password, user.hashed_password):
            log.warning("auth.verify_failed", username=username)
            return None

        # rehash-on-login migration
        new_hash = maybe_rehash(user.hashed_password, plaintext_password)
        if new_hash:
            await self.update_password_hash(username, new_hash, algorithm="argon2id")
            user.hashed_password = new_hash
            user.hash_algorithm = "argon2id"

        log.info("auth.login_success", username=username, role=user.role)
        return user

    async def disable_user(self, username: str) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(UserRecord)
                    .where(UserRecord.username == username)
                    .values(disabled=True, updated_at=datetime.now(timezone.utc))
                )
                found = result.fetchone() is not None
        if found:
            log.info("user.disabled", username=username)
        else:
            log.warning("user.disable_not_found", username=username)
        return found

# ── In-memory implementation (test / development only) ──────────────────────────────────────
class InMemoryUserRepository(AbstractUserRepository):
    """
    Thread-safe (asyncio-safe) in-memory user store.

    Loaded when ENVIRONMENT=test or ENVIRONMENT=development.
    Seeded from the USERS dict in config (argon2id hashes only).

    NOT for production use: data is lost on process restart and there is
    no horizontal-scaling support.
    """

    def __init__(self, users: dict[str, dict[str, object]]) -> None:
        # username → UserRecord
        self._store: dict[str, UserRecord] = {
            username: UserRecord.from_dict(username, data)
            for username, data in users.items()
        }

    async def get_by_username(self, username: str) -> Optional[UserRecord]:
        return self._store.get(username)

    async def update_password_hash(
        self, username: str, new_hash: str, algorithm: str = "argon2id"
    ) -> None:
        user = self._store.get(username)
        if user is None:
            log.warning("user.update_hash_not_found", username=username)
            return
        user.hashed_password = new_hash
        user.hash_algorithm = algorithm
        user.updated_at = datetime.now(timezone.utc)
        log.info("user.password_hash_updated", username=username, algorithm=algorithm)

    async def authenticate(
        self, username: str, plaintext_password: str
    ) -> Optional[UserRecord]:
        user = await self.get_by_username(username)
        if user is None or user.disabled:
            return None

        if not verify_password(plaintext_password, user.hashed_password):
            log.warning("auth.verify_failed", username=username)
            return None

        # rehash-on-login migration
        new_hash = maybe_rehash(user.hashed_password, plaintext_password)
        if new_hash:
            await self.update_password_hash(username, new_hash, algorithm="argon2id")

        log.info("auth.login_success", username=username, role=user.role)
        return user

    async def disable_user(self, username: str) -> bool:
        user = self._store.get(username)
        if user is None:
            log.warning("user.disable_not_found", username=username)
            return False
        user.disabled = True
        user.updated_at = datetime.now(timezone.utc)
        log.info("user.disabled", username=username)
        return True


# ── Factory ──────────────────────────────────────────────────────────────────────────────────
def get_user_repository() -> AbstractUserRepository:
    """
    FastAPI dependency: returns the appropriate repository implementation.

    Development / test: InMemoryUserRepository seeded from settings.USERS.
    Production:         PostgresUserRepository backed by settings.DATABASE_URL.

    Inject via Depends(get_user_repository) in route handlers.
    """
    settings = get_settings()
    if settings.environment in ("test", "development"):
        return InMemoryUserRepository(settings.users)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return PostgresUserRepository(session_factory)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency yielding a single AsyncSession per request.

    Used by route handlers that need direct session access (e.g. bulk queries).
    For most use-cases prefer get_user_repository() which manages sessions
    internally.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
