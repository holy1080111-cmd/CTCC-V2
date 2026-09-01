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
            intent_checks = await connection.run_sync(
                lambda sync_connection: inspect(
                    sync_connection
                ).get_check_constraints("okx_live_execution_intents")
            )
            intent_uniques = await connection.run_sync(
                lambda sync_connection: inspect(
                    sync_connection
                ).get_unique_constraints("okx_live_execution_intents")
            )
            checkpoint_columns = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_columns(
                    "okx_live_sync_checkpoints"
                )
            )
            checkpoint_checks = await connection.run_sync(
                lambda sync_connection: inspect(
                    sync_connection
                ).get_check_constraints("okx_live_sync_checkpoints")
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
            "operator_reconciled_at",
            "operator_resolution_code",
            "protection_client_order_id",
            "expected_protection_size",
            "expected_stop_loss",
            "expected_take_profit",
            "expected_trigger_price_type",
        } <= intent_column_names
        intent_check_names = {item["name"] for item in intent_checks}
        assert {
            "ck_okx_live_execution_intents_operator_resolution_pair",
            "ck_okx_live_execution_intents_operator_resolution_allowed",
            "ck_okx_live_execution_intents_protection_expectation_complete",
        } <= intent_check_names
        assert any(
            item.get("column_names") == ["protection_client_order_id"]
            for item in intent_uniques
        )
        checkpoint_column_names = {
            column["name"] for column in checkpoint_columns
        }
        assert {
            "safety_latched",
            "safety_latch_code",
            "safety_latch_version",
            "safety_latched_at",
        } <= checkpoint_column_names
        checkpoint_check_names = {item["name"] for item in checkpoint_checks}
        assert {
            "ck_okx_live_sync_checkpoints_safety_latch_pair",
            "ck_okx_live_sync_checkpoints_safety_latch_version_nonnegative",
            "ck_okx_live_sync_checkpoints_safety_latch_code_safe",
        } <= checkpoint_check_names
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
