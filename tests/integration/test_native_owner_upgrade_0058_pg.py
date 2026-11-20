"""Real isolated57→58 owner DDL/rollback; no installed DB, credential or venue.

The public/private archive boundary is covered separately. These components use
the genuine TLS three-role lab, real catalog observations and intact migrations.
"""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.demo_automation import DemoAutomationState
from app.database.models.demo_control import DemoAccountControl
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from scripts import native_owner_upgrade_0058_v1 as subject
from tests.integration import test_native_demo_profile_0058_pg as lab

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_owner58_lab = lab._actual58_demo_lab


def _parent(connection, script):
    subject.profiles.require_migration(subject.profiles.DB0058, subject.ROOT)
    assert MigrationContext.configure(connection).get_current_heads() == ("0058",)
    migration = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._downgrade_revs("0057", heads)}
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == ("0057",)


@pytest.fixture
async def _actual57_owner_profile_lab(_owner58_lab):
    owner, selected, names = _owner58_lab
    config = Config(str(subject.ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(subject.ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    async with owner.begin() as connection:
        await connection.run_sync(_parent, script)
    seed_at = datetime.now(UTC) - timedelta(seconds=2)
    state = subject.parent.control.stopped_genesis(
        subject.parent.control.ControlScope("demo", "730058"), seed_at
    )
    raw = subject.parent.control.canonical(subject.parent.control.document(state))
    values = {
        name: getattr(state, name)
        for name in (
            "owner_sha256",
            "owner_epoch",
            "lease_until",
            "emergency_stop",
            "arm_request_id",
            "arm_expires_at",
            "created_at",
            "updated_at",
        )
    }
    shared = genesis._stopped_values(seed_at, "f" * 64)
    shared.update(
        control_revision=7,
        daily_pnl=Decimal("-12.5"),
        trades_today=13,
        consecutive_losses=3,
        peak_equity=Decimal(1234),
        realized_pnl_events=[{"net_pnl": "-12.5", "closed_at": seed_at.isoformat()}],
    )
    factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    async with factory.begin() as session:
        session.add(
            DemoAccountControl(
                environment="demo",
                account_id="730058",
                control_revision=state.revision,
                state_json=raw,
                state_sha256=subject.parent.control.digest(raw),
                **values,
            )
        )
        session.add(DemoAutomationState(**shared))
    async with owner.connect() as connection, connection.begin():
        for sql in subject.parent.old._READBACK_SETTINGS:
            await connection.exec_driver_sql(sql)
        actual = tuple((await connection.execute(subject.route._PROFILE_SQL)).one())
        oids = tuple(
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
    pins = subject.OwnerProfileUpgrade0058PinsV1(
        "a" * 40,
        "b" * 40,
        "c" * 64,
        subject.route.OwnerRoutePinsV1(
            subject.sha((subject.ROOT / "MANIFEST.sha256").read_bytes()),
            os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
            subject.sha(actual[11].encode("ascii")),
            owner.url.host,
            owner.url.port or 5432,
            actual[2],
            actual[0],
            actual[5],
            actual[4],
            "0057",
        ),
        names,
        oids,
    )
    yield owner, selected, pins, script


async def test_actual0057_owner58_rehearsal_and_commit_preserve_every_row_actor_and44_acl(
    _actual57_owner_profile_lab,
):
    owner, selected, pins, script = _actual57_owner_profile_lab
    before_identity, incarnation = await subject._role_observation(
        selected, owner, pins, "0057"
    )
    before = await subject._inventory(owner, pins)
    final = await subject._commit_once(script, pins, owner, before)
    prepared = subject.route._source_for_profile(subject.profiles.DB0058)
    pin_context = type(
        "PinContext", (), {"migration_sha256": prepared.actual_migration_sha256}
    )()
    after_pins = replace(
        pins,
        route=replace(pins.route, database_head="0058"),
        installed_catalog_sha256=subject._installed_pin(pin_context, final["logical"]),
    )
    after_identity, after_incarnation = await subject._role_observation(
        selected, owner, after_pins, "0058"
    )
    assert after_incarnation == incarnation
    assert after_identity.role_oids == before_identity.role_oids == pins.role_oids
    assert after_identity.schema != before_identity.schema
    assert (
        after_identity.execution_authority is after_identity.startup_admission is False
    )
    assert await subject._inventory(owner, after_pins) == final
    subject._preserved(before, final)
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
        ) == "0058"
        assert (
            await connection.scalar(
                subject.catalog.NEW_LIVE_ACCESS,
                {
                    "tables": list(subject.live.TABLES),
                    "functions": list(subject._live_functions()),
                },
            )
        ) is True
        assert tuple(
            (
                await connection.execute(
                    text(
                        "SELECT daily_pnl,trades_today,consecutive_losses,peak_equity FROM public.demo_automation_state WHERE id=1"
                    )
                )
            ).one()
        ) == (Decimal("-12.5"), 13, 3, Decimal(1234))
    # A stale57 command is refused, without a second migration or grant.
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(script, pins, owner, before)
    assert await subject._inventory(owner, after_pins) == final


async def test_actual0057_owner58_final_ddl_failure_restores_original_head_rows_oids_and_acl(
    _actual57_owner_profile_lab, monkeypatch
):
    owner, selected, pins, script = _actual57_owner_profile_lab
    before = await subject._inventory(owner, pins)
    identity, _ = await subject._role_observation(selected, owner, pins, "0057")
    original = subject._migrate
    calls = []

    def fail_second(connection, current_script):
        calls.append("DDL")
        original(connection, current_script)
        if len(calls) == 2:
            raise RuntimeError("declared disposable-lab failure after final DDL")

    monkeypatch.setattr(subject, "_migrate", fail_second)
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(script, pins, owner, before)
    assert calls == ["DDL", "DDL"]
    assert await subject._inventory(owner, pins) == before
    restored, _ = await subject._role_observation(selected, owner, pins, "0057")
    assert restored == identity


async def test_actual0057_owner58_unreviewed_live_access_rolls_back_without_granting_or_commit(
    _actual57_owner_profile_lab, monkeypatch
):
    owner, selected, pins, script = _actual57_owner_profile_lab
    before = await subject._inventory(owner, pins)
    original = subject._migrate

    def unexpected_access(connection, current_script):
        original(connection, current_script)
        # Synthetic DDL fault injection only in this disposable owner fixture.
        connection.exec_driver_sql(
            'GRANT SELECT ON public.native_live_controls_v1 TO "'
            + pins.role_names[0]
            + '"'
        )

    monkeypatch.setattr(subject, "_migrate", unexpected_access)
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(script, pins, owner, before)
    assert await subject._inventory(owner, pins) == before
    restored, _ = await subject._role_observation(selected, owner, pins, "0057")
    assert restored.role_oids == pins.role_oids
