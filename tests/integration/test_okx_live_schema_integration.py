from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings


LIVE_TABLES = {
    "okx_live_account_config_state",
    "okx_live_balance_state",
    "okx_live_order_state",
    "okx_live_position_state",
    "okx_live_algo_order_state",
    "okx_live_sync_checkpoints",
    "okx_live_execution_intents",
}
DEMO_TABLES = {
    "okx_demo_balance_state",
    "okx_demo_order_state",
    "okx_demo_position_state",
    "okx_demo_algo_order_state",
    "okx_demo_sync_checkpoints",
}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_okx_live_mirror_schema_is_isolated_and_fail_closed() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)

    try:
        async with engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            position_pk = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_pk_constraint(
                    "okx_live_position_state"
                )
            )
            account_columns = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_columns(
                    "okx_live_account_config_state"
                )
            )
            order_uniques = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                    "okx_live_order_state"
                )
            )
            algo_uniques = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                    "okx_live_algo_order_state"
                )
            )
            order_indexes = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_indexes(
                    "okx_live_order_state"
                )
            )
            algo_indexes = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_indexes(
                    "okx_live_algo_order_state"
                )
            )
            intent_columns = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_columns(
                    "okx_live_execution_intents"
                )
            )

        assert LIVE_TABLES <= table_names
        assert DEMO_TABLES <= table_names
        assert LIVE_TABLES.isdisjoint(DEMO_TABLES)
        assert position_pk["constrained_columns"] == ["position_id"]

        account_column_names = {column["name"] for column in account_columns}
        assert "ip_bound" in account_column_names
        assert {"ip", "api_key", "api_secret", "passphrase", "raw"}.isdisjoint(
            account_column_names
        )

        assert not any(
            constraint.get("column_names") == ["client_order_id"]
            for constraint in order_uniques
        )
        assert not any(
            constraint.get("column_names") == ["client_algo_order_id"]
            for constraint in algo_uniques
        )
        assert any(
            index.get("column_names") == ["client_order_id"] and not index.get("unique", False)
            for index in order_indexes
        )
        assert any(
            index.get("column_names") == ["client_algo_order_id"]
            and not index.get("unique", False)
            for index in algo_indexes
        )
        intent_column_names = {column["name"] for column in intent_columns}
        assert {
            "idempotency_key",
            "request_hash",
            "action",
            "status",
            "detail_codes",
        } <= intent_column_names
        assert {
            "request",
            "payload",
            "response",
            "api_key",
            "secret",
            "passphrase",
            "uid",
        }.isdisjoint(intent_column_names)
    finally:
        await engine.dispose()
