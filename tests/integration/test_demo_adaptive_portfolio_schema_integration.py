from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings


@pytest.mark.integration
@pytest.mark.asyncio
async def test_demo_adaptive_portfolio_columns_are_durable_jsonb() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)

    try:
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_columns(
                    "demo_automation_state"
                )
            )
        by_name = {column["name"]: column for column in columns}

        assert {
            "active_trades",
            "symbol_cooldowns",
            "equity_basis",
            "realized_pnl_events",
            "risk_peak_equity",
        } <= set(by_name)
        assert by_name["active_trades"]["nullable"] is False
        assert by_name["symbol_cooldowns"]["nullable"] is False
        assert by_name["active_trades"]["type"].__class__.__name__ == "JSONB"
        assert by_name["symbol_cooldowns"]["type"].__class__.__name__ == "JSONB"
        assert by_name["equity_basis"]["nullable"] is True
        assert by_name["realized_pnl_events"]["nullable"] is False
        assert by_name["realized_pnl_events"]["type"].__class__.__name__ == "JSONB"
        assert by_name["risk_peak_equity"]["nullable"] is True
        assert {
            "api_key",
            "api_secret",
            "passphrase",
            "secret",
            "token",
        }.isdisjoint(by_name)
    finally:
        await engine.dispose()
