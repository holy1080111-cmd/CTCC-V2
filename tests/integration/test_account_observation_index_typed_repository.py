"""DB0040 typed B3 persistence over synthetic, immutable Demo B1 captures."""

import asyncio
import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.database.repositories.account_observation_index_typed import (
    PERSISTENCE_POLICY_SHA256,
    AccountObservationTypedIndexRepository,
    Batch,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as query
from app.trade_qualification import account_observation_index as observed
from tests.durable_migration_fixtures import load_migration
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    now,
    stored,
)
from tests.integration.test_durable_migration_downgrade import migrate, shape
from tests.integration.test_durable_migration_downgrade import (
    sandbox as sandbox,  # noqa: PLC0414
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _db0040_guard_bodies(migration):
    rendered = {
        "account_observation_typed_batch_guard": migration._batch_guard(),
        "account_observation_typed_member_guard": migration._member_guard(),
        "account_observation_typed_membership_guard": migration._membership_guard(),
    }
    bodies = {}
    for name, sql in rendered.items():
        opening = f"CREATE FUNCTION public.{name}() RETURNS trigger AS $$"
        closing = "$$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;"
        assert sql.count(opening) == sql.count(closing) == 1
        before, after = sql.split(opening, 1)
        body, remainder = after.split(closing, 1)
        assert not before.strip() and not remainder.strip()
        bodies[name] = body.encode("utf-8")
    return bodies


def _db0040_search_path_is_pinned(settings):
    if type(settings) not in (list, tuple) or len(settings) != 1:
        return False
    setting = settings[0]
    if type(setting) is not str:
        return False
    name, separator, value = setting.partition("=")
    return (
        name == "search_path"
        and separator == "="
        and tuple(part.strip() for part in value.split(","))
        == ("pg_catalog", "pg_temp")
    )


async def _db0040_catalog_matches(connection, bodies, tables):
    function_rows = await connection.execute(
        text("""
                SELECT p.proname AS name, p.prosrc AS body,
                       p.proconfig AS settings,
                       (lang.lanname='plpgsql' AND p.provolatile='v'
                        AND NOT p.prosecdef AND p.prokind='f' AND p.pronargs=0
                        AND p.prorettype='pg_catalog.trigger'::regtype)
                         AS properties_valid,
                       NOT EXISTS (
                         SELECT 1 FROM pg_catalog.aclexplode(
                           COALESCE(p.proacl,
                                    pg_catalog.acldefault('f',p.proowner))
                         ) AS acl
                         WHERE acl.grantee=0
                           AND acl.privilege_type='EXECUTE'
                       ) AS public_execute_revoked
                FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                JOIN pg_catalog.pg_language lang ON lang.oid=p.prolang
                WHERE n.nspname='public'
                  AND p.proname IN (
                    'account_observation_typed_batch_guard',
                    'account_observation_typed_member_guard',
                    'account_observation_typed_membership_guard'
                  )
            """)
    )
    functions = function_rows.mappings().all()
    if len(functions) != len(bodies) or {row["name"] for row in functions} != set(
        bodies
    ):
        return False
    for function in functions:
        name = function["name"]
        body = function["body"]
        if (
            type(body) is not str
            or body.encode("utf-8") != bodies[name]
            or function["properties_valid"] is not True
            or not _db0040_search_path_is_pinned(function["settings"])
            or function["public_execute_revoked"] is not True
        ):
            return False

    batch = "demo_account_observation_typed_batches"
    immutable = "qualification_ledger_immutable"
    expected = {
        (batch, "account_observation_typed_batch_guard"): (
            7,
            "account_observation_typed_batch_guard",
            False,
            False,
            False,
        ),
        (batch, "account_observation_typed_membership_guard"): (
            5,
            "account_observation_typed_membership_guard",
            True,
            True,
            True,
        ),
    }
    for table in tables:
        if table != batch:
            expected[(table, "account_observation_typed_member_guard")] = (
                7,
                "account_observation_typed_member_guard",
                False,
                False,
                False,
            )
        expected[(table, f"{table}_immutable")] = (27, immutable, False, False, False)
        expected[(table, f"{table}_no_truncate")] = (34, immutable, False, False, False)
    assert len(expected) == 13
    trigger_rows = await connection.execute(
        text("""
                SELECT tab.relname AS table_name, tg.tgname AS trigger_name,
                       tg.tgtype::integer AS kind,
                       tg.tgdeferrable AS deferrable,
                       tg.tginitdeferred AS initially_deferred,
                       tg.tgconstraint::bigint<>0 AS constraint_trigger,
                       proc.proname AS function_name,
                       (tg.tgenabled='O' AND proc_ns.nspname='public'
                        AND proc.pronargs=0
                        AND proc.prorettype='pg_catalog.trigger'::regtype
                        AND proc.oid=pg_catalog.to_regprocedure(
                          'public.'||proc.proname||'()')) AS binding_valid
                FROM pg_catalog.pg_trigger tg
                JOIN pg_catalog.pg_class tab ON tab.oid=tg.tgrelid
                JOIN pg_catalog.pg_namespace tab_ns
                  ON tab_ns.oid=tab.relnamespace
                JOIN pg_catalog.pg_proc proc ON proc.oid=tg.tgfoid
                JOIN pg_catalog.pg_namespace proc_ns
                  ON proc_ns.oid=proc.pronamespace
                WHERE tab_ns.nspname='public' AND NOT tg.tgisinternal
                  AND tab.relname IN (
                    'demo_account_observation_typed_batches',
                    'demo_account_observation_typed_facts',
                    'demo_account_observation_typed_coverage',
                    'demo_account_observation_typed_findings'
                  )
            """)
    )
    triggers = trigger_rows.mappings().all()
    if len(triggers) != len(expected):
        return False
    actual = {}
    for trigger in triggers:
        key = (trigger["table_name"], trigger["trigger_name"])
        if key in actual or trigger["binding_valid"] is not True:
            return False
        actual[key] = (
            trigger["kind"],
            trigger["function_name"],
            trigger["deferrable"],
            trigger["initially_deferred"],
            trigger["constraint_trigger"],
        )
    return actual == expected


async def test_db0040_catalog_matches_guard_source_and_rejects_drift(sandbox):
    engine, _ = sandbox
    migration = load_migration("0040")
    bodies = _db0040_guard_bodies(migration)
    tables = migration._TABLES
    changed_guard = migration._batch_guard()
    old_reason = "account_observation_typed_append_denied"
    assert changed_guard.count(old_reason) == 1
    changed_guard = changed_guard.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    ).replace(old_reason, "account_observation_typed_drift_probe_denied", 1)
    mutations = (
        changed_guard,
        (
            "ALTER TABLE public.demo_account_observation_typed_batches "
            "DISABLE TRIGGER account_observation_typed_batch_guard"
        ),
        (
            "GRANT EXECUTE ON FUNCTION "
            "public.account_observation_typed_batch_guard() TO PUBLIC"
        ),
    )
    for statement in mutations:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                assert await _db0040_catalog_matches(connection, bodies, tables)
                await connection.exec_driver_sql(statement)
                assert not await _db0040_catalog_matches(connection, bodies, tables)
            finally:
                await transaction.rollback()

    async with engine.connect() as connection:
        assert await _db0040_catalog_matches(connection, bodies, tables)


@pytest.mark.parametrize("revision", ["0040"])
async def test_db0040_rendered_batch_guard_compiles_in_postgresql(sandbox, revision):
    engine, _ = sandbox
    guard = load_migration(revision)._batch_guard()
    production = "public.account_observation_typed_batch_guard()"
    probe = "public.account_observation_typed_batch_guard_syntax_probe()"
    assert guard.count(production) == 1
    async with engine.begin() as connection:
        await connection.exec_driver_sql(guard.replace(production, probe, 1))
        assert (
            await connection.scalar(
                text("SELECT to_regprocedure(:probe)").bindparams(probe=probe)
            )
            is not None
        )
        await connection.exec_driver_sql(f"DROP FUNCTION {probe}")


async def test_old_b3_and_typed_b3_coexist_with_exact_original_source(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    old_bytes = old_receipt.replay.receipt_json
    old_document = old_receipt.document_json
    original = await old.journal.read_chain(scope, reference.capture_id)
    pins = observed._pins(reference, scope)
    predecessor = query.verify_history_query_chain(original, **pins)
    typed_source = query.verify_history_query_chain_v2(original, **pins)

    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    committed = await typed.append(scope, reference, window, expected_revision=0)
    value = json.loads(committed.replay.receipt_json)
    document = json.loads(committed.document_json)
    assert document["policy_sha256"] == PERSISTENCE_POLICY_SHA256
    assert document["typed_policy_sha256"] == observed.TYPED_POLICY_SHA256
    assert value["schema_version"] == "ctcc.observed_account_execution_window.v3"
    assert value["captures"][0]["query_receipt_sha256"] == typed_source.receipt_sha256
    assert (
        value["captures"][0]["legacy_v1_query_receipt_sha256"]
        == predecessor.receipt_sha256
    )
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["source_authenticity_verified"] is False
    assert value["admission"] == "DENY"
    assert all("generation_at" not in item for item in value["source_index"])
    assert await old.read(scope) == (old_receipt,)
    assert old_receipt.replay.receipt_json == old_bytes
    assert old_receipt.document_json == old_document
    restarted = AccountObservationTypedIndexRepository(database[1], clock=now)
    assert await restarted.read(scope) == (committed,)
    assert (
        await typed.append(scope, reference, window, expected_revision=0) == committed
    )


async def test_typed_prefix_extends_without_touching_legacy_prefix(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    first = await typed.append(scope, reference, window, expected_revision=0)
    next_reference, next_window = await additional_capture(monkeypatch, old, scope, [])
    second = await typed.append(scope, next_reference, next_window, expected_revision=1)
    assert second.sequence == 2
    assert second.event_sha256 != first.event_sha256
    assert (
        json.loads(second.replay.receipt_json)["continuation_anchor"][
            "previous_event_sha256"
        ]
        == first.event_sha256
    )
    assert await typed.read(scope) == (first, second)
    assert await old.read(scope) == (old_receipt,)


async def test_typed_append_concurrency_is_one_durable_row(monkeypatch, database):
    old, scope, reference, window, _ = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    outcomes = await asyncio.gather(
        typed.append(scope, reference, window, expected_revision=0),
        typed.append(scope, reference, window, expected_revision=0),
        return_exceptions=True,
    )
    assert any(not isinstance(item, Exception) for item in outcomes)
    async with database[1]() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Batch)
            .filter_by(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
            )
        )
    assert count == 1
    assert (await typed.read(scope))[0].admission == "DENY"
    assert await old.read(scope)


async def test_typed_batches_are_immutable_and_original_legacy_survives(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    committed = await typed.append(scope, reference, window, expected_revision=0)
    for statement in (
        "UPDATE public.demo_account_observation_typed_batches SET receipt_json=receipt_json WHERE capture_id=:capture_id",
        "DELETE FROM public.demo_account_observation_typed_batches WHERE capture_id=:capture_id",
        "TRUNCATE public.demo_account_observation_typed_batches",
    ):
        with pytest.raises(DBAPIError):
            async with database[1]() as session, session.begin():
                await session.execute(
                    text(statement), {"capture_id": reference.capture_id}
                )
    assert await typed.read(scope) == (committed,)
    assert await old.read(scope) == (old_receipt,)


async def test_database_refuses_forged_typed_policy_even_with_matching_hash(
    monkeypatch, database
):
    old, scope, reference, window, old_receipt = await stored(monkeypatch, database)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    document, replay, _, _, _, chain, _ = await typed._derive_with_current_source(
        scope, reference, window, 1
    )
    forged = json.loads(document)
    forged["typed_policy_sha256"] = "0" * 64
    bytes_to_store = journal.canonical(forged)
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            session.add(
                Batch(
                    environment=scope.environment,
                    account_id=scope.account_id,
                    settlement_currency=scope.settlement_currency,
                    sequence=1,
                    capture_id=reference.capture_id,
                    source_sequence=len(chain),
                    previous_sha256=None,
                    event_sha256=journal.digest(bytes_to_store),
                    document_json=bytes_to_store.decode(),
                    receipt_json=replay.receipt_json.decode(),
                )
            )
            await session.flush()
    assert await typed.read(scope) == ()
    assert await old.read(scope) == (old_receipt,)


@pytest.mark.parametrize("revision", ["0040"])
async def test_db0040_empty_downgrade_and_reupgrade_preserves_exact_schema(
    sandbox, revision
):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "downgrade")
    assert await shape(engine) != before
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "upgrade")
    assert await shape(engine) == before


@pytest.mark.parametrize("revision", ["0040"])
async def test_db0040_active_writer_denies_downgrade_without_partial_drop(
    sandbox, revision
):
    engine, _ = sandbox
    before = await shape(engine)
    locked_tables = (
        "demo_account_capture_events",
        *load_migration(revision)._TABLES,
    )
    assert len(locked_tables) == len(set(locked_tables)) == 5
    for table in locked_tables:
        async with engine.begin() as writer:
            await writer.execute(
                text(f"LOCK TABLE public.{table} IN ROW EXCLUSIVE MODE")
            )
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as migration:
                    await migration.execute(
                        text("SET LOCAL statement_timeout='2000ms'")
                    )
                    await migration.run_sync(migrate, revision, "downgrade")
            assert denied.value.orig.sqlstate == "55P03"  # lock_not_available
        assert await shape(engine) == before


@pytest.mark.parametrize("revision", ["0040"])
async def test_db0040_nonempty_downgrade_keeps_old_and_typed_evidence(
    monkeypatch, sandbox, revision
):
    engine, _ = sandbox
    old, scope, reference, window, legacy = await stored(monkeypatch, sandbox)
    typed = AccountObservationTypedIndexRepository(sandbox[1], clock=now)
    accepted = await typed.append(scope, reference, window, expected_revision=0)
    before = await shape(engine)
    with pytest.raises(
        DBAPIError, match="account_observation_typed_downgrade_requires_empty"
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, revision, "downgrade")
    assert await shape(engine) == before
    assert await old.read(scope) == (legacy,)
    assert await typed.read(scope) == (accepted,)
