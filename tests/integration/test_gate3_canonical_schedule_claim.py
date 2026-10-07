"""Disposable PostgreSQL acceptance for the 0029 canonical key boundary.

An explicit isolated DATABASE_URL is required. This runs the narrow 0024,
0026, 0028, 0029 DDL sequence; full-chain migration acceptance is separate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import timedelta
from types import SimpleNamespace
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
from app.mie.validation.prospective_capture_schedule import build_capture_schedule
from tests.durable_migration_fixtures import load_migration
from tests.unit.mie.test_gate3_capture_schedule import valid_schedule

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def migrate(connection, revision: str, direction: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = load_migration(revision)
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, direction)()


def pin_record(seal, schedule) -> dict[str, str]:
    coordinate = schedule.coordinate_plan
    return {
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


async def raw_pin(connection, record) -> None:
    await connection.execute(
        text("SELECT public.gate3_capture_schedule_append(CAST(:record AS jsonb))"),
        {"record": json.dumps(record, separators=(",", ":"), sort_keys=True)},
    )


def safe_schedule_db_diagnostic(error: DBAPIError) -> str:
    """Return bounded PostgreSQL metadata, never SQL, parameters or error text."""
    sqlstate = constraint = failure_code = function = line = column = "unknown"
    source = error.orig
    seen: set[int] = set()
    for _ in range(4):
        if source is None or id(source) in seen:
            break
        seen.add(id(source))
        candidate_state = getattr(source, "sqlstate", None)
        if isinstance(candidate_state, str) and re.fullmatch(
            r"[0-9A-Z]{5}", candidate_state
        ):
            sqlstate = candidate_state
        diagnostic = getattr(source, "diag", None)
        candidate_constraint = getattr(diagnostic, "constraint_name", None)
        if isinstance(candidate_constraint, str) and re.fullmatch(
            r"[a-z][a-z0-9_]{0,127}", candidate_constraint
        ):
            constraint = candidate_constraint
        candidate_column = getattr(diagnostic, "column_name", None) or getattr(
            source, "column_name", None
        )
        if isinstance(candidate_column, str) and re.fullmatch(
            r"[a-z][a-z0-9_]{0,127}", candidate_column
        ):
            column = candidate_column
        for candidate_message in (
            getattr(diagnostic, "message_primary", None),
            getattr(source, "message", None),
        ):
            if not isinstance(candidate_message, str):
                continue
            if re.fullmatch(r"gate3_[a-z0-9_]+", candidate_message):
                failure_code = candidate_message
            if sqlstate == "42703":
                for pattern in (
                    r'column "(?P<column>[a-z][a-z0-9_]{0,127})" does not exist',
                    r"column [a-z][a-z0-9_]{0,127}\.(?P<column>[a-z][a-z0-9_]{0,127}) does not exist",
                    r'record "[a-z][a-z0-9_]{0,127}" has no field "(?P<column>[a-z][a-z0-9_]{0,127})"',
                ):
                    match = re.fullmatch(pattern, candidate_message)
                    if match:
                        column = match.group("column")
                        break
        for candidate_context in (
            getattr(diagnostic, "context", None),
            getattr(source, "context", None),
        ):
            if not isinstance(candidate_context, str):
                continue
            matches = re.findall(
                r"PL/pgSQL function (?:public\.)?(gate3_[a-z0-9_]{1,127})"
                r"\([^()\r\n]{0,120}\) line ([1-9][0-9]{0,4}) at ",
                candidate_context,
            )
            if matches:
                function, line = matches[0]
        source = getattr(source, "__cause__", None)
    return (
        f"sqlstate={sqlstate} constraint={constraint} code={failure_code} "
        f"function={function} line={line} column={column}"
    )


async def test_safe_schedule_db_diagnostic_never_echoes_context_or_parameters():
    private_context = "synthetic-private-sql-and-parameters"
    original = SimpleNamespace(
        sqlstate="42703",
        message='column "recorded_at" does not exist',
        context=(
            f'SQL statement "{private_context}"\n'
            "PL/pgSQL function public.gate3_schedule_pin_claim_after_insert() "
            "line 78 at SQL statement"
        ),
        __cause__=None,
    )
    diagnostic = safe_schedule_db_diagnostic(SimpleNamespace(orig=original))
    assert diagnostic == (
        "sqlstate=42703 constraint=unknown code=unknown "
        "function=gate3_schedule_pin_claim_after_insert line=78 column=recorded_at"
    )
    assert private_context not in diagnostic


async def publish_or_diagnose(pin_repo, schedule, seal):
    """Expose only a synthetic, disposable DB failure code in CI diagnostics.

    Production deliberately redacts the SQL error. If that boundary rejects an
    otherwise expected test publication, repeat the same synthetic append in
    a new transaction and report only allowlisted PostgreSQL metadata. Never
    include the exception text, parameters, or connection URL.
    """
    try:
        return await pin_repo.publish(schedule=schedule, seal=seal)
    except Gate3SchedulePinError as error:
        if str(error) != "schedule_publish_rejected":
            raise
    try:
        async with pin_repo.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "SELECT public.gate3_capture_schedule_claim_append("
                    "CAST(:record AS jsonb))"
                ),
                {"record": json.dumps(pin_record(seal, schedule))},
            )
    except DBAPIError as error:
        pytest.fail(
            f"synthetic_schedule_publish_rejected {safe_schedule_db_diagnostic(error)}",
            pytrace=False,
        )
    except Exception:  # noqa: BLE001 - Keep diagnostic details redacted.
        pytest.fail("synthetic_schedule_diagnostic_unavailable", pytrace=False)
    pytest.fail("synthetic_schedule_retry_accepted_after_rejection", pytrace=False)


def numeric_holdout_record(seal, schedule) -> dict[str, str]:
    """A byte-canonical JSON record with a non-Pydantic holdout type."""
    record = pin_record(seal, schedule)
    coordinate = json.loads(record["coordinate_plan_json"])
    coordinate["holdout_id"] = 123
    coordinate_json = json.dumps(coordinate, separators=(",", ":"), sort_keys=True)
    schedule_payload = json.loads(record["schedule_json"])
    schedule_payload["coordinate_plan"] = coordinate
    schedule_payload["coordinate_plan_sha256"] = hashlib.sha256(
        coordinate_json.encode()
    ).hexdigest()
    schedule_json = json.dumps(schedule_payload, separators=(",", ":"), sort_keys=True)
    record.update(
        schedule_sha256=hashlib.sha256(schedule_json.encode()).hexdigest(),
        coordinate_plan_sha256=schedule_payload["coordinate_plan_sha256"],
        holdout_id="123",
        coordinate_plan_json=coordinate_json,
        schedule_json=schedule_json,
    )
    return record


async def raw_legacy_ack(connection, record) -> None:
    await connection.execute(
        text("""
          SELECT public.gate3_capture_schedule_ack_append(
            :schedule_sha,:seal_sha,:coordinate_sha,:window_key,:holdout_id)
        """),
        {
            "schedule_sha": record["schedule_sha256"],
            "seal_sha": record["seal_sha256"],
            "coordinate_sha": record["coordinate_plan_sha256"],
            "window_key": record["window_key"],
            "holdout_id": record["holdout_id"],
        },
    )


@pytest.fixture
async def isolated_claim_database(request):
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit isolated DATABASE_URL required")
    database = "ctcc_g3_claim_" + uuid4().hex
    roles = ["ctcc_g3_" + uuid4().hex[:18] for _ in range(4)]
    assert re.fullmatch(r"ctcc_g3_claim_[a-f0-9]{32}", database)
    assert all(re.fullmatch(r"ctcc_g3_[a-f0-9]{18}", role) for role in roles)
    legacy_pin_role, legacy_ack_role, pin_role, ack_role = roles
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
            for revision in ("0024", "0026", "0028"):
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
            for function in (
                "gate3_capture_schedule_append(jsonb)",
                "gate3_capture_schedule_read(text)",
            ):
                await connection.execute(
                    text(
                        f"GRANT EXECUTE ON FUNCTION public.{function} TO {legacy_pin_role}"
                    )
                )
            for function in (
                "gate3_capture_schedule_ack_append(text,text,text,text,text)",
                "gate3_capture_schedule_ack_read(text)",
            ):
                await connection.execute(
                    text(
                        f"GRANT EXECUTE ON FUNCTION public.{function} TO {legacy_ack_role}"
                    )
                )
        legacy_pin_engine = create_async_engine(
            db_url.set(username=legacy_pin_role, password=None), poolclass=NullPool
        )
        legacy_ack_engine = create_async_engine(
            db_url.set(username=legacy_ack_role, password=None), poolclass=NullPool
        )
        role_engines.extend((legacy_pin_engine, legacy_ack_engine))
        seal, schedule = valid_schedule()
        poison = pin_record(seal, schedule)
        mode = getattr(request, "param", "noncanonical")
        if mode == "noncanonical":
            raw_schedule = json.dumps(
                json.loads(poison["schedule_json"]), indent=2, sort_keys=True
            )
        elif mode == "unknown":
            expanded = json.loads(poison["schedule_json"])
            expanded["unrecognized_legacy_field"] = "reserved"
            raw_schedule = json.dumps(
                expanded, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
        elif mode == "canonical":
            raw_schedule = poison["schedule_json"]
        else:
            raise AssertionError("unsupported legacy fixture classification")
        poison["schedule_json"] = raw_schedule
        poison["schedule_sha256"] = hashlib.sha256(raw_schedule.encode()).hexdigest()
        async with legacy_pin_engine.begin() as connection:
            await raw_pin(connection, poison)
        async with legacy_ack_engine.begin() as connection:
            await raw_legacy_ack(connection, poison)

        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0029", "upgrade")
            await connection.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION "
                    f"public.gate3_capture_schedule_claim_append(jsonb) TO {pin_role}"
                )
            )
            await connection.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION "
                    f"public.gate3_capture_schedule_claim_read(text) TO {pin_role}"
                )
            )
            for function in (
                "gate3_capture_schedule_claim_ack_append(text,text,text,text,text)",
                "gate3_capture_schedule_claim_ack_read(text)",
            ):
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {ack_role}")
                )
        pin_engine = create_async_engine(
            db_url.set(username=pin_role, password=None), poolclass=NullPool
        )
        ack_engine = create_async_engine(
            db_url.set(username=ack_role, password=None), poolclass=NullPool
        )
        role_engines.extend((pin_engine, ack_engine))
        pin_repo = Gate3CaptureSchedulePinRepository(
            async_sessionmaker(pin_engine, expire_on_commit=False)
        )
        ack_repo = Gate3CaptureSchedulePublicationAckRepository(
            async_sessionmaker(ack_engine, expire_on_commit=False), pin_repo
        )
        await pin_repo.verify_role()
        await ack_repo.verify_role()
        yield (
            seal,
            schedule,
            poison,
            db_engine,
            pin_repo,
            ack_repo,
            legacy_pin_engine,
            legacy_ack_engine,
        )
    except DBAPIError as error:
        pytest.fail(
            f"synthetic_schedule_fixture_rejected {safe_schedule_db_diagnostic(error)}",
            pytrace=False,
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


async def test_poisoned_raw_keys_do_not_block_canonical_retry_or_claim_ack(
    isolated_claim_database,
):
    (
        seal,
        schedule,
        poison,
        db_engine,
        pin_repo,
        ack_repo,
        legacy_pin_engine,
        legacy_ack_engine,
    ) = isolated_claim_database
    legacy_pin_repo = Gate3CaptureSchedulePinRepository(
        async_sessionmaker(legacy_pin_engine, expire_on_commit=False)
    )
    legacy_ack_repo = Gate3CaptureSchedulePublicationAckRepository(
        async_sessionmaker(legacy_ack_engine, expire_on_commit=False), pin_repo
    )
    with pytest.raises(
        Gate3SchedulePinError, match="restricted_schedule_role_required"
    ):
        await legacy_pin_repo.verify_role()
    with pytest.raises(
        Gate3SchedulePublicationAckError,
        match="restricted_schedule_ack_role_required",
    ):
        await legacy_ack_repo.verify_role()
    async with legacy_pin_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_read(:sha)"),
                {"sha": poison["schedule_sha256"]},
            )
        ) == 0
    async with legacy_ack_engine.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM public.gate3_capture_schedule_ack_read(:sha)"
                ),
                {"sha": poison["schedule_sha256"]},
            )
        ) == 0
    with pytest.raises(DBAPIError, match="gate3_schedule_legacy_ack_closed"):
        async with legacy_ack_engine.begin() as connection:
            await raw_legacy_ack(connection, poison)
    # An old publisher login retains its EXECUTE grant on the 0026 function
    # OID. It must not create a new raw pin or a canonical key claim.
    with pytest.raises(DBAPIError, match="gate3_schedule_legacy_append_closed"):
        async with legacy_pin_engine.begin() as connection:
            await raw_pin(connection, pin_record(seal, schedule))
    # Even a mistaken grant of the new entrypoint to the old login cannot
    # bypass the trigger. This also covers an old function body that was
    # already running before cutover and reaches INSERT afterward.
    legacy_role = legacy_pin_engine.url.username
    assert legacy_role is not None
    assert re.fullmatch(r"ctcc_g3_[a-f0-9]{18}", legacy_role)
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "GRANT EXECUTE ON FUNCTION "
                "public.gate3_capture_schedule_claim_append(jsonb) "
                f"TO {legacy_role}"
            )
        )
    with pytest.raises(DBAPIError, match="gate3_schedule_claim_role_denied"):
        async with legacy_pin_engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT public.gate3_capture_schedule_claim_append("
                    "CAST(:record AS jsonb))"
                ),
                {"record": json.dumps(pin_record(seal, schedule))},
            )
    # A record can have stable JSON bytes and valid 0026 hashes while carrying
    # a number where the Python V1 schema requires a string. It cannot claim.
    with pytest.raises(DBAPIError, match="gate3_schedule_claim_noncanonical"):
        async with pin_repo.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "SELECT public.gate3_capture_schedule_claim_append("
                    "CAST(:record AS jsonb))"
                ),
                {"record": json.dumps(numeric_holdout_record(seal, schedule))},
            )
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_key_claims")
            )
            == 0
        )
    with pytest.raises(
        Gate3SchedulePinError, match="schedule_pin_missing_or_duplicate"
    ):
        await pin_repo.read(
            expected_schedule_sha256=poison["schedule_sha256"], seal=seal
        )

    # The SQL serializer must match both Python canonical encoders, including
    # six-digit UTC timestamps. This is a PostgreSQL assertion, not an offline
    # string check over the migration source.
    async with db_engine.connect() as connection:
        parity = (
            await connection.execute(
                text("""
                  SELECT public.gate3_schedule_canonical_jsonb(
                           CAST(:schedule_payload AS jsonb)) =
                           CAST(:schedule_text AS text)
                           AS schedule,
                         public.gate3_schedule_canonical_jsonb(
                           CAST(:coordinate_payload AS jsonb)) =
                           CAST(:coordinate_text AS text)
                           AS coordinate
                """),
                {
                    "schedule_payload": schedule.canonical_json(),
                    "schedule_text": schedule.canonical_json(),
                    "coordinate_payload": schedule.coordinate_plan.canonical_json(),
                    "coordinate_text": schedule.coordinate_plan.canonical_json(),
                },
            )
        ).one()
        assert parity.schedule and parity.coordinate
        classification = await connection.scalar(
            text("""
              SELECT classification FROM
                public.gate3_capture_schedule_legacy_inventory
              WHERE schedule_sha256=:sha
            """),
            {"sha": poison["schedule_sha256"]},
        )
        assert classification == "noncanonical"

    pin = await publish_or_diagnose(pin_repo, schedule, seal)
    assert pin.schedule_sha256 == schedule.canonical_sha256()
    ack = await ack_repo.acknowledge(
        expected_schedule_sha256=pin.schedule_sha256, seal=seal
    )
    assert ack.schedule_sha256 == pin.schedule_sha256
    assert pin.recorded_at <= ack.acknowledged_at < schedule.coordinate_plan.start_at
    async with db_engine.connect() as connection:
        raw = (
            (
                await connection.execute(
                    text("""
                  SELECT schedule_sha256 FROM public.gate3_capture_schedule_pins
                  ORDER BY schedule_sha256
                """)
                )
            )
            .scalars()
            .all()
        )
        assert raw == sorted((poison["schedule_sha256"], pin.schedule_sha256))
        old_ack = await connection.scalar(
            text("SELECT count(*) FROM public.gate3_capture_schedule_publication_acks")
        )
        assert old_ack == 1
        claim = (
            await connection.execute(
                text("""
                  SELECT schedule_sha256,claimed_at FROM
                    public.gate3_capture_schedule_key_claims
                """)
            )
        ).one()
        assert claim.schedule_sha256 == pin.schedule_sha256
        assert claim.claimed_at <= ack.acknowledged_at
        new_ack = await connection.scalar(
            text("SELECT count(*) FROM public.gate3_capture_schedule_claim_acks")
        )
        assert new_ack == 1
    with pytest.raises(
        DBAPIError, match="gate3_schedule_claim_downgrade_requires_empty"
    ):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0029", "downgrade")


@pytest.mark.parametrize(
    "isolated_claim_database", ("canonical", "unknown"), indirect=True
)
async def test_legacy_canonical_or_unknown_keys_remain_reserved(
    isolated_claim_database,
):
    (
        seal,
        schedule,
        legacy,
        db_engine,
        pin_repo,
        ack_repo,
        _,
        _,
    ) = isolated_claim_database
    async with db_engine.connect() as connection:
        classification = await connection.scalar(
            text("""
              SELECT classification FROM
                public.gate3_capture_schedule_legacy_inventory
              WHERE schedule_sha256=:sha
            """),
            {"sha": legacy["schedule_sha256"]},
        )
    expected = (
        "canonical"
        if legacy["schedule_sha256"] == schedule.canonical_sha256()
        else "unknown"
    )
    assert classification == expected
    with pytest.raises(
        Gate3SchedulePublicationAckError, match="schedule_pin_replay_failed"
    ):
        await ack_repo.read(
            expected_schedule_sha256=legacy["schedule_sha256"], seal=seal
        )
    with pytest.raises(Gate3SchedulePinError):
        await pin_repo.publish(schedule=schedule, seal=seal)
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_key_claims")
            )
            == 0
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_pins")
            )
            == 1
        )


async def test_concurrent_canonical_schedules_get_at_most_one_claim(
    isolated_claim_database,
):
    seal, schedule, _, db_engine, pin_repo, _, _, _ = isolated_claim_database
    alternate = build_capture_schedule(
        seal=seal,
        coordinate_plan=schedule.coordinate_plan,
        planned_at=schedule.planned_at + timedelta(seconds=1),
    )
    assert alternate.canonical_sha256() != schedule.canonical_sha256()
    attempts = await asyncio.gather(
        pin_repo.publish(schedule=schedule, seal=seal),
        pin_repo.publish(schedule=alternate, seal=seal),
        return_exceptions=True,
    )
    if all(isinstance(item, Exception) for item in attempts):
        await publish_or_diagnose(pin_repo, schedule, seal)
        pytest.fail("synthetic_concurrent_claims_rejected_before_retry", pytrace=False)
    assert sum(not isinstance(item, Exception) for item in attempts) == 1
    assert sum(isinstance(item, Gate3SchedulePinError) for item in attempts) == 1
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_key_claims")
            )
            == 1
        )
