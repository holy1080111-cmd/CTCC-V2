import app.database.models  # noqa: F401 - registers all ORM tables
from app.database.base import Base
from app.database.models.okx_live import (
    OkxLiveAccountConfigState,
    OkxLiveAlgoOrderState,
    OkxLiveOrderState,
    OkxLivePositionState,
)


LIVE_TABLES = {
    "okx_live_account_config_state",
    "okx_live_balance_state",
    "okx_live_order_state",
    "okx_live_position_state",
    "okx_live_algo_order_state",
    "okx_live_sync_checkpoints",
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
