"""
tests/unit/test_gdpr_routes_unit.py
=====================================
Unit tests for api/gdpr_routes.py — targets the 37 uncovered lines.

Covered:
  - _pseudo_id: returns 16-char hex, deterministic, different for different usernames
  - export_my_ 200 JSON response with correct structure and Content-Disposition header
  - export_my_ unknown username (sub missing) falls back to "unknown"
  - delete_my_account: 200 soft-delete response with correct fields
  - delete_my_account: 404 when user_repo.disable_user returns False
  - delete_my_account: token revocation called when jti+exp present
  - delete_my_account: token revocation failure is swallowed (no crash)
  - delete_my_account: no denylist on request (revocation skipped gracefully)
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.responses import JSONResponse

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_gdpr.db")
os.environ.setdefault("JWT_SECRET_KEY", "ci-unit-test-secret-32chars-safe!!")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    username: str = "alice",
    role: str = "user",
    jti: str = "jti-123",
    exp: int = 9999999999,
    has_denylist: bool = True,
    denylist_revoke_raises: bool = False,
    disable_user_result: bool = True,
) -> MagicMock:
    req = MagicMock()
    req.client.host = "127.0.0.1"

    # user_repo stub
    user_repo = MagicMock()
    user_repo.disable_user = AsyncMock(return_value=disable_user_result)
    req.app.state.user_repo = user_repo

    # denylist stub
    if has_denylist:
        denylist = MagicMock()
        if denylist_revoke_raises:
            denylist.revoke = AsyncMock(side_effect=Exception("redis gone"))
        else:
            denylist.revoke = AsyncMock()
        req.app.state.denylist = denylist
    else:
        req.app.state.denylist = None

    return req, {
        "sub": username,
        "role": role,
        "jti": jti,
        "exp": exp,
        "username": username,
        "disabled": False,
    }


# ---------------------------------------------------------------------------
# _pseudo_id
# ---------------------------------------------------------------------------

class TestPseudoId:
    def test_returns_16_char_hex(self):
        from api.gdpr_routes import _pseudo_id
        result = _pseudo_id("alice")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        from api.gdpr_routes import _pseudo_id
        assert _pseudo_id("alice") == _pseudo_id("alice")

    def test_different_users_different_ids(self):
        from api.gdpr_routes import _pseudo_id
        assert _pseudo_id("alice") != _pseudo_id("bob")


# ---------------------------------------------------------------------------
# export_my_data
# ---------------------------------------------------------------------------

class TestExportMyData:
    @pytest.mark.asyncio
    async def test_returns_200_with_correct_structure(self):
        from api.gdpr_routes import export_my_data
        req, current_user = _make_request()
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=req.app.state.denylist):
            response = await export_my_data(request=req, current_user=current_user)
        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        import json
        body = json.loads(response.body)
        assert body["gdpr_request"] == "Article 15 — Right of Access"
        assert "account" in body
        assert body["account"]["username"] == "alice"
        assert body["account"]["role"] == "user"

    @pytest.mark.asyncio
    async def test_content_disposition_header_set(self):
        from api.gdpr_routes import export_my_data
        req, current_user = _make_request()
        response = await export_my_data(request=req, current_user=current_user)
        assert "attachment" in response.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_cache_control_no_store(self):
        from api.gdpr_routes import export_my_data
        req, current_user = _make_request()
        response = await export_my_data(request=req, current_user=current_user)
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.asyncio
    async def test_missing_sub_falls_back_to_unknown(self):
        from api.gdpr_routes import export_my_data
        req, _ = _make_request()
        current_user = {"role": "user"}  # no "sub"
        response = await export_my_data(request=req, current_user=current_user)
        import json
        body = json.loads(response.body)
        assert body["account"]["username"] == "unknown"

    @pytest.mark.asyncio
    async def test_no_client_ip_handled(self):
        from api.gdpr_routes import export_my_data
        req, current_user = _make_request()
        req.client = None  # simulate missing client
        response = await export_my_data(request=req, current_user=current_user)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# delete_my_account
# ---------------------------------------------------------------------------

class TestDeleteMyAccount:
    @pytest.mark.asyncio
    async def test_200_soft_delete_response(self):
        from api.gdpr_routes import delete_my_account
        req, current_user = _make_request()
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=req.app.state.denylist):
            result = await delete_my_account(request=req, current_user=current_user)
        assert result["status"] == "erasure_completed"
        assert result["username"] == "alice"
        assert result["retention_period_days"] == 30

    @pytest.mark.asyncio
    async def test_404_when_user_not_found(self):
        from api.gdpr_routes import delete_my_account
        req, current_user = _make_request(disable_user_result=False)
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=req.app.state.denylist):
            with pytest.raises(HTTPException) as exc_info:
                await delete_my_account(request=req, current_user=current_user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_token_revoked_on_delete(self):
        from api.gdpr_routes import delete_my_account
        req, current_user = _make_request()
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=req.app.state.denylist):
            await delete_my_account(request=req, current_user=current_user)
        req.app.state.denylist.revoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_failure_swallowed(self):
        from api.gdpr_routes import delete_my_account
        req, current_user = _make_request(denylist_revoke_raises=True)
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=req.app.state.denylist):
            # Should NOT raise even though revoke fails
            result = await delete_my_account(request=req, current_user=current_user)
        assert result["status"] == "erasure_completed"

    @pytest.mark.asyncio
    async def test_no_denylist_skips_revocation(self):
        from api.gdpr_routes import delete_my_account
        req, current_user = _make_request(has_denylist=False)
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=None):
            result = await delete_my_account(request=req, current_user=current_user)
        assert result["status"] == "erasure_completed"

    @pytest.mark.asyncio
    async def test_no_client_ip_handled(self):
        from api.gdpr_routes import delete_my_account
        req, current_user = _make_request()
        req.client = None
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=req.app.state.denylist):
            result = await delete_my_account(request=req, current_user=current_user)
        assert result["status"] == "erasure_completed"

    @pytest.mark.asyncio
    async def test_no_jti_skips_revocation(self):
        from api.gdpr_routes import delete_my_account
        req, current_user = _make_request()
        current_user.pop("jti", None)
        current_user.pop("exp", None)
        with patch("api.dependencies.get_user_repo", return_value=req.app.state.user_repo), \
             patch("api.dependencies.get_denylist", return_value=req.app.state.denylist):
            result = await delete_my_account(request=req, current_user=current_user)
        req.app.state.denylist.revoke.assert_not_awaited()
        assert result["status"] == "erasure_completed"
