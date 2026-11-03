"""UNRUN actual-PG installation/runtime separation and formal DB0052 readback.

Positive native catalogs come only from the R10 fresh PostgreSQL fixture. No
synthetic definition, OID, role or native-baseline hash is accepted as a seal.
These tests confer no account, native acquisition or execution authority.
"""

import pytest
from sqlalchemy import text

from app.domain import account_history_original_frame_v1 as frame
from app.domain import account_history_original_source_catalog_v1 as source
from app.domain import account_history_partition_catalog_v1 as parent
from app.domain.prospective_event_cutover_catalog_v2 import CATALOG_SQL as CUTOVER_SQL
from scripts import verify_final_hermetic as hermetic
from scripts.verify_controlled_writer_coverage_schema_v1 import read_catalog
from scripts.verify_migration_identity import database_head
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
    settings,
)
from tests.integration.test_account_history_original_frame_repository_v1 import (
    original_frame_database as original_frame_database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _catalogs(connection):
    return (
        await connection.scalar(text(parent.CATALOG_SQL)),
        await connection.scalar(text(frame.CATALOG_SQL)),
    )


async def _formal_inputs(connection):
    """Read every existing formal operand from the same actual PG transaction."""
    previous, actual = await _catalogs(connection)
    facts = {
        "observed": await connection.run_sync(database_head),
        "witness_present": await connection.scalar(
            text(
                "SELECT to_regclass('public.demo_account_revision_source_witnesses') IS NOT NULL"
            )
        ),
        "guard_present": await connection.scalar(
            text(
                "SELECT to_regprocedure('public.account_revision_source_witness_guard()') IS NOT NULL"
            )
        ),
        "trigger_catalog": (
            await connection.execute(
                text(hermetic.ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL)
            )
        ).all(),
        "hwm_present": await connection.scalar(
            text(
                "SELECT to_regclass('public.demo_account_measured_hwm_v2') IS NOT NULL"
            )
        ),
        "hwm_guard_present": await connection.scalar(
            text(
                "SELECT to_regprocedure('public.account_measured_hwm_v2_guard()') IS NOT NULL"
            )
        ),
        "hwm_trigger_catalog": (
            await connection.execute(
                text(hermetic.ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL)
            )
        ).all(),
        "runtime_catalog": await connection.scalar(
            text(hermetic.PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL)
        ),
        "native_identity_catalog": await connection.scalar(
            text(hermetic.NATIVE_WITNESS_IDENTITY_SCHEMA_SQL)
        ),
        "coverage_catalog": await read_catalog(connection),
        "partition_catalog": previous,
        "cutover_catalog": await connection.scalar(text(CUTOVER_SQL)),
    }
    return facts, actual


async def test_actual_owner_installation_passes_both_seals_but_runtime_admin_denies(
    original_frame_database,
):
    async with original_frame_database.begin() as connection:
        await settings(connection)
        previous, actual = await _catalogs(connection)
        retained = parent.canonical([previous, actual])
        observed = frame.verify_owner_installation_catalog(actual, previous)
        assert observed == frame.expected_owner_installation_schema()
        assert observed["runtime_admission"] is False
        assert observed["native_history_prefix_verified"] is False
        assert observed["account_complete"] is False
        assert observed["execution_authority"] is False
        assert len(actual["functions"][0]["acl"]) == 1
        with pytest.raises(
            parent.AccountHistoryPartitionError,
            match="^original_frame_runtime_role_denied$",
        ):
            frame.verify_catalog(actual, previous)
        # Neither API is allowed to fabricate a runtime/owner projection.
        assert parent.canonical([previous, actual]) == retained


async def test_actual_minimal_frame_reader_runtime_passes_and_installer_denies(
    original_frame_database,
):
    admin = original_frame_database
    async with collector(admin, grant=True) as (reader, oid):
        async with reader.begin() as connection:
            await settings(connection)
            previous, actual = await _catalogs(connection)
            retained = parent.canonical([previous, actual])
            assert (
                frame.verify_catalog(actual, previous, approved_collector_role_oid=oid)
                == frame.DDL_SHA256
            )
            assert actual["source_table_access"] is False
            with pytest.raises(
                parent.AccountHistoryPartitionError,
                match="^original_frame_acl_drift$",
            ):
                frame.verify_owner_installation_catalog(actual, previous)
            assert parent.canonical([previous, actual]) == retained
        # An installer must not silently accept or remove a live reader grant.
        async with admin.begin() as connection:
            await settings(connection)
            previous, actual = await _catalogs(connection)
            with pytest.raises(
                parent.AccountHistoryPartitionError,
                match="^original_frame_acl_drift$",
            ):
                frame.verify_owner_installation_catalog(actual, previous)
    # The fixture explicitly revoked the grant; no new API performed a write.
    async with admin.begin() as connection:
        await settings(connection)
        previous, actual = await _catalogs(connection)
        assert (
            frame.verify_owner_installation_catalog(actual, previous)
            == frame.expected_owner_installation_schema()
        )


@pytest.mark.parametrize("target", ["source", "frame"])
async def test_actual_installer_rejects_native_definition_drift_without_source_rewrite(
    original_frame_database,
    target,
):
    admin = original_frame_database
    async with admin.begin() as connection:
        await settings(connection)
        previous, original = await _catalogs(connection)
        assert (
            frame.verify_owner_installation_catalog(original, previous)
            == frame.expected_owner_installation_schema()
        )
    statement = (
        "ALTER FUNCTION public.account_capture_event_guard() COST 101"
        if target == "source"
        else f"ALTER FUNCTION public.{frame.FUNCTION}({frame.SIGNATURE}) COST 101"
    )
    async with admin.begin() as connection:
        await connection.execute(text(statement))
    async with admin.begin() as connection:
        await settings(connection)
        previous, changed = await _catalogs(connection)
        if target == "source":
            assert (
                changed["source_catalog"]["structure"]
                == original["source_catalog"]["structure"]
            )
            assert (
                changed["source_catalog"]["definitions"]
                != original["source_catalog"]["definitions"]
            )
            expected = "^original_frame_source_deparser_drift$"
        else:
            assert (
                changed["functions"][0]["source"]
                == original["functions"][0]["source"]
                == frame.BODY
            )
            assert (
                changed["functions"][0]["definition"]
                != original["functions"][0]["definition"]
            )
            expected = "^original_frame_native_baseline_pending$"
        with pytest.raises(parent.AccountHistoryPartitionError, match=expected):
            frame.verify_owner_installation_catalog(changed, previous)


async def test_actual_0052_formal_readback_v9_keeps_exact_0051_parent_fields(
    original_frame_database,
):
    async with original_frame_database.begin() as connection:
        await settings(connection)
        facts, actual = await _formal_inputs(connection)
        assert facts["observed"] == "0052"
        retained = parent.canonical([facts["partition_catalog"], actual])
        observed = hermetic.migration_revision_readback(
            "0052", **facts, original_frame_catalog=actual
        )
        assert hermetic.native_migration_json_equal(
            observed, hermetic.expected_migration_revision_readback("0052")
        )
        assert observed["schema"] == "ctcc.migration_revision_readback.v9"
        assert observed["expected"] == observed["observed"] == "0052"
        prior = hermetic.expected_migration_revision_readback("0051")
        for name in prior.keys() - {"schema", "expected", "observed"}:
            assert hermetic.native_migration_json_equal(observed[name], prior[name])
        assert observed["execution_authority"] is False
        assert parent.canonical([facts["partition_catalog"], actual]) == retained
        with pytest.raises(
            parent.AccountHistoryPartitionError, match="^original_frame_catalog_shape$"
        ):
            hermetic.migration_revision_readback("0052", **facts)
        for requested in ("0051", "0053"):
            with pytest.raises(
                RuntimeError, match="^migration_revision_readback_mismatch$"
            ):
                hermetic.migration_revision_readback(
                    requested, **facts, original_frame_catalog=actual
                )


async def test_actual_0052_formal_readback_rejects_missing_frame_native_seal(
    original_frame_database,
    monkeypatch,
):
    async with original_frame_database.begin() as connection:
        await settings(connection)
        facts, actual = await _formal_inputs(connection)
        assert (
            frame.verify_owner_installation_catalog(actual, facts["partition_catalog"])
            == frame.expected_owner_installation_schema()
        )
        # Removing a pin is a negative only; no test remints a matching pin.
        monkeypatch.setattr(frame, "PG17_NATIVE_BASELINE_SHA256", None)
        with pytest.raises(
            parent.AccountHistoryPartitionError,
            match="^original_frame_native_baseline_pending$",
        ):
            hermetic.migration_revision_readback(
                "0052", **facts, original_frame_catalog=actual
            )


async def test_actual_0052_formal_readback_rejects_missing_source_native_seal(
    original_frame_database,
    monkeypatch,
):
    async with original_frame_database.begin() as connection:
        await settings(connection)
        facts, actual = await _formal_inputs(connection)
        assert (
            frame.verify_owner_installation_catalog(actual, facts["partition_catalog"])
            == frame.expected_owner_installation_schema()
        )
        monkeypatch.setattr(source, "PG17_NATIVE_BASELINE_SHA256", None)
        with pytest.raises(
            parent.AccountHistoryPartitionError,
            match="^original_frame_source_native_baseline_pending$",
        ):
            hermetic.migration_revision_readback(
                "0052", **facts, original_frame_catalog=actual
            )
