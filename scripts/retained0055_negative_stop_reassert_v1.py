"""Separate source-owned revision0->1 negative Stop, never Arm or Stop clear.

Confirmed mutation remains blocked by the original-copy lineage prerequisite.
Read-only output, hashes and saved receipts are DATA, never write authority.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import io
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from scripts import native_owner_upgrade_0056_v1 as canonical
from scripts import retained0017_owner_upgrade_0055_v1 as owner
from scripts import retained0055_negative_stop_guard_v1 as guard
from scripts.evidence_json import read_evidence_json

SCHEMA = "ctcc.retained0055.negative_stop_reassertion.v1"
PINS_SCHEMA = "ctcc.retained0055.negative_stop_pins.v1"
CONFIRMATION = "REASSERT STORED STOP AT DB0055 REVISION ZERO"
ACTION = "retained0055_negative_stop_reassert_v1"
_CONTROL_SQL = text("""SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
 'id',id,'armed',armed,'emergency_stop',emergency_stop,'locked',locked,
 'restart_latch_required',restart_latch_required,'control_revision',control_revision,
 'lock_reasons',lock_reasons) ORDER BY id) FROM public.demo_automation_state""")
_CAS_SQL = text("""UPDATE public.demo_automation_state
 SET control_revision=1,lock_reasons=lock_reasons||'["emergency_stop_engaged"]'::pg_catalog.jsonb
 WHERE id=1 AND control_revision=0 AND armed IS FALSE AND emergency_stop IS TRUE
 AND locked IS TRUE AND restart_latch_required IS TRUE
 AND lock_reasons IS NOT DISTINCT FROM CAST(:old_reasons AS pg_catalog.jsonb)
 RETURNING control_revision""")
_INSERT_SQL = text("""INSERT INTO public.audit_logs
 (id,actor,action,resource_type,resource_id,"before","after",ip_address,occurred_at)
 VALUES(CAST(:operation_id AS pg_catalog.uuid),'source_owned_maintenance',
 'retained0055_negative_stop_reassert_v1','demo_automation_state','1',
 CAST(:before_json AS pg_catalog.jsonb),CAST(:after_json AS pg_catalog.jsonb),NULL,CURRENT_TIMESTAMP)
 RETURNING id::pg_catalog.text""")


class NegativeStopRefused(ValueError):
    pass


class NegativeStopCommitUnknown(RuntimeError):
    def __init__(self, operation_id):
        super().__init__("retained0055_negative_stop_commit_unknown_do_not_retry")
        self.operation_id = operation_id


class NegativeStopReadbackRequired(RuntimeError):
    def __init__(self, operation_id):
        super().__init__("retained0055_negative_stop_committed_readback_required")
        self.operation_id = operation_id


def _need(value):
    if value is not True:
        raise NegativeStopRefused("retained0055_negative_stop_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class NegativeStopPinsV1:
    retained: canonical.OwnerUpgradePinsV1
    maintenance_settings_sha256: str
    reviewed_snapshot_sha256: str | None = None


class _Phase(Enum):
    READ = "readonly_negative_eligibility"
    WRITE = "one_negative_reassertion"


class _Projection(Enum):
    EXPECTED_CONTROL = "expected_revision1_and_one_appended_reason"
    ORIGINAL_AUDIT = "original_audit_rows_excluding_only_operation_uuid"
    NEW_JOURNAL = "exactly_one_new_operation_uuid"


def _prepare(pins, owner_dsn_file, ca_file, archive_file):
    _need(type(pins) is NegativeStopPinsV1)
    _need(type(pins.retained) is canonical.OwnerUpgradePinsV1)
    _need(pins.retained.route.database_head == "0055")
    _need(owner.original._digest(pins.maintenance_settings_sha256))
    _need(
        pins.reviewed_snapshot_sha256 is None
        or owner.original._digest(pins.reviewed_snapshot_sha256)
    )
    canonical._require_positive_private_file_v2(owner_dsn_file)
    return canonical._prepared(pins.retained, owner_dsn_file, ca_file, archive_file)


def _source(pins, archive_file):
    canonical._verify_reviewed_archive(pins.retained, archive_file)
    with redirect_stdout(io.StringIO()):
        scope = canonical.scopes.database0055_source_scope(canonical._SOURCE)
    _need(
        scope.actual_source_head in ("0056", "0057", "0058")
        and scope.database_target == "0055"
        and scope.manifest_sha256 == pins.retained.route.source_manifest_sha256
    )


def _inspection_pins_v1(pins):
    _need(type(pins) is NegativeStopPinsV1)
    inspection = owner._StoppedTarget55PinsV1(
        pins.retained, pins.maintenance_settings_sha256
    )
    owner._transaction_source_v1(inspection)
    return inspection


async def _begin(connection, pins, phase):
    _need(type(phase) is _Phase)
    inspection = _inspection_pins_v1(pins)
    probe = await connection.begin()
    try:
        await owner.rows.heap_am.require_native_owner_storage_v1(connection)
        await owner._maintenance(connection, inspection, before_settings=True)
    finally:
        await probe.rollback()
    transaction = await connection.begin()
    try:
        for statement in (
            canonical._OWNER_SETTINGS
            if phase is _Phase.WRITE
            else canonical._READBACK_SETTINGS
        ):
            await connection.exec_driver_sql(statement)
    except BaseException:
        await transaction.rollback()
        raise
    return transaction


def _control(value, *, revision):
    _need(type(revision) is int and revision in (0, 1))
    keys = {
        "id",
        "armed",
        "emergency_stop",
        "locked",
        "restart_latch_required",
        "control_revision",
        "lock_reasons",
    }
    _need(
        type(value) is list
        and len(value) == 1
        and type(value[0]) is dict
        and set(value[0]) == keys
    )
    row = value[0]
    _need(type(row["id"]) is int and row["id"] == 1)
    _need(type(row["control_revision"]) is int and row["control_revision"] == revision)
    _need(
        row["armed"] is False
        and row["emergency_stop"]
        is row["locked"]
        is row["restart_latch_required"]
        is True
    )
    reasons = row["lock_reasons"]
    _need(
        type(reasons) is list
        and all(type(reason) is str and len(reason) > 0 for reason in reasons)
    )
    return row


async def _observe(connection, pins, phase):
    _need(type(phase) is _Phase)
    inspection = _inspection_pins_v1(pins)
    # Native bootstrap before the first head/row/deparser. The marker and all
    # source55 tables remain locked through the entire current observation.
    await owner.rows.heap_am.lock_native_owner_marker_v1(connection)
    await owner._actor(connection, inspection, "0055", read_only=phase is _Phase.READ)
    layouts = await owner._layouts(
        connection, inspection, expected_names=owner._target_tables()
    )
    await connection.exec_driver_sql(
        "LOCK TABLE "
        + ",".join('public."' + name + '"' for name in sorted(owner._target_tables()))
        + (
            " IN ACCESS EXCLUSIVE MODE NOWAIT"
            if phase is _Phase.WRITE
            else " IN ACCESS SHARE MODE NOWAIT"
        )
    )
    await owner.rows.heap_am.require_native_owner_storage_v1(connection)
    _need(await connection.scalar(text(owner.baseline._LANGUAGE_ADMISSION_SQL)) is True)
    metadata = await connection.scalar(text(guard.CATALOG_SQL))
    meta_sha = guard.require_control_audit_v1(
        metadata, layouts, pins.retained.route.owner_oid
    )
    document = await connection.scalar(text(owner.target55.OWNER_CATALOG_SQL))
    observed = (
        owner.target55._observe_retained_postcommit_owner_catalog_v1(document)
        if phase is _Phase.READ
        else owner.target55._observed(document, owner_mode=True, read_only="off")
    )
    _need(observed["native_definitions_sha256"] == owner.target55._required_baseline())
    _need(
        await owner._layouts(
            connection, inspection, expected_names=owner._target_tables()
        )
        == layouts
    )
    await owner._maintenance(connection, inspection, before_settings=False)
    state = await owner._state(connection, inspection, layouts)
    state["control"] = await connection.scalar(_CONTROL_SQL)
    state["legacy_control_audit_metadata_sha256"] = meta_sha
    return state


async def _read(engine, pins, *, expected_after_sha256=None):
    if expected_after_sha256 is not None:
        from scripts import retained_pg_bootstrap_custody_v1 as custody

        custody._guard()
    async with engine.connect() as connection:
        transaction = await _begin(connection, pins, _Phase.READ)
        try:
            if expected_after_sha256 is not None:
                actual = (
                    await connection.exec_driver_sql(
                        "SELECT pg_catalog.current_setting('default_transaction_read_only'), "
                        "pg_catalog.current_setting('transaction_read_only'), "
                        "pg_catalog.current_setting('transaction_isolation'), "
                        "pg_catalog.host(pg_catalog.inet_server_addr())"
                    )
                ).one()
                _need(tuple(actual) == ("on", "on", "repeatable read", "127.0.0.1"))
                await custody._read_maintenance_lock_profile_v1(connection)
                custody._guard()
                await owner._replay_stopped0055_continuity_v1(
                    connection,
                    _inspection_pins_v1(pins),
                    expected_after_sha256=expected_after_sha256,
                )
            return await _observe(connection, pins, _Phase.READ)
        finally:
            await transaction.rollback()


async def _plan_stopped_copy_v1(engine, pins, archive_file, *, expected_after_sha256):
    """Read the sealed target and negative Stop evidence twice. DATA only."""
    _inspection_pins_v1(pins)
    _need(type(engine) is AsyncEngine and type(engine.pool) is NullPool)
    _need(owner.original._digest(expected_after_sha256))
    _source(pins, archive_file)
    async with asyncio.timeout(300):
        before = await _read(engine, pins, expected_after_sha256=expected_after_sha256)
        _control(before["control"], revision=0)
        _need(
            await _read(engine, pins, expected_after_sha256=expected_after_sha256)
            == before
        )
    _source(pins, archive_file)
    snapshot = _digest(before)
    _need(
        pins.reviewed_snapshot_sha256 is None
        or pins.reviewed_snapshot_sha256 == snapshot
    )
    return {
        "schema": SCHEMA,
        "status": "READ_ONLY_STOPPED_COPY_NEGATIVE_ELIGIBILITY_DATA",
        "database_head": "0055",
        "control_revision": 0,
        "source_upgrade_after_sha256": expected_after_sha256,
        "snapshot_sha256": snapshot,
        "independent_readbacks": 2,
        "mutation_performed": False,
        "exchange_flat": None,
        "account_complete": False,
        "execution_authority": False,
    }


def _projection_sql(layouts, phase):
    _need(type(phase) is _Projection)
    name = guard.STATE if phase is _Projection.EXPECTED_CONTROL else guard.AUDIT
    matches = [columns for actual, _oid, _kind, columns in layouts if actual == name]
    _need(len(matches) == 1)
    columns = [column[0] for column in matches[0]]
    original = 'public."' + name + '" r'
    if phase is _Projection.EXPECTED_CONTROL:
        expressions = []
        for column in columns:
            value = 's."' + column + '"'
            if column == "control_revision":
                value += "+1"
            elif column == "lock_reasons":
                value += "||'[\"emergency_stop_engaged\"]'::pg_catalog.jsonb"
            expressions.append(value + ' AS "' + column + '"')
        replacement = (
            "(SELECT " + ",".join(expressions) + ' FROM public."' + name + '" s) r'
        )
    else:
        operator = "<>" if phase is _Projection.ORIGINAL_AUDIT else "="
        replacement = (
            "(SELECT "
            + ",".join('s."' + column + '"' for column in columns)
            + ' FROM public."'
            + name
            + '" s WHERE s.id'
            + operator
            + "CAST(:operation_id AS pg_catalog.uuid)) r"
        )
    statements = owner.rows.row_multiset_sql_v2(name, columns)
    _need(all(str(statement).count(original) == 1 for statement in statements))
    # Substitute only this fixed source template's FROM relation. The common
    # SQL-null bitmap, duplicate-row encoding and C ordering stay identical.
    return tuple(
        text(str(statement).replace(original, replacement)) for statement in statements
    )


async def _project(connection, layouts, phase, operation_id=None):
    size, digest = _projection_sql(layouts, phase)
    parameters = {} if operation_id is None else {"operation_id": operation_id}
    count, capacity = tuple((await connection.execute(size, parameters)).one())
    _need(type(count) is int and 0 <= count <= owner.original.MAX_ROWS)
    _need(type(capacity) is int and 0 <= capacity <= owner.original.MAX_TABLE_BYTES)
    sha = await connection.scalar(digest, parameters)
    _need(owner.original._digest(sha))
    return count, sha


def _digest(value):
    return owner.baseline.sha(owner.baseline.canonical(value))


def _expected_rows(before, expected_control):
    rows = copy.deepcopy(before["rows"])
    _need(sum(row[0] == guard.STATE for row in rows) == 1)
    for row in rows:
        if row[0] == guard.STATE:
            _need(row[1] == expected_control[0] == 1)
            row[2] = expected_control[1]
    return rows


async def _verify_delta(
    connection, before, after, expected_control, operation_id, journal
):
    _need(set(before) == set(after))
    for field in before:
        if field not in ("rows", "control"):
            _need(before[field] == after[field])
    old = {row[0]: row for row in before["rows"]}
    new = {row[0]: row for row in after["rows"]}
    _need(
        len(old) == len(before["rows"])
        and len(new) == len(after["rows"])
        and set(old) == set(new)
    )
    for name, value in old.items():
        if name == guard.STATE:
            _need(tuple(new[name][1:]) == expected_control)
        elif name == guard.AUDIT:
            _need(new[name][1] == value[1] + 1)
            _need(
                await _project(
                    connection,
                    after["row_layouts"],
                    _Projection.ORIGINAL_AUDIT,
                    operation_id,
                )
                == tuple(value[1:])
            )
        else:
            _need(new[name] == value)
    old_control = _control(before["control"], revision=0)
    actual = _control(after["control"], revision=1)
    expected = copy.deepcopy(old_control)
    expected["control_revision"] = 1
    expected["lock_reasons"] = old_control["lock_reasons"] + [guard.REASON]
    _need(actual == expected)
    _need(
        await _project(
            connection, after["row_layouts"], _Projection.NEW_JOURNAL, operation_id
        )
        == journal
    )


async def _write_once(engine, pins, archive_file, before):
    owner._require_apply_lineage()
    operation_id = uuid4()
    attempted = committed = False
    try:
        async with engine.connect() as connection:
            transaction = await _begin(connection, pins, _Phase.WRITE)
            try:
                await owner.rows.heap_am.require_native_owner_storage_v1(connection)
                await connection.execute(canonical._OWNER_LOCK)
                _need(
                    await connection.scalar(
                        text("SELECT pg_catalog.pg_try_advisory_xact_lock(1680010)")
                    )
                    is True
                )
                await connection.exec_driver_sql(canonical._LOCK_REVISION)
                current = await _observe(connection, pins, _Phase.WRITE)
                _need(current == before)
                control = _control(current["control"], revision=0)
                expected_control = await _project(
                    connection, current["row_layouts"], _Projection.EXPECTED_CONTROL
                )
                _need(expected_control[0] == 1)
                _source(pins, archive_file)
                before_json = {
                    "schema": SCHEMA,
                    "purpose": "negative_stop_reassertion_not_human_arm_or_clear",
                    "source_commit": pins.retained.source_commit,
                    "source_tree": pins.retained.source_tree,
                    "before_snapshot_sha256": _digest(before),
                    "before_rows_sha256": _digest(before["rows"]),
                    "control_revision": 0,
                    "execution_authority": False,
                }
                after_json = {
                    "schema": SCHEMA,
                    "expected_retained_rows_sha256": _digest(
                        _expected_rows(before, expected_control)
                    ),
                    "expected_control_table_sha256": expected_control[1],
                    "control_revision": 1,
                    "reason_appended": guard.REASON,
                    "execution_authority": False,
                }
                actual_id = await connection.scalar(
                    _INSERT_SQL,
                    {
                        "operation_id": operation_id,
                        "before_json": owner.baseline.canonical(before_json).decode(
                            "ascii"
                        ),
                        "after_json": owner.baseline.canonical(after_json).decode(
                            "ascii"
                        ),
                    },
                )
                _need(type(actual_id) is str and actual_id == str(operation_id))
                journal = await _project(
                    connection,
                    current["row_layouts"],
                    _Projection.NEW_JOURNAL,
                    operation_id,
                )
                _need(journal[0] == 1)
                revision = await connection.scalar(
                    _CAS_SQL,
                    {
                        "old_reasons": owner.baseline.canonical(
                            control["lock_reasons"]
                        ).decode("ascii")
                    },
                )
                _need(type(revision) is int and revision == 1)
                final = await _observe(connection, pins, _Phase.WRITE)
                await _verify_delta(
                    connection, before, final, expected_control, operation_id, journal
                )
                _source(pins, archive_file)
                _need(
                    asyncio.current_task() is not None
                    and not asyncio.current_task().cancelling()
                )
                attempted = True
                await transaction.commit()
                committed = True
                return operation_id, expected_control, journal, final
            except BaseException:
                if not attempted:
                    await transaction.rollback()
                raise
    except BaseException:  # noqa: BLE001 -- redact failures/cancellation after the monotone commit boundary.
        if committed:
            raise NegativeStopReadbackRequired(operation_id) from None
        if attempted:
            raise NegativeStopCommitUnknown(operation_id) from None
        raise NegativeStopRefused("retained0055_negative_stop_refused") from None


async def reassert_retained_owner_0055_stop_v1(
    pins, *, owner_dsn_file, ca_file, archive_file, confirmation=None
):
    _need(confirmation is None or confirmation == CONFIRMATION)
    if confirmation is not None:
        owner._require_apply_lineage()
    prepared = _prepare(pins, owner_dsn_file, ca_file, archive_file)
    engine = create_async_engine(
        prepared[0].url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "ssl": prepared[0].tls,
            "timeout": 10,
            "server_settings": dict(owner._STARTUP_SETTINGS),
        },
    )
    committed = False
    operation_id = None
    try:
        async with asyncio.timeout(300):
            before = await _read(engine, pins)
            _control(before["control"], revision=0)
            _need(await _read(engine, pins) == before)
            _source(pins, archive_file)
            if confirmation is None:
                return {
                    "schema": SCHEMA,
                    "status": "READ_ONLY_NEGATIVE_ELIGIBILITY_DATA",
                    "snapshot_sha256": _digest(before),
                    "mutation_performed": False,
                    "exchange_flat": None,
                    "execution_authority": False,
                }
            _need(
                owner.original._digest(pins.reviewed_snapshot_sha256)
                and _digest(before) == pins.reviewed_snapshot_sha256
            )
            operation_id, expected_control, journal, final = await _write_once(
                engine, pins, archive_file, before
            )
            committed = True
            for _ in range(2):
                _prepare(pins, owner_dsn_file, ca_file, archive_file)
                async with engine.connect() as connection:
                    transaction = await _begin(connection, pins, _Phase.READ)
                    try:
                        actual = await _observe(connection, pins, _Phase.READ)
                        _need(actual == final)
                        await _verify_delta(
                            connection,
                            before,
                            actual,
                            expected_control,
                            operation_id,
                            journal,
                        )
                    finally:
                        await transaction.rollback()
            return {
                "schema": SCHEMA,
                "status": "COMMITTED_NEGATIVE_STOP_READBACK_VERIFIED",
                "operation_id": str(operation_id),
                "database_head": "0055",
                "control_revision": 1,
                "armed": False,
                "emergency_stop": True,
                "restart_latch_required": True,
                "independent_readbacks": 2,
                "old_rows_preserved_except_declared_control_delta": True,
                "new_journal_rows": 1,
                "schema_upgrade_authorized": False,
                "full_schema0055_verified": False,
                "human_arm_or_clear_authorization": False,
                "exchange_flat": None,
                "account_complete": False,
                "execution_authority": False,
            }
    except (NegativeStopCommitUnknown, NegativeStopReadbackRequired):
        raise
    except BaseException:  # noqa: BLE001 -- preserve unknown/committed classifications without driver values.
        if committed:
            raise NegativeStopReadbackRequired(operation_id) from None
        raise NegativeStopRefused("retained0055_negative_stop_refused") from None
    finally:
        failed = sys.exc_info()[0] is not None
        try:
            await engine.dispose()
        except BaseException:  # noqa: BLE001 -- cleanup cannot replace an existing unknown result.
            if not failed:
                if committed:
                    raise NegativeStopReadbackRequired(operation_id) from None
                raise NegativeStopRefused(
                    "retained0055_negative_stop_cleanup_refused"
                ) from None


class _PrivateParser(argparse.ArgumentParser):
    def error(self, _message):
        raise NegativeStopRefused(
            "retained0055_negative_stop_private_arguments_refused"
        ) from None


def _pins(path, digest):
    canonical._require_positive_private_file_v2(path)
    raw = owner._read_bounded_regular_file_v1(path, 65536)
    _need(owner.original._digest(digest) and owner.baseline.sha(raw) == digest)
    data = read_evidence_json(raw)
    _need(
        type(data) is dict
        and set(data)
        == {
            "schema",
            "retained",
            "maintenance_settings_sha256",
            "reviewed_snapshot_sha256",
        }
        and data["schema"] == PINS_SCHEMA
    )
    retained = data["retained"]
    _need(
        type(retained) is dict
        and set(retained)
        == {"source_commit", "source_tree", "source_archive_sha256", "route"}
    )
    route = retained["route"]
    _need(
        type(route) is dict
        and set(route) == set(canonical.OwnerRoutePinsV1.__dataclass_fields__)
    )
    return NegativeStopPinsV1(
        canonical.OwnerUpgradePinsV1(
            **{**retained, "route": canonical.OwnerRoutePinsV1(**route)}
        ),
        data["maintenance_settings_sha256"],
        data["reviewed_snapshot_sha256"],
    )


def main(arguments=None):
    try:
        parser = _PrivateParser()
        for name in ("pins-file", "owner-dsn-file", "ca-file", "archive-file"):
            parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--pins-sha256", required=True)
        parser.add_argument("--confirm")
        args = parser.parse_args(arguments)
        with redirect_stdout(io.StringIO()):
            result = asyncio.run(
                reassert_retained_owner_0055_stop_v1(
                    _pins(args.pins_file, args.pins_sha256),
                    owner_dsn_file=args.owner_dsn_file,
                    ca_file=args.ca_file,
                    archive_file=args.archive_file,
                    confirmation=args.confirm,
                )
            )
        print(owner.baseline.canonical(result).decode("ascii"))
        return 0
    except (NegativeStopCommitUnknown, NegativeStopReadbackRequired) as error:
        print(
            owner.baseline.canonical(
                {
                    "status": "COMMIT_UNKNOWN_DO_NOT_RETRY"
                    if type(error) is NegativeStopCommitUnknown
                    else "COMMITTED_READBACK_REQUIRED",
                    "operation_id": str(error.operation_id),
                    "execution_authority": False,
                }
            ).decode("ascii")
        )
        return 3 if type(error) is NegativeStopCommitUnknown else 4
    except Exception:  # noqa: BLE001 -- never echo private arguments, paths or driver messages.
        print('{"status":"REFUSED","execution_authority":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
