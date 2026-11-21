"""Disposable source17 SQL projection proofs; not a whole17->55 acceptance.

The historical fixture's source pins/naming scope remain DATA. These tests
execute the exact frozen0034 delta only in the fresh lab and prove the shared
null-aware OLD-COLUMN projection. No host DB or baseline seal is installed.
"""

from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text

from scripts import retained0017_owner_plan_v1 as source
from scripts import retained0017_upgrade_projection_v1 as subject
from tests.integration import test_retained0017_owner_plan_v1_pg as original_lab

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_actual_retained17_lab = original_lab._actual_retained17_lab


def _frozen0034(connection):
    config = Config(str(source.ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(source.ROOT / "migrations"))
    graph = ScriptDirectory.from_config(config)
    migration = MigrationContext.configure(connection)
    # Exact source function; intentionally no marker promotion/full-route claim.
    with Operations.context(migration):
        graph.get_revision("0034").module.upgrade()


async def _digest(connection, table, *, phase=subject.OldColumnProjectionV1.ORIGINAL):
    columns = source._source_plan()["minimum17_columns"][table]
    size, digest = subject.old_projection_sql_v1(table, columns, phase=phase)
    return tuple((await connection.execute(size)).one()), await connection.scalar(
        digest
    )


async def test_actual0017_old_projection_matches_only_exact_frozen0034_negative_delta(
    _actual_retained17_lab,
):
    owner, _pins, _plan = _actual_retained17_lab
    async with owner.connect() as connection, connection.begin():
        automation = await connection.scalar(
            text(
                "SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY id) FROM public.demo_automation_state s"
            )
        )
        original = subject.validate_original_control_v1(automation)
        expected = await _digest(
            connection,
            "demo_automation_state",
            phase=subject.OldColumnProjectionV1.AFTER_FIXED_0034,
        )
        savepoint = await connection.begin_nested()
        await connection.run_sync(_frozen0034)
        assert await _digest(connection, "demo_automation_state") == expected
        actual = await connection.scalar(
            text(
                "SELECT pg_catalog.to_jsonb(s) FROM public.demo_automation_state s WHERE id=1"
            )
        )
        assert actual["lock_reasons"] == original["lock_reasons"] + [
            subject.REASON_0034
        ]
        for name in (
            "daily_pnl",
            "trades_today",
            "consecutive_losses",
            "risk_peak_equity",
            "realized_pnl_events",
            "updated_at",
        ):
            assert actual[name] == original[name]
        assert (
            actual["control_revision"] == 0 and actual["restart_latch_required"] is True
        )
        await savepoint.rollback()
        assert (
            await _digest(
                connection,
                "demo_automation_state",
                phase=subject.OldColumnProjectionV1.AFTER_FIXED_0034,
            )
            == expected
        )


async def test_actual0017_old_projection_rejects_sqlnull_to_jsonnull_with_json_only_zero_delta(
    _actual_retained17_lab,
):
    owner, _pins, _plan = _actual_retained17_lab
    audit_id = uuid4()
    async with owner.connect() as connection, connection.begin():
        await connection.execute(
            text(
                "INSERT INTO public.audit_logs(id,actor,action,resource_type,resource_id,\"before\") VALUES(:id,'declared_lab','old_projection','synthetic','null_flip',NULL)"
            ),
            {"id": audit_id},
        )
        before = await _digest(connection, "audit_logs")
        json_only = await connection.scalar(
            text(
                "SELECT pg_catalog.to_jsonb(a)::pg_catalog.text FROM public.audit_logs a WHERE id=:id"
            ),
            {"id": audit_id},
        )
        savepoint = await connection.begin_nested()
        await connection.execute(
            text(
                "UPDATE public.audit_logs SET \"before\"='null'::pg_catalog.jsonb WHERE id=:id"
            ),
            {"id": audit_id},
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT pg_catalog.to_jsonb(a)::pg_catalog.text FROM public.audit_logs a WHERE id=:id"
                ),
                {"id": audit_id},
            )
            == json_only
        )
        assert await _digest(connection, "audit_logs") != before
        await savepoint.rollback()
        assert await _digest(connection, "audit_logs") == before


async def test_actual0017_post0034_risk_mutation_is_nonzero_and_rollback_restores_original(
    _actual_retained17_lab,
):
    owner, _pins, _plan = _actual_retained17_lab
    async with owner.connect() as connection, connection.begin():
        original = await _digest(connection, "demo_automation_state")
        expected = await _digest(
            connection,
            "demo_automation_state",
            phase=subject.OldColumnProjectionV1.AFTER_FIXED_0034,
        )
        savepoint = await connection.begin_nested()
        await connection.run_sync(_frozen0034)
        await connection.execute(
            text(
                "UPDATE public.demo_automation_state SET daily_pnl=daily_pnl+1,control_revision=control_revision+1 WHERE id=1"
            )
        )
        assert await _digest(connection, "demo_automation_state") != expected
        await savepoint.rollback()
        assert await _digest(connection, "demo_automation_state") == original


async def test_actual0017_malformed_reasons_refuse_before_any_frozen0034_mutation(
    _actual_retained17_lab,
):
    owner, _pins, _plan = _actual_retained17_lab
    async with owner.connect() as connection, connection.begin():
        await connection.execute(
            text(
                "UPDATE public.demo_automation_state SET lock_reasons='{}'::pg_catalog.jsonb WHERE id=1"
            )
        )
        automation = await connection.scalar(
            text(
                "SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY id) FROM public.demo_automation_state s"
            )
        )
        with pytest.raises(subject.Retained0017ProjectionRefused):
            subject.validate_original_control_v1(automation)
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0017"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid='public.demo_automation_state'::pg_catalog.regclass AND attname='control_revision')"
                )
            )
            is True
        )
