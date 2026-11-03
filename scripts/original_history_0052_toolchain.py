"""Exact DB0052 probe/capture/retention contracts; historical0051 stays intact.

Only fixed new source-head contracts are defined here. No caller-supplied head,
role projection, native pin or successful boolean can replace actual catalogs.
These schema observations never issue native account or execution authority.
"""

import hashlib
import json
import re
from copy import deepcopy

from scripts import verify_final_hermetic as legacy

CURRENT_HEAD = "0052"
HISTORY_PARTITION_CAPTURE_PATHS = legacy.HISTORY_PARTITION_CAPTURE_PATHS
PROSPECTIVE_CUTOVER_CAPTURE_PATHS = legacy.PROSPECTIVE_CUTOVER_CAPTURE_PATHS
HISTORY_PARTITION_DOWNGRADE_DENIAL = legacy.HISTORY_PARTITION_DOWNGRADE_DENIAL
expected_migration_revision_readback = legacy.expected_migration_revision_readback
native_migration_json_equal = legacy.native_migration_json_equal
verify_downgrade_denial_log = legacy.verify_downgrade_denial_log


def _once(source, original, replacement):
    """Fail closed if a frozen predecessor template has drifted."""
    if type(source) is not str or source.count(original) != 1:
        raise RuntimeError("db0052_predecessor_template_changed")
    return source.replace(original, replacement, 1)


MIGRATION_REVISION_PROBE_0052 = _once(
    legacy.MIGRATION_REVISION_PROBE_0051,
    "import asyncio, json, sys",
    "import asyncio, json, sys\n"
    "if len(sys.argv)!=2 or sys.argv[1]!='0052':\n"
    "    raise RuntimeError('db0052_probe_scope_invalid')",
)
for _original, _replacement in (
    (
        'isolation_level="REPEATABLE READ"',
        'isolation_level="READ COMMITTED"',
    ),
    (
        'await connection.execute(text("SET TRANSACTION READ ONLY"))',
        (
            'await connection.execute(text("SET TRANSACTION READ ONLY"))\n'
            "            await connection.execute(text(\"SET LOCAL TIME ZONE 'UTC'\"))\n"
            "            await connection.execute(text('SET LOCAL search_path TO pg_catalog,public,pg_temp'))"
        ),
    ),
    ("('0048', '0049', '0050', '0051')", "('0048', '0049', '0050', '0051', '0052')"),
    ("observed[0] in ('0050', '0051')", "observed[0] in ('0050', '0051', '0052')"),
    ("observed[0] == '0051'", "observed[0] in ('0051', '0052')"),
    (
        "return (*observed, coverage, coverage_rows, partition, cutover)",
        (
            "from app.domain.account_history_original_frame_v1 import CATALOG_SQL as FRAME_SQL\n"
            "            original_frame=await connection.scalar(text(FRAME_SQL)) if observed[0]=='0052' else None\n"
            "            return (*observed, coverage, coverage_rows, partition, cutover, original_frame)"
        ),
    ),
    (
        "custody, coverage, _coverage_rows, partition, cutover = asyncio.run(read_head())",
        "custody, coverage, _coverage_rows, partition, cutover, original_frame = asyncio.run(read_head())",
    ),
    (
        "partition_catalog=partition, cutover_catalog=cutover,",
        "partition_catalog=partition, cutover_catalog=cutover, original_frame_catalog=original_frame,",
    ),
):
    MIGRATION_REVISION_PROBE_0052 = _once(
        MIGRATION_REVISION_PROBE_0052, _original, _replacement
    )
del _original, _replacement

COVERAGE_NATIVE_CATALOG_CAPTURE_0052 = _once(
    legacy.COVERAGE_NATIVE_CATALOG_CAPTURE_0051,
    "before.head != '0051' or head != '0051'",
    "before.head != '0052' or head != '0052'",
)
HISTORY_PARTITION_NATIVE_CATALOG_CAPTURE_0052 = _once(
    _once(
        legacy.HISTORY_PARTITION_NATIVE_CATALOG_CAPTURE_0051,
        "before.head != '0051' or head != '0051'",
        "before.head != '0052' or head != '0052'",
    ),
    "ctcc.account_history_partition_native_catalog_observation.v2",
    "ctcc.account_history_partition_native_catalog_observation.v3",
)
PROSPECTIVE_CUTOVER_NATIVE_CATALOG_CAPTURE_0052 = _once(
    _once(
        legacy.PROSPECTIVE_CUTOVER_NATIVE_CATALOG_CAPTURE,
        "before.head!='0051' or head!='0051'",
        "before.head!='0052' or head!='0052'",
    ),
    "ctcc.prospective_cutover_native_catalog_observation.v2",
    "ctcc.prospective_cutover_native_catalog_observation.v3",
)
PREDECESSOR_0047_IDENTITY_PROBE_0052 = _once(
    legacy.PREDECESSOR_0047_IDENTITY_PROBE_0051,
    "before.head!='0051'",
    "before.head!='0052' or graph.get_revision('0052').down_revision!='0051'",
)

ORIGINAL_FRAME_INSTALLATION_CAPTURE_PATHS = (
    "app/domain/account_history_original_frame_v1.py",
    "app/domain/account_history_original_source_catalog_v1.py",
    "app/domain/account_history_partition_catalog_v1.py",
    "app/domain/account_history_partition_v1.py",
    "app/domain/prospective_event_cutover_catalog_v2.py",
    "migrations/versions/0052_original_history_frame_reader.py",
    "scripts/verify_final_hermetic.py",
    "scripts/original_history_0052_toolchain.py",
)
ORIGINAL_FRAME_INSTALLATION_NATIVE_CATALOG_CAPTURE_0052 = r"""
import asyncio,hashlib,json,os
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.config.settings import get_settings
from app.domain import account_history_original_frame_v1 as policy
from app.domain import account_history_partition_catalog_v1 as parent
from app.domain.source_primitives import canonical
from scripts.original_history_0052_toolchain import ORIGINAL_FRAME_INSTALLATION_CAPTURE_PATHS
from scripts.verify_migration_identity import database_head,source_identity,SOURCE_ROOT
def pins():
    paths=(*ORIGINAL_FRAME_INSTALLATION_CAPTURE_PATHS,'MANIFEST.sha256')
    if any((SOURCE_ROOT/name).is_symlink() for name in paths):
        raise RuntimeError('original_frame_capture_source_symlink')
    return {name:hashlib.sha256((SOURCE_ROOT/name).read_bytes()).hexdigest() for name in paths}
async def capture():
    before=source_identity(SOURCE_ROOT);before_files=pins()
    engine=create_async_engine(get_settings().database_url,poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            connection=await connection.execution_options(isolation_level='READ COMMITTED')
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            await connection.execute(text('SET LOCAL search_path TO pg_catalog,public,pg_temp'))
            head=await connection.run_sync(database_head)
            facts=(await connection.execute(text("SELECT current_setting('server_version'),current_setting('server_version_num'),current_user,session_user,current_database(),current_schema()"))).one()
            previous=await connection.scalar(text(parent.CATALOG_SQL))
            observed=await connection.scalar(text(policy.CATALOG_SQL))
            installation=policy.verify_owner_installation_catalog(observed,previous)
            final_previous=await connection.scalar(text(parent.CATALOG_SQL))
            final_observed=await connection.scalar(text(policy.CATALOG_SQL))
            policy.verify_owner_installation_catalog(final_observed,final_previous)
            if canonical([previous,observed])!=canonical([final_previous,final_observed]):
                raise RuntimeError('original_frame_capture_catalog_changed')
            final_head=await connection.run_sync(database_head)
            if final_head!=head:
                raise RuntimeError('original_frame_capture_database_head_changed')
        after=source_identity(SOURCE_ROOT);after_files=pins()
        if before!=after or before.head!='0052' or head!='0052' or before_files!=after_files:
            raise RuntimeError('original_frame_capture_source_or_head_changed')
        result={'schema':'ctcc.original_history_frame_owner_catalog_observation.v1',
            'source_head':before.head,'source_migration_sha256':before.sha256,'database_head':final_head,
            'session_facts':list(facts),'catalog':observed,'partition_catalog':previous,
            'installation':installation,
            'source_files':{name:before_files[name] for name in ORIGINAL_FRAME_INSTALLATION_CAPTURE_PATHS},
            'manifest_sha256':before_files['MANIFEST.sha256'],
            'native_baseline_admission':False,'runtime_admission':False,
            'account_complete':False,'execution_authority':False}
        raw=(json.dumps(result,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode('ascii')
        if not 0<len(raw)<=8*1024*1024: raise RuntimeError('original_frame_capture_size')
        path=Path('/validation-results/original-history-frame-owner-catalog.json')
        with path.open('xb') as stream:
            if stream.write(raw)!=len(raw): raise RuntimeError('original_frame_capture_write')
            stream.flush();os.fsync(stream.fileno())
        if path.read_bytes()!=raw: raise RuntimeError('original_frame_capture_readback')
        print(json.dumps({'artifact':'test-results/original-history-frame-owner-catalog.json',
            'sha256':hashlib.sha256(raw).hexdigest(),'native_baseline_admission':False,
            'runtime_admission':False,'account_complete':False,'execution_authority':False},sort_keys=True))
    finally: await engine.dispose()
asyncio.run(capture())
"""

ORIGINAL_FRAME_ABSENT_PROBE_0051 = r"""
import asyncio,json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.config.settings import get_settings
from app.domain.account_history_original_frame_v1 import FUNCTION
from scripts.verify_migration_identity import database_head
async def inspect():
    engine=create_async_engine(get_settings().database_url,poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            head=await connection.run_sync(database_head)
            functions=(await connection.execute(text("SELECT oidvectortypes(p.proargtypes) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname=:name ORDER BY p.oid"),{'name':FUNCTION})).scalars().all()
        result={'schema':'ctcc.original_history_frame_absent.v1','database_head':head,
            'functions_present':functions,'execution_authority':False}
        if head!='0051' or functions: raise RuntimeError('original_frame_absence_unverified')
        print(json.dumps(result,sort_keys=True))
    finally: await engine.dispose()
asyncio.run(inspect())
"""


def expected_original_frame_absent_readback():
    return {
        "schema": "ctcc.original_history_frame_absent.v1",
        "database_head": "0051",
        "functions_present": [],
        "execution_authority": False,
    }


def verify_original_frame_installation_native_catalog_capture_0052(
    document, *, migration_sha256, manifest_sha256, source_files
):
    from app.domain import account_history_original_frame_v1 as policy

    fields = {
        "schema",
        "source_head",
        "source_migration_sha256",
        "database_head",
        "session_facts",
        "catalog",
        "partition_catalog",
        "installation",
        "source_files",
        "manifest_sha256",
        "native_baseline_admission",
        "runtime_admission",
        "account_complete",
        "execution_authority",
    }
    pins = {
        name: source_files[name]["sha256"]
        for name in ORIGINAL_FRAME_INSTALLATION_CAPTURE_PATHS
        if name in source_files and type(source_files[name]) is dict
    }
    partition = document.get("partition_catalog") if type(document) is dict else None
    server = partition.get("server") if type(partition) is dict else None
    if (
        type(document) is not dict
        or set(document) != fields
        or document.get("schema")
        != "ctcc.original_history_frame_owner_catalog_observation.v1"
        or document.get("source_head") != CURRENT_HEAD
        or document.get("database_head") != CURRENT_HEAD
        or document.get("source_migration_sha256") != migration_sha256
        or document.get("manifest_sha256") != manifest_sha256
        or set(pins) != set(ORIGINAL_FRAME_INSTALLATION_CAPTURE_PATHS)
        or document.get("source_files") != pins
        or any(
            type(pin) is not str or re.fullmatch(r"[a-f0-9]{64}", pin) is None
            for pin in (*pins.values(), migration_sha256, manifest_sha256)
        )
        or any(
            document.get(name) is not False
            for name in (
                "native_baseline_admission",
                "runtime_admission",
                "account_complete",
                "execution_authority",
            )
        )
        or type(document.get("session_facts")) is not list
        or len(document["session_facts"]) != 6
        or any(type(item) is not str or not item for item in document["session_facts"])
        or not document["session_facts"][1].isdigit()
        or not 170000 <= int(document["session_facts"][1]) < 180000
        or document["session_facts"][-1] != "pg_catalog"
        or type(server) is not dict
        or any(
            document["session_facts"][index] != server.get(name)
            for index, name in (
                (1, "version_num"),
                (2, "current_user"),
                (3, "session_user"),
                (4, "database"),
            )
        )
    ):
        raise ValueError("original_frame_owner_catalog_observation_invalid")
    verified = policy.verify_owner_installation_catalog(
        document["catalog"], document["partition_catalog"]
    )
    if not native_migration_json_equal(verified, document["installation"]):
        raise ValueError("original_frame_owner_installation_changed")
    return verified


def verify_prospective_cutover_native_catalog_capture_0052(
    document,
    *,
    migration_sha256,
    manifest_sha256,
    source_files,
    require_native_baseline=True,
):
    from app.domain import prospective_event_cutover_catalog_v2 as policy

    fields = {
        "schema",
        "source_head",
        "source_migration_sha256",
        "database_head",
        "session_facts",
        "catalog",
        "source_files",
        "manifest_sha256",
        "effective_ddl_sha256",
        "observed_deparser_sha256",
        "native_baseline_admission",
        "g6_qualified",
        "execution_authority",
    }
    pins = {
        name: source_files[name]["sha256"] for name in PROSPECTIVE_CUTOVER_CAPTURE_PATHS
    }
    if (
        type(require_native_baseline) is not bool
        or type(document) is not dict
        or set(document) != fields
        or document.get("schema")
        != "ctcc.prospective_cutover_native_catalog_observation.v3"
        or document.get("source_head") != "0052"
        or document.get("database_head") != "0052"
        or document.get("source_migration_sha256") != migration_sha256
        or document.get("manifest_sha256") != manifest_sha256
        or document.get("source_files") != pins
        or any(
            type(pin) is not str or re.fullmatch(r"[a-f0-9]{64}", pin) is None
            for pin in (*pins.values(), manifest_sha256, migration_sha256)
        )
        or any(
            document.get(key) is not False
            for key in (
                "native_baseline_admission",
                "g6_qualified",
                "execution_authority",
            )
        )
        or type(document.get("session_facts")) is not list
        or len(document["session_facts"]) != 6
        or any(
            type(value) is not str or not value for value in document["session_facts"]
        )
        or not document["session_facts"][1].isdigit()
        or not 170000 <= int(document["session_facts"][1]) < 180000
        or document["session_facts"][-1] != "pg_catalog"
        or document.get("effective_ddl_sha256") != policy.ddl_identity()
        or document.get("observed_deparser_sha256")
        != policy.deparser_sha256(document["catalog"])
    ):
        raise ValueError("prospective_cutover_native_catalog_observation_invalid")
    policy.verify_catalog(
        document["catalog"], require_native_baseline=require_native_baseline
    )
    return policy.expected_schema()


def verify_history_partition_native_catalog_capture_0052(
    document: dict, *, migration_sha256: str, manifest_sha256: str, source_files: dict
) -> dict:
    from app.domain.account_history_partition_catalog_v1 import (
        verify_account_history_partition_catalog,
    )

    required = {
        "schema",
        "source_head",
        "source_migration_sha256",
        "database_head",
        "session_facts",
        "catalog",
        "source_files",
        "manifest_sha256",
        "native_baseline_admission",
        "execution_authority",
    }
    pins = {
        name: source_files[name]["sha256"]
        for name in HISTORY_PARTITION_CAPTURE_PATHS
        if name in source_files and type(source_files[name]) is dict
    }
    if (
        type(document) is not dict
        or set(document) != required
        or document.get("schema")
        != "ctcc.account_history_partition_native_catalog_observation.v3"
        or document.get("source_head") != "0052"
        or document.get("database_head") != "0052"
        or document.get("source_migration_sha256") != migration_sha256
        or document.get("manifest_sha256") != manifest_sha256
        or set(pins) != set(HISTORY_PARTITION_CAPTURE_PATHS)
        or document.get("source_files") != pins
        or any(
            type(pin) is not str or re.fullmatch(r"[a-f0-9]{64}", pin) is None
            for pin in (*pins.values(), manifest_sha256, migration_sha256)
        )
        or document.get("native_baseline_admission") is not False
        or document.get("execution_authority") is not False
        or type(document.get("session_facts")) is not list
        or len(document["session_facts"]) != 6
        or any(
            type(value) is not str or not value for value in document["session_facts"]
        )
        or not document["session_facts"][1].isdigit()
        or not 170000 <= int(document["session_facts"][1]) < 180000
        or document["session_facts"][-1] != "public"
    ):
        raise ValueError("history_partition_native_catalog_observation_invalid")
    return verify_account_history_partition_catalog(
        document["catalog"], require_native_baseline=True
    )


def verify_history_partition_migration_receipt_0052(
    value, *, manifest_sha256, repository_sha256, migration_sha256
):
    from app.domain.account_history_partition_catalog_v1 import TABLES

    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "manifest_sha256",
            "repository_sha256",
            "migration_sha256",
            "server_major",
            "before",
            "after",
            "refusal_code",
            "downgrade_target",
            "native_baseline_admission",
            "execution_authority",
        }
        or value.get("schema_version")
        != "ctcc.account_history_partition_migration_acceptance.v3"
        or value.get("manifest_sha256") != manifest_sha256
        or value.get("repository_sha256") != repository_sha256
        or value.get("migration_sha256") != migration_sha256
        or any(
            type(pin) is not str or re.fullmatch(r"[a-f0-9]{64}", pin) is None
            for pin in (manifest_sha256, repository_sha256, migration_sha256)
        )
        or type(value.get("server_major")) is not int
        or value["server_major"] != 17
        or value.get("refusal_code") != HISTORY_PARTITION_DOWNGRADE_DENIAL
        or value.get("downgrade_target") != "0049"
        or value.get("native_baseline_admission") is not False
        or value.get("execution_authority") is not False
    ):
        raise ValueError("history_partition_migration_receipt_invalid")
    for snap in (value["before"], value["after"]):
        if (
            type(snap) is not dict
            or set(snap) != {"head", "rows_sha256", "counts", "catalog_sha256"}
            or snap.get("head") != "0052"
            or type(snap.get("counts")) is not dict
            or set(snap["counts"]) != set(TABLES)
            or any(
                type(n) is not int or not 0 <= n <= 8192
                for n in snap["counts"].values()
            )
            or sum(snap["counts"].values()) < 1
            or any(
                type(snap.get(k)) is not str
                or re.fullmatch(r"[a-f0-9]{64}", snap[k]) is None
                for k in ("rows_sha256", "catalog_sha256")
            )
        ):
            raise ValueError("history_partition_migration_receipt_snapshot_invalid")
    if not native_migration_json_equal(value["before"], value["after"]):
        raise ValueError("history_partition_migration_receipt_retention_changed")
    return deepcopy(value)


def history_partition_nonempty_snapshot_0052(revision, retention):
    from app.domain.account_history_partition_catalog_v1 import TABLES

    if (
        not native_migration_json_equal(
            revision, expected_migration_revision_readback("0052")
        )
        or type(retention) is not dict
        or set(retention) != {"rows", "counts", "rows_sha256"}
        or type(retention["rows"]) is not dict
        or set(retention["rows"]) != set(TABLES)
        or type(retention["counts"]) is not dict
        or set(retention["counts"]) != set(TABLES)
    ):
        raise RuntimeError("history_partition_nonempty_snapshot_invalid")
    for name in TABLES:
        count = retention["counts"][name]
        if (
            type(count) is not int
            or not 0 <= count <= 8192
            or type(retention["rows"][name]) is not list
            or len(retention["rows"][name]) != count
            or any(type(row) is not dict for row in retention["rows"][name])
        ):
            raise RuntimeError("history_partition_nonempty_snapshot_invalid")
    body = json.dumps(
        retention["rows"], sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")
    if (
        sum(retention["counts"].values()) < 1
        or len(body) > 64 * 1024 * 1024
        or hashlib.sha256(body).hexdigest() != retention["rows_sha256"]
    ):
        raise RuntimeError("history_partition_nonempty_snapshot_invalid")
    return {
        "schema": "ctcc.account_history_partition_db0052_nonempty_snapshot.v1",
        "revision_readback": deepcopy(revision),
        "retention": deepcopy(retention),
        "execution_authority": False,
    }


def history_partition_nonempty_downgrade_readback_0052(before, after):
    for value in (before, after):
        if (
            type(value) is not dict
            or set(value)
            != {"schema", "revision_readback", "retention", "execution_authority"}
            or not native_migration_json_equal(
                value,
                history_partition_nonempty_snapshot_0052(
                    value.get("revision_readback"), value.get("retention")
                ),
            )
        ):
            raise RuntimeError("history_partition_nonempty_downgrade_readback_mismatch")
    if not native_migration_json_equal(before, after):
        raise RuntimeError("history_partition_nonempty_downgrade_readback_mismatch")
    return {
        "schema": "ctcc.account_history_partition_db0052_nonempty_downgrade.v1",
        "source_head": "0052",
        "target": "0049",
        "result": "DENIED",
        "policy_refusal": HISTORY_PARTITION_DOWNGRADE_DENIAL,
        "before": deepcopy(before),
        "after": deepcopy(after),
        "rollback": "EXACT",
        "execution_authority": False,
    }


def verify_history_partition_downgrade_denial_log_0052(path):
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 2 * 1024 * 1024
    ):
        raise ValueError("history_partition_downgrade_denial_log_missing_or_unsafe")
    output = path.read_bytes().decode("utf-8")
    if (
        re.findall(r"Running downgrade (\d{4} -> \d{4})", output)
        != ["0052 -> 0051", "0051 -> 0050", "0050 -> 0049"]
        or "/app/migrations/versions/0050_account_history_partition.py" not in output
        or len(
            re.findall(
                rf"(?m)^asyncpg\.exceptions\.RaiseError: {HISTORY_PARTITION_DOWNGRADE_DENIAL}\r?$",
                output,
            )
        )
        != 1
        or not re.search(
            rf"(?m)^sqlalchemy\.exc\.DBAPIError: .*: {HISTORY_PARTITION_DOWNGRADE_DENIAL}\r?$",
            output,
        )
    ):
        raise ValueError("history_partition_downgrade_policy_refusal_unverified")


def verify_current_0052_downgrade_denial_log(path):
    verify_downgrade_denial_log(path)
    output = path.read_bytes().decode("utf-8")
    if re.findall(r"Running downgrade (\d{4} -> \d{4})", output) != [
        "0052 -> 0051",
        "0051 -> 0050",
        "0050 -> 0049",
        "0049 -> 0048",
        "0048 -> 0047",
        "0047 -> 0046",
        "0046 -> 0045",
        "0045 -> 0044",
        "0044 -> 0043",
        "0043 -> 0042",
        "0042 -> 0041",
    ]:
        raise ValueError("current_0052_downgrade_policy_refusal_unverified")


def _verify_coverage_native_catalog_capture_for_head_0052(
    document: dict, *, migration_sha256: str, source_head: str
) -> dict:
    """Require separately retained observed catalog and a sealed native policy."""
    from scripts.verify_controlled_writer_coverage_schema_v1 import verify_catalog

    if (
        source_head != "0052"
        or type(document) is not dict
        or set(document)
        != {
            "schema",
            "source_head",
            "source_migration_sha256",
            "database_head",
            "session_facts",
            "catalog",
            "native_baseline_admission",
            "execution_authority",
        }
        or document.get("schema")
        != "ctcc.controlled_writer_coverage_native_catalog_observation.v1"
        or document.get("source_head") != source_head
        or document.get("database_head") != source_head
        or document.get("source_migration_sha256") != migration_sha256
        or document.get("native_baseline_admission") is not False
        or document.get("execution_authority") is not False
        or type(document.get("session_facts")) is not list
        or len(document["session_facts"]) != 6
        or any(
            type(value) is not str or not value for value in document["session_facts"]
        )
        or not document["session_facts"][1].isdigit()
        or not 170000 <= int(document["session_facts"][1]) < 180000
        or document["session_facts"][-1] != "public"
    ):
        raise ValueError("coverage_native_catalog_observation_invalid")
    return verify_catalog(document["catalog"], require_native_baseline=True)


def verify_coverage_native_catalog_capture_0052(document, *, migration_sha256):
    return _verify_coverage_native_catalog_capture_for_head_0052(
        document, migration_sha256=migration_sha256, source_head="0052"
    )


_reader, _separator, _tail = MIGRATION_REVISION_PROBE_0052.partition(
    "\nobserved, witness_present"
)
if not _separator:
    raise RuntimeError("db0052_predecessor_template_changed")
HISTORY_PARTITION_RETENTION_PROBE_0052 = (
    _once(
        _reader,
        "return (*observed, coverage, coverage_rows, partition, cutover, original_frame)",
        "from app.database.repositories.account_history_partition import read_account_history_partition_retention\n"
        "            partition_rows=await read_account_history_partition_retention(connection)\n"
        "            return (*observed, coverage, coverage_rows, partition, cutover, original_frame, partition_rows)",
    )
    + """
observed,witness,guard,triggers,hwm,hwm_guard,hwm_triggers,runtime,native,_,_,coverage,_,partition,cutover,original_frame,retention=asyncio.run(read_head())
revision=migration_revision_readback('0052',observed,witness,guard,triggers,hwm,hwm_guard,hwm_triggers,runtime,native,coverage_catalog=coverage,partition_catalog=partition,cutover_catalog=cutover,original_frame_catalog=original_frame)
from scripts.original_history_0052_toolchain import history_partition_nonempty_snapshot_0052
print(json.dumps(history_partition_nonempty_snapshot_0052(revision,retention),sort_keys=True))
"""
)
del _reader, _separator, _tail
