"""Versioned current0053 owner capture; historical0052 contracts stay exact.

Capture records real readonly PG output without native admission. Formal
verification requires a separately reviewed current0053 native baseline and
the unchanged historical0052 installed-function seal. No account revision,
reader capability, policy flag or trading authority is issued here.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import text

from app.domain import account_history_original_frame_v1 as frame
from app.domain import account_history_partition_catalog_v1 as partition
from app.domain import collector_current_0053_owner_installation_v1 as owner
from app.domain.source_primitives import canonical
from scripts import manifest
from scripts.verify_migration_identity import (
    SOURCE_ROOT,
    database_head,
    source_identity,
)

CURRENT_HEAD = "0053"
CAPTURE_SCHEMA = "ctcc.collector_current0053.native_owner_observation.v1"
CAPTURE_PATHS = (
    "migrations/versions/0053_collector_current_revision_lineage.py",
    "app/domain/account_revision_collector_lineage_schema_v1.py",
    "app/domain/collector_current_0053_owner_installation_v1.py",
    "app/domain/account_history_original_frame_v1.py",
    "app/domain/account_history_original_source_catalog_v1.py",
    "app/domain/account_history_partition_catalog_v1.py",
    "app/database/repositories/public_runtime_bundle_custody.py",
    "scripts/collector_current_0053_toolchain.py",
    "scripts/verify_final_hermetic.py",
    "scripts/original_history_0052_toolchain.py",
    "scripts/verify_migration_identity.py",
    "scripts/manifest.py",
    "app/domain/source_primitives.py",
    "app/domain/controlled_writer_coverage_catalog_v1.py",
    "app/domain/prospective_event_cutover_catalog_v2.py",
    "app/domain/account_history_partition_v1.py",
)
MAX_CAPTURE_BYTES = 8 * 1024 * 1024


class Current0053CaptureError(ValueError):
    pass


def _deny():
    raise Current0053CaptureError("current0053_owner_capture_unavailable") from None


def _pins(root):
    if not manifest.check_manifest(root, root / "MANIFEST.sha256"):
        _deny()
    result = {}
    for name in (*CAPTURE_PATHS, "MANIFEST.sha256"):
        path = root / name
        if path.is_symlink() or not path.is_file():
            _deny()
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def expected_current0053_migration_readback():
    """A new current declaration; the historical0052 declaration is unchanged."""
    from scripts import verify_final_hermetic as legacy

    return {
        **legacy.expected_migration_revision_readback("0052"),
        "schema": "ctcc.migration_revision_readback.v10",
        "expected": "0053",
        "observed": "0053",
        "collector_current_lineage_owner_installation": owner.expected_owner_installation_schema(),
    }


def current0053_migration_readback(
    observed_head,
    witness_present,
    guard_present,
    trigger_catalog,
    hwm_present,
    hwm_guard_present,
    hwm_trigger_catalog,
    runtime_catalog,
    native_identity_catalog,
    *,
    coverage_catalog,
    partition_catalog,
    cutover_catalog,
    original_frame_catalog,
    current0053_owner_catalog,
):
    """Check the real0053 head and separate owner catalogs, with no head projection."""
    from scripts import verify_final_hermetic as legacy

    if observed_head != CURRENT_HEAD:
        _deny()
    # This helper validates unchanged parent fields only. It is not invoked
    # with a fake observed0052 revision or relabelled owner/native snapshot.
    previous = legacy._validated_0051_parent_fields(
        witness_present,
        guard_present,
        trigger_catalog,
        hwm_present,
        hwm_guard_present,
        hwm_trigger_catalog,
        runtime_catalog,
        native_identity_catalog,
        coverage_catalog=coverage_catalog,
        partition_catalog=partition_catalog,
        cutover_catalog=cutover_catalog,
    )
    historical = frame.verify_owner_installation_catalog(
        original_frame_catalog, partition_catalog
    )
    current = owner.verify_owner_installation_catalog(current0053_owner_catalog)
    if historical["revision"] != "0052":
        _deny()
    result = {
        **previous,
        "schema": "ctcc.migration_revision_readback.v10",
        "expected": CURRENT_HEAD,
        "observed": observed_head,
        "original_history_frame_owner_installation": historical,
        "collector_current_lineage_owner_installation": current,
    }
    if canonical(result) != canonical(expected_current0053_migration_readback()):
        _deny()
    return result


async def _actual_current0053_state(connection):
    from app.domain import prospective_event_cutover_catalog_v2 as cutover
    from scripts import verify_final_hermetic as legacy
    from scripts.verify_controlled_writer_coverage_schema_v1 import read_catalog

    observed = await connection.run_sync(database_head)
    witness = await connection.scalar(
        text(
            "SELECT to_regclass('public.demo_account_revision_source_witnesses') IS NOT NULL"
        )
    )
    guard = await connection.scalar(
        text(
            "SELECT to_regprocedure('public.account_revision_source_witness_guard()') IS NOT NULL"
        )
    )
    triggers = (
        await connection.execute(
            text(legacy.ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL)
        )
    ).all()
    hwm = await connection.scalar(
        text("SELECT to_regclass('public.demo_account_measured_hwm_v2') IS NOT NULL")
    )
    hwm_guard = await connection.scalar(
        text(
            "SELECT to_regprocedure('public.account_measured_hwm_v2_guard()') IS NOT NULL"
        )
    )
    hwm_triggers = (
        await connection.execute(
            text(legacy.ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL)
        )
    ).all()
    return {
        "observed_head": observed,
        "witness_present": witness,
        "guard_present": guard,
        "trigger_catalog": [tuple(row) for row in triggers],
        "hwm_present": hwm,
        "hwm_guard_present": hwm_guard,
        "hwm_trigger_catalog": [tuple(row) for row in hwm_triggers],
        "runtime_catalog": await connection.scalar(
            text(legacy.PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL)
        ),
        "native_identity_catalog": await connection.scalar(
            text(legacy.NATIVE_WITNESS_IDENTITY_SCHEMA_SQL)
        ),
        "coverage_catalog": await read_catalog(connection),
        "partition_catalog": await connection.scalar(text(partition.CATALOG_SQL)),
        "cutover_catalog": await connection.scalar(text(cutover.CATALOG_SQL)),
        "original_frame_catalog": await connection.scalar(text(frame.CATALOG_SQL)),
        "current0053_owner_catalog": await connection.scalar(text(owner.CATALOG_SQL)),
    }


async def read_current0053_migration(connection, root=SOURCE_ROOT):
    """Read one real current53 owner transaction; no historical-head substitution."""
    before = source_identity(root)
    files_before = _pins(root)
    if before.head != CURRENT_HEAD:
        _deny()
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await connection.execute(text("SET TRANSACTION READ ONLY"))
    await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await connection.execute(text("SET LOCAL search_path TO pg_catalog,public,pg_temp"))
    first = await _actual_current0053_state(connection)
    result = current0053_migration_readback(**first)
    final = await _actual_current0053_state(connection)
    current0053_migration_readback(**final)
    if (
        canonical(first) != canonical(final)
        or before != source_identity(root)
        or files_before != _pins(root)
    ):
        _deny()
    return result


async def _catalogs(connection):
    previous = await connection.scalar(text(partition.CATALOG_SQL))
    historical_frame = await connection.scalar(text(frame.CATALOG_SQL))
    actual_owner = await connection.scalar(text(owner.CATALOG_SQL))
    # These are separate real owner APIs. The runtime actor verifier is never
    # called with an owner row, and no observed role or head is projected.
    historical_installation = frame.verify_owner_installation_catalog(
        historical_frame, previous
    )
    current_observation = owner.observe_owner_installation_catalog(actual_owner)
    if historical_installation["revision"] != "0052":
        _deny()
    return {
        "partition_catalog": previous,
        "historical_frame_catalog": historical_frame,
        "current0053_owner_catalog": actual_owner,
        "historical0052_installation": historical_installation,
        "current0053_observation": current_observation,
    }


async def capture_current0053_owner(connection, root=SOURCE_ROOT):
    """Caller provides an existing controlled owner connection; this starts no engine."""
    before = source_identity(root)
    files_before = _pins(root)
    if before.head != CURRENT_HEAD:
        _deny()
    # The caller owns this fresh transaction. A prior query or write may make
    # this fail; it is never repaired by inventing a snapshot or changing head.
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await connection.execute(text("SET TRANSACTION READ ONLY"))
    await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await connection.execute(text("SET LOCAL search_path TO pg_catalog,public,pg_temp"))
    observed_head = await connection.run_sync(database_head)
    if observed_head != CURRENT_HEAD:
        _deny()
    observed = await _catalogs(connection)
    final = await _catalogs(connection)
    final_head = await connection.run_sync(database_head)
    after = source_identity(root)
    files_after = _pins(root)
    if (
        before != after
        or files_before != files_after
        or final_head != observed_head
        or canonical(observed) != canonical(final)
    ):
        _deny()
    result = {
        "schema": CAPTURE_SCHEMA,
        "source_head": before.head,
        "source_migration_sha256": before.sha256,
        "database_head": final_head,
        "manifest_sha256": files_before["MANIFEST.sha256"],
        "source_files": {name: files_before[name] for name in CAPTURE_PATHS},
        **observed,
        "native_baseline_admission": False,
        "runtime_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    raw = canonical(result) + b"\n"
    if not 0 < len(raw) <= MAX_CAPTURE_BYTES:
        _deny()
    return result, raw


def verify_current0053_owner_capture(
    document, *, migration_sha256, manifest_sha256, source_files
):
    """Validate a current-source capture; never relabel a historical0052 document."""
    if (
        type(document) is not dict
        or set(document)
        != {
            "schema",
            "source_head",
            "source_migration_sha256",
            "database_head",
            "manifest_sha256",
            "source_files",
            "partition_catalog",
            "historical_frame_catalog",
            "current0053_owner_catalog",
            "historical0052_installation",
            "current0053_observation",
            "native_baseline_admission",
            "runtime_admission",
            "account_complete",
            "execution_authority",
        }
        or document["schema"] != CAPTURE_SCHEMA
        or document["source_head"] != CURRENT_HEAD
        or document["database_head"] != CURRENT_HEAD
        or document["source_migration_sha256"] != migration_sha256
        or document["manifest_sha256"] != manifest_sha256
        or document["source_files"] != source_files
        or set(source_files) != set(CAPTURE_PATHS)
        or any(
            document[name] is not False
            for name in (
                "native_baseline_admission",
                "runtime_admission",
                "account_complete",
                "execution_authority",
            )
        )
    ):
        _deny()
    historical = frame.verify_owner_installation_catalog(
        document["historical_frame_catalog"], document["partition_catalog"]
    )
    measured = owner.observe_owner_installation_catalog(
        document["current0053_owner_catalog"]
    )
    if (
        historical != document["historical0052_installation"]
        or historical["revision"] != "0052"
        or measured != document["current0053_observation"]
    ):
        _deny()
    current = owner.verify_owner_installation_catalog(
        document["current0053_owner_catalog"]
    )
    return {
        "schema": "ctcc.current0053.owner_capture_verified.v1",
        "source_head": CURRENT_HEAD,
        "database_head": CURRENT_HEAD,
        "historical0052_installation": historical,
        "current0053_installation": current,
        "account_complete": False,
        "execution_authority": False,
    }


def publish_capture(path, raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_CAPTURE_BYTES:
        _deny()
    if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        _deny()
    with path.open("xb") as stream:
        if stream.write(raw) != len(raw):
            _deny()
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        _deny()
    return hashlib.sha256(raw).hexdigest()


async def _main_capture(path, mode):
    import re

    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    # This entry is an isolated hermetic-owner probe, not a production .env
    # loader. Windows native tests call capture_current0053_owner on their
    # existing Root-owned portable fixture connection instead.
    raw_url = os.environ.get("DATABASE_URL", "")
    if (
        os.environ.get("ENVIRONMENT") != "test"
        or os.environ.get("TRADING_MODE") != "analysis_only"
    ):
        _deny()
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "") is None
        or url.username != "ctcc"
        or url.password is not None
        or url.port != 5432
        or url.database != "ctcc"
        or url.query
    ):
        _deny()
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            if mode == "capture-owner0053":
                _document, raw = await capture_current0053_owner(connection)
            elif mode == "readback0053":
                _document = await read_current0053_migration(connection)
                raw = canonical(_document) + b"\n"
            else:
                _deny()
        pin = publish_capture(path, raw)
        print(
            json.dumps(
                {
                    "artifact": path.name,
                    "sha256": pin,
                    "scope": mode,
                    "account_complete": False,
                    "execution_authority": False,
                },
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


def main():
    import asyncio

    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--capture-owner0053", action="store_true")
    modes.add_argument("--readback0053", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mode = "capture-owner0053" if args.capture_owner0053 else "readback0053"
    asyncio.run(_main_capture(args.output, mode))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 -- fail closed with sanitized CLI output
        # Never echo a private database URL, SQL exception or connection data.
        print(
            json.dumps(
                {
                    "status": "FAIL_OR_UNKNOWN",
                    "failure": "CURRENT0053_OWNER_CAPTURE_FAILED",
                    "account_complete": False,
                    "execution_authority": False,
                },
                sort_keys=True,
            )
        )
        raise SystemExit(1) from None
