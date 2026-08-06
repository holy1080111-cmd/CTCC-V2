from __future__ import annotations

import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config.settings import get_settings

_api_key_header = APIKeyHeader(name="X-CTCC-Token", auto_error=False)


async def require_ctcc_token(token: str | None = Security(_api_key_header)) -> None:
    settings = get_settings()
    if not settings.api_token_is_safe:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="api_token_not_safely_configured",
        )
    if token is None or not hmac.compare_digest(token, settings.api_token_value):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_ctcc_api_token",
            headers={"WWW-Authenticate": "APIKey"},
        )
