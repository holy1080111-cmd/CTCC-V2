"""Explicit owner-only DB0055 to DB0056 migration; no role grants or API start.

The reviewed source, archive, route, CA and database identity are required
inputs. A caller-supplied pin is not proof of who approved it. Once COMMIT is
attempted, any uncertain result is terminal and this command never retries.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import stat
import tarfile
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.database.models  # noqa: F401 -- exact migration metadata registry.
from app.database.base import Base
from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from scripts import current0056_fitted_delta_catalog_v1 as fitted56
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import manifest
from scripts import verify_final_hermetic as archive_checks
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_route_preflight_0056_v1 import (
    _PROFILE_SQL,
    OwnerRoutePinsV1,
    preflight_owner_route_0056_v1,
)
from scripts.native_owner_route_preflight_0056_v1 import (
    _prepare as prepare_route,
)

_SOURCE = Path(__file__).resolve().parents[1]
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_OWNER_LOCK = text("SELECT pg_catalog.pg_advisory_xact_lock(5883109675522810055)")
_LOCK_REVISION = "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE NOWAIT"
_OWNER_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL synchronous_commit='on'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='120000ms'",
)
_READBACK_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)


class NativeOwnerUpgradeRefusal(ValueError):
    """Nothing committed by this invocation; fixed nonprivate refusal."""


class NativeOwnerUpgradeCommitUnknown(RuntimeError):
    """COMMIT was attempted but its result cannot be trusted; never retry."""


class NativeOwnerUpgradePostcommitIncomplete(RuntimeError):
    """COMMIT returned, but independent readback or cleanup failed."""


def _deny(code: str = "native_owner_0056_upgrade_refused") -> None:
    raise NativeOwnerUpgradeRefusal(code) from None


def _need(condition: bool) -> None:
    if condition is not True:
        _deny()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class OwnerUpgradePinsV1:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    route: OwnerRoutePinsV1


def _pin(value: object, expression: re.Pattern[str]) -> bool:
    return type(value) is str and expression.fullmatch(value) is not None


def _archive_manifest(raw: bytes) -> dict[str, str]:
    _need(raw.endswith(b"\n") and b"\r" not in raw)
    rows: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        digest, delimiter, name = line.partition("  ")
        _need(delimiter == "  " and _pin(digest, _HEX) and name and name not in rows)
        rows[name] = digest
    _need(bool(rows))
    return rows


def _canonical_source_sha(name: str, raw: bytes) -> str:
    path = PurePosixPath(name)
    if (
        path.name in manifest.TEXT_NAMES
        or path.suffix.lower() in manifest.TEXT_SUFFIXES
    ):
        raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return _sha(raw)


def _verify_reviewed_archive(pins: OwnerUpgradePinsV1, path: Path) -> None:
    """Reconstruct the reviewed Git tree and bind every archived source row."""
    _need(type(pins) is OwnerUpgradePinsV1)
    _verify_reviewed_source_archive(
        path,
        source_commit=pins.source_commit,
        source_tree=pins.source_tree,
        source_archive_sha256=pins.source_archive_sha256,
        source_manifest_sha256=pins.route.source_manifest_sha256,
    )


def _verify_reviewed_source_archive(
    path: Path,
    *,
    source_commit: str,
    source_tree: str,
    source_archive_sha256: str,
    source_manifest_sha256: str,
) -> None:
    _need(
        _pin(source_commit, _GIT)
        and _pin(source_tree, _GIT)
        and _pin(source_archive_sha256, _HEX)
        and _pin(source_manifest_sha256, _HEX)
        and type(path) is type(Path())
        and path.is_absolute()
        and ".." not in path.parts
        and not path.is_symlink()
        and not path.is_junction()
    )
    entry = path.lstat()
    _need(stat.S_ISREG(entry.st_mode) and 0 < entry.st_size <= 128 * 1024 * 1024)
    raw = path.read_bytes()
    _need(len(raw) == entry.st_size and _sha(raw) == source_archive_sha256)
    current_manifest = (_SOURCE / "MANIFEST.sha256").read_bytes()
    _need(_sha(current_manifest) == source_manifest_sha256)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        _need(
            archive.pax_headers.get("comment") == source_commit
            and archive_checks.archive_tree(archive) == source_tree
        )
        members = {item.name: item for item in archive.getmembers() if item.isfile()}
        _need("MANIFEST.sha256" in members)
        stream = archive.extractfile(members["MANIFEST.sha256"])
        _need(stream is not None and stream.read() == current_manifest)
        expected = _archive_manifest(current_manifest)
        _need(set(members) == set(expected) | {"MANIFEST.sha256"})
        for name, digest in expected.items():
            stream = archive.extractfile(members[name])
            _need(
                stream is not None
                and _canonical_source_sha(name, stream.read()) == digest
            )


def _checked_profile(
    row: tuple, pins: OwnerRoutePinsV1, head: str, *, read_only: bool
) -> None:
    _need(
        type(row) is tuple
        and len(row) == 12
        and row[0] == row[1] == pins.owner
        and row[2] == pins.database
        and row[3] == pins.port
        and row[4] == row[6] == pins.owner_oid
        and row[5] == pins.database_oid
        and row[7] is True
        and row[8] in ("TLSv1.2", "TLSv1.3")
        and row[9] == ("on" if read_only else "off")
        and row[10] == head
        and type(row[11]) is str
        and re.fullmatch(r"[1-9][0-9]{0,19}", row[11], re.ASCII) is not None
        and _sha(row[11].encode("ascii")) == pins.cluster_system_identifier_sha256
    )


def _prepared(
    pins: OwnerUpgradePinsV1, owner_dsn_file: Path, ca_file: Path, archive_file: Path
):
    _need(type(pins) is OwnerUpgradePinsV1 and pins.route.database_head == "0055")
    route = prepare_route(pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
    _verify_reviewed_archive(pins, archive_file)
    with redirect_stdout(io.StringIO()):
        before = scopes.database0055_source_scope(_SOURCE)
        after = scopes.actual_current_source_scope(_SOURCE)
    _need(
        before.actual_source_head == after.actual_source_head == "0056"
        and before.database_target == "0055"
        and after.database_target == "0056"
        and before.manifest_sha256 == after.manifest_sha256 == route.manifest_sha256
        and route.migration_sha256 == after.actual_migration_sha256
    )
    fitted56.pinned_migration(_SOURCE)
    config = Config(str(_SOURCE / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(_SOURCE / "migrations"))
    script = ScriptDirectory.from_config(config)
    _need(
        tuple(script.get_heads()) == ("0056",)
        and script.get_revision("0056").down_revision == "0055"
    )
    return route, before, after, script


def _checked_preflight(value: dict, route, pins: OwnerRoutePinsV1) -> None:
    _need(
        type(value) is dict
        and value.get("schema") == "ctcc.native_owner_0056.route_preflight.v1"
        and value.get("status") == "PASS"
        and value.get("source_head") == "0056"
        and value.get("source_manifest_sha256") == route.manifest_sha256
        and value.get("migration_sha256") == route.migration_sha256
        and value.get("database_head") == pins.database_head
        and value.get("route_sha256") == route.route_sha256
        and value.get("cluster_system_identifier_sha256")
        == pins.cluster_system_identifier_sha256
        and value.get("ca_sha256") == pins.ca_sha256
        and value.get("independent_readonly_observations") == 2
        and value.get("database_owner_verified") is True
        and value.get("schema_drift_verified") is False
        and value.get("owner_provision_verified") is False
        and value.get("startup_admission") is False
        and value.get("execution_authority") is False
    )


def _upgrade_exact_0055(sync_connection, before, after, script) -> None:
    with redirect_stdout(io.StringIO()):
        scopes.verify_database_scope(sync_connection, _SOURCE, before)
    migration = MigrationContext.configure(
        sync_connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0056", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    _need(migration.get_current_heads() == ("0055",))
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    with redirect_stdout(io.StringIO()):
        scopes.verify_database_scope(sync_connection, _SOURCE, after)


async def _commit_once(prepared, pins: OwnerUpgradePinsV1) -> None:
    """A single owner transaction; the commit-attempt bit is monotone."""
    route, before, after, script = prepared
    engine = None
    attempted = False
    committed = False
    failure: BaseException | None = None
    try:
        async with asyncio.timeout(180):
            engine = create_async_engine(
                route.url,
                poolclass=NullPool,
                connect_args={"ssl": route.tls, "timeout": 10},
            )
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    for setting in _OWNER_SETTINGS:
                        await connection.exec_driver_sql(setting)
                    await connection.execute(_OWNER_LOCK)
                    await connection.exec_driver_sql(_LOCK_REVISION)
                    row = tuple((await connection.execute(_PROFILE_SQL)).one())
                    _checked_profile(row, pins.route, "0055", read_only=False)
                    await connection.run_sync(
                        _upgrade_exact_0055, before, after, script
                    )
                    row = tuple((await connection.execute(_PROFILE_SQL)).one())
                    _checked_profile(row, pins.route, "0056", read_only=False)
                    attempted = True
                    await transaction.commit()
                    committed = True
                except BaseException:
                    if not attempted:
                        try:
                            await transaction.rollback()
                        except BaseException:  # noqa: BLE001 -- fixed denial if rollback fails.
                            _deny()
                    raise
    except BaseException as error:  # noqa: BLE001 -- classify cancellation at COMMIT.
        failure = error
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except BaseException as error:  # noqa: BLE001 -- cleanup is part of acceptance.
                failure = error
    if failure is not None:
        if committed:
            raise NativeOwnerUpgradePostcommitIncomplete(
                "native_owner_0056_committed_readback_required"
            ) from None
        if attempted:
            raise NativeOwnerUpgradeCommitUnknown(
                "native_owner_0056_commit_outcome_unknown"
            ) from None
        _deny()


async def _independent_readback(prepared, pins, owner_dsn_file, ca_file, archive_file):
    route, _, expected, _ = prepared
    fresh = replace(pins.route, database_head="0056")
    async with asyncio.timeout(60):
        observed = await preflight_owner_route_0056_v1(
            fresh, owner_dsn_file=owner_dsn_file, ca_file=ca_file
        )
    _checked_preflight(observed, route, fresh)
    reread = _prepared(pins, owner_dsn_file, ca_file, archive_file)
    _need(
        reread[0].url == route.url
        and reread[0].manifest_sha256 == route.manifest_sha256
        and reread[0].migration_sha256 == route.migration_sha256
    )
    engine = create_async_engine(
        route.url,
        poolclass=NullPool,
        connect_args={"ssl": reread[0].tls, "timeout": 10},
    )
    try:
        async with asyncio.timeout(45):
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    for setting in _READBACK_SETTINGS:
                        await connection.exec_driver_sql(setting)
                    row = tuple((await connection.execute(_PROFILE_SQL)).one())
                    _checked_profile(row, fresh, "0056", read_only=True)
                    with redirect_stdout(io.StringIO()):
                        scope = await connection.run_sync(
                            scopes.verify_database_scope, _SOURCE, expected
                        )
                    _need(
                        scope["database_head"] == "0056"
                        and scope["manifest_sha256"] == route.manifest_sha256
                        and scope["execution_authority"] is False
                    )
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()
    return observed


async def upgrade_owner_0055_to_0056_v1(
    pins: OwnerUpgradePinsV1,
    *,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
) -> dict[str, object]:
    """One explicit migration, then independent post-commit physical readback."""
    try:
        prepared = _prepared(pins, owner_dsn_file, ca_file, source_archive_file)
        async with asyncio.timeout(60):
            prior = await preflight_owner_route_0056_v1(
                pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
            )
        _checked_preflight(prior, prepared[0], pins.route)
        fresh_prepared = _prepared(pins, owner_dsn_file, ca_file, source_archive_file)
        _need(
            fresh_prepared[0].url == prepared[0].url
            and fresh_prepared[0].manifest_sha256 == prepared[0].manifest_sha256
            and fresh_prepared[0].migration_sha256 == prepared[0].migration_sha256
        )
        await _commit_once(fresh_prepared, pins)
    except (NativeOwnerUpgradeCommitUnknown, NativeOwnerUpgradePostcommitIncomplete):
        raise
    except BaseException:  # noqa: BLE001 -- no private precommit detail escapes.
        _deny()
    try:
        observed = await _independent_readback(
            fresh_prepared, pins, owner_dsn_file, ca_file, source_archive_file
        )
    except BaseException:  # noqa: BLE001 -- committed state requires independent readback.
        raise NativeOwnerUpgradePostcommitIncomplete(
            "native_owner_0056_committed_readback_required"
        ) from None
    return {
        "schema": "ctcc.native_owner_0056.migration_commit_readback.v1",
        "status": "PASS",
        "source_commit": pins.source_commit,
        "source_tree": pins.source_tree,
        "source_archive_sha256": pins.source_archive_sha256,
        "source_manifest_sha256": prepared[0].manifest_sha256,
        "migration_sha256": prepared[0].migration_sha256,
        "route_sha256": prepared[0].route_sha256,
        "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
        "database_head_before": "0055",
        "database_head_after": observed["database_head"],
        "independent_postcommit_readonly_observations": 3,
        "schema_drift_verified": False,
        "grants_installed": False,
        "startup_admission": False,
        "execution_authority": False,
    }


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _deny()


def _load_pins(path: Path, expected_sha256: str) -> OwnerUpgradePinsV1:
    _need(_pin(expected_sha256, _HEX))
    raw = _read_bounded_regular_file_v1(path, 65536)
    _need(_sha(raw) == expected_sha256)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and set(value)
        == {"schema", "source_commit", "source_tree", "source_archive_sha256", "route"}
        and value["schema"] == "ctcc.native_owner_0056.reviewed_upgrade_inputs.v1"
        and type(value["route"]) is dict
        and set(value["route"]) == set(OwnerRoutePinsV1.__dataclass_fields__)
    )
    return OwnerUpgradePinsV1(
        value["source_commit"],
        value["source_tree"],
        value["source_archive_sha256"],
        OwnerRoutePinsV1(**value["route"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(add_help=False)
    parser.add_argument(
        "--upgrade-reviewed-native-owner-0055-to-0056", action="store_true"
    )
    parser.add_argument("--reviewed-pins-file", type=Path)
    parser.add_argument("--reviewed-pins-sha256")
    parser.add_argument("--owner-dsn-file", type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--source-archive-file", type=Path)
    try:
        args = parser.parse_args(argv)
        _need(args.upgrade_reviewed_native_owner_0055_to_0056 is True)
        pins = _load_pins(args.reviewed_pins_file, args.reviewed_pins_sha256)
        receipt = asyncio.run(
            upgrade_owner_0055_to_0056_v1(
                pins,
                owner_dsn_file=args.owner_dsn_file,
                ca_file=args.ca_file,
                source_archive_file=args.source_archive_file,
            )
        )
    except NativeOwnerUpgradeCommitUnknown:
        print(
            '{"status":"INCOMPLETE","code":"native_owner_0056_commit_outcome_unknown"}'
        )
        return 3
    except NativeOwnerUpgradePostcommitIncomplete:
        print(
            '{"status":"INCOMPLETE","code":"native_owner_0056_committed_readback_required"}'
        )
        return 4
    except BaseException:  # noqa: BLE001 -- never reveal route, SQL or private files.
        print('{"status":"REFUSED","code":"native_owner_0056_upgrade_refused"}')
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
