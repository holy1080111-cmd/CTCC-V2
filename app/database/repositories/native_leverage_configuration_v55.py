"""Actual UID-locked configuration commits and complete independent readback.

Only the fixed native actor replay can supply context. This repository never
accepts caller factories, financial claims, timestamps or source truth flags.
The sole55 catalog must include the new leaves before its seal can admit them.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog,
)
from app.database.repositories import demo_control_current55_v2 as controls
from app.database.repositories import qualification_ledger as ledger
from app.domain import native_leverage_configuration_schema_v55 as schema
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import demo_control as control

_SOURCE_KEY_V1 = object()
_EVENTS = {}
_READBACKS = {}
_MAX_EVENTS = 64
_SCOPE_NAMES = (
    "environment",
    "account_id",
    "settlement_currency",
    "account_revision",
    "ledger_revision",
    "claims_json",
    "claims_sha256",
    "created_at",
    "updated_at",
)
_FENCE_NAMES = (
    "environment",
    "account_id",
    "settlement_currency",
    "account_revision",
    "previous_ledger_revision",
    "ledger_revision",
    "db_role_oid",
    "db_backend_pid",
    "db_transaction_id",
    "db_recorded_at",
)
_INTENT_NAMES = _FENCE_NAMES + (
    "intent_id",
    "claims_sha256",
    "control_revision",
    "control_event_sha256",
    "core_json",
    "source_json",
    "source_sha256",
    "plan_json",
    "plan_sha256",
    "request_json",
    "request_sha256",
    "original_expires_at",
    "phase",
    "latest_result_sequence",
)
_ATTEMPT_NAMES = _FENCE_NAMES + ("intent_id", "attempt_id", "attempt_json")
_RESULT_NAMES = _FENCE_NAMES + (
    "result_id",
    "intent_id",
    "attempt_id",
    "result_sequence",
    "previous_phase",
    "phase",
    "result_json",
)
_META_SQL = text(
    "SELECT current_user,session_user,current_setting('role'),r.oid,r.rolsuper,r.rolcreaterole,r.rolbypassrls,pg_backend_pid(),txid_current()::text,clock_timestamp() FROM pg_catalog.pg_roles r WHERE r.rolname=current_user"
)


class NativeConfigurationRepositoryError(ValueError):
    pass


def _need(condition, code="native_configuration_database_unavailable"):
    if condition is not True:
        raise NativeConfigurationRepositoryError(code) from None


def _key(key):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    _need(key is actor._REPOSITORY_KEY_V1)
    return actor


def _context(binding):
    from app.trade_qualification import native_leverage_configuration_v1 as actor

    return actor._replay_native_leverage_configuration_repository_v1(
        binding, key=_SOURCE_KEY_V1
    )


def _scalar(value):
    if type(value) is datetime:
        _need(value.tzinfo is UTC)
        return value.isoformat()
    _need(value is None or type(value) in (str, int, bool))
    return value


def _row(raw, names):
    _need(type(raw) is dict and set(raw) == set(names))
    return canonical({name: _scalar(raw[name]) for name in names})


def _metadata(value):
    _need(type(value) is tuple and len(value) == 10)
    _need(
        type(value[0]) is str
        and value[0] == value[1]
        and value[2] == "none"
        and type(value[3]) is int
        and value[3] > 0
        and value[4:7] == (False, False, False)
        and all(type(item) is bool for item in value[4:7])
        and type(value[7]) is int
        and value[7] > 0
        and type(value[8]) is str
        and value[8].isascii()
        and value[8].isdigit()
        and 0 < len(value[8]) <= 20
        and type(value[9]) is datetime
        and value[9].tzinfo is UTC
    )
    return value


async def _read_metadata(session, binding):
    result = _metadata(tuple((await session.execute(_META_SQL)).one()))
    _context(binding)
    return result


async def verify_actual_configuration_catalog_v1(session):
    _need(
        type(session) is AsyncSession
        and session.in_transaction()
        and session.get_bind().dialect.name == "postgresql"
    )
    actual = await catalog.verify_actual_issuer_catalog_v2(session)
    _need(
        type(actual) is dict
        and actual.get("database_head") == "0055"
        and actual.get("native_baseline_admission") is True
    )
    # The shared55 verifier must already close all table/FK/index/ACL/trigger
    # predicates. This extra check pins the five exact source-defined bodies.
    expected = schema.source_descriptor()
    for name, _digest, sql in expected:
        function_name = {
            "blocked": "qualification_leverage_configuration_blocked_v55",
            "initial_ledger": "qualification_leverage_configuration_initial_ledger_v55",
            "local_flat": "qualification_leverage_configuration_local_flat_v55",
            "guard": "qualification_leverage_configuration_guard_v55",
            "fence": "qualification_leverage_configuration_fence_v55",
        }[name]
        rows = (
            await session.execute(
                text(
                    "SELECT p.prosrc,p.prosecdef,p.proleakproof,l.lanname,p.proconfig FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace JOIN pg_catalog.pg_language l ON l.oid=p.prolang WHERE n.nspname='public' AND p.proname=:name"
                ),
                {"name": function_name},
            )
        ).all()
        _need(
            len(rows) == 1
            and rows[0][0] == sql.split("$f$")[1]
            and rows[0][1] is False
            and rows[0][2] is False
            and rows[0][3] == ("plpgsql" if name in ("guard", "fence") else "sql")
            and tuple(rows[0][4]) == ("search_path=pg_catalog, public, pg_temp",)
        )
    return actual


async def require_configuration_initial_checkpoint_v1(repository, session, scope, row):
    """Exact A0 configuration-fence chain; no financial/account-zero claim."""
    _need(
        type(repository) is ledger.QualificationLedgerRepository
        and type(session) is AsyncSession
        and type(row) is ledger.QualificationAccountScope
        and row.account_revision == 0
        and type(row.ledger_revision) is int
        and row.ledger_revision > 0
        and row.claims_json is None
        and row.claims_sha256 is None
        and (row.environment, row.account_id, row.settlement_currency)
        == (scope.environment, scope.account_id, scope.settlement_currency)
    )
    await verify_actual_configuration_catalog_v1(session)
    value = (
        await session.execute(
            text(
                "SELECT public.qualification_leverage_configuration_initial_ledger_v55(:e,:a,:c,:ar,:lr,:cs)"
            ),
            {
                "e": scope.environment,
                "a": scope.account_id,
                "c": scope.settlement_currency,
                "ar": row.account_revision,
                "lr": row.ledger_revision,
                "cs": row.claims_sha256,
            },
        )
    ).scalar_one()
    _need(value is True, "native_configuration_initial_fence_chain_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class ConfigurationCommitReadbackV1:
    intent_row_json: bytes
    attempt_row_json: bytes | None
    result_row_json: bytes | None
    scope_json: bytes
    write_metadata: tuple
    read_metadata: tuple


class _CommittedConfigurationEventV1:
    __slots__ = ()

    def __new__(cls):
        raise NativeConfigurationRepositoryError(
            "native_configuration_commit_not_constructible"
        )


async def _transaction_setup(session, binding):
    ctx = _context(binding)
    _need(
        type(session) is AsyncSession
        and session.bind is ctx.source_view.session_factory.kw["bind"]
    )
    for sql in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET LOCAL search_path TO pg_catalog,public,pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit='on'",
        "SET LOCAL lock_timeout='1000ms'",
        "SET LOCAL statement_timeout='1000ms'",
    ):
        await session.execute(text(sql))
        _context(binding)
    await verify_actual_configuration_catalog_v1(session)
    _context(binding)
    actual, event = await controls.DemoControlRepositoryV2.read_in_transaction(
        session, ctx.source_view.control_state.scope
    )
    _context(binding)
    _need(
        canonical(control.document(actual))
        == canonical(control.document(ctx.expected_control_state))
        and event == ctx.expected_control_event_sha256
    )
    repository = ledger.QualificationLedgerRepository(
        ctx.source_view.session_factory, clock=lambda: _context(binding).sampled_at
    )
    row = await repository._locked(session, ctx.source_view.scope)
    _context(binding)
    # _locked acquires the same UID advisory lock reentrantly, after the actual
    # Control read acquired it first. No independently supplied lock key exists.
    scope_raw = _row({name: getattr(row, name) for name in _SCOPE_NAMES}, _SCOPE_NAMES)
    return ctx, repository, row, scope_raw


async def _actual_rows(session, binding, intent_id):
    rows = []
    for table, names, suffix in (
        (schema.INTENT_TABLE, _INTENT_NAMES, ""),
        (schema.ATTEMPT_TABLE, _ATTEMPT_NAMES, ""),
        (schema.RESULT_TABLE, _RESULT_NAMES, " ORDER BY result_sequence DESC LIMIT 1"),
    ):
        values = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM public."
                        + table
                        + " WHERE intent_id=:identity"
                        + suffix
                    ),
                    {"identity": intent_id},
                )
            )
            .mappings()
            .all()
        )
        _context(binding)
        _need(len(values) <= 1)
        rows.append(None if not values else _row(dict(values[0]), names))
    return tuple(rows)


async def _commit(binding, kind, document):
    ctx = _context(binding)
    _need(type(document) is bytes and canonical(decode(document, 262144)) == document)
    _need(len(_EVENTS) < _MAX_EVENTS)
    source = ctx.source_view
    expected = decode(ctx.scope_json)
    payload = decode(document)
    body = {
        "environment": source.scope.environment,
        "account_id": source.scope.account_id,
        "settlement_currency": source.scope.settlement_currency,
        "account_revision": expected["account_revision"],
        "previous_ledger_revision": expected["ledger_revision"],
        "ledger_revision": expected["ledger_revision"] + 1,
    }
    if kind == "intent":
        _need(
            payload["previous_ledger_revision"] == body["previous_ledger_revision"]
            and payload["ledger_revision"] == body["ledger_revision"]
        )
        body.update(
            intent_id=sha(document),
            claims_sha256=expected["claims_sha256"],
            control_revision=source.control_state.revision,
            control_event_sha256=source.control_event_sha256,
            core_json=document.decode("utf-8"),
            source_json=source.source_receipt_json.decode("utf-8"),
            source_sha256=sha(source.source_receipt_json),
            plan_json=source.plan.receipt_json.decode("utf-8"),
            plan_sha256=source.plan.receipt_sha256,
            request_json=source.plan.request_json.decode("ascii"),
            request_sha256=sha(source.plan.request_json),
            original_expires_at=source.original_expires_at,
            phase="prepared",
            latest_result_sequence=0,
        )
        table = schema.INTENT_TABLE
    elif kind == "attempt":
        _need(
            payload["intent_id"] == ctx.intent_id
            and payload["request_sha256"] == sha(source.plan.request_json)
        )
        body.update(
            intent_id=ctx.intent_id,
            attempt_id=sha(document),
            attempt_json=document.decode("utf-8"),
        )
        table = schema.ATTEMPT_TABLE
    else:
        _need(
            kind == "result"
            and payload["intent_id"] == ctx.intent_id
            and payload["request_sha256"] == sha(source.plan.request_json)
        )
        body.update(
            result_id=sha(document),
            intent_id=ctx.intent_id,
            attempt_id=ctx.attempt_id,
            result_sequence=ctx.result_sequence + 1,
            previous_phase=ctx.database_phase,
            phase=payload["classification"],
            result_json=document.decode("utf-8"),
        )
        table = schema.RESULT_TABLE
    for name in ("account_revision", "previous_ledger_revision", "ledger_revision"):
        _need(
            type(body[name]) is int
            and 0 <= body[name] < 2**63
            and payload[name] == body[name]
        )
    async with source.session_factory() as session, session.begin():
        _ctx, repository, row, scope_raw = await _transaction_setup(session, binding)
        _need(scope_raw == ctx.scope_json)
        if kind in ("intent", "attempt"):
            checkpoint = await repository._read_portfolio_checkpoint_locked(
                session, source.scope, row
            )
            _context(binding)
            _need(checkpoint.known_flat is True)
        if kind != "intent":
            actual = await _actual_rows(session, binding, ctx.intent_id)
            _need(
                actual
                == (ctx.intent_row_json, ctx.attempt_row_json, ctx.result_row_json)
            )
        before = await _read_metadata(session, binding)
        names = tuple(body)
        await session.execute(
            text(
                "INSERT INTO public."
                + table
                + " ("
                + ",".join(names)
                + ") VALUES ("
                + ",".join(":" + name for name in names)
                + ")"
            ),
            body,
        )
        _context(binding)
        identity = body["intent_id"]
        actual = await _actual_rows(session, binding, identity)
        saved = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM public.qualification_account_scopes WHERE environment=:environment AND account_id=:account_id AND settlement_currency=:settlement_currency"
                    ),
                    {
                        name: body[name]
                        for name in ("environment", "account_id", "settlement_currency")
                    },
                )
            )
            .mappings()
            .one()
        )
        _context(binding)
        scope_after = _row(dict(saved), _SCOPE_NAMES)
        after = await _read_metadata(session, binding)
        _need(before[:9] == after[:9] and before[9] <= after[9])
        changed = decode(scope_after)
        _need(
            changed["account_revision"] == expected["account_revision"]
            and changed["ledger_revision"] == expected["ledger_revision"] + 1
            and changed["claims_json"] == expected["claims_json"]
            and changed["claims_sha256"] == expected["claims_sha256"]
        )
        _need(actual[0] is not None)
        inserted = decode(
            actual[0]
            if kind == "intent"
            else actual[1]
            if kind == "attempt"
            else actual[2]
        )
        _need(
            inserted["db_role_oid"] == after[3]
            and inserted["db_backend_pid"] == after[7]
            and inserted["db_transaction_id"] == after[8]
            and before[9]
            <= datetime.fromisoformat(inserted["db_recorded_at"])
            <= after[9]
        )
    _context(binding)
    cap = object.__new__(_CommittedConfigurationEventV1)
    _EVENTS[cap] = (
        binding,
        asyncio.current_task(),
        identity,
        actual,
        scope_after,
        after,
        session,
    )
    return cap


async def insert_configuration_intent_v1(binding, document, *, key):
    _key(key)
    return await _commit(binding, "intent", document)


async def insert_configuration_attempt_v1(binding, document, *, key):
    _key(key)
    return await _commit(binding, "attempt", document)


async def append_configuration_result_v1(binding, document, *, key):
    _key(key)
    return await _commit(binding, "result", document)


async def read_configuration_commit_v1(cap, *, key):
    _key(key)
    record = _EVENTS.get(cap) if type(cap) is _CommittedConfigurationEventV1 else None
    _need(record is not None and record[1] is asyncio.current_task())
    (
        binding,
        _task,
        identity,
        expected,
        scope_expected,
        write_metadata,
        write_session,
    ) = record
    ctx = _context(binding)
    async with ctx.source_view.session_factory() as session, session.begin():
        await _transaction_setup(session, binding)
        before = await _read_metadata(session, binding)
        _need(
            session is not write_session
            and before[8] != write_metadata[8]
            and write_metadata[9] <= before[9]
        )
        actual = await _actual_rows(session, binding, identity)
        saved = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM public.qualification_account_scopes WHERE environment=:e AND account_id=:a AND settlement_currency=:c"
                    ),
                    {
                        "e": ctx.source_view.scope.environment,
                        "a": ctx.source_view.scope.account_id,
                        "c": ctx.source_view.scope.settlement_currency,
                    },
                )
            )
            .mappings()
            .one()
        )
        _context(binding)
        scope_after = _row(dict(saved), _SCOPE_NAMES)
        after = await _read_metadata(session, binding)
        _need(
            before[:9] == after[:9]
            and before[9] <= after[9]
            and actual == expected
            and scope_after == scope_expected
        )
    _context(binding)
    result = ConfigurationCommitReadbackV1(*actual, scope_after, write_metadata, after)
    _READBACKS[id(result)] = (result, cap, record)
    return result


def _replay_configuration_readback_v1(value, *, key):
    _key(key)
    record = _READBACKS.get(id(value))
    _need(
        record is not None
        and record[0] is value
        and record[2][1] is asyncio.current_task()
    )
    _context(record[2][0])
    return record[2][0], value


def _retained_configuration_repository_buffers_v1(binding, *, key):
    _key(key)
    values = []
    for record in _EVENTS.values():
        if record[0] is binding:
            values.extend(raw for raw in (*record[3], record[4]) if type(raw) is bytes)
    for readback, _cap, record in _READBACKS.values():
        if record[0] is binding:
            values.extend(
                raw
                for raw in (
                    readback.intent_row_json,
                    readback.attempt_row_json,
                    readback.result_row_json,
                    readback.scope_json,
                )
                if type(raw) is bytes
            )
            values.extend(
                item
                for meta in (readback.write_metadata, readback.read_metadata)
                for item in meta
                if type(item) is str
            )
    return tuple(values)


def _compact_configuration_commits_v1(binding, commits, readbacks, *, key):
    _key(key)
    _need(
        type(commits) is tuple
        and type(readbacks) is tuple
        and len(commits) == len(readbacks)
        and len(commits) in (2, 4)
    )
    task = asyncio.current_task()
    entries = []
    for cap, readback in zip(commits, readbacks, strict=True):
        event = (
            _EVENTS.get(cap) if type(cap) is _CommittedConfigurationEventV1 else None
        )
        saved = _READBACKS.get(id(readback))
        _need(
            event is not None
            and event[0] is binding
            and event[1] is task
            and saved is not None
            and saved[0] is readback
            and saved[1] is cap
            and saved[2] is event
            and type(readback) is ConfigurationCommitReadbackV1
            and readback.write_metadata[8] != readback.read_metadata[8]
        )
        entries.append((cap, id(readback)))
    _need(
        len({cap for cap, _ in entries}) == len(entries)
        and {cap for cap, row in _EVENTS.items() if row[0] is binding} == set(commits)
        and {identity for identity, row in _READBACKS.items() if row[2][0] is binding}
        == {identity for _, identity in entries}
    )
    final = readbacks[-1]
    expected = "no_change" if len(commits) == 2 else "settled"
    _need(
        type(final.result_row_json) is bytes
        and decode(final.result_row_json)["phase"] == expected
    )
    for cap, identity in entries:
        del _READBACKS[identity]
        del _EVENTS[cap]
