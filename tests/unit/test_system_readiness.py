"""Production readiness must fail closed without contacting external services."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import JSONResponse

from app.api.routers import system
from app.domain.system import ReadinessResponse


async def _readiness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment: str,
    token_safe: bool,
    database_ok: bool,
    redis_ok: bool,
) -> JSONResponse | ReadinessResponse:
    monkeypatch.setattr(
        system,
        "settings",
        SimpleNamespace(
            environment=environment,
            app_version="test-version",
            readiness_require_database=False,
            readiness_require_redis=False,
            api_token_is_safe=token_safe,
            api_token_value="private-test-token-must-not-appear",
            auto_trade=False,
            trading_mode="analysis_only",
        ),
    )
    database_check = AsyncMock(return_value=(database_ok, "database checked"))
    redis_check = AsyncMock(return_value=(redis_ok, "redis checked"))
    monkeypatch.setattr(system, "check_database", database_check)
    monkeypatch.setattr(system, "check_redis", redis_check)

    response = await system.readiness()
    database_check.assert_awaited_once_with()
    redis_check.assert_awaited_once_with()
    return response


@pytest.mark.asyncio
async def test_production_readiness_rejects_missing_or_unsafe_api_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await _readiness(
        monkeypatch,
        environment="production",
        token_safe=False,
        database_ok=True,
        redis_ok=True,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert json.loads(response.body)["blockers"] == ["api_token_not_safely_configured"]
    assert b"private-test-token-must-not-appear" not in response.body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_ok", "redis_ok", "expected_blockers"),
    [
        (False, True, ["database_unavailable"]),
        (True, False, ["redis_unavailable"]),
        (False, False, ["database_unavailable", "redis_unavailable"]),
    ],
)
async def test_production_readiness_requires_dependencies_even_when_toggles_off(
    monkeypatch: pytest.MonkeyPatch,
    database_ok: bool,
    redis_ok: bool,
    expected_blockers: list[str],
) -> None:
    response = await _readiness(
        monkeypatch,
        environment="production",
        token_safe=True,
        database_ok=database_ok,
        redis_ok=redis_ok,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert json.loads(response.body)["blockers"] == expected_blockers


@pytest.mark.asyncio
async def test_production_readiness_accepts_safe_token_and_healthy_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await _readiness(
        monkeypatch,
        environment="production",
        token_safe=True,
        database_ok=True,
        redis_ok=True,
    )

    assert isinstance(response, ReadinessResponse)
    assert response.status == "ready"
    assert response.blockers == []


@pytest.mark.asyncio
async def test_nonproduction_readiness_preserves_optional_dependencies_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await _readiness(
        monkeypatch,
        environment="test",
        token_safe=False,
        database_ok=False,
        redis_ok=False,
    )

    assert isinstance(response, ReadinessResponse)
    assert response.status == "ready"
    assert response.blockers == []
