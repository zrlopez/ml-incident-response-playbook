"""
tests/unit/test_incident_tracker_facade.py
===========================================
Unit tests for the src/incident_tracker.py re-export facade.

Covers the init_db() code paths not exercised by the characterization suite:
  - PostgreSQL branch: alembic_version present (lines 83-115)
  - PostgreSQL branch: alembic_version table empty / missing
  - PostgreSQL branch: DB unreachable raises RuntimeError

All tests use mocks — no real DB or engine is required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.incident_tracker import init_db


def _make_pg_engine(version_row=None, version_exc=None, connect_exc=None):
    """Build a mock _engine that looks like PostgreSQL."""
    mock_engine = MagicMock()
    mock_engine.url = MagicMock()
    mock_engine.url.__str__ = lambda _: "postgresql+asyncpg://user:pass@db:5432/inc"
    mock_engine.url.startswith = lambda prefix: not str(mock_engine.url).startswith("sqlite")

    # First connect() call: SELECT 1 health check
    conn1 = AsyncMock()
    conn1.execute = AsyncMock(return_value=MagicMock())

    if connect_exc:
        # Both connects raise
        cm_fail = AsyncMock()
        cm_fail.__aenter__ = AsyncMock(side_effect=connect_exc)
        cm_fail.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect.return_value = cm_fail
        return mock_engine

    # Second connect() call: alembic_version query
    conn2 = AsyncMock()
    if version_exc:
        conn2.execute = AsyncMock(side_effect=version_exc)
    else:
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, i: "abc1234" if version_row else None
        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row if version_row else None
        conn2.execute = AsyncMock(return_value=mock_result)

    call_count = {"n": 0}

    class _CM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *_):
            return False

    def _connect_side_effect():
        call_count["n"] += 1
        return _CM(conn1 if call_count["n"] == 1 else conn2)

    mock_engine.connect.side_effect = _connect_side_effect
    return mock_engine


@pytest.mark.asyncio
async def test_init_db_pg_alembic_version_present() -> None:
    """PostgreSQL path: alembic_version row found — logs info, no error."""
    engine = _make_pg_engine(version_row=True)
    with patch("src.incident_tracker._engine", engine):
        await init_db()  # must not raise


@pytest.mark.asyncio
async def test_init_db_pg_alembic_version_empty() -> None:
    """PostgreSQL path: alembic_version table empty — logs warning, no error."""
    engine = _make_pg_engine(version_row=False)
    with patch("src.incident_tracker._engine", engine):
        await init_db()  # must not raise


@pytest.mark.asyncio
async def test_init_db_pg_alembic_query_fails() -> None:
    """PostgreSQL path: alembic query raises — logs warning, no error."""
    engine = _make_pg_engine(version_exc=RuntimeError("table does not exist"))
    with patch("src.incident_tracker._engine", engine):
        await init_db()  # must not raise


@pytest.mark.asyncio
async def test_init_db_db_unreachable_raises() -> None:
    """DB unreachable at startup — RuntimeError is raised."""
    engine = _make_pg_engine(connect_exc=OSError("connection refused"))
    with patch("src.incident_tracker._engine", engine):
        with pytest.raises(RuntimeError, match="unreachable"):
            await init_db()
