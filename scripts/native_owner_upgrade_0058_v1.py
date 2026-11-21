"""Reviewed owner-only physical 0057→0058 installation, disarmed by default.

The exact migration is rehearsed and rolled back under a savepoint before the
single outer COMMIT. Logical definitions replace fresh object OIDs only for
comparison; observed database heads, retained rows and actor OIDs stay exact.
An uncertain COMMIT is terminal and must be reconciled without retrying.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.domain import native_database_profile_v1 as profiles
from app.domain import native_live_controlled_close_schema_v1 as close
from app.domain import native_live_financial_epoch_schema_v1 as financial
from app.domain import native_live_ledger_schema_v1 as live
from app.domain import native_live_lifecycle_schema_v1 as lifecycle
from app.domain.source_primitives import canonical, sha
from scripts import native_owner_catalog_snapshot_0058_v1 as catalog
from scripts import native_owner_retained_rows_v2 as retained_rows
from scripts import native_owner_route_preflight_0056_v1 as route
from scripts import native_owner_target_plan_0056_v1 as private_inputs
from scripts import native_owner_upgrade_0057_v1 as parent
from scripts.evidence_json import read_evidence_json

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "UPGRADE CTCC OWNER DB0057 TO DB0058"
ADDED_TABLES = (*live.TABLES, *financial.TABLES, *lifecycle.TABLES, *close.TABLES)


class OwnerProfileUpgrade0058Refused(ValueError):
    pass


class OwnerProfileUpgrade0058CommitUnknown(RuntimeError):
    pass


class OwnerProfileUpgrade0058ReadbackRequired(RuntimeError):
    pass


def _need(value):
    if value is not True:
        raise OwnerProfileUpgrade0058Refused("owner_profile_0058_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class OwnerProfileUpgrade0058PinsV1:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    route: route.OwnerRoutePinsV1
    role_names: tuple[str, str, str]
    role_oids: tuple[int, int, int]
    # Required for a read-only reinstall check of an already installed head58.
    # This is the reviewed postcommit catalog digest, never an execution permit.
    installed_catalog_sha256: str | None = None


def _pins(pins):
    _need(type(pins) is OwnerProfileUpgrade0058PinsV1)
    _need(type(pins.route) is route.OwnerRoutePinsV1)
    _need(pins.route.database_head in {"0057", "0058"})
    _need(
        type(pins.role_names) is tuple
        and len(pins.role_names) == 3
        and len(set(pins.role_names)) == 3
        and pins.route.owner not in pins.role_names
        and all(
            type(name) is str
            and parent.old.re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is not None
            for name in pins.role_names
        )
        and type(pins.role_oids) is tuple
        and len(pins.role_oids) == 3
        and len(set(pins.role_oids)) == 3
        and all(
            type(oid) is int and 0 < oid < 2**32 and oid != pins.route.owner_oid
            for oid in pins.role_oids
        )
    )
    _need(
        pins.installed_catalog_sha256 is None
        if pins.route.database_head == "0057"
        else parent.old._pin(pins.installed_catalog_sha256, parent.old._HEX)
    )
    _need(len(profiles.grant_plan(profiles.DB0057, pins.role_names)) == 44)
    _need(
        profiles.grant_plan(profiles.DB0057, pins.role_names)
        == profiles.grant_plan(profiles.DB0058, pins.role_names)
    )


def _prepare(pins, dsn, ca, archive):
    _pins(pins)
    private_inputs._require_private_file(dsn)
    prepared = route._prepare(
        pins.route, owner_dsn_file=dsn, ca_file=ca, profile=profiles.DB0058
    )
    parent.old._verify_reviewed_source_archive(
        archive,
        source_commit=pins.source_commit,
        source_tree=pins.source_tree,
        source_archive_sha256=pins.source_archive_sha256,
        source_manifest_sha256=pins.route.source_manifest_sha256,
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    _need(
        prepared.source_head == "0058"
        and tuple(script.get_heads()) == ("0058",)
        and script.get_revision("0058").down_revision == "0057"
    )
    return prepared, script


def _checked_preflight(observation, pins, prepared):
    _need(
        type(observation) is dict
        and canonical(observation)
        == canonical(
            {
                "schema": "ctcc.native_owner_0058.route_preflight.v1",
                "status": "PASS",
                "source_head": "0058",
                "source_manifest_sha256": prepared.manifest_sha256,
                "migration_sha256": prepared.migration_sha256,
                "database_head": pins.route.database_head,
                "route_sha256": prepared.route_sha256,
                "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
                "ca_sha256": pins.route.ca_sha256,
                "independent_readonly_observations": 2,
                "database_owner_verified": True,
                "password_authentication_verified": False,
                "schema_object_ownership_verified": False,
                "schema_drift_verified": False,
                "owner_provision_verified": False,
                "startup_admission": False,
                "execution_authority": False,
            }
        )
    )


async def _owner_observation(engine, pins, head):
    from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles

    _need(head in {"0057", "0058"})
    profile = profiles.from_head(head)
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    async with factory() as session, session.begin():
        for sql in roles._OWNER_SETTINGS:
            await session.execute(text(sql))
        await retained_rows.heap_am.lock_native_owner_marker_v1(session)
        row = tuple((await session.execute(route._PROFILE_SQL)).one())
        parent._checked_owner(row, pins, head, read_only=True)
        actor = await roles._identity_at_0056(
            session, pins.route.owner, profile=profile
        )
        _need(
            (actor[3], actor[4], actor[8])
            == (pins.route.owner_oid, pins.route.database_oid, pins.route.port)
        )
    return actor


async def _role_observation(selected, owner, pins, head):
    async with parent.old._marker_checked_v2(owner, pins):
        return await _role_observation_body(selected, owner, pins, head)


async def _role_observation_body(selected, owner, pins, head):
    from app.core import native_role_identity_0056_v2 as identity

    profile = profiles.from_head(head)
    before = await _owner_observation(owner, pins, head)
    actual = await identity.observe_restricted_role_identity_0056_v2(
        selected, source=ROOT, profile=profile
    )
    after = await _owner_observation(owner, pins, head)
    _need(
        type(actual) is identity.NativeRoleIdentity0056V2
        and actual.schema
        == profiles.schema(profile, "ctcc.native_only.restricted_role_identity0056.v2")
        and actual.deployment_reference_authenticated is False
        and before == after
        and actual.database_oid == pins.route.database_oid
        and actual.owner_oid == pins.route.owner_oid
        and actual.role_names == pins.role_names
        and actual.role_oids == pins.role_oids
        and actual.server_incarnation_sha256 == parent._server_sha(before, profile)
        and actual.startup_admission is actual.execution_authority is False
        and actual.admission == "DENY"
    )
    return actual, before


def _live_functions():
    return tuple(
        statement.split("public.", 1)[1].split("(", 1)[0]
        for statement in (
            *live.UPGRADE_SQL,
            *financial.UPGRADE_SQL,
            *lifecycle.UPGRADE_SQL,
            *close.UPGRADE_SQL,
        )
        if statement.startswith("CREATE FUNCTION public.")
    )


async def _live_stopped(connection):
    _need(
        await connection.scalar(
            text("""SELECT NOT EXISTS(SELECT 1 FROM public.native_live_controls_v1
 WHERE armed OR NOT emergency_stop OR arm_id IS NOT NULL OR arm_expires_at IS NOT NULL
 OR lease_until>pg_catalog.clock_timestamp())
 AND NOT EXISTS(SELECT 1 FROM public.native_live_reservations_v1 WHERE state<>'reconciled')""")
        )
        is True
    )


async def _logical(connection):
    size, digest = (await connection.execute(catalog.LOGICAL_CATALOG)).one()
    _need(type(size) is int and 0 < size <= 32 * 1024 * 1024)
    _need(parent.old._pin(digest, parent.old._HEX))
    acl = tuple(
        tuple(row) for row in (await connection.execute(catalog.LOGICAL_ACL)).all()
    )
    _need(0 < len(acl) <= 100_000)
    return {"definitions_sha256": digest, "acl_sha256": sha(canonical(acl))}


async def _inventory(owner, pins):
    async with owner.connect() as connection, connection.begin():
        for sql in parent.old._READBACK_SETTINGS:
            await connection.exec_driver_sql(sql)
        await retained_rows.heap_am.lock_native_owner_marker_v1(connection)
        parent._checked_owner(
            tuple((await connection.execute(route._PROFILE_SQL)).one()),
            pins,
            pins.route.database_head,
            read_only=True,
        )
        relations = await parent._relations(connection, lock=False)
        await parent._local(connection)
        if pins.route.database_head == "0058":
            await _live_stopped(connection)
        snapshot = await parent._snapshot(connection, relations, pins)
        return {
            "relations": relations,
            "snapshot": snapshot,
            "logical": await _logical(connection),
        }


def _preserved(before, after):
    _need(
        before["snapshot"].get("row_encoding")
        == after["snapshot"].get("row_encoding")
        == retained_rows.ROW_ENCODING
    )
    old_rows = {item[0]: tuple(item) for item in before["snapshot"]["rows"]}
    new_rows = {item[0]: tuple(item) for item in after["snapshot"]["rows"]}
    _need(
        len(old_rows) == len(before["snapshot"]["rows"])
        and len(new_rows) == len(after["snapshot"]["rows"])
    )
    added = set(new_rows) - set(old_rows)
    _need(added == set(ADDED_TABLES))
    _need(all(new_rows[name] == value for name, value in old_rows.items()))
    empty = sha(b"")
    _need(all(new_rows[name] == (name, 0, empty) for name in added))
    old_rel = {row[0]: tuple(row) for row in before["relations"]}
    new_rel = {row[0]: tuple(row) for row in after["relations"]}
    _need(set(new_rel) - set(old_rel) == set(ADDED_TABLES))
    _need(all(new_rel[name] == value for name, value in old_rel.items()))
    old_layout = {row[0]: row for row in before["snapshot"]["row_layouts"]}
    new_layout = {row[0]: row for row in after["snapshot"]["row_layouts"]}
    _need(
        len(old_layout) == len(before["snapshot"]["row_layouts"])
        and len(new_layout) == len(after["snapshot"]["row_layouts"])
    )
    _need(set(new_layout) - set(old_layout) == set(ADDED_TABLES))
    _need(all(new_layout.get(name) == value for name, value in old_layout.items()))
    # Current exact0058 DDL creates no sequences. An added/replaced/advanced
    # old sequence therefore has no source-defined exception to preservation.
    _need(before["snapshot"]["sequence_state"] == after["snapshot"]["sequence_state"])
    _need(
        before["snapshot"]["role_state_sha256"]
        == after["snapshot"]["role_state_sha256"]
    )


async def _held_inventory(connection, pins, head):
    await retained_rows.heap_am.require_native_owner_storage_v1(connection)
    parent._checked_owner(
        tuple((await connection.execute(route._PROFILE_SQL)).one()),
        pins,
        head,
        read_only=False,
    )
    relations = await parent._relations(connection, lock=False)
    await parent._local(connection)
    if head == "0058":
        await _live_stopped(connection)
        _need(
            await connection.scalar(
                catalog.NEW_LIVE_ACCESS,
                {"tables": list(ADDED_TABLES), "functions": list(_live_functions())},
            )
            is True
        )
    return {
        "relations": relations,
        "snapshot": await parent._snapshot(connection, relations, pins),
        "logical": await _logical(connection),
    }


def _migrate(connection, script):
    migration = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._upgrade_revs("0058", heads)}
    )
    _need(migration.get_current_heads() == ("0057",))
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    _need(migration.get_current_heads() == ("0058",))


async def _commit_once(script, pins, owner, expected):
    attempted = committed = False
    stage = "INPUT_VALIDATION"
    try:
        _pins(pins)
        _need(pins.route.database_head == "0057")
        async with asyncio.timeout(240), owner.connect() as connection:
            transaction = await connection.begin()
            try:
                for sql in parent.old._OWNER_SETTINGS:
                    await connection.exec_driver_sql(sql)
                await retained_rows.heap_am.require_native_owner_storage_v1(connection)
                await connection.execute(parent.old._OWNER_LOCK)
                await connection.exec_driver_sql(parent.old._LOCK_REVISION)
                await retained_rows.heap_am.require_native_owner_storage_v1(connection)
                await parent._relations(connection, lock=True)
                stage = "LOCKED_PREVIOUS_INVENTORY"
                before = await _held_inventory(connection, pins, "0057")
                _need(before == expected)
                # No grants are executed. The preview determines the exact
                # logical catalog delta of this reviewed migration and is then
                # actually rolled back, including the Alembic revision.
                rehearsal = await connection.begin_nested()
                stage = "REHEARSAL_MIGRATION"
                await connection.run_sync(_migrate, script)
                stage = "REHEARSAL_INVENTORY"
                preview = await _held_inventory(connection, pins, "0058")
                stage = "REHEARSAL_PRESERVATION"
                _preserved(before, preview)
                await rehearsal.rollback()
                stage = "ROLLBACK_READBACK"
                _need(await _held_inventory(connection, pins, "0057") == before)
                stage = "FINAL_MIGRATION"
                await connection.run_sync(_migrate, script)
                stage = "FINAL_INVENTORY"
                final = await _held_inventory(connection, pins, "0058")
                stage = "FINAL_PRESERVATION"
                _preserved(before, final)
                stage = "REHEARSAL_FINAL_CATALOG_MATCH"
                _need(final["logical"] == preview["logical"])
                stage = "FINAL_SOURCE_READBACK"
                current_source = route._source_for_profile(profiles.DB0058)
                _need(
                    current_source.actual_source_head == "0058"
                    and current_source.manifest_sha256
                    == pins.route.source_manifest_sha256
                    and current_source.execution_authority is False
                )
                attempted = True
                await transaction.commit()
                committed = True
                return final
            except BaseException:
                if not attempted:
                    await transaction.rollback()
                raise
    except BaseException:  # noqa: BLE001 -- private failures and cancellation stay redacted.
        if committed:
            raise OwnerProfileUpgrade0058ReadbackRequired(
                "owner_profile_0058_committed_readback_required"
            ) from None
        if attempted:
            raise OwnerProfileUpgrade0058CommitUnknown(
                "owner_profile_0058_commit_unknown_do_not_retry"
            ) from None
        failure = OwnerProfileUpgrade0058Refused("owner_profile_0058_refused")
        # A closed source-defined stage aids diagnosis without SQL, routes,
        # credentials, provider errors or arbitrary exception text.
        failure.stage = stage
        raise failure from None


async def install_reviewed_owner_profile_0058_v1(
    pins, *, owner_dsn_file, ca_file, source_archive_file, confirmation=None
):
    """Read-only by default; unchanged44 Demo grants and zero Live grants."""
    committed = False
    try:
        _need(
            confirmation is None
            or type(confirmation) is str
            and confirmation == CONFIRMATION
        )
        prepared, script = _prepare(pins, owner_dsn_file, ca_file, source_archive_file)
        async with AsyncExitStack() as stack:
            owner, selected = await parent._configured_resources(stack, prepared, pins)
            initial = await route.preflight_owner_route_0056_v1(
                pins.route,
                owner_dsn_file=owner_dsn_file,
                ca_file=ca_file,
                profile=profiles.DB0058,
            )
            _checked_preflight(initial, pins, prepared)
            before = await _inventory(owner, pins)
            identity, incarnation = await _role_observation(
                selected, owner, pins, pins.route.database_head
            )
            _need(await _inventory(owner, pins) == before)
            fresh, _ = _prepare(pins, owner_dsn_file, ca_file, source_archive_file)
            parent._same_source(fresh, prepared)
            if pins.route.database_head == "0058":
                _need(
                    _installed_pin(prepared, before["logical"])
                    == pins.installed_catalog_sha256
                )
                return _receipt(
                    pins,
                    prepared,
                    identity,
                    identity,
                    before,
                    before,
                    status="ALREADY_INSTALLED_READBACK_PASS",
                )
            if confirmation is None:
                return {
                    "schema": "ctcc.owner_profile0058.dry_run.v2",
                    "row_encoding": retained_rows.ROW_ENCODING,
                    "status": "DRY_RUN_PASS",
                    "source_head": "0058",
                    "database_head": "0057",
                    "role_profile_sha256": identity.stable_identity_sha256,
                    "ddl_rehearsal_executed": False,
                    "exchange_flat_verified": False,
                    "execution_authority": False,
                    "account_complete": False,
                    "order_writes": False,
                }
            final = await _commit_once(script, pins, owner, before)
            committed = True
            after_pins = replace(
                pins,
                route=replace(pins.route, database_head="0058"),
                installed_catalog_sha256=_installed_pin(prepared, final["logical"]),
            )
            after_prepared, _ = _prepare(
                after_pins, owner_dsn_file, ca_file, source_archive_file
            )
            _checked_preflight(
                await route.preflight_owner_route_0056_v1(
                    after_pins.route,
                    owner_dsn_file=owner_dsn_file,
                    ca_file=ca_file,
                    profile=profiles.DB0058,
                ),
                after_pins,
                after_prepared,
            )
            after, current_incarnation = await _role_observation(
                selected, owner, after_pins, "0058"
            )
            _need(current_incarnation == incarnation)
            observed = await _inventory(owner, after_pins)
            _need(observed == final)
            last, _ = _prepare(after_pins, owner_dsn_file, ca_file, source_archive_file)
            parent._same_source(last, prepared)
            return _receipt(
                pins, prepared, identity, after, before, observed, status="PASS"
            )
    except (
        OwnerProfileUpgrade0058CommitUnknown,
        OwnerProfileUpgrade0058ReadbackRequired,
    ):
        raise
    except BaseException:  # noqa: BLE001 -- committed state can only request reconciliation.
        if committed:
            raise OwnerProfileUpgrade0058ReadbackRequired(
                "owner_profile_0058_committed_readback_required"
            ) from None
        raise OwnerProfileUpgrade0058Refused("owner_profile_0058_refused") from None


def _receipt(pins, prepared, before_identity, after_identity, before, after, *, status):
    return {
        "schema": "ctcc.owner_profile0058.commit_readback.v2",
        "row_encoding": retained_rows.ROW_ENCODING,
        "status": status,
        "database_head_before": pins.route.database_head,
        "database_head_after": "0058",
        "source_head": "0058",
        "source_commit": pins.source_commit,
        "source_tree": pins.source_tree,
        "source_archive_sha256": pins.source_archive_sha256,
        "source_manifest_sha256": prepared.manifest_sha256,
        "migration_sha256": prepared.migration_sha256,
        "before_role_profile_sha256": before_identity.stable_identity_sha256,
        "after_role_profile_sha256": after_identity.stable_identity_sha256,
        "preserved_rows_sha256": sha(
            canonical(
                {
                    name: before["snapshot"][name]
                    for name in (
                        "row_encoding",
                        "rows",
                        "row_layouts",
                        "sequence_state",
                    )
                }
            )
        ),
        "installed_catalog_sha256": _installed_pin(prepared, after["logical"]),
        "live_spec_sha256": live.SPEC_SHA256,
        "financial_spec_sha256": financial.live_financial_epoch_spec_sha256_v1(),
        "lifecycle_spec_sha256": lifecycle.SPEC_SHA256,
        "controlled_close_spec_sha256": close.SPEC_SHA256,
        "restricted_grant_statements_after": 44,
        "grants_executed": 0,
        "live_grants_executed": 0,
        "ddl_rehearsal_executed": status == "PASS",
        "stopped_local_inventory_verified": True,
        "exchange_flat_verified": False,
        "account_complete": False,
        "startup_admission": False,
        "execution_authority": False,
        "order_writes": False,
    }


def _installed_pin(prepared, logical):
    return sha(
        canonical(
            {
                "schema": "ctcc.owner_profile0058.installed_catalog_pin.v1",
                "migration_sha256": prepared.migration_sha256,
                "live_spec_sha256": live.SPEC_SHA256,
                "financial_spec_sha256": financial.live_financial_epoch_spec_sha256_v1(),
                "lifecycle_spec_sha256": lifecycle.SPEC_SHA256,
                "controlled_close_spec_sha256": close.SPEC_SHA256,
                "catalog": logical,
            }
        )
    )


def _load_pins(path, expected_sha):
    _need(parent.old._pin(expected_sha, parent.old._HEX))
    private_inputs._require_private_file(path)
    raw = _read_bounded_regular_file_v1(path, 65536)
    _need(sha(raw) == expected_sha)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and canonical(value) == raw
        and set(value)
        == {
            "schema",
            "source_commit",
            "source_tree",
            "source_archive_sha256",
            "route",
            "role_names",
            "role_oids",
            "installed_catalog_sha256",
        }
        and value["schema"] == "ctcc.owner_profile0058.reviewed_inputs.v1"
        and type(value["route"]) is dict
        and set(value["route"]) == set(route.OwnerRoutePinsV1.__dataclass_fields__)
        and type(value["role_names"]) is list
        and type(value["role_oids"]) is list
    )
    pins = OwnerProfileUpgrade0058PinsV1(
        value["source_commit"],
        value["source_tree"],
        value["source_archive_sha256"],
        route.OwnerRoutePinsV1(**value["route"]),
        tuple(value["role_names"]),
        tuple(value["role_oids"]),
        value["installed_catalog_sha256"],
    )
    _pins(pins)
    return pins


def main(argv=None):
    class PrivateParser(argparse.ArgumentParser):
        def error(self, message):
            _need(False)

    parser = PrivateParser(add_help=False)
    parser.add_argument("--reviewed-pins-file", type=Path)
    parser.add_argument("--reviewed-pins-sha256")
    parser.add_argument("--owner-dsn-file", type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--source-archive-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmation")
    try:
        args = parser.parse_args(argv)
        _need(
            all(
                isinstance(value, Path)
                for value in (
                    args.reviewed_pins_file,
                    args.owner_dsn_file,
                    args.ca_file,
                    args.source_archive_file,
                )
            )
        )
        _need(
            (args.execute and args.confirmation == CONFIRMATION)
            or not args.execute
            and args.confirmation is None
        )
        result = asyncio.run(
            install_reviewed_owner_profile_0058_v1(
                _load_pins(args.reviewed_pins_file, args.reviewed_pins_sha256),
                owner_dsn_file=args.owner_dsn_file,
                ca_file=args.ca_file,
                source_archive_file=args.source_archive_file,
                confirmation=args.confirmation if args.execute else None,
            )
        )
    except OwnerProfileUpgrade0058CommitUnknown:
        print(
            '{"status":"INCOMPLETE","code":"owner_profile_0058_commit_unknown_do_not_retry"}'
        )
        return 3
    except OwnerProfileUpgrade0058ReadbackRequired:
        print(
            '{"status":"INCOMPLETE","code":"owner_profile_0058_committed_readback_required"}'
        )
        return 4
    except BaseException:  # noqa: BLE001 -- fixed CLI codes redact private transport and cancellation.
        print('{"status":"REFUSED","code":"owner_profile_0058_refused"}')
        return 1
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
