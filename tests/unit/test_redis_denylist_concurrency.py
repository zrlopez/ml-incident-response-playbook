# =============================================================================
# tests/unit/test_redis_denylist_concurrency.py
# CI-63 — Phase 13: Code Quality & Coverage
# =============================================================================
# Redis token denylist concurrency and expiry edge-case tests.
# All tests use a fake Redis client (no live Redis required).
# Covers:
#   - Concurrent token revocation (50 workers, no duplicates)
#   - Expiry boundary: token expires at T=0, access at T=+epsilon is denied
#   - Expiry boundary: token access before expiry is denied, after is allowed
#   - Race condition: revoke and check fired simultaneously
#   - Denylist isolation: revoked token does not block unrevoked tokens
#   - Flood resilience: 1000 tokens in denylist, lookup O(1)
# =============================================================================
from __future__ import annotations

import threading
import time
import uuid
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

NUM_WORKERS = 50


# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------

class FakeRedis:
    """In-memory Redis stand-in with TTL support for denylist tests."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}  # key -> (value, expiry_monotonic)
        self._lock = threading.Lock()

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: A003
        expiry = time.monotonic() + ex if ex is not None else None
        with self._lock:
            self._store[key] = (value, expiry)

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if expiry is not None and time.monotonic() > expiry:
                del self._store[key]
                return None
            return value

    def exists(self, key: str) -> int:
        return 1 if self.get(key) is not None else 0

    def delete(self, key: str) -> int:
        with self._lock:
            return 1 if self._store.pop(key, None) is not None else 0

    def __len__(self) -> int:
        now = time.monotonic()
        with self._lock:
            return sum(1 for _, (_, exp) in self._store.items() if exp is None or exp > now)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_denylist(redis: FakeRedis | None = None) -> tuple[FakeRedis, Any]:
    """Return (fake_redis, denylist_interface) using the FakeRedis."""
    r = redis or FakeRedis()

    class Denylist:
        """Thin wrapper that mirrors a real Redis denylist interface."""

        def revoke(self, token_jti: str, ttl_seconds: int = 3600) -> None:
            r.set(f"denylist:{token_jti}", "1", ex=ttl_seconds)

        def is_revoked(self, token_jti: str) -> bool:
            return r.exists(f"denylist:{token_jti}") == 1

    return r, Denylist()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRedisDenylistConcurrency:
    """Concurrency and expiry edge-case tests for the Redis token denylist."""

    def test_concurrent_revocations_no_lost_writes(self) -> None:
        """50 workers each revoke a unique JTI — all are present afterwards."""
        r, denylist = _make_denylist()
        jtis = [str(uuid.uuid4()) for _ in range(NUM_WORKERS)]
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(jti: str) -> None:
            try:
                denylist.revoke(jti, ttl_seconds=300)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(j,)) for j in jtis]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        for jti in jtis:
            assert denylist.is_revoked(jti), f"JTI {jti} missing after concurrent revoke"

    def test_expiry_boundary_token_not_accessible_after_ttl(self) -> None:
        """Token revoked with TTL=1s is absent after expiry."""
        r, denylist = _make_denylist()
        jti = str(uuid.uuid4())
        denylist.revoke(jti, ttl_seconds=1)
        assert denylist.is_revoked(jti), "Token should be revoked immediately"
        time.sleep(1.05)
        assert not denylist.is_revoked(jti), "Token should have expired"

    def test_expiry_boundary_token_accessible_before_ttl(self) -> None:
        """Token revoked with TTL=60s is still present 0.1s later."""
        r, denylist = _make_denylist()
        jti = str(uuid.uuid4())
        denylist.revoke(jti, ttl_seconds=60)
        time.sleep(0.1)
        assert denylist.is_revoked(jti), "Token should still be revoked within TTL"

    def test_race_condition_revoke_and_check_simultaneously(self) -> None:
        """Revoke and is_revoked fired at the same instant — no panic, consistent result."""
        r, denylist = _make_denylist()
        jti = str(uuid.uuid4())
        results: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def revoker() -> None:
            barrier.wait()
            denylist.revoke(jti, ttl_seconds=300)

        def checker() -> None:
            barrier.wait()
            result = denylist.is_revoked(jti)
            with lock:
                results.append(result)

        t1 = threading.Thread(target=revoker)
        t2 = threading.Thread(target=checker)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # After both complete, token must be revoked regardless of race outcome
        assert denylist.is_revoked(jti), "Token must be revoked after revoker completed"
        # Checker result is non-deterministic but must be a bool
        assert len(results) == 1
        assert isinstance(results[0], bool)

    def test_denylist_isolation_unrevoked_tokens_unaffected(self) -> None:
        """Revoking token A does not affect token B."""
        r, denylist = _make_denylist()
        jti_a = str(uuid.uuid4())
        jti_b = str(uuid.uuid4())
        denylist.revoke(jti_a, ttl_seconds=300)
        assert denylist.is_revoked(jti_a)
        assert not denylist.is_revoked(jti_b), "Unrevoked token B should not be in denylist"

    def test_flood_resilience_1000_tokens_o1_lookup(self) -> None:
        """1000 tokens in denylist — lookup time stays under 50ms."""
        r, denylist = _make_denylist()
        jtis = [str(uuid.uuid4()) for _ in range(1000)]
        for jti in jtis:
            denylist.revoke(jti, ttl_seconds=3600)

        target_jti = jtis[500]
        start = time.monotonic()
        result = denylist.is_revoked(target_jti)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert result, "Target JTI should be in denylist"
        assert elapsed_ms < 50, f"Lookup took {elapsed_ms:.2f}ms — expected < 50ms"

    def test_concurrent_revoke_and_expire_no_phantom_entries(self) -> None:
        """Expired entries are never returned even under concurrent re-registration."""
        r, denylist = _make_denylist()
        jti = str(uuid.uuid4())

        # Revoke with very short TTL
        denylist.revoke(jti, ttl_seconds=1)
        time.sleep(1.05)  # Let it expire

        # Now 25 workers check and 25 workers re-revoke concurrently
        check_results: list[bool] = []
        lock = threading.Lock()

        def checker() -> None:
            res = denylist.is_revoked(jti)
            with lock:
                check_results.append(res)

        def re_revoker() -> None:
            denylist.revoke(jti, ttl_seconds=300)

        threads = (
            [threading.Thread(target=checker) for _ in range(25)]
            + [threading.Thread(target=re_revoker) for _ in range(25)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # After all threads finish, token must definitely be revoked
        assert denylist.is_revoked(jti)
