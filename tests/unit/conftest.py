"""tests/unit/conftest.py — Unit-tier pytest fixtures.

Restores the module-level engine and session_factory in src.incident_tracker
after test_api.py's module-scoped client fixture swaps them for an in-memory
SQLite engine. Without this, test files that run after test_api.py (e.g.
test_incident_tracker.py) inherit the SQLite engine and fail with async loop
or schema errors against the wrong backend.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="module")
def restore_incident_tracker_engine():
    """Save and restore src.incident_tracker engine state around each module."""
    import src.incident_tracker as _mod
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _orig_engine = _mod._engine
    _orig_factory = _mod._session_factory
    yield
    # Only restore if they were swapped out (i.e. engine URL changed)
    if _mod._engine is not _orig_engine:
        _mod._engine = _orig_engine
        _mod._session_factory = _orig_factory
