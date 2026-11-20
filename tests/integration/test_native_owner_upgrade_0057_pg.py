"""Actual isolated owner 56→57 transaction; no installed DB or venue calls.

The public archive/private-input boundary is tested separately. These tests
exercise the real held migration/grant transaction, rollback and restricted
three-role independent readback against the existing disposable TLS lab.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.database.repositories.demo_control import DemoControlRepository
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from scripts import native_owner_upgrade_0057_v1 as subject
from tests.integration import test_g12_restricted_writer_0057_pg as lab

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_actual57_lab = lab._isolated_0057_g12_lab


@pytest.fixture
async def _actual56_owner_profile_lab(_actual57_lab):
    owner, readers, names = _actual57_lab
    owner_factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    async with owner.begin() as connection:
        for table in subject.grants.g12.TABLES:
            await connection.exec_driver_sql(
                f'REVOKE SELECT,INSERT ON public."{table}" FROM "{names[0]}"'
            )
        await connection.run_sync(lambda sync: lab._migrate(sync, "0056"))
    seed_at = datetime.now(UTC) - timedelta(seconds=2)
    # Exercise the real stopped transition and its immutable paired journal;
    # a naked Control INSERT is correctly refused by the deferred DB guard.
    await DemoControlRepository(owner_factory, clock=lambda: seed_at).latch_stop(
        subject.control.ControlScope("demo", "730057"), command_id="a" * 64
    )
    shared = genesis._stopped_values(seed_at, "f" * 64)
    shared.update(
        control_revision=7,
        daily_pnl=Decimal("-12.5"),
        trades_today=13,
        consecutive_losses=3,
        peak_equity=Decimal(1234),
        realized_pnl_events=[{"net_pnl": "-12.5", "closed_at": seed_at.isoformat()}],
    )
    async with owner_factory.begin() as session:
        session.add(DemoAutomationState(**shared))
    selected = ConfiguredNativeDemoFactoriesV2(
        *(
            async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
            for engine in readers
        ),
        readers[1],
        readers[2],
    )
    async with owner.connect() as connection, connection.begin():
        for sql in subject.old._READBACK_SETTINGS:
            await connection.exec_driver_sql(sql)
        actual = tuple((await connection.execute(subject.route._PROFILE_SQL)).one())
        role_oids = tuple(
            [
                await connection.scalar(
                    text(
                        "SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:name"
                    ),
                    {"name": name},
                )
                for name in names
            ]
        )
    url = owner.url
    pins = subject.OwnerProfileUpgrade0057PinsV1(
        "a" * 40,
        "b" * 40,
        "c" * 64,
        subject.route.OwnerRoutePinsV1(
            subject.sha((subject.ROOT / "MANIFEST.sha256").read_bytes()),
            os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
            subject.sha(actual[11].encode("ascii")),
            url.host,
            url.port or 5432,
            actual[2],
            actual[0],
            actual[5],
            actual[4],
            "0056",
        ),
        names,
        role_oids,
    )
    config = Config(str(subject.ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(subject.ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    yield owner, selected, pins, script


async def test_actual0056_owner_upgrade_commits_once_preserves_all_rows_and_reads_full44(
    _actual56_owner_profile_lab,
):
    owner, selected, pins, script = _actual56_owner_profile_lab
    before, incarnation = await subject._role_observation(selected, owner, pins, "0056")
    snapshot, acl = await subject._readonly_inventory(owner, pins)
    retained, acl_sha = await subject._commit_once(script, pins, owner, snapshot, acl)
    actual57 = replace(pins, route=replace(pins.route, database_head="0057"))
    after, current_incarnation = await subject._role_observation(
        selected, owner, actual57, "0057"
    )
    readback, readback_acl = await subject._readonly_inventory(owner, actual57)
    assert incarnation == current_incarnation
    assert readback == retained == snapshot
    assert subject.sha(subject.canonical(readback_acl)) == acl_sha
    assert len(set(readback_acl) - set(acl)) == 4
    assert before.role_oids == after.role_oids == pins.role_oids
    assert before.schema != after.schema
    assert after.startup_admission is after.execution_authority is False
    # Upgrade cannot be repeated using the old head or stale 42-grant proof.
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        await subject._commit_once(script, pins, owner, snapshot, acl)
    assert await subject._readonly_inventory(owner, actual57) == (
        readback,
        readback_acl,
    )


async def test_actual0056_owner_upgrade_rollback_restores_head_acl_and_every_retained_row(
    _actual56_owner_profile_lab, monkeypatch
):
    owner, selected, pins, script = _actual56_owner_profile_lab
    before_identity, _ = await subject._role_observation(selected, owner, pins, "0056")
    before = await subject._readonly_inventory(owner, pins)
    original = subject._migrate

    def abort_after_real_migration(connection, source_script):
        original(connection, source_script)
        raise RuntimeError("declared isolated-lab failure injection")

    monkeypatch.setattr(subject, "_migrate", abort_after_real_migration)
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        await subject._commit_once(script, pins, owner, *before)
    assert await subject._readonly_inventory(owner, pins) == before
    restored, _ = await subject._role_observation(selected, owner, pins, "0056")
    assert before_identity == restored


async def test_actual0056_owner_upgrade_refuses_uncertain_local_state_before_ddl(
    _actual56_owner_profile_lab,
):
    owner, _selected, pins, script = _actual56_owner_profile_lab
    before = await subject._readonly_inventory(owner, pins)
    # A retained local uncertainty blocks the profile upgrade even while all
    # controls are stopped. Do not mutate a durable Control row.
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "LOCK TABLE public.demo_automation_state IN ACCESS EXCLUSIVE MODE"
        )
        await connection.exec_driver_sql(
            "UPDATE public.demo_automation_state SET last_error='injected_uncertain',control_revision=control_revision+1 WHERE id=1"
        )
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        await subject._commit_once(script, pins, owner, *before)
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0056"
        )
        assert (
            await connection.scalar(
                text("SELECT last_error FROM public.demo_automation_state WHERE id=1")
            )
            == "injected_uncertain"
        )


@pytest.mark.parametrize("original_json_null", [False, True])
async def test_actual0056_owner_upgrade_null_bitmap_refuses_changed_audit_value_and_rolls_back(
    _actual56_owner_profile_lab, monkeypatch, original_json_null
):
    owner, selected, pins, script = _actual56_owner_profile_lab
    audit_id = uuid4()
    initial = "'null'::jsonb" if original_json_null else "NULL"
    final = "NULL" if original_json_null else "'null'::jsonb"
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO public.audit_logs(id,actor,action,resource_type,resource_id,\"before\") VALUES(:id,'declared_lab','retained_null','synthetic','owner-row',"
                + initial
                + ")"
            ),
            {"id": audit_id},
        )
    before = await subject._readonly_inventory(owner, pins)
    old_profile, _ = await subject._role_observation(selected, owner, pins, "0056")
    original = subject._migrate
    differential = []

    def change_only_nullness(connection, current_script):
        original(connection, current_script)
        # Genuine PostgreSQL proves the old JSON-only encoding has no delta.
        prior = connection.execute(
            text("SELECT to_jsonb(r)::text FROM public.audit_logs r WHERE id=:id"),
            {"id": audit_id},
        ).scalar_one()
        connection.execute(
            text('UPDATE public.audit_logs SET "before"=' + final + " WHERE id=:id"),
            {"id": audit_id},
        )
        current = connection.execute(
            text("SELECT to_jsonb(r)::text FROM public.audit_logs r WHERE id=:id"),
            {"id": audit_id},
        ).scalar_one()
        differential.append(prior == current)

    monkeypatch.setattr(subject, "_migrate", change_only_nullness)
    with pytest.raises(subject.OwnerProfileUpgrade0057Refused):
        await subject._commit_once(script, pins, owner, *before)
    assert differential == [True]
    assert await subject._readonly_inventory(owner, pins) == before
    restored, _ = await subject._role_observation(selected, owner, pins, "0056")
    assert restored == old_profile
    async with owner.connect() as connection:
        assert await connection.scalar(
            text('SELECT "before" IS NULL FROM public.audit_logs WHERE id=:id'),
            {"id": audit_id},
        ) is (not original_json_null)
