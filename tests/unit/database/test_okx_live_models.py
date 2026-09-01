import app.database.models  # noqa: F401 - registers all ORM tables
from app.database.base import Base
from app.database.models.okx_live import (
    OkxLiveAccountConfigState,
    OkxLiveAlgoOrderState,
    OkxLiveExecutionIntent,
    OkxLiveOrderState,
    OkxLivePositionState,
    OkxLiveSyncCheckpoint,
)


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


def test_live_mirror_tables_are_registered_separately_from_demo() -> None:
    registered_tables = set(Base.metadata.tables)

    assert LIVE_TABLES <= registered_tables
    assert DEMO_TABLES <= registered_tables
    assert LIVE_TABLES.isdisjoint(DEMO_TABLES)


def test_live_position_uses_exchange_position_id_as_primary_key() -> None:
    table = OkxLivePositionState.__table__

    assert [column.name for column in table.primary_key.columns] == ["position_id"]
    assert "position_key" not in table.c


def test_live_client_order_identifiers_are_indexed_but_not_unique() -> None:
    order_table = OkxLiveOrderState.__table__
    algo_table = OkxLiveAlgoOrderState.__table__

    assert order_table.c.client_order_id.unique is not True
    assert algo_table.c.client_algo_order_id.unique is not True
    assert any(
        [column.name for column in index.columns] == ["client_order_id"]
        and index.unique is not True
        for index in order_table.indexes
    )
    assert any(
        [column.name for column in index.columns] == ["client_algo_order_id"]
        and index.unique is not True
        for index in algo_table.indexes
    )


def test_live_account_state_excludes_credentials_and_actual_ip() -> None:
    columns = set(OkxLiveAccountConfigState.__table__.c.keys())

    assert {
        "uid_fingerprint",
        "main_uid_fingerprint",
        "permissions",
        "unknown_permissions",
        "read_permission",
        "trade_permission",
        "withdraw_permission",
        "ip_bound",
    } <= columns
    assert {
        "uid",
        "main_uid",
        "ip",
        "api_key",
        "api_secret",
        "passphrase",
        "raw",
    }.isdisjoint(columns)


def test_live_execution_intent_stores_only_hashes_ids_and_safe_codes() -> None:
    columns = set(OkxLiveExecutionIntent.__table__.c.keys())

    assert {
        "idempotency_key",
        "request_hash",
        "action",
        "status",
        "instrument_id",
        "client_order_id",
        "exchange_order_id",
        "protection_client_order_id",
        "expected_protection_size",
        "expected_stop_loss",
        "expected_take_profit",
        "expected_trigger_price_type",
        "detail_codes",
        "operator_reconciled_at",
        "operator_resolution_code",
    } <= columns
    assert {
        "request",
        "payload",
        "response",
        "api_key",
        "secret",
        "passphrase",
        "uid",
    }.isdisjoint(columns)


def test_live_safety_latch_schema_is_versioned_and_constrained() -> None:
    table = OkxLiveSyncCheckpoint.__table__
    columns = set(table.c.keys())
    constraint_names = {item.name for item in table.constraints}

    assert {
        "safety_latched",
        "safety_latch_code",
        "safety_latch_version",
        "safety_latched_at",
    } <= columns
    assert {
        "ck_okx_live_sync_checkpoints_safety_latch_pair",
        "ck_okx_live_sync_checkpoints_safety_latch_version_nonnegative",
        "ck_okx_live_sync_checkpoints_safety_latch_code_safe",
    } <= constraint_names
