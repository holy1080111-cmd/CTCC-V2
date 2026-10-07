"""Disposable PostgreSQL acceptance for the 0030 preregistration boundary.

An explicit isolated DATABASE_URL is required. No market, account or order
request is made; the future seal and capture schedule are synthetic fixtures.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
    Gate3SchedulePinError,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
    Gate3SchedulePublicationAckError,
)
from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3CommittedSealError,
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.prospective_capture_schedule import build_capture_schedule
from tests.integration.test_gate3_canonical_schedule_claim import (
    migrate,
    pin_record,
    publish_or_diagnose,
)
from tests.unit.mie.test_gate3_capture_schedule import valid_schedule

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def seal_record(seal, schedule) -> dict[str, str]:
    coordinate = schedule.coordinate_plan
    return {
        "seal_sha256": seal.canonical_sha256(),
        "preregistration_id": seal.preregistration_id,
        "coordinate_plan_sha256": coordinate.canonical_sha256(),
        "window_key": coordinate.window_key(),
        "holdout_id": coordinate.holdout_id,
        "window_start": coordinate.start_at.isoformat(),
        "window_end": coordinate.end_at.isoformat(),
        "created_at": seal.created_at.isoformat(),
        "seal_json": seal.canonical_json(),
    }


async def append_seal(engine, record):
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT public.gate3_prereg_seal_append(CAST(:record AS jsonb))"),
            {"record": json.dumps(record, separators=(",", ":"), sort_keys=True)},
        )


async def append_pin(engine, record):
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "SELECT public.gate3_capture_schedule_claim_append("
                "CAST(:record AS jsonb))"
            ),
            {"record": json.dumps(record, separators=(",", ":"), sort_keys=True)},
        )


@pytest.fixture
async def isolated_prereg_database(request):
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit isolated DATABASE_URL required")
    database = "ctcc_g3_prereg_" + uuid4().hex
    roles = ["ctcc_g3_" + uuid4().hex[:18] for _ in range(4)]
    assert re.fullmatch(r"ctcc_g3_prereg_[a-f0-9]{32}", database)
    assert all(re.fullmatch(r"ctcc_g3_[a-f0-9]{18}", role) for role in roles)
    seal_role, observer_role, pin_role, pin_ack_role = roles
    admin = create_async_engine(raw_url, poolclass=NullPool)
    db_engine = None
    role_engines = []
    created_db = False
    created_roles: list[str] = []
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"CREATE DATABASE {database}"))
        created_db = True
        db_url = make_url(raw_url).set(database=database)
        db_engine = create_async_engine(db_url, poolclass=NullPool)
        async with db_engine.begin() as connection:
            for revision in ("0024", "0026", "0028", "0029"):
                await connection.run_sync(migrate, revision, "upgrade")
        for role in roles:
            async with admin.begin() as connection:
                await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
            created_roles.append(role)
        async with db_engine.begin() as connection:
            for role in roles:
                await connection.execute(
                    text(f"GRANT USAGE ON SCHEMA public TO {role}")
                )
            for role, functions in (
                (
                    pin_role,
                    (
                        "gate3_capture_schedule_claim_append(jsonb)",
                        "gate3_capture_schedule_claim_read(text)",
                    ),
                ),
                (
                    pin_ack_role,
                    (
                        "gate3_capture_schedule_claim_ack_append(text,text,text,text,text)",
                        "gate3_capture_schedule_claim_ack_read(text)",
                    ),
                ),
            ):
                for function in functions:
                    await connection.execute(
                        text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {role}")
                    )
        pin_engine = create_async_engine(
            db_url.set(username=pin_role, password=None), poolclass=NullPool
        )
        pin_ack_engine = create_async_engine(
            db_url.set(username=pin_ack_role, password=None), poolclass=NullPool
        )
        role_engines.extend((pin_engine, pin_ack_engine))
        pin_repo = Gate3CaptureSchedulePinRepository(
            async_sessionmaker(pin_engine, expire_on_commit=False)
        )
        pin_ack_repo = Gate3CaptureSchedulePublicationAckRepository(
            async_sessionmaker(pin_ack_engine, expire_on_commit=False), pin_repo
        )
        seal, old_schedule = valid_schedule()
        legacy_sha = None
        if getattr(request, "param", None) == "legacy":
            await publish_or_diagnose(pin_repo, old_schedule, seal)
            await pin_ack_repo.acknowledge(
                expected_schedule_sha256=old_schedule.canonical_sha256(), seal=seal
            )
            legacy_sha = old_schedule.canonical_sha256()
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0030", "upgrade")
            for role, functions in (
                (
                    seal_role,
                    ("gate3_prereg_seal_append(jsonb)", "gate3_prereg_seal_read(text)"),
                ),
                (
                    observer_role,
                    (
                        "gate3_prereg_direct_role_allowed(text)",
                        "gate3_prereg_seal_read(text)",
                        "gate3_prereg_seal_ack_append(text)",
                        "gate3_prereg_seal_ack_read(text)",
                    ),
                ),
            ):
                for function in functions:
                    await connection.execute(
                        text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {role}")
                    )
        seal_engine = create_async_engine(
            db_url.set(username=seal_role, password=None), poolclass=NullPool
        )
        observer_engine = create_async_engine(
            db_url.set(username=observer_role, password=None), poolclass=NullPool
        )
        role_engines.extend((seal_engine, observer_engine))
        await pin_repo.verify_role()
        await pin_ack_repo.verify_role()
        yield (
            seal,
            old_schedule,
            legacy_sha,
            db_engine,
            seal_engine,
            observer_engine,
            pin_engine,
            pin_repo,
            pin_ack_repo,
        )
    finally:
        for engine in reversed(role_engines):
            await engine.dispose()
        if db_engine is not None:
            await db_engine.dispose()
        if created_db:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f"DROP DATABASE {database}"))
        for role in reversed(created_roles):
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()


async def test_seal_ack_must_commit_before_capture_claim(isolated_prereg_database):
    (
        seal,
        old_schedule,
        _,
        db_engine,
        seal_engine,
        observer_engine,
        pin_engine,
        pin_repo,
        pin_ack_repo,
    ) = isolated_prereg_database
    with pytest.raises(DBAPIError, match="gate3_prereg_claim_seal_missing"):
        await append_pin(pin_engine, pin_record(seal, old_schedule))
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_pins")
            )
            == 0
        )
    alternate = seal.model_copy(
        update={"preregistration_id": "gate3:unacknowledged:alternate:v1"}
    )
    await append_seal(seal_engine, seal_record(alternate, old_schedule))
    # The alternate proposal is immutable but has not been independently
    # accepted, so it cannot monopolize the same future window.
    await append_seal(seal_engine, seal_record(seal, old_schedule))
    async with seal_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT * FROM public.gate3_prereg_seal_read(:sha)"),
                {"sha": seal.canonical_sha256()},
            )
        ).one()
        assert row.seal_json == seal.canonical_json()
    with pytest.raises(DBAPIError, match="gate3_prereg_claim_ack_missing"):
        await append_pin(pin_engine, pin_record(seal, old_schedule))
    observer = Gate3PreregistrationSealObserver(
        async_sessionmaker(observer_engine, expire_on_commit=False)
    )
    witnessed = await observer.read_seal(
        expected_seal_sha256=seal.canonical_sha256(),
        expected_coordinate_plan_sha256=old_schedule.coordinate_plan.canonical_sha256(),
        coordinate_plan=old_schedule.coordinate_plan,
    )
    assert witnessed.seal.canonical_json() == seal.canonical_json()
    accepted = await observer.acknowledge(
        expected_seal_sha256=seal.canonical_sha256(),
        expected_coordinate_plan_sha256=old_schedule.coordinate_plan.canonical_sha256(),
        coordinate_plan=old_schedule.coordinate_plan,
    )
    assert accepted.acknowledged_at >= witnessed.seal_recorded_at
    with pytest.raises(DBAPIError, match="uq_gate3_preregistration_seal_acks"):
        async with observer_engine.begin() as connection:
            await connection.execute(
                text("SELECT public.gate3_prereg_seal_ack_append(:sha)"),
                {"sha": alternate.canonical_sha256()},
            )
    planned_at = accepted.database_readback_at
    # The old, backdated synthetic schedule cannot be used to retrofit a
    # commitment. Build a new exact schedule after observing the ACK commit.
    schedule = build_capture_schedule(
        seal=seal,
        coordinate_plan=old_schedule.coordinate_plan,
        planned_at=planned_at,
    )
    pinned = await publish_or_diagnose(pin_repo, schedule, seal)
    acknowledged = await pin_ack_repo.acknowledge(
        expected_schedule_sha256=schedule.canonical_sha256(), seal=seal
    )
    assert pinned.schedule_sha256 == schedule.canonical_sha256()
    assert acknowledged.schedule_sha256 == pinned.schedule_sha256
    assert pinned.predictive_oos_eligible is False
    assert pinned.execution_authority is False
    with pytest.raises(DBAPIError, match="gate3_prereg_downgrade_requires_empty"):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0030", "downgrade")


@pytest.mark.parametrize("isolated_prereg_database", ["legacy"], indirect=True)
async def test_old_claim_and_ack_never_become_seal_proof(isolated_prereg_database):
    (
        seal,
        old_schedule,
        legacy_sha,
        db_engine,
        seal_engine,
        observer_engine,
        _,
        pin_repo,
        pin_ack_repo,
    ) = isolated_prereg_database
    assert legacy_sha == old_schedule.canonical_sha256()
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_key_claims")
            )
            == 1
        )
    with pytest.raises(Gate3SchedulePinError, match="missing_or_duplicate"):
        await pin_repo.read(expected_schedule_sha256=legacy_sha, seal=seal)
    with pytest.raises(Gate3SchedulePublicationAckError):
        await pin_ack_repo.read(expected_schedule_sha256=legacy_sha, seal=seal)
    # No 0030 seal exists yet: an old 0029 claim alone must prevent a
    # downgrade that would re-enable its earlier, seal-unaware reader.
    with pytest.raises(DBAPIError, match="gate3_prereg_downgrade_requires_empty"):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0030", "downgrade")
    with pytest.raises(Gate3SchedulePinError, match="missing_or_duplicate"):
        await pin_repo.read(expected_schedule_sha256=legacy_sha, seal=seal)
    await append_seal(seal_engine, seal_record(seal, old_schedule))
    async with observer_engine.begin() as connection:
        await connection.execute(
            text("SELECT public.gate3_prereg_seal_ack_append(:sha)"),
            {"sha": seal.canonical_sha256()},
        )
    # A later valid seal/ACK cannot retroactively certify the already
    # committed 0029 schedule: its planned_at predates this new ACK.
    with pytest.raises(Gate3SchedulePinError, match="missing_or_duplicate"):
        await pin_repo.read(expected_schedule_sha256=legacy_sha, seal=seal)
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_claim_acks")
            )
            == 1
        )


async def test_direct_dual_grant_cannot_publish_a_claim(isolated_prereg_database):
    (
        seal,
        schedule,
        _,
        db_engine,
        _,
        _,
        pin_engine,
        _,
        _,
    ) = isolated_prereg_database
    role = pin_engine.url.username
    assert role is not None and re.fullmatch(r"ctcc_g3_[a-f0-9]{18}", role)
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "GRANT EXECUTE ON FUNCTION public.gate3_prereg_seal_append(jsonb) "
                f"TO {role}"
            )
        )
        await connection.execute(
            text(
                "GRANT EXECUTE ON FUNCTION public.gate3_prereg_seal_read(text) "
                f"TO {role}"
            )
        )
    with pytest.raises(DBAPIError, match="gate3_prereg_capture_role_denied"):
        await append_pin(pin_engine, pin_record(seal, schedule))
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_pins")
            )
            == 0
        )


async def test_observer_refuses_malformed_proposal_before_ack(isolated_prereg_database):
    (
        seal,
        schedule,
        _,
        db_engine,
        seal_engine,
        observer_engine,
        pin_engine,
        _,
        _,
    ) = isolated_prereg_database
    malformed = json.loads(seal.canonical_json())
    malformed["candidate"] = {}
    malformed_json = json.dumps(
        malformed, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    malformed_sha = hashlib.sha256(malformed_json.encode("utf-8")).hexdigest()
    record = seal_record(seal, schedule)
    record.update(seal_sha256=malformed_sha, seal_json=malformed_json)
    await append_seal(seal_engine, record)
    observer = Gate3PreregistrationSealObserver(
        async_sessionmaker(observer_engine, expire_on_commit=False)
    )
    with pytest.raises(Gate3CommittedSealError, match="prereg_seal_read_unavailable"):
        await observer.acknowledge(
            expected_seal_sha256=malformed_sha,
            expected_coordinate_plan_sha256=schedule.coordinate_plan.canonical_sha256(),
            coordinate_plan=schedule.coordinate_plan,
        )
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_preregistration_seal_acks")
            )
            == 0
        )
    claim = pin_record(seal, schedule)
    claim_json = json.loads(claim["schedule_json"])
    claim_json["preregistration_sha256"] = malformed_sha
    schedule_json = json.dumps(
        claim_json, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    claim.update(
        seal_sha256=malformed_sha,
        schedule_json=schedule_json,
        schedule_sha256=hashlib.sha256(schedule_json.encode("utf-8")).hexdigest(),
    )
    with pytest.raises(DBAPIError, match="gate3_prereg_claim_ack_missing"):
        await append_pin(pin_engine, claim)
