"""Disposable PostgreSQL 0026 witness acceptance; no public network I/O.

An explicitly supplied isolated DATABASE_URL must permit a generated test DB.
The generated database and role are the only objects removed by this module.
This narrow fixture invokes 0024 and 0026 DDL directly; it does not prove the
complete 0017-0025 Alembic upgrade or production schema drift. Hermetic full-
chain migration acceptance must be run separately on the final exact source.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation.blind_window_schedule_binding import (
    freeze_schedule_bound_blind_window_dataset,
    verify_schedule_bound_blind_window_dataset,
)
from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
    Gate3SchedulePinError,
    Gate3SchedulePinReadback,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
)
from tests.durable_migration_fixtures import load_migration
from tests.unit.mie.test_gate3_blind_window_schedule_binding import binding_case
from tests.unit.mie.test_gate3_capture_schedule import valid_schedule

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def migrate(connection, revision: str, direction: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = load_migration(revision)
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, direction)()


@pytest.fixture
async def isolated_database():
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit isolated DATABASE_URL required")
    test_database = "ctcc_gate3_schedule_" + uuid4().hex
    role = "ctcc_g3_" + uuid4().hex[:18]
    assert re.fullmatch(r"ctcc_gate3_schedule_[a-f0-9]{32}", test_database)
    assert re.fullmatch(r"ctcc_g3_[a-f0-9]{18}", role)
    admin = create_async_engine(raw_url, poolclass=NullPool)
    db_engine = None
    role_engine = None
    created_db = created_role = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"CREATE DATABASE {test_database}"))
        created_db = True
        db_url = make_url(raw_url).set(database=test_database)
        db_engine = create_async_engine(db_url, poolclass=NullPool)
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0024", "upgrade")
            await connection.run_sync(migrate, "0026", "upgrade")
        async with admin.begin() as connection:
            # The isolated CI database uses trust authentication. Never print a
            # password or use this role outside the generated database.
            await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
        created_role = True
        async with db_engine.begin() as connection:
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            for function in (
                "public_receipt_witness_append(jsonb)",
                "public_receipt_witness_read(text)",
                "gate3_capture_schedule_append(jsonb)",
                "gate3_capture_schedule_read(text)",
            ):
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {role}")
                )
        role_engine = create_async_engine(
            db_url.set(username=role, password=None), poolclass=NullPool
        )
        repository = Gate3CaptureSchedulePinRepository(
            async_sessionmaker(role_engine, expire_on_commit=False)
        )
        await repository.verify_role()
        yield repository, db_engine, role_engine
    finally:
        if role_engine is not None:
            await role_engine.dispose()
        if db_engine is not None:
            await db_engine.dispose()
        if created_db:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f"DROP DATABASE {test_database}"))
        if created_role:
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()


async def test_isolated_0026_empty_downgrade_and_reupgrade(isolated_database):
    _, db_engine, _ = isolated_database
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT to_regclass('public.gate3_capture_schedule_pins')")
            )
            is not None
        )
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0026", "downgrade")
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT to_regclass('public.gate3_capture_schedule_pins')")
            )
            is None
        )
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0026", "upgrade")
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT to_regclass('public.gate3_capture_schedule_pins')")
            )
            is not None
        )


async def test_restricted_pin_readback_conflict_concurrency_and_retention(
    isolated_database,
):
    repository, db_engine, role_engine = isolated_database
    seal, schedule = valid_schedule()
    async with role_engine.connect() as connection:
        grants = (
            await connection.execute(
                text("""
                    SELECT current_user=session_user AS direct_login,
                      has_table_privilege(current_user,
                        'public.gate3_capture_schedule_pins','SELECT') AS table_select,
                      has_table_privilege(current_user,
                        'public.gate3_capture_schedule_pins','INSERT') AS table_insert
                """)
            )
        ).one()
    assert grants.direct_login and not grants.table_select and not grants.table_insert
    attempts = await asyncio.gather(
        repository.publish(schedule=schedule, seal=seal),
        repository.publish(schedule=schedule, seal=seal),
        return_exceptions=True,
    )
    assert sum(type(item) is Gate3SchedulePinReadback for item in attempts) == 1
    assert sum(type(item) is Gate3SchedulePinError for item in attempts) == 1
    receipt = await repository.read(
        expected_schedule_sha256=schedule.canonical_sha256(), seal=seal
    )
    accepted = next(item for item in attempts if type(item) is Gate3SchedulePinReadback)
    assert receipt.schedule_sha256 == accepted.schedule_sha256
    assert receipt.recorded_at == accepted.recorded_at
    assert receipt.database_readback_at >= accepted.database_readback_at
    assert (
        schedule.planned_at
        <= receipt.recorded_at
        <= accepted.database_readback_at
        <= receipt.database_readback_at
        < schedule.coordinate_plan.start_at
    )
    assert not receipt.independently_protected
    assert not receipt.predictive_oos_eligible
    assert not receipt.execution_authority
    restarted_engine = create_async_engine(role_engine.url, poolclass=NullPool)
    try:
        restarted = Gate3CaptureSchedulePinRepository(
            async_sessionmaker(restarted_engine, expire_on_commit=False)
        )
        restarted_read = await restarted.read(
            expected_schedule_sha256=schedule.canonical_sha256(), seal=seal
        )
        assert restarted_read.schedule == receipt.schedule
        assert restarted_read.recorded_at == receipt.recorded_at
        assert restarted_read.database_readback_at >= receipt.database_readback_at
    finally:
        await restarted_engine.dispose()

    # A renamed candidate/receipt for the same source window cannot replace
    # the original pin, even with internally consistent new hashes.
    changed = seal.model_dump(mode="python")
    changed["preregistration_id"] = "gate3:renamed:synthetic:v2"
    changed_seal = Gate3ProspectivePreregistration.model_validate(changed)
    changed_schedule = build_capture_schedule(
        seal=changed_seal,
        coordinate_plan=schedule.coordinate_plan,
        planned_at=schedule.planned_at,
    )
    with pytest.raises(Gate3SchedulePinError, match="schedule_publish_rejected"):
        await repository.publish(schedule=changed_schedule, seal=changed_seal)
    renamed_coordinate = ProspectiveCoordinatePlanV1.model_validate(
        {
            **schedule.coordinate_plan.model_dump(mode="python"),
            "holdout_id": "okx:renamed:future:2m",
        }
    )
    changed["prospective_holdout"]["holdout_id"] = renamed_coordinate.holdout_id
    changed["prospective_holdout"]["coordinate_plan_sha256"] = (
        renamed_coordinate.canonical_sha256()
    )
    renamed_seal = Gate3ProspectivePreregistration.model_validate(changed)
    renamed_schedule = build_capture_schedule(
        seal=renamed_seal,
        coordinate_plan=renamed_coordinate,
        planned_at=schedule.planned_at,
    )
    assert renamed_coordinate.window_key() == schedule.coordinate_plan.window_key()
    with pytest.raises(Gate3SchedulePinError, match="schedule_publish_rejected"):
        await repository.publish(schedule=renamed_schedule, seal=renamed_seal)
    with pytest.raises(DBAPIError, match="gate3_schedule_immutable"):
        async with db_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE public.gate3_capture_schedule_pins "
                    "SET holdout_id='forged' WHERE schedule_sha256=:sha"
                ),
                {"sha": schedule.canonical_sha256()},
            )
    with pytest.raises(DBAPIError, match="gate3_schedule_downgrade_requires_empty"):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0026", "downgrade")
    retained = await repository.read(
        expected_schedule_sha256=schedule.canonical_sha256(), seal=seal
    )
    assert retained.schedule == receipt.schedule
    assert retained.recorded_at == receipt.recorded_at
    assert retained.database_readback_at >= receipt.database_readback_at


async def test_publish_denies_if_post_commit_db_readback_reaches_window(
    isolated_database, monkeypatch
):
    repository, _, _ = isolated_database
    seal, schedule = valid_schedule()
    actual_read = repository.read

    async def delayed_readback(*, expected_schedule_sha256, seal):
        result = await actual_read(
            expected_schedule_sha256=expected_schedule_sha256, seal=seal
        )
        # Deterministic boundary injection after a genuine new-session DB
        # readback. No wall-clock sleeps and no claim about actual DB latency.
        return replace(result, database_readback_at=schedule.coordinate_plan.start_at)

    with monkeypatch.context() as context:
        context.setattr(repository, "read", delayed_readback)
        with pytest.raises(
            Gate3SchedulePinError, match="schedule_publication_readback_late"
        ):
            await repository.publish(schedule=schedule, seal=seal)
    audit = await actual_read(
        expected_schedule_sha256=schedule.canonical_sha256(), seal=seal
    )
    assert audit.schedule_sha256 == schedule.canonical_sha256()
    assert audit.database_readback_at >= audit.recorded_at
    assert not audit.predictive_oos_eligible


async def test_server_time_rejects_late_schedule_even_when_payload_claims_early(
    isolated_database,
):
    repository, _, _ = isolated_database
    end = datetime.now(UTC) - timedelta(minutes=1)
    start = end.replace(second=0, microsecond=0)
    seal, schedule = valid_schedule(start=start)
    assert schedule.planned_at < start
    with pytest.raises(Gate3SchedulePinError, match="schedule_publish_rejected"):
        await repository.publish(schedule=schedule, seal=seal)


async def test_raw_function_rejects_forged_window_key_and_coordinate_bytes(
    isolated_database,
):
    repository, _, role_engine = isolated_database
    seal, schedule = valid_schedule()
    coordinate = schedule.coordinate_plan
    record = {
        "schedule_sha256": schedule.canonical_sha256(),
        "seal_sha256": seal.canonical_sha256(),
        "coordinate_plan_sha256": coordinate.canonical_sha256(),
        "window_key": coordinate.window_key(),
        "holdout_id": coordinate.holdout_id,
        "window_start": coordinate.start_at.isoformat(),
        "window_end": coordinate.end_at.isoformat(),
        "planned_at": schedule.planned_at.isoformat(),
        "coordinate_plan_json": coordinate.canonical_json(),
        "schedule_json": schedule.canonical_json(),
    }

    async def raw_append(payload):
        async with role_engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT public.gate3_capture_schedule_append("
                    "CAST(:payload AS jsonb))"
                ),
                {"payload": json.dumps(payload, sort_keys=True)},
            )

    forged_window = {**record, "window_key": "f" * 64}
    with pytest.raises(DBAPIError, match="gate3_schedule_identity_denied"):
        await raw_append(forged_window)
    forged_coordinate = {**record, "coordinate_plan_json": "{}"}
    with pytest.raises(DBAPIError, match="gate3_schedule_coordinates_denied"):
        await raw_append(forged_coordinate)
    incomplete = json.loads(schedule.canonical_json())
    incomplete["plans"].pop()
    incomplete_json = json.dumps(incomplete, sort_keys=True, separators=(",", ":"))
    incomplete_record = {
        **record,
        "schedule_json": incomplete_json,
        "schedule_sha256": hashlib.sha256(incomplete_json.encode()).hexdigest(),
    }
    with pytest.raises(DBAPIError, match="gate3_schedule_coverage_denied"):
        await raw_append(incomplete_record)
    changed = json.loads(schedule.canonical_json())
    changed["plans"][0]["start_ns"] += 60_000_000_000
    changed_json = json.dumps(changed, sort_keys=True, separators=(",", ":"))
    changed_record = {
        **record,
        "schedule_json": changed_json,
        "schedule_sha256": hashlib.sha256(changed_json.encode()).hexdigest(),
    }
    with pytest.raises(DBAPIError, match="gate3_schedule_plan_coordinate_denied"):
        await raw_append(changed_record)
    accepted = await repository.publish(schedule=schedule, seal=seal)
    assert accepted.window_key == coordinate.window_key()


@pytest.fixture
def future_synthetic_binding(monkeypatch):
    """Build original-byte synthetic journals before entering the async DB test."""
    start = (datetime.now(UTC) + timedelta(minutes=10)).replace(second=0, microsecond=0)
    planned_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    return binding_case(monkeypatch, start=start, planned_at=planned_at)


async def test_0026_readback_binds_every_synthetic_window_plan(
    isolated_database, future_synthetic_binding
):
    repository, _, _ = isolated_database
    args, _, schedule, dataset = future_synthetic_binding
    seal = args["preregistration"]
    publication = await repository.publish(schedule=schedule, seal=seal)
    assert publication.recorded_at < schedule.coordinate_plan.start_at
    assert publication.database_readback_at < schedule.coordinate_plan.start_at
    args = {**args, "repository": repository}
    frozen = await freeze_schedule_bound_blind_window_dataset(**args)
    assert frozen.contract.capture_schedule_sha256 == publication.schedule_sha256
    assert frozen.contract.schedule_recorded_at == publication.recorded_at
    assert frozen.contract.pre_window_commit_proven is False
    assert frozen.contract.matched_plan_count == len(dataset.rows)
    assert (
        await verify_schedule_bound_blind_window_dataset(
            frozen.payload, expected_sha256=frozen.sha256, **args
        )
        == frozen.contract
    )
