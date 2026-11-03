"""Real0054 SQL mechanics with DECLARED SYNTHETIC account/source parents.

These manual rows are not native source owners, genuine HWM measurements,
complete accounts, submission permits or trading evidence. Actual migrations,
restricted roles, guard SQL, CAS, commit, independent readback and real lock
waits are exercised without replacing a production admission/owner function.
The post-UPDATE expiry fault uses an explicit extra scope trigger and a real
advisory-lock wait. The original0054 guard stays byte-exact. Injector-installed
schema has no native admission claim; the extra trigger/function are removed.
Raw/writer native positives require a genuinely sealed future exact source.
"""

import asyncio
import copy
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.account_capture_journal import DemoAccountCaptureEvent
from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision as Issued,
)
from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage as Lineage,
)
from app.database.repositories import (
    account_history_original_native_read_current0053_v1 as native_fence,
)
from app.database.repositories import collector_current_0054_catalog as writer_api
from app.database.repositories import (
    collector_current_0054_multirole_catalog_v1 as raw_api,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import account_issued_revision_schema_v1 as issued_schema
from app.domain import account_revision_collector_lineage_v1 as lineage_policy
from app.domain import collector_current_0054_catalog_v1 as native54
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import reservations
from app.trade_qualification.reservations import LedgerScope
from scripts import collector_current_0054_toolchain as capture54
from scripts import current_contract_source_scopes_v1 as scopes
from tests.controlled_writer_coverage_fixtures import seed_controlled_writer_control
from tests.current0054_fixtures import (
    ROOT,
)
from tests.current0054_fixtures import (
    current0054_database as current0054_database,  # noqa: PLC0414 -- genuine pytest fixture binding.
)
from tests.integration import test_account_revision_collector_lineage_pg_v1 as seed
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
    settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
COMPONENT = "DECLARED_SYNTHETIC_DB_CONSTRAINTS_COMPONENT_ONLY"
_ISSUED = "demo_collector_issued_account_revisions"
_LINEAGE = "demo_collector_current_revision_lineages"
_BASE_GRANTS = (
    ("qualification_account_scopes", "SELECT,UPDATE"),
    ("qualification_reservations", "SELECT"),
    ("demo_account_controls", "SELECT,UPDATE"),
    ("demo_control_journal", "SELECT"),
    ("demo_account_capture_events", "SELECT"),
)
_PROOFS = (
    "registration",
    "current_identity",
    "history_prefix",
    "product_scope",
    "history_coverage",
    "cashflow_and_funding_finality",
    "loss_streak_seed",
    "unchanged_window_drawdown",
    "inventory_metadata",
    "protection",
    "local_ledger",
    "current_portfolio",
)
_LOCK_SQL = """
SELECT ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')
 ||decode('00','hex')||convert_to(:environment,'UTF8')||decode('00','hex')
 ||convert_to(:account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint
"""
_META_SQL = """
SELECT (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user),
 pg_backend_pid(),pg_current_xact_id()::text,clock_timestamp(),
 current_user=session_user,current_setting('role')='none'
"""
_ROW_SQL = """
SELECT a.account_revision,a.ledger_revision,a.claims_json,a.claims_sha256,
 c.control_revision,c.state_sha256,cj.event_sha256
FROM public.qualification_account_scopes a
JOIN public.demo_account_controls c USING(environment,account_id)
JOIN public.demo_control_journal cj ON cj.environment=c.environment
 AND cj.account_id=c.account_id AND cj.control_revision=c.control_revision
WHERE a.environment=:environment AND a.account_id=:account_id
 AND a.settlement_currency=:currency
"""
_COLUMNS = (
    "issuer_sha256",
    "environment",
    "account_id",
    "settlement_currency",
    "previous_account_revision",
    "previous_ledger_revision",
    "previous_claims_sha256",
    "account_revision",
    "ledger_revision",
    "claims_sha256",
    "claims_json",
    "control_revision",
    "control_event_sha256",
    "control_state_sha256",
    "source_lineage_sha256",
    "hwm_custody_id",
    "hwm_bundle_sha256",
    "proof_json",
    "original_expires_at",
)
_INSERT = text(
    "INSERT INTO public.demo_collector_issued_account_revisions ("
    + ",".join(_COLUMNS)
    + ") VALUES ("
    + ",".join(":" + name for name in _COLUMNS)
    + ") RETURNING issuer_sha256,db_recorded_at"
)


async def _grant(admin, role, table, privileges, *, revoke=False):
    # Names are fixed test constants or collector-created native role names.
    assert re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", role)
    async with admin.begin() as connection:
        exists = await connection.scalar(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": "public." + table}
        )
        if exists:
            verb, preposition = ("REVOKE", "FROM") if revoke else ("GRANT", "TO")
            await connection.execute(
                text(f'{verb} {privileges} ON public.{table} {preposition} "{role}"')
            )


@asynccontextmanager
async def _lab(admin):
    async with (
        collector(admin, grant=False) as (app, _),
        collector(admin, grant=False) as (retained, _),
    ):
        app_role, retained_role = app.url.username, retained.url.username
        app_grants = (
            *_BASE_GRANTS,
            (_LINEAGE, "SELECT,INSERT"),
            (_ISSUED, "SELECT,INSERT"),
            ("alembic_version", "SELECT"),
        )
        try:
            for table, privileges in app_grants:
                await _grant(admin, app_role, table, privileges)
            await _grant(admin, retained_role, "alembic_version", "SELECT")
            async with admin.begin() as connection:
                for signature in ("append(text,text,text,bytea)", "read(text)"):
                    await connection.execute(
                        text(
                            f'GRANT EXECUTE ON FUNCTION public.public_runtime_bundle_v1_{signature} TO "{retained_role}"'
                        )
                    )
            factory = async_sessionmaker(app, autoflush=False, expire_on_commit=False)
            repository = custody.RuntimeBundleCustodyRepository(
                async_sessionmaker(retained, autoflush=False, expire_on_commit=False)
            )
            await repository.verify_deployment()
            yield SimpleNamespace(
                admin=admin,
                app=app,
                factory=factory,
                custody=repository,
                app_role=app_role,
            )
        finally:
            for table, privileges in reversed(app_grants):
                await _grant(admin, app_role, table, privileges, revoke=True)
            await _grant(admin, retained_role, "alembic_version", "SELECT", revoke=True)
            async with admin.begin() as connection:
                for signature in ("append(text,text,text,bytea)", "read(text)"):
                    await connection.execute(
                        text(
                            f'REVOKE EXECUTE ON FUNCTION public.public_runtime_bundle_v1_{signature} FROM "{retained_role}"'
                        )
                    )


def _scope_args(scope):
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "currency": scope.settlement_currency,
    }


async def _manual_parents(monkeypatch, lab):
    # Reuse only the explicit wire fixture and generic byte-retention helper.
    # Do NOT call seed._inputs/_candidate/_admit or replace any native owner.
    chain, reference, packet = await seed._wire_source(monkeypatch)
    scope = LedgerScope(account_id=seed.UID, settlement_currency="USDT")
    admin_factory = async_sessionmaker(
        lab.admin, autoflush=False, expire_on_commit=False
    )
    checkpoint = await QualificationLedgerRepository(
        admin_factory, clock=seed._now
    ).initialize_capture_scope(scope)
    assert journal.checked_event(chain[0].event)["data"][
        "local_checkpoint_sha256"
    ] == reservations.digest(checkpoint.state)
    async with admin_factory() as session, session.begin():
        await seed_controlled_writer_control(session, uid=scope.account_id)
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
    raw, proof_pin, retained = await seed._store_bundle(lab, chain, reference)
    async with lab.factory() as session, session.begin():
        await settings(session)
        actual = (await session.execute(text(_ROW_SQL), _scope_args(scope))).one()
        role, pid, xid, sampled, same_user, no_set_role = (
            await session.execute(text(_META_SQL))
        ).one()
        assert same_user is no_set_role is True
        assert actual[:4] == (0, 0, None, None)
        expires = sampled + timedelta(seconds=45)
        document = {
            "schema_version": lineage_policy.SCHEMA,
            "scope": [scope.environment, scope.account_id, scope.settlement_currency],
            "source_reference": observed.reference_document(reference),
            "source_bundle_sha256": journal.digest(raw),
            "source_policy_sha256": current.POLICY_SHA256,
            "proof_envelope_sha256": proof_pin,
            "native_proof_sha256": "3" * 64,
            "native_readback_sha256": "4" * 64,
            "custody_id": retained.bundle_id,
            "owner_sampled_at": sampled.isoformat(),
            "original_expires_at": expires.isoformat(),
            "packet_completed_at": packet.completed_at.isoformat(),
            "db_recorded_at": sampled.isoformat(),
            "account_revision": 0,
            "ledger_revision": 0,
            "claims_sha256": None,
            "claims_binding_kind": "UNAUTHENTICATED_LEDGER_SNAPSHOT",
            "control_revision": actual[4],
            "control_state_sha256": actual[5],
            "control_event_sha256": actual[6],
            "db_role_oid": role,
            "db_backend_pid": pid,
            "db_transaction_id": xid,
            "unresolved_obligations": list(lineage_policy.UNRESOLVED),
            "account_revision_issued": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
            "component": COMPONENT,
        }
        lineage_raw = journal.canonical(document)
        lineage_sha = journal.digest(lineage_raw)
        session.add(
            Lineage(
                lineage_sha256=lineage_sha,
                lineage_json=lineage_raw.decode(),
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                account_revision=0,
                ledger_revision=0,
                claims_json=None,
                claims_sha256=None,
                control_revision=actual[4],
                control_state_sha256=actual[5],
                control_event_sha256=actual[6],
                current_capture_id=reference.capture_id,
                current_terminal_sequence=len(chain),
                current_head_sha256=reference.head_sha256,
                custody_id=retained.bundle_id,
            )
        )
    # This is generic retained JSON, NOT a minted native sampled-HWM receipt.
    hwm_raw = journal.canonical(
        {
            "schema_version": "ctcc.native_owned_sampled_balance_peak_record.v1",
            "component": COMPONENT,
            "observed_source": {
                "scope": document["scope"],
                "current_source_reference": document["source_reference"],
                "current_proof_envelope_sha256": proof_pin,
                "original_request_expires_at": expires.isoformat(),
            },
            "historical_account_peak": None,
            "account_revision_issued": False,
            "account_complete": False,
            "execution_authority": False,
        }
    )
    hwm_sha = journal.digest(hwm_raw)
    hwm_proof = journal.digest(
        journal.canonical({"component": COMPONENT, "source": lineage_sha})
    )
    receipt = await lab.custody.seal_bundle(
        bundle_raw=hwm_raw, expected_sha256=hwm_sha, proof_envelope_sha256=hwm_proof
    )
    hwm = custody._consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=hwm_sha,
        expected_proof_envelope_sha256=hwm_proof,
    )
    assert hwm.execution_authority is False
    return SimpleNamespace(
        scope=scope,
        lineage_sha=lineage_sha,
        hwm_id=hwm.bundle_id,
        hwm_sha=hwm_sha,
        expires=expires,
    )


async def _candidate(connection, item, *, expires=None):
    await settings(connection)
    actual = (await connection.execute(text(_ROW_SQL), _scope_args(item.scope))).one()
    role, pid, xid, sampled, same_user, no_set_role = (
        await connection.execute(text(_META_SQL))
    ).one()
    assert same_user is no_set_role is True
    # Fixed previous revision0 is intentional: a second writer must fail CAS.
    deadline = item.expires if expires is None else expires
    scope = [
        item.scope.environment,
        item.scope.account_id,
        item.scope.settlement_currency,
    ]
    claims_raw = journal.canonical(
        {
            "schema_version": "ctcc.account_ledger_claims.v2",
            "scope": scope,
            "component": COMPONENT,
            "account_complete": False,
            "account_revision_issued": False,
            "native_source_authenticated": False,
            "execution_authority": False,
        }
    )
    claims_sha = journal.digest(claims_raw)
    proof = {
        "schema_version": "ctcc.collector_issued_account_revision.v1",
        "scope": scope,
        "component": COMPONENT,
        "account_complete": False,
        "account_revision_issued": False,
        "native_source_authenticated": False,
        "execution_authority": False,
        "claims_sha256": claims_sha,
        "source_lineage_sha256": item.lineage_sha,
        "hwm_custody_id": item.hwm_id,
        "hwm_bundle_sha256": item.hwm_sha,
        "previous_account_revision": 0,
        "previous_ledger_revision": 0,
        "account_revision": 1,
        "ledger_revision": 1,
        "control_revision": actual[4],
        "control_state_sha256": actual[5],
        "control_event_sha256": actual[6],
        "db_role_oid": role,
        "db_backend_pid": pid,
        "db_transaction_id": xid,
        "owner_sampled_at": sampled.isoformat(),
        "original_expires_at": deadline.isoformat(),
        "proofs": {
            name: journal.digest((COMPONENT + ":" + name).encode()) for name in _PROOFS
        },
    }
    values = {
        "environment": item.scope.environment,
        "account_id": item.scope.account_id,
        "settlement_currency": item.scope.settlement_currency,
        "previous_account_revision": 0,
        "previous_ledger_revision": 0,
        "previous_claims_sha256": None,
        "account_revision": 1,
        "ledger_revision": 1,
        "claims_json": claims_raw.decode(),
        "claims_sha256": claims_sha,
        "control_revision": actual[4],
        "control_state_sha256": actual[5],
        "control_event_sha256": actual[6],
        "source_lineage_sha256": item.lineage_sha,
        "hwm_custody_id": item.hwm_id,
        "hwm_bundle_sha256": item.hwm_sha,
        "original_expires_at": deadline,
    }
    return proof, _bound(proof, values)


def _bound(proof, values):
    result = dict(values)
    raw = journal.canonical(proof)
    result.update(proof_json=raw.decode(), issuer_sha256=journal.digest(raw))
    return result


async def _write(lab, item, *, expires=None, prepared=None, lock_timeout_ms=None):
    async with lab.app.begin() as connection:
        proof, values = await _candidate(connection, item, expires=expires)
        if lock_timeout_ms is not None:
            # Test-local override only for the bounded post-UPDATE fault wait.
            assert type(lock_timeout_ms) is int and 1000 < lock_timeout_ms <= 8000
            await connection.execute(
                text("SELECT set_config('lock_timeout',:duration,true)"),
                {"duration": f"{lock_timeout_ms}ms"},
            )
        if prepared is not None:
            prepared.set_result((proof, values))
        result = (await connection.execute(_INSERT, values)).one()
    return proof, values, result


async def _snapshot(lab, item):
    # Actual separate ORM session and transaction, never the writing connection.
    async with lab.factory() as session, session.begin():
        rows = (
            await session.scalars(select(Issued).order_by(Issued.issuer_sha256))
        ).all()
        scope = (await session.execute(text(_ROW_SQL), _scope_args(item.scope))).one()
        xid = await session.scalar(text("SELECT pg_current_xact_id()::text"))
        return (
            [
                (row.issuer_sha256, row.proof_json, row.claims_json, row.db_recorded_at)
                for row in rows
            ],
            tuple(scope),
            xid,
        )


async def _held_advisory_lock(connection):
    rows = (
        (
            await connection.execute(
                text("""
      SELECT database::bigint AS database_oid,classid::bigint AS classid,
        objid::bigint AS objid,objsubid,mode
      FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted
    """)
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 1 and rows[0]["mode"] == "ExclusiveLock"
    result = dict(rows[0])
    assert all(
        type(result[name]) is int and 0 <= result[name] < 2**32
        for name in ("database_oid", "classid", "objid", "objsubid")
    )
    return result


async def _await_phase(future, task, *, timeout):
    done, _ = await asyncio.wait(
        (future, task), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
    )
    if future in done:
        return future.result()
    if task in done:
        await task  # Preserve an original pre-phase error/cancellation.
        raise AssertionError("component_task_exited_without_phase")
    raise TimeoutError("component_phase_not_observed")


async def _wait_for_lock(admin, pid, identity, task):
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        if task.done():
            await task  # Do not replace an early SQL failure with a timeout.
            raise AssertionError("component_writer_exited_before_lock_wait")
        async with admin.connect() as connection:
            waiting = await connection.scalar(
                text("""
              SELECT EXISTS(SELECT 1 FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid
                WHERE a.pid=:pid AND a.wait_event_type='Lock' AND l.locktype='advisory'
                AND NOT l.granted AND l.database::bigint=:database_oid
                AND l.classid::bigint=:classid AND l.objid::bigint=:objid
                AND l.objsubid=:objsubid AND l.mode=:mode)
            """),
                {"pid": pid, **identity},
            )
        if waiting is True:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("actual_postgres_lock_wait_not_observed")


def _migrate(connection, target, *, direction):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert (
        script.get_current_head()
        == scopes.current0054_source_scope(ROOT).actual_source_head
        == "0054"
    )
    resolve = script._upgrade_revs if direction == "upgrade" else script._downgrade_revs
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: resolve(target, heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


async def test_genuine54_migration_and_public_owner_capture_are_observe_only(
    current0054_database,
):
    async with current0054_database.connect() as connection:
        document, raw = await capture54.capture_current0054_owner(connection, ROOT)
    assert document["source_head"] == document["database_head"] == "0054"
    assert journal.canonical(document) + b"\n" == raw
    assert (
        document["native_baseline_admission"] is document["runtime_admission"] is False
    )
    assert document["account_complete"] is document["execution_authority"] is False
    assert document["current0054_observation"]["native_baseline_admission"] is False


async def test_valid_sql_insert_cas_and_committed_independent_orm_readback(
    monkeypatch, current0054_database
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        proof, values, returned = await _write(lab, item)
        rows, actual, read_xid = await _snapshot(lab, item)
        assert len(rows) == 1 and rows[0][0] == returned[0] == values["issuer_sha256"]
        assert rows[0][1].encode() == journal.canonical(proof)
        assert actual[:4] == (1, 1, values["claims_json"], values["claims_sha256"])
        assert read_xid != proof["db_transaction_id"]
        assert (
            datetime.fromisoformat(proof["owner_sampled_at"])
            <= rows[0][3]
            < item.expires
        )
        assert (
            proof["component"] == COMPONENT
            and proof["account_complete"] is proof["execution_authority"] is False
        )
        # A manually valid audit row does not become a native owner/catalog.
        with pytest.raises(native54.Current0054CatalogError):
            native54.observe_actual_owner_catalog({"passed": True, "saved_row": proof})


@pytest.mark.parametrize(
    "damage",
    [
        "uid",
        "lineage",
        "hwm",
        "control",
        "claims",
        "backend",
        "transaction",
        "proof_membership",
    ],
)
async def test_foreign_or_rehashed_manual_binding_denies_without_cas(
    monkeypatch, current0054_database, damage
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        with pytest.raises(DBAPIError, match="collector_issuer_write_denied"):
            async with lab.app.begin() as connection:
                proof, values = await _candidate(connection, item)
                proof, values = copy.deepcopy(proof), dict(values)
                if damage == "uid":
                    values["account_id"] = proof["scope"][1] = "999999999"
                elif damage == "lineage":
                    values["source_lineage_sha256"] = proof["source_lineage_sha256"] = (
                        "f" * 64
                    )
                elif damage == "hwm":
                    values["hwm_bundle_sha256"] = proof["hwm_bundle_sha256"] = "f" * 64
                elif damage == "control":
                    values["control_state_sha256"] = proof["control_state_sha256"] = (
                        "f" * 64
                    )
                elif damage == "claims":
                    # Rehash proof, retaining a mismatched actual claims payload.
                    values["claims_sha256"] = proof["claims_sha256"] = "f" * 64
                elif damage == "backend":
                    proof["db_backend_pid"] += 1
                elif damage == "transaction":
                    proof["db_transaction_id"] = str(
                        int(proof["db_transaction_id"]) + 1
                    )
                else:
                    proof["proofs"].pop("cashflow_and_funding_finality")
                await connection.execute(_INSERT, _bound(proof, values))
        rows, actual, _ = await _snapshot(lab, item)
        assert rows == [] and actual[:4] == (0, 0, None, None)


async def test_two_actual_workers_advance_one_scope_revision(
    monkeypatch, current0054_database
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        start, ready = (
            asyncio.Event(),
            [asyncio.get_running_loop().create_future() for _ in range(2)],
        )

        async def worker(index):
            async with lab.app.begin() as connection:
                proof, values = await _candidate(connection, item)
                before = (
                    await connection.execute(text(_ROW_SQL), _scope_args(item.scope))
                ).one()
                ready[index].set_result(
                    (before[0], proof["db_backend_pid"], proof["db_transaction_id"])
                )
                await start.wait()
                await connection.execute(_INSERT, values)
            return values["issuer_sha256"]

        tasks = [asyncio.create_task(worker(index)) for index in range(2)]
        try:
            observed_workers = [
                await _await_phase(ready[index], tasks[index], timeout=3)
                for index in range(2)
            ]
            assert all(row[0] == 0 for row in observed_workers)
            assert (
                len({row[1] for row in observed_workers})
                == len({row[2] for row in observed_workers})
                == 2
            )
            start.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=5
            )
            assert sum(type(value) is str for value in outcomes) == 1
            assert sum(isinstance(value, DBAPIError) for value in outcomes) == 1
        finally:
            start.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        rows, actual, _ = await _snapshot(lab, item)
        assert len(rows) == 1 and actual[:2] == (1, 1)


async def test_duplicate_current_lineage_keeps_first_committed_audit(
    monkeypatch, current0054_database
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        await _write(lab, item)
        before = (await _snapshot(lab, item))[:2]
        with pytest.raises(DBAPIError, match="collector_issuer_write_denied"):
            await _write(lab, item)
        assert (await _snapshot(lab, item))[:2] == before


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
async def test_committed_issuer_audit_is_immutable_even_for_owner(
    monkeypatch, current0054_database, operation
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        await _write(lab, item)
        before = (await _snapshot(lab, item))[:2]
        sql = {
            "UPDATE": "UPDATE public.demo_collector_issued_account_revisions SET issuer_sha256=issuer_sha256",
            "DELETE": "DELETE FROM public.demo_collector_issued_account_revisions",
            "TRUNCATE": "TRUNCATE public.demo_collector_issued_account_revisions",
        }[operation]
        with pytest.raises(DBAPIError, match="collector_issuer_immutable"):
            async with lab.admin.begin() as connection:
                await connection.execute(text(sql))
        assert (await _snapshot(lab, item))[:2] == before


@pytest.mark.parametrize("boundary", ["equal_sample", "past", "extends_original"])
async def test_original_expiry_equality_past_or_extension_rolls_back(
    monkeypatch, current0054_database, boundary
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        with pytest.raises(DBAPIError, match="collector_issuer_write_denied"):
            async with lab.app.begin() as connection:
                proof, values = await _candidate(connection, item)
                sampled = datetime.fromisoformat(proof["owner_sampled_at"])
                expires = (
                    sampled
                    if boundary == "equal_sample"
                    else sampled - timedelta(microseconds=1)
                )
                if boundary == "extends_original":
                    expires = item.expires + timedelta(microseconds=1)
                proof["original_expires_at"] = expires.isoformat()
                values["original_expires_at"] = expires
                await connection.execute(_INSERT, _bound(proof, values))
        rows, actual, _ = await _snapshot(lab, item)
        assert rows == [] and actual[:4] == (0, 0, None, None)


async def _guard_and_scope_triggers(admin):
    async with admin.connect() as connection:
        body = await connection.scalar(
            text(
                "SELECT prosrc FROM pg_proc WHERE oid='public.collector_issued_account_revision_guard_v1()'::regprocedure"
            )
        )
        rows = (
            await connection.execute(
                text("""
          SELECT t.oid::bigint,t.tgname,pg_get_triggerdef(t.oid,true)
          FROM pg_trigger t WHERE t.tgrelid='public.qualification_account_scopes'::regclass
          ORDER BY t.oid
        """)
            )
        ).all()
        return body, [tuple(row) for row in rows]


@asynccontextmanager
async def _test_only_scope_wait(admin, key):
    # Explicit DDL fault injector on the scope AFTER UPDATE, not a replacement
    # clock or0054 guard. No native admission is claimed while this injector is
    # installed; a separate fresh-owner fault test checks population rejection.
    before = await _guard_and_scope_triggers(admin)
    source = (
        issued_schema.statements()[1]
        .split("AS $ctcc$", 1)[1]
        .split("$ctcc$ LANGUAGE", 1)[0]
    )
    assert before[0] == source
    installed = False
    try:
        async with admin.begin() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT to_regprocedure('public.ctcc_test_only_issuer_scope_wait()') IS NULL"
                    )
                )
                is True
            )
            assert not await connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.qualification_account_scopes'::regclass AND tgname='ctcc_test_only_issuer_scope_wait')"
                )
            )
            await connection.execute(
                text(f"""
              CREATE FUNCTION public.ctcc_test_only_issuer_scope_wait() RETURNS trigger
              LANGUAGE plpgsql AS $test_only$
              BEGIN PERFORM pg_advisory_xact_lock({key}); RETURN NEW; END;
              $test_only$
            """)
            )
            await connection.execute(
                text("""
              CREATE TRIGGER ctcc_test_only_issuer_scope_wait AFTER UPDATE
              ON public.qualification_account_scopes FOR EACH ROW
              EXECUTE FUNCTION public.ctcc_test_only_issuer_scope_wait()
            """)
            )
            installed = True
        during = await _guard_and_scope_triggers(admin)
        assert during[0] == before[0] and len(during[1]) == len(before[1]) + 1
        assert any(row[1] == "ctcc_test_only_issuer_scope_wait" for row in during[1])
        yield
    finally:
        if installed:
            async with admin.begin() as connection:
                await connection.execute(text("SET LOCAL lock_timeout='1000ms'"))
                await connection.execute(
                    text(
                        "DROP TRIGGER ctcc_test_only_issuer_scope_wait ON public.qualification_account_scopes"
                    )
                )
                await connection.execute(
                    text("DROP FUNCTION public.ctcc_test_only_issuer_scope_wait()")
                )
            assert await _guard_and_scope_triggers(admin) == before


async def test_original_final_clock_guard_rolls_back_update_after_actual_scope_trigger_wait(
    monkeypatch, current0054_database
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        key, task = 42543100004002, None
        async with _test_only_scope_wait(lab.admin, key):
            # All data below is DB_COMPONENT_ONLY, with native admission false
            # while the explicit extra scope trigger is installed.
            prepared = asyncio.get_running_loop().create_future()
            try:
                async with lab.admin.begin() as blocker:
                    expires = min(
                        item.expires,
                        await blocker.scalar(
                            text("SELECT clock_timestamp()+interval '3 seconds'")
                        ),
                    )
                    await blocker.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
                    )
                    identity = await _held_advisory_lock(blocker)
                    task = asyncio.create_task(
                        _write(
                            lab,
                            item,
                            expires=expires,
                            prepared=prepared,
                            lock_timeout_ms=8000,
                        )
                    )
                    proof, _ = await _await_phase(prepared, task, timeout=3)
                    await _wait_for_lock(
                        lab.admin, proof["db_backend_pid"], identity, task
                    )
                    # The only occurrence of this dedicated lock in the writer
                    # is the real AFTER UPDATE scope trigger. The original0054
                    # guard has already performed its CAS before this wait.
                    deadline = asyncio.get_running_loop().time() + 4
                    while True:
                        async with lab.admin.connect() as observer:
                            passed = await observer.scalar(
                                text("SELECT clock_timestamp()>=:expires"),
                                {"expires": expires},
                            )
                        if passed is True:
                            break
                        assert asyncio.get_running_loop().time() < deadline
                        assert not task.done()
                        await asyncio.sleep(0.01)
                    # Must still be blocked on this exact held resource AFTER
                    # original expiry, not already failed through lock_timeout.
                    assert not task.done()
                    await _wait_for_lock(
                        lab.admin, proof["db_backend_pid"], identity, task
                    )
                    assert not task.done()
                with pytest.raises(DBAPIError, match="collector_issuer_write_denied"):
                    await asyncio.wait_for(task, timeout=3)
            finally:
                if task is not None and not task.done():
                    task.cancel()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            rows, actual, _ = await _snapshot(lab, item)
            assert rows == [] and actual[:4] == (0, 0, None, None)


async def test_actual_uid_lock_cancellation_has_no_partial_cas(
    monkeypatch, current0054_database
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        prepared = asyncio.get_running_loop().create_future()
        task = None
        try:
            async with lab.admin.begin() as blocker:
                key = await blocker.scalar(text(_LOCK_SQL), _scope_args(item.scope))
                await blocker.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
                )
                identity = await _held_advisory_lock(blocker)
                task = asyncio.create_task(_write(lab, item, prepared=prepared))
                proof, _ = await _await_phase(prepared, task, timeout=3)
                await _wait_for_lock(lab.admin, proof["db_backend_pid"], identity, task)
                assert not task.done()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3)
        finally:
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        rows, actual, _ = await _snapshot(lab, item)
        assert rows == [] and actual[:4] == (0, 0, None, None)
        async with lab.admin.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM public.demo_collector_current_revision_lineages"
                    )
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM public.public_runtime_raw_bundles_v1")
                )
                == 2
            )


class _DeclaredAcknowledgementLost(Exception):
    pass


@pytest.mark.parametrize("fault", ["caller_cancelled", "acknowledgement_withheld"])
async def test_committed_audit_survives_caller_result_loss_without_resubmit(
    monkeypatch, current0054_database, fault
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        committed, release = asyncio.get_running_loop().create_future(), asyncio.Event()

        async def caller():
            result = await _write(lab, item)
            committed.set_result(result)
            if fault == "acknowledgement_withheld":
                raise _DeclaredAcknowledgementLost()
            await release.wait()

        task = asyncio.create_task(caller())
        try:
            proof, values, _ = await _await_phase(committed, task, timeout=5)
            if fault == "caller_cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(_DeclaredAcknowledgementLost):
                    await task
            rows, actual, xid = await _snapshot(lab, item)
            assert len(rows) == 1 and rows[0][0] == values["issuer_sha256"]
            assert actual[:2] == (1, 1) and xid != proof["db_transaction_id"]
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_committed_audit_survives_actual_postgres_backend_termination(
    monkeypatch, current0054_database
):
    # Actual PG connection-process failure; not an OS/application restart claim.
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        async with lab.app.connect() as connection:
            async with connection.begin():
                proof, values = await _candidate(connection, item)
                identity = (
                    (
                        await connection.execute(
                            text("""
                  SELECT pid,backend_start,usesysid::bigint AS role_oid,datid::bigint AS database_oid
                  FROM pg_stat_activity WHERE pid=pg_backend_pid() AND backend_type='client backend'
                """)
                        )
                    )
                    .mappings()
                    .one()
                )
                assert identity["pid"] == proof["db_backend_pid"]
                assert identity["role_oid"] == proof["db_role_oid"]
                assert isinstance(identity["backend_start"], datetime)
                assert (
                    type(identity["database_oid"]) is int
                    and identity["database_oid"] > 0
                )
                await connection.execute(_INSERT, values)
            async with lab.admin.begin() as administrator:
                stopped = (
                    (
                        await administrator.execute(
                            text("""
                  SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                  WHERE pid=:pid AND backend_start=:backend_start
                    AND usesysid::bigint=:role_oid AND datid::bigint=:database_oid
                    AND datname=current_database() AND backend_type='client backend'
                """),
                            dict(identity),
                        )
                    )
                    .scalars()
                    .all()
                )
                assert stopped == [True]
            with pytest.raises(DBAPIError):
                await connection.execute(text("SELECT 1"))
        rows, actual, xid = await _snapshot(lab, item)
        assert len(rows) == 1 and rows[0][0] == values["issuer_sha256"]
        assert actual[:2] == (1, 1) and xid != proof["db_transaction_id"]


async def test_nonempty54_downgrade_preserves_audit_after_exact_actor_grant_revocation(
    monkeypatch, current0054_database
):
    async with _lab(current0054_database) as lab:
        item = await _manual_parents(monkeypatch, lab)
        await _write(lab, item)
        before = (await _snapshot(lab, item))[:2]
        await _grant(lab.admin, lab.app_role, _ISSUED, "SELECT,INSERT", revoke=True)
        try:
            with pytest.raises(
                RuntimeError,
                match="^collector_issuer_revoke_grants_and_preserve_audit$",
            ):
                async with lab.admin.begin() as connection:
                    await connection.run_sync(_migrate, "0053", direction="downgrade")
        finally:
            await _grant(lab.admin, lab.app_role, _ISSUED, "SELECT,INSERT")
        assert (await _snapshot(lab, item))[:2] == before
        async with lab.admin.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                )
                == "0054"
            )


@asynccontextmanager
async def _raw_roles(admin):
    async with (
        collector(admin, grant=True) as (raw, raw_oid),
        collector(admin, grant=False) as (writer, _),
    ):
        roles = SimpleNamespace(
            admin=admin,
            raw=raw,
            raw_oid=raw_oid,
            writer=writer,
            extra_raw_issuer=False,
            raw_factory=async_sessionmaker(
                raw, autoflush=False, expire_on_commit=False
            ),
            writer_factory=async_sessionmaker(
                writer, autoflush=False, expire_on_commit=False
            ),
        )
        try:
            for engine in (raw, writer):
                await _grant(admin, engine.url.username, "alembic_version", "SELECT")
            for table in (_LINEAGE, _ISSUED):
                await _grant(admin, writer.url.username, table, "SELECT,INSERT")
            yield roles
        finally:
            for table in (_LINEAGE, _ISSUED):
                await _grant(
                    admin, writer.url.username, table, "SELECT,INSERT", revoke=True
                )
            if roles.extra_raw_issuer:
                await _grant(admin, raw.url.username, _ISSUED, "SELECT", revoke=True)
            for engine in (raw, writer):
                await _grant(
                    admin, engine.url.username, "alembic_version", "SELECT", revoke=True
                )


async def _raw_read(roles, *, writer_factory=None, raw_oid=None, set_role=False):
    session = roles.raw_factory()
    try:
        await session.begin()
        await settings(session)
        if set_role:
            await session.execute(text(f'SET LOCAL ROLE "{roles.raw.url.username}"'))
        return await raw_api.verify_actual_raw_history_catalog_v1(
            session,
            native_fence._Fence(datetime.now(UTC) + timedelta(seconds=15)),
            roles.raw_oid if raw_oid is None else raw_oid,
            roles.writer_factory if writer_factory is None else writer_factory,
            roles.raw_factory,
        )
    finally:
        await raw_api._close_owned(session)


def _require_real_sealed54():
    # Never monkeypatch or derive a desired baseline from the test's own query.
    assert native54.PG17_NATIVE_BASELINE_STATUS == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    assert native54._required_baseline() == native54.PG17_NATIVE_BASELINE_SHA256


async def test_genuine54_raw_and_separate_writer_factories_preserve_native_source_gates(
    current0054_database,
):
    _require_real_sealed54()
    async with _raw_roles(current0054_database) as roles:
        raw = await _raw_read(roles)
        result = json.loads(raw)["current0054"]
        assert result["profile"]["actual_separate_roles_verified"] is True
        assert result["profile"]["database_head"] == "0054"
        assert (
            result["profile"]["account_complete"]
            is result["profile"]["execution_authority"]
            is False
        )
        assert result["raw"]["role"]["oid"] == roles.raw_oid
        assert result["writer"]["role"]["oid"] != roles.raw_oid
        async with roles.writer_factory() as session, session.begin():
            await settings(session)
            writer = await writer_api.verify_actual_issuer_catalog_v1(session)
            assert writer["role_oid"] == result["writer"]["role"]["oid"]
            assert writer["account_complete"] is writer["execution_authority"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "same_factory",
        "foreign_raw_oid",
        "missing_metadata",
        "missing_issuer_grant",
        "extra_raw_acl",
        "set_role",
        "wrong_head",
    ],
)
async def test_real54_raw_writer_faults_reject_after_actual_positive(
    current0054_database, fault
):
    _require_real_sealed54()
    async with _raw_roles(current0054_database) as roles:
        await _raw_read(roles)
        arguments = {}
        if fault == "same_factory":
            arguments["writer_factory"] = roles.raw_factory
        elif fault == "foreign_raw_oid":
            arguments["raw_oid"] = roles.raw_oid + 1
        elif fault == "missing_metadata":
            await _grant(
                roles.admin,
                roles.raw.url.username,
                "alembic_version",
                "SELECT",
                revoke=True,
            )
        elif fault == "missing_issuer_grant":
            await _grant(
                roles.admin,
                roles.writer.url.username,
                _ISSUED,
                "SELECT,INSERT",
                revoke=True,
            )
        elif fault == "extra_raw_acl":
            roles.extra_raw_issuer = True
            await _grant(roles.admin, roles.raw.url.username, _ISSUED, "SELECT")
        elif fault == "set_role":
            arguments["set_role"] = True
        else:
            async with roles.admin.begin() as connection:
                await connection.execute(
                    text("UPDATE public.alembic_version SET version_num='0053'")
                )
        with pytest.raises(raw_api.Current0054MultiRoleError):
            await _raw_read(roles, **arguments)


async def _actual_owner54(admin):
    async with admin.connect() as connection:
        await settings(connection)
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        return await connection.scalar(text(native54.OWNER_CATALOG_SQL))


async def test_actual54_scope_parent_population_has_three_triggers_and_two_reviewed_functions(
    current0054_database,
):
    value = await _actual_owner54(current0054_database)
    parent = value["scope_parent"]
    assert {row["name"] for row in parent["triggers"]} == {
        "qualification_scope_no_delete",
        "qualification_scope_no_truncate",
        "qualification_scope_update_guard",
    }
    assert {row["name"] for row in parent["functions"]} == {
        "qualification_ledger_immutable",
        "qualification_scope_update",
    }
    observed54 = native54.observe_actual_owner_catalog(value)
    assert (
        observed54["native_baseline_admission"]
        is observed54["account_complete"]
        is observed54["execution_authority"]
        is False
    )
    async with current0054_database.connect() as connection:
        document, raw = await capture54.capture_current0054_owner(connection, ROOT)
    assert document["current0054_observation"] == observed54
    assert journal.canonical(document) + b"\n" == raw


@pytest.mark.parametrize("fault", ["missing", "extra", "disabled", "body_drift"])
async def test_actual54_scope_parent_trigger_or_function_drift_rejects_after_positive(
    current0054_database, fault
):
    admin = current0054_database
    before = await _actual_owner54(admin)
    baseline = native54.observe_actual_owner_catalog(before)
    assert baseline["native_baseline_admission"] is False
    if fault == "extra":
        async with _test_only_scope_wait(admin, 42543100004003):
            changed = await _actual_owner54(admin)
            assert len(changed["scope_parent"]["triggers"]) == 4
            assert len(changed["scope_parent"]["functions"]) == 3
            with pytest.raises(native54.Current0054CatalogError):
                native54.observe_actual_owner_catalog(changed)
    else:
        original_trigger = next(
            row[1]
            for row in before["scope_parent"]["native_definitions"]["triggers"]
            if row[0] == "qualification_scope_update_guard"
        )
        original_function = next(
            row[1]
            for row in before["scope_parent"]["native_definitions"]["functions"]
            if row[0] == "qualification_scope_update"
        )
        try:
            async with admin.begin() as connection:
                if fault == "missing":
                    await connection.execute(
                        text(
                            "DROP TRIGGER qualification_scope_update_guard ON public.qualification_account_scopes"
                        )
                    )
                elif fault == "disabled":
                    await connection.execute(
                        text(
                            "ALTER TABLE public.qualification_account_scopes DISABLE TRIGGER qualification_scope_update_guard"
                        )
                    )
                else:
                    # Explicit fault to the old0017 backing function, never0054.
                    await connection.execute(
                        text("""
                      CREATE OR REPLACE FUNCTION public.qualification_scope_update()
                      RETURNS trigger LANGUAGE plpgsql AS $test_only$ BEGIN RETURN NEW; END; $test_only$
                    """)
                    )
            changed = await _actual_owner54(admin)
            if fault == "missing":
                assert len(changed["scope_parent"]["triggers"]) == 2
            elif fault == "disabled":
                assert (
                    next(
                        row["enabled"]
                        for row in changed["scope_parent"]["triggers"]
                        if row["name"] == "qualification_scope_update_guard"
                    )
                    == "D"
                )
            else:
                assert (
                    next(
                        row["source_sha256"]
                        for row in changed["scope_parent"]["functions"]
                        if row["name"] == "qualification_scope_update"
                    )
                    != native54._SCOPE_PARENT_FUNCTIONS["qualification_scope_update"]
                )
            with pytest.raises(native54.Current0054CatalogError):
                native54.observe_actual_owner_catalog(changed)
        finally:
            async with admin.begin() as connection:
                if fault == "missing":
                    await connection.execute(text(original_trigger))
                elif fault == "disabled":
                    await connection.execute(
                        text(
                            "ALTER TABLE public.qualification_account_scopes ENABLE TRIGGER qualification_scope_update_guard"
                        )
                    )
                else:
                    await connection.execute(text(original_function))
    restored = native54.observe_actual_owner_catalog(await _actual_owner54(admin))
    assert (
        restored["native_definitions_sha256"] == baseline["native_definitions_sha256"]
    )
    assert (await _guard_and_scope_triggers(admin))[0] == issued_schema.statements()[
        1
    ].split("AS $ctcc$", 1)[1].split("$ctcc$ LANGUAGE", 1)[0]
