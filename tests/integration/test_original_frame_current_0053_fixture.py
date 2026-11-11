"""Real current0053 fixture/owner observations; no borrowed native0052 admission."""

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.domain import account_revision_collector_lineage_schema_v1 as runtime
from app.domain import collector_current_0053_owner_installation_v1 as owner
from app.domain.account_revision_collector_lineage_v1 import (
    CollectorRevisionLineageError,
)
from scripts import collector_current_0053_toolchain as capture
from scripts import current_contract_source_scopes_v1 as source_scopes
from scripts import historical0052_0053_on_current0055_toolchain as source55
from scripts import historical0052_0053_on_current0056_toolchain as source56
from scripts import historical0052_on_current0054_toolchain as historical52
from scripts import historical0053_on_current0054_toolchain as historical53
from tests.integration.test_account_history_original_frame_repository_v1 import (
    original_frame_database as original_frame_database,  # noqa: PLC0414 -- explicit DB0052 fixture binding
)
from tests.integration.test_account_history_original_frame_repository_v1 import settings
from tests.original_frame_current_0053_fixtures import (
    ROOT,
)
from tests.original_frame_current_0053_fixtures import (
    original_frame_database_0053 as original_frame_database_0053,  # noqa: PLC0414 -- explicit pytest fixture binding
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _actual_owner(admin):
    async with admin.connect() as connection:
        await settings(connection)
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        return await connection.scalar(text(owner.CATALOG_SQL))


async def test_fresh_current0053_fixture_has_real_source_and_database_head(
    original_frame_database_0053, original_frame_database
):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    graph = ScriptDirectory.from_config(config)
    source_scope = source_scopes.historical0053_source_scope(ROOT)
    assert graph.get_current_head() == source_scope.actual_source_head
    assert source_scope.database_target == "0053"
    assert graph.get_revision("0053").down_revision == "0052"
    value = await _actual_owner(original_frame_database_0053)
    assert value["database_head"] == "0053"
    assert value["owner"] == value["actor"] == value["server"]["role_oid"]
    observed = owner.observe_owner_installation_catalog(value)
    assert observed["database_head"] == "0053"
    assert observed["native_baseline_admission"] is False
    assert observed["runtime_admission"] is False
    assert observed["account_complete"] is observed["execution_authority"] is False
    if source_scope.actual_source_head in {"0055", "0056"}:
        operation = {
            "0055": source55.read_historical0052_migration_on_current0055,
            "0056": source56.read_historical0052_migration_on_current0056,
        }[source_scope.actual_source_head]
        async with original_frame_database.connect() as connection:
            readback = await operation(connection)
        assert readback["source_head"] == source_scope.actual_source_head
        assert readback["database_head"] == "0052"
        assert readback["schema"] == (
            f"ctcc.source{source_scope.actual_source_head}_historical0052."
            "migration_readback.v1"
        )
        assert (
            readback["source_scope"]["source_head"] == source_scope.actual_source_head
        )
        assert readback["source_scope"]["database_head"] == "0052"
        legacy = readback["historical0052_readback"]
        assert legacy["expected"] == legacy["observed"] == "0052"
        assert legacy["original_history_frame_owner_installation"]["revision"] == "0052"
        assert readback["account_complete"] is readback["execution_authority"] is False
        async with original_frame_database.connect() as connection:
            with pytest.raises(historical52.Historical0052ScopeError):
                await historical52.read_historical0052_migration_on_current0054(
                    connection
                )
            if source_scope.actual_source_head == "0056":
                with pytest.raises(source55.Current0055ReadbackError):
                    await source55.read_historical0052_migration_on_current0055(
                        connection
                    )


async def test_current0053_observation_has_all_four_native_FK_action_check_pairs(
    original_frame_database_0053,
):
    value = await _actual_owner(original_frame_database_0053)
    owner.observe_owner_installation_catalog(value)
    expected = {name for name, key in runtime.KEYS.items() if key[0] == "f"}
    assert len(expected) == 4
    assert len(value["ri_triggers"]) == 16
    assert {row["constraint"] for row in value["ri_triggers"]} == expected
    assert len({row["trigger_oid"] for row in value["ri_triggers"]}) == 16
    assert value["custody_incoming"][0][0] == "fk_collector_current_custody"


async def test_current0053_capture_keeps_real0053_head_and_historical0052_component_distinct(
    original_frame_database_0053,
):
    source_scope = source_scopes.historical0053_source_scope(ROOT)
    async with original_frame_database_0053.connect() as connection:
        operation = {
            "0053": capture.capture_current0053_owner,
            "0054": historical53.capture_historical0053_owner_on_current0054,
            "0055": source55.capture_historical0053_owner_on_current0055,
            "0056": source56.capture_historical0053_owner_on_current0056,
        }[source_scope.actual_source_head]
        document, raw = await operation(connection)
    assert raw and document["source_head"] == source_scope.actual_source_head
    assert document["database_head"] == "0053"
    if source_scope.actual_source_head in {"0055", "0056"}:
        assert document["schema"] == (
            f"ctcc.source{source_scope.actual_source_head}_historical0053."
            "owner_observation.v1"
        )
        assert (
            document["source_scope"]["source_head"] == source_scope.actual_source_head
        )
        assert document["source_scope"]["database_head"] == "0053"
    assert document["historical0052_installation"]["revision"] == "0052"
    assert document["current0053_observation"]["database_head"] == "0053"
    assert (
        document["native_baseline_admission"] is document["runtime_admission"] is False
    )
    assert document["account_complete"] is document["execution_authority"] is False


async def test_owner_is_rejected_by_the_existing_runtime_actor_gate(
    original_frame_database_0053,
):
    async with original_frame_database_0053.connect() as connection:
        await settings(connection)
        value = await connection.scalar(text(runtime.CATALOG_SQL))
    assert value["owner"] == value["actor"]
    assert value["table_ok"] is False
    with pytest.raises(CollectorRevisionLineageError):
        runtime.verify_catalog(value)


async def test_formal_current0053_gate_uses_its_own_real_native_baseline(
    original_frame_database_0053,
):
    value = await _actual_owner(original_frame_database_0053)
    if owner.PG17_NATIVE_BASELINE_STATUS == "PENDING_GENUINE_NATIVE_PG17_CAPTURE":
        with pytest.raises(
            owner.Current0053OwnerError,
            match="genuine_native_baseline_pending_or_changed",
        ):
            owner.verify_owner_installation_catalog(value)
    else:
        admitted = owner.verify_owner_installation_catalog(value)
        assert admitted["native_baseline_admission"] is True
        assert admitted["database_head"] == "0053"
        assert admitted["account_complete"] is admitted["execution_authority"] is False


async def test_real_current0053_migration_readback_composes_separate_owner_contracts(
    original_frame_database_0053,
):
    source_scope = source_scopes.historical0053_source_scope(ROOT)
    operation = {
        "0053": capture.read_current0053_migration,
        "0054": historical53.read_historical0053_migration_on_current0054,
        "0055": source55.read_historical0053_migration_on_current0055,
        "0056": source56.read_historical0053_migration_on_current0056,
    }[source_scope.actual_source_head]
    async with original_frame_database_0053.connect() as connection:
        if owner.PG17_NATIVE_BASELINE_STATUS == "PENDING_GENUINE_NATIVE_PG17_CAPTURE":
            with pytest.raises(
                owner.Current0053OwnerError,
                match="genuine_native_baseline_pending_or_changed",
            ):
                await operation(connection)
        else:
            result = await operation(connection)
            if source_scope.actual_source_head in {"0054", "0055", "0056"}:
                assert (
                    result["source_head"] == source_scope.actual_source_head
                    and result["database_head"] == "0053"
                )
                if source_scope.actual_source_head in {"0055", "0056"}:
                    assert result["schema"] == (
                        f"ctcc.source{source_scope.actual_source_head}_historical0053."
                        "migration_readback.v1"
                    )
                result = result["historical0053_readback"]
            assert result["expected"] == result["observed"] == "0053"
            assert (
                result["original_history_frame_owner_installation"]["revision"]
                == "0052"
            )
            assert (
                result["collector_current_lineage_owner_installation"]["database_head"]
                == "0053"
            )
            assert (
                result["collector_current_lineage_owner_installation"][
                    "execution_authority"
                ]
                is False
            )


async def test_actual0053_missing_custody_FK_is_rejected_without_projecting_catalog(
    original_frame_database_0053,
):
    async with original_frame_database_0053.begin() as connection:
        await settings(connection)
        await connection.execute(
            text(
                "ALTER TABLE public.demo_collector_current_revision_lineages "
                "DROP CONSTRAINT fk_collector_current_custody"
            )
        )
        value = await connection.scalar(text(owner.CATALOG_SQL))
        with pytest.raises(owner.Current0053OwnerError):
            owner.observe_owner_installation_catalog(value)
        await connection.rollback()
    restored = await _actual_owner(original_frame_database_0053)
    owner.observe_owner_installation_catalog(restored)


async def test_strict_current0053_capture_never_relabels_source0054(
    original_frame_database_0053,
):
    source_scope = source_scopes.historical0053_source_scope(ROOT)
    async with original_frame_database_0053.connect() as connection:
        if source_scope.actual_source_head in {"0054", "0055", "0056"}:
            with pytest.raises(capture.Current0053CaptureError):
                await capture.capture_current0053_owner(connection)
        else:
            document, _ = await capture.capture_current0053_owner(connection)
            assert document["source_head"] == document["database_head"] == "0053"
            assert document["native_baseline_admission"] is False
        if source_scope.actual_source_head in {"0055", "0056"}:
            with pytest.raises(historical53.Historical0053ScopeError):
                await historical53.capture_historical0053_owner_on_current0054(
                    connection
                )
        if source_scope.actual_source_head == "0056":
            with pytest.raises(source55.Current0055ReadbackError):
                await source55.capture_historical0053_owner_on_current0055(connection)
