from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.api.security as security


@pytest.mark.asyncio
async def test_valid_ctcc_token_is_accepted(monkeypatch) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(api_token_is_safe=True, api_token_value="x" * 32),
    )
    assert await security.require_ctcc_token(token="x" * 32) is None


@pytest.mark.asyncio
async def test_invalid_ctcc_token_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(api_token_is_safe=True, api_token_value="x" * 32),
    )
    with pytest.raises(HTTPException) as exc_info:
        await security.require_ctcc_token(token="wrong")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid_ctcc_api_token"


@pytest.mark.asyncio
async def test_unsafe_server_token_disables_protected_routes(monkeypatch) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(api_token_is_safe=False, api_token_value="short"),
    )
    with pytest.raises(HTTPException) as exc_info:
        await security.require_ctcc_token(token="short")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "api_token_not_safely_configured"
