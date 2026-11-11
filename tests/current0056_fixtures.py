"""Read back preserved account catalogs on the independently measured DB0056."""

from pathlib import Path

from app.domain import account_history_original_frame_v1 as frame
from scripts import collector_current_0054_toolchain as current54
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import historical0052_on_current0054_toolchain as historical52

ROOT = Path(__file__).resolve().parents[1]


async def read_current0056_preserved_parent(connection):
    scope = scopes.actual_current_source_scope(ROOT)
    assert scope.actual_source_head == scope.database_target == "0056"

    def source_check(sync_connection):
        return scopes.verify_database_scope(sync_connection, ROOT, scope)

    observed_scope = await connection.run_sync(source_check)
    first = await historical52._actual_historical0052_state(connection)
    assert first["observed_head"] == "0056"
    parent = current54._parent_readback(first)
    original_frame = frame.verify_owner_installation_catalog(
        first["original_frame_catalog"], first["partition_catalog"]
    )
    assert original_frame["revision"] == "0052"
    assert await historical52._actual_historical0052_state(connection) == first
    assert await connection.run_sync(source_check) == observed_scope
    return {
        **parent,
        "schema": "ctcc.source0056_current0056.preserved_parent_readback.v1",
        "expected": "0056",
        "observed": first["observed_head"],
        "original_history_frame_owner_installation": original_frame,
        "source_scope": observed_scope,
        "account_complete": False,
        "execution_authority": False,
    }
