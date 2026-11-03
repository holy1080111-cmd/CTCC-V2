"""SOURCE-ONLY PG0053 mechanisms; every account/source input is synthetic.

Real frozen migrations, native catalog, restricted logins, row/UID locks,
INSERT/commit and independent ORM/XID readback are exercised. The explicit
owner-handoff seam below does NOT authenticate CURRENT/TLS/credentials or issue
an account revision. No source-owner, raw-reader or trading authority is earned.
"""

import asyncio
import copy
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.database.models.account_capture_journal import DemoAccountCaptureEvent
from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage as Lineage,
)
from app.database.repositories import account_revision_collector_lineage as repository
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import account_revision_collector_lineage_schema_v1 as schema
from app.domain import account_revision_collector_lineage_v1 as policy
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import utc_from_ns
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import reservations
from app.trade_qualification.reservations import LedgerScope
from scripts import current_contract_source_scopes_v1 as source_scopes
from tests.controlled_writer_coverage_fixtures import seed_controlled_writer_control
from tests.original_frame_portable_pg_profile import (
    create_portable_reader_role,
    parse_portable_url,
    portable_profile,
    verify_portable_server,
    verify_portable_url,
)
from tests.unit import test_account_current_source_v7 as wire_fixture
from tests.unit.test_qualification_account_capture import NOW, UID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
# These are prerequisite permissions of the existing ledger/control/B1 reader.
# Only the final entry is the NEW 0053 role grant. No bundle-table or 0052
# original-frame raw-reader permission is granted to either role.
_BASELINE_APP_GRANTS = (
    ("qualification_account_scopes", "SELECT,UPDATE"),
    ("qualification_reservations", "SELECT"),
    ("demo_account_controls", "SELECT,UPDATE"),
    ("demo_control_journal", "SELECT"),
    ("demo_account_capture_events", "SELECT"),
)
_LINEAGE_GRANT = ("demo_collector_current_revision_lineages", "SELECT,INSERT")
_CUSTODY_SIGNATURES = ("append(text,text,text,bytea)", "read(text)")


def _now():
    return datetime.now(UTC)


def _migrate(connection, target="0053", direction="upgrade"):
    assert (target, direction) in {("0052", "downgrade"), ("0053", "upgrade")}
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    scope_reader = (
        source_scopes.historical0052_source_scope
        if target == "0052"
        else source_scopes.historical0053_source_scope
    )
    source_scope = scope_reader(ROOT)
    assert scripts.get_current_head() == source_scope.actual_source_head
    resolver = (
        scripts._upgrade_revs if direction == "upgrade" else scripts._downgrade_revs
    )
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: resolver(target, heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()
    source_scopes.verify_database_scope(connection, ROOT, source_scope)


@pytest.fixture
async def lineage_database():
    """Fresh strict0053 DB: explicit isolated Docker or Root-owned portable PG17."""
    raw = os.environ.get("DATABASE_URL", "")
    assert raw, "lineage_explicit_isolated_profile_required"
    profile = portable_profile(ROOT)
    if profile is None:
        url = make_url(raw)
        assert url.drivername == "postgresql+asyncpg"
        assert re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
        assert url.username == "ctcc" and url.password is None
        assert url.port == 5432 and url.database == "ctcc" and not url.query
    else:
        url = parse_portable_url(raw)
        verify_portable_url(url, profile)
    primary = create_async_engine(url, poolclass=NullPool)
    name = "ctcc_original_frame_" + uuid4().hex
    admin = app = retained = None
    created, roles = False, []
    try:
        async with primary.connect() as connection:
            if profile is not None:
                await verify_portable_server(connection, url, profile)
            assert (
                170000
                <= await connection.scalar(
                    text("SELECT current_setting('server_version_num')::int")
                )
                < 180000
            )
        async with primary.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            if profile is not None:
                await verify_portable_server(connection, url, profile)
            assert re.fullmatch(r"ctcc_original_frame_[a-f0-9]{32}", name)
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        admin = create_async_engine(url.set(database=name), poolclass=NullPool)
        async with admin.begin() as connection:
            await connection.run_sync(_migrate)
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0053"
            )
            assert not await connection.scalar(
                text("""
                SELECT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL
                  aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
                  WHERE c.oid='public.demo_collector_current_revision_lineages'::regclass
                    AND x.grantee<>c.relowner)
            """)
            )
            for kind in ("app", "custody"):
                role = "ctcc_raw_frame_" + uuid4().hex
                if profile is None:
                    await connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
                    password = None
                else:
                    password = await create_portable_reader_role(connection, role)
                roles.append((kind, role, password))
                await connection.execute(
                    text(f'GRANT USAGE ON SCHEMA public TO "{role}"')
                )
                # Exact schema-version metadata only, never raw custody table access.
                await connection.execute(
                    text(f'GRANT SELECT ON public.alembic_version TO "{role}"')
                )
                if kind == "app":
                    for table, privileges in (*_BASELINE_APP_GRANTS, _LINEAGE_GRANT):
                        await connection.execute(
                            text(f'GRANT {privileges} ON public.{table} TO "{role}"')
                        )
                else:
                    for signature in _CUSTODY_SIGNATURES:
                        await connection.execute(
                            text(
                                "GRANT EXECUTE ON FUNCTION public.public_runtime_bundle_v1_"
                                + signature
                                + f' TO "{role}"'
                            )
                        )
        engines = {}
        for kind, role, password in roles:
            engines[kind] = create_async_engine(
                url.set(database=name, username=role, password=password),
                poolclass=NullPool,
            )
            if kind == "app":
                app = engines[kind]
            else:
                retained = engines[kind]
        factory = async_sessionmaker(app, expire_on_commit=False, autoflush=False)
        retained_factory = async_sessionmaker(
            retained, expire_on_commit=False, autoflush=False
        )
        yield SimpleNamespace(
            admin=admin,
            app=app,
            retained=retained,
            factory=factory,
            custody=custody.RuntimeBundleCustodyRepository(retained_factory),
            app_role=next(role for kind, role, _ in roles if kind == "app"),
        )
    finally:
        for engine in (app, retained, admin):
            if engine is not None:
                await engine.dispose()
        if created:
            async with primary.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                if profile is not None:
                    await verify_portable_server(connection, url, profile)
                assert (
                    await connection.scalar(text("SELECT current_database()")) == "ctcc"
                )
                assert re.fullmatch(r"ctcc_original_frame_[a-f0-9]{32}", name)
                await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        if roles:
            async with primary.begin() as connection:
                for _, role, _ in reversed(roles):
                    assert re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", role)
                    await connection.execute(text(f'DROP ROLE "{role}"'))
        await primary.dispose()


def _shift_declared_source(value, offset):
    if type(value) is list:
        return [_shift_declared_source(item, offset) for item in value]
    if type(value) is dict:
        return {
            key: str(int(item) + offset)
            if key in {"ts", "cTime", "uTime", "fillTime"}
            and type(item) is str
            and item.isdigit()
            else _shift_declared_source(item, offset)
            for key, item in value.items()
        }
    return value


async def _wire_source(monkeypatch):
    # All timestamp choices occur BEFORE this new synthetic capture. The old
    # source/fixture bytes remain untouched. No old chain is re-signed/relabelled.
    anchor = (_now() - timedelta(seconds=5)).replace(microsecond=0)
    offset = int((anchor - NOW).total_seconds() * 1000)
    pages = _shift_declared_source(wire_fixture.empty_v7_pages(), offset)
    with monkeypatch.context() as synthetic:
        synthetic.setattr(wire_fixture, "NOW", anchor)
        chain, harness = await wire_fixture.recorded_v7(
            synthetic,
            pages=pages,
            plan_changes={
                "created_at": anchor,
                "history_start": anchor - timedelta(days=7),
                "history_end": anchor - timedelta(seconds=1),
            },
        )
        assert harness.requests and all(
            request.method == "GET" for request in harness.requests
        )
    reference = observed.source_reference(chain)
    packet = lifecycle._packet(chain, reference)
    assert packet.plan.contract_version == "ctcc.demo_current_account_plan.v7"
    return chain, reference, packet


async def _store_bundle(lab, chain, reference, *, wrong_original_bytes=False):
    rows = [
        {
            "event_hex": row.event.event_json.hex(),
            "raw_hex": None if row.event.raw_body is None else row.event.raw_body.hex(),
            "packet_hex": None
            if row.event.packet_payload is None
            else row.event.packet_payload.hex(),
            "db_recorded_at": row.db_recorded_at.isoformat(),
            "readback_at": row.readback_at.isoformat(),
        }
        for row in chain
    ]
    if wrong_original_bytes:
        item = next(row for row in rows if row["raw_hex"] is not None)
        original = bytes.fromhex(item["raw_hex"])
        item["raw_hex"] = (b"X" + original[1:]).hex()
    envelope = {
        "source_reference": observed.reference_document(reference),
        "native_proof_sha256": "3" * 64,
        "native_readback_sha256": "4" * 64,
    }
    # Explicit synthetic owner seam: this is NOT a native replayable V4 bundle.
    # DB0046 retains generic bytes; the 0053 SQL guard reopens exact B1 binding.
    raw = journal.canonical(
        {
            "schema_version": "ctcc.declared_lineage_pg_fixture.v1",
            "policy_sha256": current.POLICY_SHA256,
            "proof_envelope": envelope,
            "chain": rows,
        }
    )
    proof_pin = journal.digest(journal.canonical(envelope))
    await lab.custody.verify_deployment()
    receipt = await lab.custody.seal_bundle(
        bundle_raw=raw,
        expected_sha256=journal.digest(raw),
        proof_envelope_sha256=proof_pin,
    )
    readback = custody._consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=journal.digest(raw),
        expected_proof_envelope_sha256=proof_pin,
    )
    return raw, proof_pin, readback


async def _inputs(monkeypatch, lab):
    chain, reference, packet = await _wire_source(monkeypatch)
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    administrator = async_sessionmaker(
        lab.admin, expire_on_commit=False, autoflush=False
    )
    ledger = QualificationLedgerRepository(administrator, clock=_now)
    checkpoint = await ledger.initialize_capture_scope(scope)
    first = journal.checked_event(chain[0].event)
    assert first["data"]["local_checkpoint_sha256"] == reservations.digest(
        checkpoint.state
    )
    async with administrator() as session, session.begin():
        await seed_controlled_writer_control(session, uid=UID)
        total = 0
        for row in chain:
            item = journal.checked_event(row.event)
            total += (
                len(row.event.event_json)
                + len(row.event.raw_body or b"")
                + len(row.event.packet_payload or b"")
            )
            session.add(
                DemoAccountCaptureEvent(
                    capture_id=reference.capture_id,
                    sequence=item["sequence"],
                    environment=scope.environment,
                    account_id=scope.account_id,
                    settlement_currency=scope.settlement_currency,
                    previous_sha256=item["previous_sha256"],
                    chain_bytes=total,
                    event_sha256=journal.digest(row.event.event_json),
                    event_json=row.event.event_json.decode(),
                    raw_body=row.event.raw_body,
                    packet_payload=row.event.packet_payload,
                    db_recorded_at=row.db_recorded_at,
                )
            )
    raw, proof_pin, retained = await _store_bundle(lab, chain, reference)
    sample = native_stamp()
    expires = utc_from_ns(sample["utc_ns"]) + timedelta(seconds=30)
    replay = SimpleNamespace(
        packet=packet,
        reference=reference,
        proof_envelope_sha256=proof_pin,
        native_proof_sha256="3" * 64,
        native_readback_sha256="4" * 64,
        expires_at=expires,
        monotonic_deadline_ns=sample["monotonic_ns"] + 30_000_000_000,
    )
    state = {
        "lineage_factory": lab.factory,
        "lineage_custody": lab.custody,
        "bundle_raw": raw,
        "bundle_sha256": journal.digest(raw),
        "custody_id": retained.bundle_id,
    }
    token, task, taken = object(), asyncio.current_task(), []

    def take(source, *, key):
        assert source is token and key is repository._SOURCE_KEY
        assert asyncio.current_task() is task and not taken
        taken.append(True)
        return state

    def owner(actual):
        assert actual is state and actual and asyncio.current_task() is task
        stamp = native_stamp()
        assert stamp["utc_ns"] < sample["utc_ns"] + 30_000_000_000
        assert stamp["monotonic_ns"] < replay.monotonic_deadline_ns
        return replay, stamp

    # These TWO source-authority seams are the whole substitution list.
    # Do not replace catalog, SQL guards, custody, _metadata, _locked, ORM or IO.
    monkeypatch.setattr(current, "_take_account_revision_lineage_v1", take)
    monkeypatch.setattr(repository, "_owner", owner)
    return SimpleNamespace(
        chain=chain,
        reference=reference,
        packet=packet,
        scope=scope,
        state=state,
        token=token,
        replay=replay,
        taken=taken,
        owner=owner,
        lab=lab,
    )


async def _count(lab):
    async with lab.admin.connect() as connection:
        return await connection.scalar(
            text("SELECT count(*) FROM public.demo_collector_current_revision_lineages")
        )


async def _candidate(session, item):
    replay, stamp = item.owner(item.state)
    await repository._admit(session, replay, stamp)
    ledger = QualificationLedgerRepository(item.lab.factory, clock=_now)
    row, control, control_pin = await repository._locked(session, ledger, item.scope)
    metadata = await repository._metadata(session)
    replay, stamp = item.owner(item.state)
    document = {
        "schema_version": policy.SCHEMA,
        "scope": [
            item.scope.environment,
            item.scope.account_id,
            item.scope.settlement_currency,
        ],
        "source_reference": observed.reference_document(item.reference),
        "source_bundle_sha256": item.state["bundle_sha256"],
        "source_policy_sha256": current.POLICY_SHA256,
        "proof_envelope_sha256": replay.proof_envelope_sha256,
        "native_proof_sha256": replay.native_proof_sha256,
        "native_readback_sha256": replay.native_readback_sha256,
        "custody_id": item.state["custody_id"],
        "owner_sampled_at": utc_from_ns(stamp["utc_ns"]).isoformat(),
        "original_expires_at": replay.expires_at.isoformat(),
        "packet_completed_at": item.packet.completed_at.isoformat(),
        "account_revision": row.account_revision,
        "ledger_revision": row.ledger_revision,
        "claims_sha256": row.claims_sha256,
        "claims_binding_kind": "UNAUTHENTICATED_LEDGER_SNAPSHOT",
        "control_revision": control.revision,
        "control_event_sha256": control_pin,
        "control_state_sha256": repository._control_pin(control),
        "db_role_oid": metadata[3],
        "db_backend_pid": metadata[7],
        "db_transaction_id": metadata[8],
        "db_recorded_at": metadata[9].isoformat(),
        "unresolved_obligations": list(policy.UNRESOLVED),
        "account_revision_issued": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    values = {
        "environment": item.scope.environment,
        "account_id": item.scope.account_id,
        "settlement_currency": item.scope.settlement_currency,
        "account_revision": row.account_revision,
        "ledger_revision": row.ledger_revision,
        "claims_json": row.claims_json,
        "claims_sha256": row.claims_sha256,
        "control_revision": control.revision,
        "control_event_sha256": control_pin,
        "control_state_sha256": repository._control_pin(control),
        "current_capture_id": item.reference.capture_id,
        "current_terminal_sequence": len(item.chain),
        "current_head_sha256": item.reference.head_sha256,
        "custody_id": item.state["custody_id"],
    }
    return document, values


def _row(document, values):
    raw = journal.canonical(document)
    return Lineage(
        lineage_sha256=journal.digest(raw), lineage_json=raw.decode(), **values
    )


class _RollbackProbe(Exception):
    pass


async def _assert_candidate_really_inserts_then_rollback(session, document, values):
    try:
        async with session.begin_nested():
            row = _row(document, values)
            session.add(row)
            await session.flush()
            assert row.db_recorded_at is not None
            raise _RollbackProbe()
    except _RollbackProbe:
        pass
    assert await session.scalar(select(func.count()).select_from(Lineage)) == 0


async def test_record_commits_and_independent_orm_xid_readback_without_claims(
    monkeypatch, lineage_database
):
    lab = lineage_database
    item = await _inputs(monkeypatch, lab)
    observed_transactions, real_metadata = [], repository._metadata

    async def metadata(session):
        value = await real_metadata(session)
        observed_transactions.append((session, value[8]))
        return value

    # Observation delegates to the real query; no fabricated SQL result.
    monkeypatch.setattr(repository, "_metadata", metadata)
    result = await repository.record_owned_current_revision_lineage_v1(item.token)
    data = json.loads(result.lineage_json)
    assert await _count(lab) == 1 and item.state == {} and item.taken == [True]
    assert len({id(session) for session, _ in observed_transactions}) == 2
    assert len({transaction for _, transaction in observed_transactions}) == 2
    assert data["claims_binding_kind"] == "UNAUTHENTICATED_LEDGER_SNAPSHOT"
    assert data["account_revision"] == data["ledger_revision"] == 0
    assert (
        data["account_complete"]
        is data["account_revision_issued"]
        is data["execution_authority"]
        is False
    )
    assert result.account_revision_issued is result.execution_authority is False
    assert result.admission == "DENY"
    async with lab.factory() as session, session.begin():
        schema.verify_catalog(await session.scalar(text(schema.CATALOG_SQL)))
        saved = await session.scalar(select(Lineage))
        assert (
            saved.lineage_json.encode() == result.lineage_json
            and saved.lineage_sha256 == result.lineage_sha256
        )
        assert saved.current_head_sha256 == item.reference.head_sha256
    assert not await _raw_rights(lab)


async def _raw_rights(lab):
    async with lab.app.connect() as connection:
        return await connection.scalar(
            text("""
            SELECT has_table_privilege(current_user,'public.public_runtime_raw_bundles_v1','SELECT')
              OR has_function_privilege(current_user,
                'public.account_history_original_frame_v1_read(text,text,text,bigint,text,bigint,text,bigint,bigint,bigint,timestamp with time zone)','EXECUTE')
        """)
        )


@pytest.mark.parametrize(
    "damage",
    ["uid", "currency", "head", "claims", "control", "source_policy", "source_bytes"],
)
async def test_real_insert_guard_rejects_rehashed_foreign_bindings(
    monkeypatch, lineage_database, damage
):
    lab = lineage_database
    item = await _inputs(monkeypatch, lab)
    alternative = None
    if damage == "source_bytes":
        alternative = await _store_bundle(
            lab, item.chain, item.reference, wrong_original_bytes=True
        )
    async with lab.factory() as session, session.begin():
        document, values = await _candidate(session, item)
        await _assert_candidate_really_inserts_then_rollback(session, document, values)
        document, values = copy.deepcopy(document), dict(values)
        if damage == "uid":
            values["account_id"] = document["scope"][1] = "999999"
        elif damage == "currency":
            values["settlement_currency"] = document["scope"][2] = "USDC"
        elif damage == "head":
            values["current_head_sha256"] = document["source_reference"][
                "head_sha256"
            ] = "f" * 64
        elif damage == "claims":
            raw = journal.canonical({"declared_synthetic_claim": True}).decode()
            values.update(claims_json=raw, claims_sha256=journal.digest(raw.encode()))
            document["claims_sha256"] = values["claims_sha256"]
        elif damage == "control":
            values["control_revision"] += 1
            document["control_revision"] = values["control_revision"]
        elif damage == "source_policy":
            document["source_policy_sha256"] = "f" * 64
        else:
            raw, proof, retained = alternative
            values["custody_id"] = document["custody_id"] = retained.bundle_id
            document["source_bundle_sha256"] = journal.digest(raw)
            document["proof_envelope_sha256"] = proof
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                session.add(_row(document, values))
                await session.flush()
        assert await session.scalar(select(func.count()).select_from(Lineage)) == 0
    assert await _count(lab) == 0


async def test_runtime_admission_reads_real_changed_function_body(
    monkeypatch, lineage_database
):
    lab = lineage_database
    item = await _inputs(monkeypatch, lab)
    async with lab.admin.begin() as connection:
        await connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION public.collector_current_revision_lineage_guard_v1() RETURNS trigger "
                "AS $x$ BEGIN RETURN NEW; END; $x$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp"
            )
        )
    with pytest.raises(
        policy.CollectorRevisionLineageError,
        match="^collector_lineage_schema_or_role_changed$",
    ):
        await repository.record_owned_current_revision_lineage_v1(item.token)
    assert await _count(lab) == 0 and item.state == {}


@pytest.mark.parametrize("kind", ["owner", "set_role"])
async def test_privileged_or_set_role_session_fails_real_metadata(
    monkeypatch, lineage_database, kind
):
    lab = lineage_database
    await _inputs(monkeypatch, lab)
    engine = lab.admin if kind == "owner" else lab.app
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session, session.begin():
        if kind == "set_role":
            await session.execute(text(f'SET LOCAL ROLE "{lab.app_role}"'))
        with pytest.raises(
            policy.CollectorRevisionLineageError,
            match="^collector_lineage_database_identity_invalid$",
        ):
            await repository._metadata(session)
    assert await _count(lab) == 0


async def test_duplicate_binding_is_denied_with_first_audit_row_retained(
    monkeypatch, lineage_database
):
    lab = lineage_database
    item = await _inputs(monkeypatch, lab)
    async with lab.factory() as session, session.begin():
        document, values = await _candidate(session, item)
        session.add(_row(document, values))
        await session.flush()
        document["db_recorded_at"] = _now().isoformat()
        document["owner_sampled_at"] = _now().isoformat()
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                session.add(_row(document, values))
                await session.flush()
    assert await _count(lab) == 1


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
async def test_missing_app_mutation_privilege_and_owner_immutable_trigger(
    monkeypatch, lineage_database, operation
):
    lab = lineage_database
    item = await _inputs(monkeypatch, lab)
    await repository.record_owned_current_revision_lineage_v1(item.token)
    async with lab.app.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT has_table_privilege(current_user,'public.demo_collector_current_revision_lineages',:operation)"
                ),
                {"operation": operation},
            )
            is False
        )
    sql = {
        "UPDATE": "UPDATE public.demo_collector_current_revision_lineages SET lineage_json=lineage_json",
        "DELETE": "DELETE FROM public.demo_collector_current_revision_lineages",
        "TRUNCATE": "TRUNCATE public.demo_collector_current_revision_lineages",
    }[operation]
    with pytest.raises(DBAPIError):
        async with lab.admin.begin() as connection:
            await connection.execute(text(sql))
    assert await _count(lab) == 1


async def _coordination_signal(signal, task):
    """Propagate an actual worker failure instead of waiting for a missing signal."""
    completed, _pending = await asyncio.wait(
        (signal, task), return_when=asyncio.FIRST_COMPLETED
    )
    if task in completed:
        # Await preserves the original exception in-process. No args, SQL,
        # credential fields or exception text are serialized by this helper.
        await task
        pytest.fail("coordination_task_finished_before_expected_signal")
    return signal.result()


async def _require_coordination_tasks_running(tasks):
    for task in tasks:
        if task.done():
            await task
            pytest.fail("coordination_task_finished_before_observed_SQL_phase")


async def _wait_actual_uid_lock(
    lab,
    key,
    *,
    role,
    granted,
    blocked_by,
    deadline,
    exact_pid=None,
    exclude_pid=None,
    watch=(),
):
    """Observe actual lock manager state; no sleeps, hooks or synthetic timings."""
    unsigned = key & ((1 << 64) - 1)
    parameters = {
        "high": unsigned >> 32,
        "low": unsigned & ((1 << 32) - 1),
        "role": role,
        "granted": granted,
    }
    async with asyncio.timeout_at(deadline), lab.admin.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        while True:
            await _require_coordination_tasks_running(watch)
            rows = (
                await connection.execute(
                    text("""
                    SELECT a.pid,a.backend_start,l.granted,pg_blocking_pids(a.pid) AS blockers,
                           l.classid::bigint,l.objid::bigint,l.objsubid,a.usesysid::bigint
                    FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
                    WHERE l.locktype='advisory'
                      AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database())
                      AND l.classid::bigint=:high AND l.objid::bigint=:low AND l.objsubid=1
                      AND a.usename=:role AND l.granted=:granted
                    """),
                    parameters,
                )
            ).all()
            candidates = [
                tuple(row)
                for row in rows
                if (exact_pid is None or row[0] == exact_pid)
                and (exclude_pid is None or row[0] != exclude_pid)
                and blocked_by in row[3]
            ]
            assert len(candidates) <= 1
            if candidates:
                result = candidates[0]
                assert result[2] is granted
                assert result[4:7] == (parameters["high"], parameters["low"], 1)
                return result


async def _queue_actual_uid_fence(lab, key, ready, acquired, release):
    """Queue before writer commit, then hold its released UID lock for readback."""
    async with lab.admin.connect() as connection, connection.begin():
        pid, role = (
            await connection.execute(text("SELECT pg_backend_pid(),current_user"))
        ).one()
        await connection.execute(text("SET LOCAL statement_timeout='15000ms'"))
        await connection.execute(text("SET LOCAL lock_timeout='15000ms'"))
        ready.set_result((pid, role))
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
        )
        acquired.set_result(pid)
        await release.wait()


@pytest.mark.parametrize(
    "kind", ["cancel_before_commit", "cancel_after_commit", "unknown_after_commit"]
)
async def test_transaction_cancellation_and_commit_unknown_never_return_receipt(
    monkeypatch, lineage_database, kind
):
    """Real SQL waits/commit/termination; no class/engine/dialect event listener.

    _locked takes the UID advisory lock before the control row, then the ledger
    scope row. A real admin control-row gate holds the writer after UID entry;
    an admin queued on that UID before commit owns it before the separate
    readback can proceed. Every transition is observed with pg_locks and
    pg_blocking_pids, rather than inferred from elapsed time or a sleep.
    """
    from app.database.repositories.qualification_ledger import _account_uid_lock_key
    from app.trade_qualification import demo_control

    lab = lineage_database
    loop = asyncio.get_running_loop()
    inputs_ready, fence_ready, fence_acquired = (
        loop.create_future(),
        loop.create_future(),
        loop.create_future(),
    )
    start, release_fence = asyncio.Event(), asyncio.Event()

    async def real_operation():
        # The existing declared owner seam is task-local. Build it in the
        # actual operation Task; do not relax its parent-task identity check.
        actual = await _inputs(monkeypatch, lab)
        inputs_ready.set_result(actual)
        await start.wait()
        return await repository.record_owned_current_revision_lineage_v1(actual.token)

    operation = asyncio.create_task(real_operation())
    fence = gate = gate_transaction = None
    body_accepted = False
    try:
        async with asyncio.timeout(15):
            item = await _coordination_signal(inputs_ready, operation)
        key = _account_uid_lock_key(item.scope)
        assert key == demo_control.account_lock_key(
            demo_control.ControlScope(item.scope.environment, item.scope.account_id)
        )
        deadline = loop.time() + 15
        gate = await lab.admin.connect()
        gate_transaction = await gate.begin()
        async with asyncio.timeout_at(deadline):
            gate_pid = await gate.scalar(text("SELECT pg_backend_pid()"))
            if kind == "cancel_before_commit":
                await gate.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
                )
                start.set()
                waiting = await _wait_actual_uid_lock(
                    lab,
                    key,
                    role=lab.app_role,
                    granted=False,
                    blocked_by=gate_pid,
                    deadline=deadline,
                    watch=(operation,),
                )
                assert waiting[0] != gate_pid and await _count(lab) == 0
                operation.cancel("DECLARED_SYNTHETIC_ARGUMENT")
                with pytest.raises(asyncio.CancelledError) as caught:
                    await operation
                assert caught.value.args == ()
            else:
                # Admin coordination only: lock precisely this seeded control
                # row without taking the UID; source lock ordering is untouched.
                gate_row = (
                    await gate.execute(
                        text("""
                        SELECT environment,account_id FROM public.demo_account_controls
                        WHERE environment=:environment AND account_id=:account_id FOR UPDATE
                        """),
                        {
                            "environment": item.scope.environment,
                            "account_id": item.scope.account_id,
                        },
                    )
                ).one()
                assert tuple(gate_row) == (
                    item.scope.environment,
                    item.scope.account_id,
                )
                start.set()
                writer = await _wait_actual_uid_lock(
                    lab,
                    key,
                    role=lab.app_role,
                    granted=True,
                    blocked_by=gate_pid,
                    deadline=deadline,
                    watch=(operation,),
                )
                assert await _count(lab) == 0
                fence = asyncio.create_task(
                    _queue_actual_uid_fence(
                        lab, key, fence_ready, fence_acquired, release_fence
                    )
                )
                fence_pid, fence_role = await _coordination_signal(fence_ready, fence)
                queued = await _wait_actual_uid_lock(
                    lab,
                    key,
                    role=fence_role,
                    granted=False,
                    blocked_by=writer[0],
                    deadline=deadline,
                    exact_pid=fence_pid,
                    watch=(operation, fence),
                )
                assert queued[0] == fence_pid and fence_pid not in (gate_pid, writer[0])
                # This releases only the admin control-row gate. The queued
                # advisory fence was proven waiting before the writer commits.
                await gate.commit()
                assert await _coordination_signal(fence_acquired, fence) == fence_pid
                assert await _count(lab) == 1
                readback = await _wait_actual_uid_lock(
                    lab,
                    key,
                    role=lab.app_role,
                    granted=False,
                    blocked_by=fence_pid,
                    deadline=deadline,
                    watch=(operation, fence),
                )
                assert readback[0] not in (gate_pid, fence_pid)
                assert readback[:2] != writer[:2]
                if kind == "cancel_after_commit":
                    operation.cancel("DECLARED_SYNTHETIC_ARGUMENT")
                    with pytest.raises(asyncio.CancelledError) as caught:
                        await operation
                    assert caught.value.args == ()
                else:
                    # Terminate only the exact still-blocked readback backend
                    # in this disposable database, including backend-start/role
                    # identity so PID reuse cannot select a different process.
                    async with lab.admin.connect() as terminator:
                        killed = await terminator.scalar(
                            text("""
                            SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a
                            WHERE a.pid=:pid AND a.backend_start=:started
                              AND a.usesysid::bigint=:role_oid AND a.usename=:role
                              AND a.datname=current_database() AND a.wait_event_type='Lock'
                              AND :blocker=ANY(pg_blocking_pids(a.pid))
                            """),
                            {
                                "pid": readback[0],
                                "started": readback[1],
                                "role_oid": readback[7],
                                "role": lab.app_role,
                                "blocker": fence_pid,
                            },
                        )
                    assert killed is True
                    with pytest.raises(
                        policy.CollectorRevisionLineageError,
                        match="^collector_lineage_write_or_readback_unknown$",
                    ):
                        await operation
        body_accepted = True
    finally:
        # Cancel/drain the source task before releasing any coordinator fence.
        # No source retry or deletion of committed journal/lineage evidence.
        try:
            if not operation.done():
                operation.cancel()
            async with asyncio.timeout(5):
                await asyncio.gather(operation, return_exceptions=True)
        finally:
            release_fence.set()
            try:
                if fence is not None:
                    if not fence_acquired.done() and not fence.done():
                        fence.cancel()
                    async with asyncio.timeout(5):
                        fence_results = await asyncio.gather(
                            fence, return_exceptions=True
                        )
                        if body_accepted:
                            assert fence_results == [None]
            finally:
                if gate is not None:
                    try:
                        async with asyncio.timeout(5):
                            if (
                                gate_transaction is not None
                                and gate_transaction.is_active
                            ):
                                await gate_transaction.rollback()
                    finally:
                        async with asyncio.timeout(5):
                            await gate.close()
    assert item.state == {} and item.taken == [True]
    assert await _count(lab) == (0 if kind == "cancel_before_commit" else 1)


async def test_global_Session_listener_is_rejected_before_any_lineage_write(
    monkeypatch, lineage_database
):
    """Keep the genuine native-factory rejection as its own explicit negative."""
    lab = lineage_database
    item = await _inputs(monkeypatch, lab)
    fired = []

    def forbidden_global_hook(*_args):
        fired.append(True)

    event.listen(Session, "after_commit", forbidden_global_hook)
    try:
        with pytest.raises(
            policy.CollectorRevisionLineageError,
            match="^collector_lineage_write_or_readback_unknown$",
        ):
            await repository.record_owned_current_revision_lineage_v1(item.token)
    finally:
        event.remove(Session, "after_commit", forbidden_global_hook)
    assert fired == [] and item.state == {} and item.taken == [True]
    assert await _count(lab) == 0


async def test_nonempty_downgrade_refuses_after_exact_app_grants_revoked(
    monkeypatch, lineage_database
):
    lab = lineage_database
    item = await _inputs(monkeypatch, lab)
    await repository.record_owned_current_revision_lineage_v1(item.token)
    async with lab.admin.begin() as connection:
        await connection.execute(
            text(
                f'REVOKE SELECT,INSERT ON public.demo_collector_current_revision_lineages FROM "{lab.app_role}"'
            )
        )
    with pytest.raises(
        RuntimeError, match="^collector_lineage_revoke_grants_and_preserve_audit$"
    ):
        async with lab.admin.begin() as connection:
            await connection.run_sync(_migrate, "0052", "downgrade")
    async with lab.admin.connect() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0053"
        )
    assert await _count(lab) == 1


async def test_empty_downgrade_reupgrade_runs_frozen_perstatement_migration(
    lineage_database,
):
    lab = lineage_database
    async with lab.admin.begin() as connection:
        await connection.execute(
            text(
                f'REVOKE SELECT,INSERT ON public.demo_collector_current_revision_lineages FROM "{lab.app_role}"'
            )
        )
        await connection.run_sync(_migrate, "0052", "downgrade")
        assert (
            await connection.scalar(
                text(
                    "SELECT to_regclass('public.demo_collector_current_revision_lineages')"
                )
            )
            is None
        )
        await connection.run_sync(_migrate, "0053", "upgrade")
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0053"
        )
        # Re-upgrade itself never restores the old application's table grant.
        assert not await connection.scalar(
            text(
                "SELECT has_table_privilege(:role,'public.demo_collector_current_revision_lineages','SELECT')"
            ),
            {"role": lab.app_role},
        )


async def test_custody_accepts_actual_frozen0053_native_parent_RI_pair(
    lineage_database,
):
    lab = lineage_database
    async with lab.retained.begin() as connection:
        rows = (
            await connection.execute(
                text("""
            SELECT g.tgtype,g.tgenabled::text,p.proname,g.tgisinternal,
              g.oid::bigint AS trigger_oid,g.tgname
            FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
            WHERE g.tgrelid='public.public_runtime_raw_bundles_v1'::regclass AND g.tgisinternal
        """)
            )
        ).all()
    assert len(rows) == 2
    assert {
        (row.tgtype, row.tgenabled, row.proname, row.tgisinternal) for row in rows
    } == {
        (9, "O", "RI_FKey_restrict_del", True),
        (17, "O", "RI_FKey_noaction_upd", True),
    }
    assert all(
        row.tgname == f"RI_ConstraintTrigger_a_{row.trigger_oid}" for row in rows
    )
    await lab.custody.verify_deployment()
    raw = b'{"declared_synthetic":"native_0053_RI_catalog_only"}'
    proof_pin = journal.digest(b"declared-synthetic-RI-proof")
    receipt = await lab.custody.seal_bundle(
        bundle_raw=raw,
        expected_sha256=journal.digest(raw),
        proof_envelope_sha256=proof_pin,
    )
    retained = custody._consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=journal.digest(raw),
        expected_proof_envelope_sha256=proof_pin,
    )
    assert retained.bundle_raw == raw


async def test_custody_actual0052_absence_requires_no0053_parent_RI(lineage_database):
    lab = lineage_database
    assert re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", lab.app_role)
    async with lab.admin.begin() as connection:
        await connection.execute(
            text(
                f'REVOKE ALL ON public.demo_collector_current_revision_lineages FROM "{lab.app_role}"'
            )
        )
        await connection.run_sync(lambda sync: _migrate(sync, "0052", "downgrade"))
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0052"
        )
        assert (
            await connection.scalar(
                text("""
            SELECT count(*) FROM pg_catalog.pg_trigger
            WHERE tgrelid='public.public_runtime_raw_bundles_v1'::regclass AND tgisinternal
        """)
            )
            == 0
        )
    await lab.custody.verify_deployment()


@pytest.mark.parametrize(
    "damage", ("missing_fk", "extra_incoming", "cascade_action", "disabled_RI")
)
async def test_custody_actual0053_foreign_or_damaged_RI_is_denied(
    lineage_database, damage
):
    lab = lineage_database
    async with lab.admin.begin() as connection:
        if damage in {"missing_fk", "cascade_action"}:
            await connection.execute(
                text("""
                ALTER TABLE public.demo_collector_current_revision_lineages
                DROP CONSTRAINT fk_collector_current_custody
            """)
            )
            if damage == "cascade_action":
                await connection.execute(
                    text("""
                    ALTER TABLE public.demo_collector_current_revision_lineages
                    ADD CONSTRAINT fk_collector_current_custody FOREIGN KEY(custody_id)
                    REFERENCES public.public_runtime_raw_bundles_v1(bundle_id) ON DELETE CASCADE
                """)
                )
        elif damage == "extra_incoming":
            await connection.execute(
                text("""
                CREATE TABLE public.declared_unapproved_custody_ref(
                  bundle_id varchar(32) REFERENCES public.public_runtime_raw_bundles_v1(bundle_id))
            """)
            )
        else:
            name = await connection.scalar(
                text("""
                SELECT g.tgname FROM pg_catalog.pg_trigger g
                JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
                WHERE g.tgrelid='public.public_runtime_raw_bundles_v1'::regclass
                  AND g.tgisinternal AND p.proname='RI_FKey_restrict_del'
            """)
            )
            assert type(name) is str and re.fullmatch(
                r"RI_ConstraintTrigger_a_[0-9]+", name
            )
            await connection.execute(
                text(
                    f'ALTER TABLE public.public_runtime_raw_bundles_v1 DISABLE TRIGGER "{name}"'
                )
            )
    with pytest.raises(
        custody.RuntimeBundleCustodyError, match="^runtime_bundle_custody_unavailable$"
    ):
        await lab.custody.verify_deployment()


async def test_custody_cached_preflight_rejects_actual_same_definition_FK_recreation(
    lineage_database,
):
    lab = lineage_database
    await lab.custody.verify_deployment()
    original = lab.custody._deployment.fingerprint
    async with lab.admin.begin() as connection:
        await connection.execute(
            text("""
            ALTER TABLE public.demo_collector_current_revision_lineages
            DROP CONSTRAINT fk_collector_current_custody
        """)
        )
        await connection.execute(
            text("""
            ALTER TABLE public.demo_collector_current_revision_lineages
            ADD CONSTRAINT fk_collector_current_custody FOREIGN KEY(custody_id)
            REFERENCES public.public_runtime_raw_bundles_v1(bundle_id) ON DELETE RESTRICT
        """)
        )
    async with lab.custody._factory() as session, session.begin():
        changed = await custody.RuntimeBundleCustodyRepository._catalog(session)
    assert changed != original
    raw = b'{"declared_synthetic":"old_preflight_cannot_survive_FK_recreation"}'
    with pytest.raises(
        custody.RuntimeBundleCustodyError, match="^runtime_bundle_custody_unavailable$"
    ):
        await lab.custody.seal_bundle(
            bundle_raw=raw,
            expected_sha256=journal.digest(raw),
            proof_envelope_sha256=journal.digest(b"declared-synthetic-RI-proof"),
        )
