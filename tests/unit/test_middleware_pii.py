"""Regression tests for HIGH-01 privacy protections in middleware + rate limiting.

Locks the following contracts permanently:
  - api.middleware._pseudo_ip(): deterministic, never returns raw IP, 8-hex output
  - api.config._rate_limit_key(): hashes request.client.host; falls back to
    X-Forwarded-For first hop; api/config.py no longer imports get_remote_address

Coverage target: api/config.py (_rate_limit_key) + api/middleware.py (_pseudo_ip)
Cycle: 2 | IDs: R-P11, R-P21
"""
from __future__ import annotations

import hashlib
from pathlib import Path


# ── _pseudo_ip contract (api/middleware.py) ───────────────────────────────────

def test_pseudo_ip_is_deterministic() -> None:
    """Same IP always produces same pseudonym — rate-limit consistency preserved."""
    from api.middleware import _pseudo_ip

    assert _pseudo_ip("1.2.3.4") == _pseudo_ip("1.2.3.4")
    assert _pseudo_ip("10.0.0.1") == _pseudo_ip("10.0.0.1")


def test_pseudo_ip_is_not_raw_ip() -> None:
    """Output must never equal the input IP — HIGH-01 core contract."""
    from api.middleware import _pseudo_ip

    raw_ip = "10.0.0.1"
    result = _pseudo_ip(raw_ip)
    assert result != raw_ip
    assert len(result) == 8  # 8-hex-char prefix


# ── _rate_limit_key contract (api/config.py) ─────────────────────────────────

def test_rate_limit_key_hashes_client_ip() -> None:
    """When request.client is set, key is SHA-256(ip)[:16] — not the raw IP."""
    from fastapi import Request
    from api.config import _rate_limit_key

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/healthz",
        "headers": [],
        "client": ("203.0.113.10", 5150),
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)

    expected = hashlib.sha256("203.0.113.10".encode()).hexdigest()[:16]
    assert _rate_limit_key(request) == expected
    assert _rate_limit_key(request) != "203.0.113.10"


def test_rate_limit_key_falls_back_to_x_forwarded_for() -> None:
    """When client is None, first X-Forwarded-For hop is used and hashed."""
    from fastapi import Request
    from api.config import _rate_limit_key

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/healthz",
        "headers": [(b"x-forwarded-for", b"198.51.100.20, 10.0.0.9")],
        "client": None,
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)

    expected = hashlib.sha256("198.51.100.20".encode()).hexdigest()[:16]
    assert _rate_limit_key(request) == expected


def test_config_module_no_longer_uses_get_remote_address() -> None:
    """Static guard: get_remote_address must not exist in api/config.py source."""
    contents = Path("api/config.py").read_text()
    assert "get_remote_address" not in contents, (
        "get_remote_address found in api/config.py — HIGH-01 regression"
    )
    assert "Limiter(key_func=_rate_limit_key" in contents, (
        "_rate_limit_key not wired into Limiter — R-P11 fix missing"
    )
