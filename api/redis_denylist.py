"""
api/redis_denylist.py — Redis-backed JWT denylist
==================================================
Remediation: CRIT-B (Phase 0)

Root cause fixed: The original implementation used asyncio.get_event_loop()
and loop.run_until_complete() inside an already-running async event loop
(FastAPI's). This causes RuntimeError in Python 3.10+ and silently breaks
token revocation — a security-critical path.

Fix strategy:
  - ALL methods are now natively async. Sync wrappers completely removed.
  - `revoke()` is now `await revoke()` — callers MUST await it. This
    guarantees the SETEX is confirmed written before the response returns.
  - `is_revoked()` is now `await is_revoked()` — no loop gymnastics.
  - Fail-CLOSED semantics preserved: if Redis is unreachable, raise
    DenylistUnavailableError and the caller returns HTTP 503.
  - Structured logging via structlog for observability continuity.
  - Connection health tracked via _connected flag for clean error messages.

Production requirements:
  - REDIS_URL must use AUTH + TLS: rediss://:password@host:6380/0
  - Enable Redis AOF persistence for revocation durability across restarts.
  - Use Redis Sentinel or Cluster for HA in production.

Remediations applied:
  CRIT-B  async/sync boundary fully removed — all callers must use await
  HIGH-A  documented AUTH+TLS requirement; enforced via settings validation
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REDIS_AVAILABLE = False

_KEY_PREFIX = "jwt:denied:"


class DenylistUnavailableError(RuntimeError):
    """
    Raised when the denylist cannot be consulted.
    Callers must treat this as a security failure and return HTTP 503
    (fail-closed) rather than allowing the request through.
    """


class RedisDenylist:
    """
    Async-native Redis-backed JWT ID denylist with TTL garbage collection.

    All public methods are coroutines. Call them with ``await``.

    Lifecycle (FastAPI lifespan):
        startup:  await denylist.connect()
        shutdown: await denylist.close()

    Usage in route handlers:
        await denylist.revoke(jti, ttl_seconds)      # logout
        revoked = await denylist.is_revoked(jti)     # auth check
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        if not redis_url:
            raise ValueError("redis_url must not be empty")
        self._url = redis_url
        self._client: "aioredis.Redis | None" = None
        self._connected: bool = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Establish the async Redis connection pool.
        Called once during FastAPI lifespan startup.
        Raises RuntimeError if redis package is missing.
        Raises ConnectionError if Redis is unreachable (fail-fast).
        """
        if not _REDIS_AVAILABLE:  # pragma: no cover
            raise RuntimeError(
                "redis package not installed. "
                "Add 'redis[hiredis]>=5.0' to requirements.txt."
            )
        self._client = aioredis.from_url(
            self._url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        await self.ping()  # Fail fast at startup rather than at first request
        self._connected = True
        log.info(
            "redis_denylist.connected",
            url=self._url.split("@")[-1],  # strip credentials from log
        )

    async def close(self) -> None:
        """
        Gracefully close the connection pool.
        Called during FastAPI lifespan shutdown.
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._connected = False
            log.info("redis_denylist.disconnected")

    # ── Health ────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """
        Return True if Redis responds to PING.
        Raises ConnectionError if unreachable.
        Used by /ready health probe.
        """
        if self._client is None:
            raise DenylistUnavailableError(
                "Denylist client not initialised — call connect() first."
            )
        return bool(await self._client.ping())

    # ── Core operations ───────────────────────────────────────────────────────

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        """
        Add a JTI to the denylist with a TTL matching the token's remaining
        lifetime. The key is automatically garbage-collected by Redis when
        the TTL expires — no background cleanup job required.

        SECURITY: This method is awaited to CONFIRM the write before the
        calling route handler returns its HTTP 200 logout response.
        Fire-and-forget revocation (the old pattern) created a race window
        where the revoked token remained usable if Redis was briefly slow.

        Args:
            jti:         JWT ID claim value from the token being revoked.
            ttl_seconds: Remaining lifetime of the token in seconds (>= 1).

        Raises:
            DenylistUnavailableError: If Redis is not connected or write fails.
            ValueError: If ttl_seconds < 1.
        """
        if ttl_seconds < 1:
            raise ValueError(f"ttl_seconds must be >= 1, got {ttl_seconds}")
        if self._client is None:
            raise DenylistUnavailableError(
                "Cannot revoke token — Redis denylist not connected."
            )
        try:
            key = f"{_KEY_PREFIX}{jti}"
            await self._client.setex(key, ttl_seconds, "1")
            log.info(
                "jwt.revoked",
                log_type="audit",
                jti=jti,
                ttl_seconds=ttl_seconds,
            )
        except Exception as exc:
            log.error(
                "redis_denylist.revoke_failed",
                jti=jti,
                error=str(exc),
            )
            raise DenylistUnavailableError(
                f"Failed to write revocation for jti={jti}: {exc}"
            ) from exc

    async def is_revoked(self, jti: str) -> bool:
        """
        Return True if the JTI is present in the denylist (i.e., token
        has been explicitly revoked and has not yet expired).

        SECURITY: Fail-CLOSED. If Redis is unavailable, raise
        DenylistUnavailableError rather than returning False (which
        would allow revoked tokens through). The caller must respond
        with HTTP 503 so the client retries.

        Args:
            jti: JWT ID claim value to check.

        Returns:
            True if revoked, False if not found in denylist.

        Raises:
            DenylistUnavailableError: If Redis is not connected or check fails.
        """
        if self._client is None:
            raise DenylistUnavailableError(
                "Cannot check revocation — Redis denylist not connected."
            )
        try:
            key = f"{_KEY_PREFIX}{jti}"
            result = await self._client.exists(key)
            return bool(result)
        except Exception as exc:
            log.error(
                "redis_denylist.check_failed",
                jti=jti,
                error=str(exc),
            )
            raise DenylistUnavailableError(
                f"Denylist check failed for jti={jti}: {exc}"
            ) from exc

    # ── Diagnostics ───────────────────────────────────────────────────────────

    async def revocation_count(self) -> int:
        """
        Return the number of currently active revocations (for /metrics).
        Best-effort — returns -1 on error rather than raising.
        """
        if self._client is None:
            return -1
        try:
            # SCAN-based count to avoid blocking with KEYS *
            count = 0
            async for _ in self._client.scan_iter(
                match=f"{_KEY_PREFIX}*", count=100
            ):
                count += 1
            return count
        except Exception as exc:
            log.warning("redis_denylist.count_failed", error=str(exc))
            return -1
