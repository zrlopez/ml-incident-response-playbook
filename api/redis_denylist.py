"""
api/redis_denylist.py — Redis-backed JWT denylist
==================================================
Replaces the process-local set[str] denylist so revocations survive
process restarts and are shared across horizontally-scaled API pods.

Design:
  - SETEX per-JTI key: key  = "jwt:denied:{jti}"
                        TTL  = remaining token lifetime (seconds)
  - TTL ensures the key is automatically garbage-collected when the
    token would have expired anyway — no background cleanup job needed.
  - Uses aioredis (redis-py >= 4.2 asyncio driver) for compatibility
    with FastAPI’s async event loop.

Production considerations:
  - Run Redis with AUTH + TLS (REDIS_URL = rediss://user:pass@host:6380/0)
  - Enable Redis persistence (AOF) if strict no-gap guarantees required.
  - Consider Redis Sentinel or Cluster for HA.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REDIS_AVAILABLE = False

_KEY_PREFIX = "jwt:denied:"


class RedisDenylist:
    """Async Redis-backed JWT ID denylist with TTL garbage collection."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._url = redis_url
        self._client: "aioredis.Redis | None" = None

    async def connect(self) -> None:
        """Create the async Redis connection. Called in FastAPI lifespan startup."""
        if not _REDIS_AVAILABLE:  # pragma: no cover
            raise RuntimeError(
                "redis package not installed.  Add 'redis[asyncio]' to requirements."
            )
        self._client = aioredis.from_url(
            self._url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await self.ping()  # Fail fast if Redis unreachable at startup
        log.info("redis_denylist.connected", extra={"url": self._url})

    async def close(self) -> None:
        """Gracefully close the Redis connection. Called in lifespan shutdown."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> bool:
        """Return True if Redis is reachable; raise otherwise."""
        if self._client is None:
            raise RuntimeError("Not connected — call connect() first")
        return await self._client.ping()  # raises ConnectionError if unreachable

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        """
        Synchronously queue a SETEX command.

        Note: This is intentionally sync so callers don’t need to be
        async (e.g. from a sync test harness).  Internally we use
        execute_command which is safe from an async context too, because
        redis-py’s aioredis client is thread-safe for single commands.

        For pure async callers, use ``await revoke_async()`` instead.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        loop.create_task(self.revoke_async(jti, ttl_seconds))

    async def revoke_async(self, jti: str, ttl_seconds: int) -> None:
        """Async version — awaitable for use inside async route handlers."""
        if self._client is None:
            raise RuntimeError("Not connected")
        key = f"{_KEY_PREFIX}{jti}"
        await self._client.setex(key, ttl_seconds, "1")
        log.debug("jwt.revoked", extra={"jti": jti, "ttl": ttl_seconds})

    def is_revoked(self, jti: str) -> bool:
        """
        Synchronous check — performs a blocking EXISTS call.
        Suitable for use inside FastAPI dependency injection which can
        call sync functions from an async context via run_in_threadpool.

        For pure async contexts prefer ``await is_revoked_async()``.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.is_revoked_async(jti))

    async def is_revoked_async(self, jti: str) -> bool:
        """Return True if the JTI is present in the denylist."""
        if self._client is None:
            return False
        key = f"{_KEY_PREFIX}{jti}"
        result = await self._client.exists(key)
        return bool(result)
