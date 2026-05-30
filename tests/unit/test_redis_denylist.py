"""
tests/unit/test_redis_denylist.py
=================================
Unit tests for api/redis_denylist.py — targets the 47 uncovered lines.

Covered:
  - RedisDenylist.__init__: empty url raises ValueError
  - connect: sets _connected, calls ping, logs url without credentials
  - close: clears _client and _connected; no-op if already closed
  - ping: returns True on redis response; raises DenylistUnavailableError if _client is None
  - revoke: writes SETEX; raises on ttl<1; raises DenylistUnavailableError if not connected;
            wraps redis errors in DenylistUnavailableError
  - is_revoked: returns True/False; raises if not connected; wraps redis errors
  - revocation_count: iterates scan_iter; returns -1 if no client; returns -1 on error
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.redis_denylist import RedisDenylist, DenylistUnavailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_denylist(url: str = "redis://localhost:6379/0") -> RedisDenylist:
    return RedisDenylist(redis_url=url)


def _attached_mock_client(dl: RedisDenylist) -> MagicMock:
    """Attach a mock async redis client and mark as connected."""
    client = MagicMock()
    client.ping = AsyncMock(return_value=True)
    client.setex = AsyncMock(return_value=True)
    client.exists = AsyncMock(return_value=1)
    client.aclose = AsyncMock()

    async def _scan(*args, **kwargs):
        yield "jwt:denied:tok1"
        yield "jwt:denied:tok2"

    client.scan_iter = _scan
    dl._client = client
    dl._connected = True
    return client


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="redis_url must not be empty"):
            RedisDenylist(redis_url="")

    def test_stores_url(self):
        dl = _make_denylist("redis://host:6379/1")
        assert dl._url == "redis://host:6379/1"
        assert dl._connected is False
        assert dl._client is None


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------

class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_sets_connected_flag(self):
        dl = _make_denylist()
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        with patch("api.redis_denylist.aioredis.from_url", return_value=mock_client):
            await dl.connect()
        assert dl._connected is True
        assert dl._client is mock_client

    @pytest.mark.asyncio
    async def test_connect_strips_credentials_from_log(self, caplog):
        dl = _make_denylist("redis://:secret@host:6379/0")
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        with patch("api.redis_denylist.aioredis.from_url", return_value=mock_client):
            await dl.connect()
        # url logged should be post-@ only
        assert dl._connected is True

    @pytest.mark.asyncio
    async def test_connect_propagates_ping_failure(self):
        dl = _make_denylist()
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(side_effect=ConnectionError("refused"))
        with patch("api.redis_denylist.aioredis.from_url", return_value=mock_client):
            with pytest.raises(DenylistUnavailableError):
                await dl.connect()


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

class TestClose:
    @pytest.mark.asyncio
    async def test_close_clears_client(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)
        await dl.close()
        client.aclose.assert_awaited_once()
        assert dl._client is None
        assert dl._connected is False

    @pytest.mark.asyncio
    async def test_close_noop_when_not_connected(self):
        dl = _make_denylist()
        # Should not raise
        await dl.close()
        assert dl._client is None


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------

class TestPing:
    @pytest.mark.asyncio
    async def test_ping_returns_true(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)
        client.ping = AsyncMock(return_value=True)
        result = await dl.ping()
        assert result is True

    @pytest.mark.asyncio
    async def test_ping_raises_if_no_client(self):
        dl = _make_denylist()
        with pytest.raises(DenylistUnavailableError, match="not initialised"):
            await dl.ping()


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------

class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoke_calls_setex(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)
        await dl.revoke("jti-abc", 300)
        client.setex.assert_awaited_once_with("jwt:denied:jti-abc", 300, "1")

    @pytest.mark.asyncio
    async def test_revoke_raises_on_bad_ttl(self):
        dl = _make_denylist()
        _attached_mock_client(dl)
        with pytest.raises(ValueError, match="ttl_seconds must be >= 1"):
            await dl.revoke("jti-x", 0)

    @pytest.mark.asyncio
    async def test_revoke_raises_if_not_connected(self):
        dl = _make_denylist()
        with pytest.raises(DenylistUnavailableError, match="not connected"):
            await dl.revoke("jti-x", 60)

    @pytest.mark.asyncio
    async def test_revoke_wraps_redis_error(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)
        client.setex = AsyncMock(side_effect=Exception("boom"))
        with pytest.raises(DenylistUnavailableError, match="Failed to write revocation"):
            await dl.revoke("jti-x", 60)


# ---------------------------------------------------------------------------
# is_revoked
# ---------------------------------------------------------------------------

class TestIsRevoked:
    @pytest.mark.asyncio
    async def test_is_revoked_true(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)
        client.exists = AsyncMock(return_value=1)
        assert await dl.is_revoked("jti-abc") is True

    @pytest.mark.asyncio
    async def test_is_revoked_false(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)
        client.exists = AsyncMock(return_value=0)
        assert await dl.is_revoked("jti-abc") is False

    @pytest.mark.asyncio
    async def test_is_revoked_raises_if_not_connected(self):
        dl = _make_denylist()
        with pytest.raises(DenylistUnavailableError, match="not connected"):
            await dl.is_revoked("jti-x")

    @pytest.mark.asyncio
    async def test_is_revoked_wraps_redis_error(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)
        client.exists = AsyncMock(side_effect=Exception("conn reset"))
        with pytest.raises(DenylistUnavailableError, match="Denylist check failed"):
            await dl.is_revoked("jti-x")


# ---------------------------------------------------------------------------
# revocation_count
# ---------------------------------------------------------------------------

class TestRevocationCount:
    @pytest.mark.asyncio
    async def test_count_iterates_scan(self):
        dl = _make_denylist()
        _attached_mock_client(dl)
        count = await dl.revocation_count()
        assert count == 2

    @pytest.mark.asyncio
    async def test_count_returns_minus_one_when_no_client(self):
        dl = _make_denylist()
        assert await dl.revocation_count() == -1

    @pytest.mark.asyncio
    async def test_count_returns_minus_one_on_error(self):
        dl = _make_denylist()
        client = _attached_mock_client(dl)

        async def _bad_scan(*args, **kwargs):
            raise Exception("scan failed")
            yield  # make it a generator

        client.scan_iter = _bad_scan
        result = await dl.revocation_count()
        assert result == -1
