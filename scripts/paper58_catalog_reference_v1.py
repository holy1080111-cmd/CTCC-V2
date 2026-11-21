"""Fresh source58 structural DATA; never Paper actor or startup admission.

The fixed reference tests build independent canonical55 databases, validate
their actual native55 predecessor, and run the unchanged56–58 migrations.
Only then is this new-purpose full typed catalog query used. No source55
document, profile or database head is retagged into a source58 observation.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from scripts import current0058_hermetic_contract_v1 as source_contract
from scripts import native_owner_heap_am_v1 as storage
from scripts import paper58_source_coverage_v1 as source
from scripts import paper58_source_shape_v1 as shape
from scripts.evidence_json import read_evidence_json

RAW_SCHEMA = "ctcc.paper58.structural_raw_catalog.v1"
RECEIPT_SCHEMA = "ctcc.paper58.fresh_structural_reference.v1"
PAIR_SCHEMA = "ctcc.paper58.fresh_structural_pair.v1"
CONTRACT = "ctcc.source58.paper58_structural_reference.v1"
IDENTITY_SCHEMA = "ctcc.source58.paper58_structural_reference_identity.v1"
SCHEMA = IDENTITY_SCHEMA
SCOPE = "two_fresh_canonical55_to58_full_catalog_data_no_paper_admission"
SUITE = "paper58-structural-reference"
REPORT = "test-results/paper58-structural-reference.xml"
ENV = "CTCC_DEVVAL_PAPER58_REFERENCE_DIR"
LABELS = ("a", "b")
MAX_CATALOG_BYTES = 32 * 1024 * 1024
MODULE = "tests/integration/test_paper58_catalog_reference_v1_pg.py"
CASES = tuple(
    (
        MODULE.removesuffix(".py").replace("/", "."),
        "test_actual0058_fresh_structural_reference[" + label + "]",
    )
    for label in ("a", "b")
)
TABLES = tuple(sorted((*source.RELATIONS, "alembic_version")))
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)
_HEX = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
ROOT = Path(__file__).resolve().parents[1]
_PRODUCER = "scripts/paper58_catalog_reference_v1.py"
_COMPARATOR = "scripts/paper58_catalog_normalization_v1.py"
_RUNNER = "scripts/run_retained0017_reference_probe_v1.py"
canonical = source_contract.canonical
digest = source_contract.digest


def paths(label):
    _need(type(label) is str and label in LABELS)
    return tuple(
        "test-results/paper58-reference-" + label + "-" + suffix + ".json"
        for suffix in ("raw", "normalized", "receipt")
    )


def _need(value):
    if value is not True:
        raise ValueError("paper58_structural_reference_refused") from None


def catalog_query_v1():
    from scripts.retained0017_catalog_baseline_v1 import CATALOG_SQL

    previous = "'ctcc.retained0017.raw_catalog.v1'"
    _need(CATALOG_SQL.count(previous) == 1)
    return CATALOG_SQL.replace(previous, "'" + RAW_SCHEMA + "'")


def raw_safety_queries_v1():
    # This existing new55-purpose wrapper removes ONLY the source17 blanket
    # partial-index exclusion. The complete native index/AST/dependency fence
    # remains mandatory before it. No owner55/owner58 actor API is called here.
    from scripts.canonical0055_catalog_reference_v1 import raw_predicates_v1

    return raw_predicates_v1()


async def capture_fresh_catalog58_v1(connection):
    """Capture one actual owner-held READ ONLY catalog after native safety.

    This is only for fixed fresh reference fixtures. A Paper21 actor is neither
    this owner nor permitted to borrow its system query/table-lock privileges.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncConnection

    _need(
        type(connection) is AsyncConnection
        and connection.in_transaction()
        and connection.dialect.name == "postgresql"
        and connection.dialect.driver == "asyncpg"
    )
    admission = """SELECT current_user=session_user
 AND pg_catalog.current_setting('role')='none'
 AND pg_catalog.current_setting('transaction_read_only')='on'
 AND pg_catalog.current_setting('search_path')='pg_catalog'
 AND pg_catalog.current_setting('TimeZone')='UTC'
 AND pg_catalog.current_setting('server_version_num')='170011'
 AND pg_catalog.version() ~ ' on x86_64[^,]*,'
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl
  WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_database d
 JOIN pg_catalog.pg_roles r ON r.oid=d.datdba
 WHERE d.datname=pg_catalog.current_database() AND r.rolname=current_user
 AND r.rolsuper AND r.rolcanlogin AND NOT r.rolinherit
 AND NOT r.rolcreatedb AND NOT r.rolcreaterole
 AND NOT r.rolreplication AND NOT r.rolbypassrls)
"""
    _need(await connection.scalar(text(admission)) is True)
    await storage.lock_native_owner_marker_v1(connection)
    safety = raw_safety_queries_v1()
    for query in safety:
        _need(await connection.scalar(text(query)) is True)
    # The only observed names admitted to this operation are exactly the
    # reviewed105 source names; unknown relations fail before interpolation.
    names = tuple(
        (
            await connection.execute(
                text(
                    "SELECT c.relname::pg_catalog.text FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind='r' "
                    'ORDER BY c.relname COLLATE pg_catalog."C"'
                )
            )
        ).scalars()
    )
    _need(
        len(TABLES) == 105
        and names == TABLES
        and all(
            type(name) is str and _NAME.fullmatch(name) is not None for name in names
        )
    )
    await connection.exec_driver_sql(
        "LOCK TABLE "
        + ",".join('public."' + name + '"' for name in TABLES)
        + " IN ACCESS SHARE MODE NOWAIT"
    )
    await storage.require_native_owner_storage_v1(connection)
    for query in safety:
        _need(await connection.scalar(text(query)) is True)
    query = catalog_query_v1()
    raw = await connection.scalar(text(query))
    _need(type(raw) is dict and raw.get("schema") == RAW_SCHEMA)
    _need(raw.get("head") == ["0058"])
    _need(raw == await connection.scalar(text(query)))
    return raw, {
        "original_storage_predicate_sha256": hashlib.sha256(
            storage.NATIVE_MARKER_AND_HEAP_SQL_V1.encode("ascii")
        ).hexdigest(),
        "full_typed_catalog_query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "source_table_names": list(TABLES),
        "native_storage_before_and_after_locks": True,
        "raw_safety_before_and_after_locks": True,
        "repeated_catalog_exactly_equal": True,
        "schema_admission": False,
        "paper_actor_admission": False,
        "startup_admission": False,
        "execution_authority": False,
    }


def source_identity(identity, files, files_raw):
    _need(type(identity) is dict and type(files) is dict and type(files_raw) is bytes)
    _need(canonical(files) == files_raw and len(files_raw) <= 2_000_000)
    if identity.get("runner_contract") == CONTRACT:
        _need(
            identity.get("schema") == SCHEMA
            and identity.get("suite") == SUITE
            and identity.get("validation_scope") == SCOPE
            and identity.get("source_contract_head") == "0058"
            and identity.get("build_context") == "admitted_archive_bytes_v1"
            and identity.get("hermetic_regression") == "NOT_RUN"
            and identity.get("host_credentials") is False
            and identity.get("host_env") is False
            and identity.get("full_suite_acceptance") is False
            and identity.get("trading_acceptance") is False
            and type(identity.get("external_order_writes")) is int
            and identity["external_order_writes"] == 0
        )
        for key, width in (
            ("commit", 40),
            ("tree", 40),
            ("archive_sha256", 64),
            ("source_files_sha256", 64),
            ("manifest_sha256", 64),
            ("runner_sha256", 64),
        ):
            _need(type(identity.get(key)) is str)
            _need(
                re.fullmatch("[a-f0-9]{" + str(width) + "}", identity[key], re.ASCII)
                is not None
            )
        _need(identity["source_files_sha256"] == digest(files_raw))
        _need(identity["manifest_sha256"] == files["MANIFEST.sha256"]["sha256"])
        _need(identity["runner_sha256"] == files[_RUNNER]["sha256"])
        source_contract.migration_digest(files)
        bound = {
            key: identity[key]
            for key in (
                "commit",
                "tree",
                "archive_sha256",
                "source_files_sha256",
                "manifest_sha256",
                "runner_sha256",
            )
        } | {
            "source_head": "0058",
            "migration_inventory_sha256": source_contract.pins.MIGRATION_INVENTORY_SHA256,
            "migration_leaf_sha256": source_contract.pins.MIGRATION_SHA256,
            "diagnostic_scope": SCOPE,
        }
    else:
        bound = source_contract.source_identity(identity, files, files_raw)
        _need(
            identity.get("suite") == "full"
            or (
                identity.get("suite") == "postgres"
                and type(identity.get("postgres_component_index")) is int
                and identity["postgres_component_index"] == 0
            )
        )
    for name in (_PRODUCER, _COMPARATOR, shape.PATH):
        _need(type(files.get(name)) is dict)
        _need(_HEX.fullmatch(files[name]["sha256"]) is not None)
        _need(digest((ROOT / name).read_bytes()) == files[name]["sha256"])
    value = shape.source_shape_v1()
    return bound | {
        "producer_sha256": files[_PRODUCER]["sha256"],
        "comparator_sha256": files[_COMPARATOR]["sha256"],
        "source_shape_sha256": files[shape.PATH]["sha256"],
        "source58_scope_sha256": value["source_scope_sha256"],
    }


def _physical(document, context):
    actor, profile = document["actor_physical"], document["profile"]
    owner = actor["owner"]
    _need(type(owner) is list and len(owner) == 9)
    _need(type(owner[0]) is int and 16384 <= owner[0] < 2**32)
    _need(owner[1] == profile["owner"] == profile["session_owner"])
    _need(all(type(flag) is bool for flag in owner[2:]))
    _need(owner[2:] == [True, False, False, False, True, False, False])
    _need(type(actor["database_oid"]) is int and 16384 <= actor["database_oid"] < 2**32)
    _need(type(actor["membership_count"]) is int and actor["membership_count"] == 0)
    _need(profile["server_version"] == "170011" and profile["tls"] is True)
    _need(profile["datum_abi"] == "pg17.amd64.little.datum8.signedchar")
    _need(profile["role"] == "none" and profile["read_only"] == "on")
    _need(profile["search_path"] == "pg_catalog" and profile["timezone"] == "UTC")
    _need(_HEX.fullmatch(context["cluster_system_identifier_sha256"]) is not None)
    marker = [
        row["oid"]
        for kind, row, _ in document["entries"]
        if kind == "relation"
        and row["relkind"] == "r"
        and row["relname"] == "alembic_version"
    ]
    _need(len(marker) == 1 and type(marker[0]) is int and 16384 <= marker[0] < 2**32)
    return {
        "database": profile["database"],
        "database_oid": actor["database_oid"],
        "owner": owner[1],
        "owner_oid": owner[0],
        "owner_flags": owner[2:],
        "owner_memberships": actor["membership_count"],
        "marker_relation_oid": marker[0],
        "server_version_num": profile["server_version"],
        "actual_tls": profile["tls"],
        "cluster_system_identifier_sha256": context["cluster_system_identifier_sha256"],
    }


def _context(context, document):
    _need(type(context) is dict and context["schema"] == RECEIPT_SCHEMA)
    _need(context["fresh_fixture_only"] is True)
    _need(context["original55_to58_migrations_unmodified"] is True)
    _need(context["fixture_only_stopped_genesis_inserted"] is False)
    for key in (
        "preserved_data_opened",
        "owner58_actor_admission",
        "paper_actor_admission",
        "startup_admission",
        "execution_authority",
    ):
        _need(context[key] is False)
    predecessor = context["predecessor_native55_catalog"]
    observation = context["predecessor_native55_observation"]
    _need(predecessor["schema"] == "ctcc.collector_current0055.actual_catalog.v1")
    _need(observation["schema"] == "ctcc.collector_current0055.owner_installation.v1")
    _need(
        observation["database_head"] == "0055"
        and observation["native_baseline_admission"] is True
    )
    _need(
        observation["execution_authority"] is False
        and observation["account_complete"] is False
    )
    _need(predecessor["role"]["name"] == document["profile"]["owner"])
    _need(predecessor["role"]["oid"] == document["actor_physical"]["owner"][0])
    _need(
        predecessor["role"]["membership"] is True
        and predecessor["role"]["create"] is True
    )
    safety = context["safety"]
    _need(safety["source_table_names"] == list(TABLES))
    for key in (
        "native_storage_before_and_after_locks",
        "raw_safety_before_and_after_locks",
        "repeated_catalog_exactly_equal",
    ):
        _need(safety[key] is True)
    _need(
        safety["full_typed_catalog_query_sha256"] == digest(catalog_query_v1().encode())
    )


def _write(root, name, value):
    payload = canonical(value)
    _need(0 < len(payload) <= MAX_CATALOG_BYTES)
    target = root / Path(name).name
    with target.open("xb") as output:
        _need(output.write(payload) == len(payload))
        output.flush()
        os.fsync(output.fileno())
    _need(target.read_bytes() == payload)


def record_reference_v1(label, document, context):
    _need(type(label) is str and label in LABELS)
    directory = os.environ.get(ENV)
    root, bound = None, None
    if directory is not None:
        _need(directory == "/validation-results")
        root = Path(directory)
        _need(root.is_dir() and not root.is_symlink())
        raw_map = Path("/ctcc-source-files.json").read_bytes()
        bound = source_identity(
            read_evidence_json(Path("/ctcc-run-identity.json").read_bytes()),
            read_evidence_json(raw_map),
            raw_map,
        )
    from scripts import paper58_catalog_normalization_v1 as comparator

    try:
        normalized = comparator.normalize_catalog58_v1(document)
        comparator._plain(context)
        _context(context, document)
        physical = _physical(document, context)
    except Exception as error:
        if root is not None:
            stem = "FAILED_CAPTURE_RAW-paper58-reference-" + label
            _write(root, stem + "-raw.json", document)
            _write(
                root,
                stem + "-receipt.json",
                {
                    "schema": "ctcc.paper58.failed_capture_data.v1",
                    "source": bound,
                    "context": context,
                    "raw_catalog_sha256": digest(canonical(document)),
                    "stage": "after_fresh58_capture_before_reference_codec",
                    "codec_error_type": type(error).__name__,
                    "accepted_reference": False,
                    "diagnostic_only": True,
                    "paper_actor_admission": False,
                    "startup_admission": False,
                    "execution_authority": False,
                },
            )
        raise
    if root is None:
        return
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "reference": label,
        "source": bound,
        "database_head": "0058",
        "physical": physical,
        "context": context,
        "raw_catalog_sha256": digest(canonical(document)),
        "normalized_catalog_sha256": digest(canonical(normalized)),
        "source58_scope_sha256": normalized["source58_scope_sha256"],
        "schema_admission": False,
        "paper_actor_admission": False,
        "startup_admission": False,
        "execution_authority": False,
    }
    for name, value in zip(paths(label), (document, normalized, receipt), strict=True):
        _write(root, name, value)


def verify_measurement(raw, normalized, receipt, *, source, label):
    from scripts import paper58_catalog_normalization_v1 as comparator

    _need(
        all(
            type(item) is bytes and 0 < len(item) <= MAX_CATALOG_BYTES
            for item in (raw, normalized, receipt)
        )
    )
    document, normal, record = (
        read_evidence_json(item) for item in (raw, normalized, receipt)
    )
    _need(
        canonical(document) == raw
        and canonical(normal) == normalized
        and canonical(record) == receipt
    )
    _need(record["schema"] == RECEIPT_SCHEMA and record["reference"] == label)
    _need(record["source"] == source and record["database_head"] == "0058")
    _need(record["raw_catalog_sha256"] == digest(raw))
    _need(record["normalized_catalog_sha256"] == digest(normalized))
    _need(comparator.normalize_catalog58_v1(document) == normal)
    _context(record["context"], document)
    _need(record["physical"] == _physical(document, record["context"]))
    _need(
        record["source58_scope_sha256"]
        == normal["source58_scope_sha256"]
        == source["source58_scope_sha256"]
    )
    _need(
        all(
            record[key] is False
            for key in (
                "schema_admission",
                "paper_actor_admission",
                "startup_admission",
                "execution_authority",
            )
        )
    )
    return {
        key: record[key]
        for key in (
            "reference",
            "source",
            "database_head",
            "raw_catalog_sha256",
            "normalized_catalog_sha256",
            "source58_scope_sha256",
            "physical",
        )
    }


def verify_pair_values(values, *, source):
    _need(type(values) is list and len(values) == 2)
    _need([value["reference"] for value in values] == list(LABELS))
    _need(
        all(
            value["source"] == source and value["database_head"] == "0058"
            for value in values
        )
    )
    for key in ("normalized_catalog_sha256", "source58_scope_sha256"):
        _need(
            _HEX.fullmatch(values[0][key]) is not None
            and values[0][key] == values[1][key]
        )
    _need(values[0]["raw_catalog_sha256"] != values[1]["raw_catalog_sha256"])
    for key in (
        "database",
        "database_oid",
        "owner",
        "owner_oid",
        "marker_relation_oid",
    ):
        _need(values[0]["physical"][key] != values[1]["physical"][key])
    _need(
        values[0]["physical"]["server_version_num"]
        == values[1]["physical"]["server_version_num"]
        == "170011"
    )
    _need(
        values[0]["physical"]["cluster_system_identifier_sha256"]
        == values[1]["physical"]["cluster_system_identifier_sha256"]
    )
    return {
        "schema": PAIR_SCHEMA,
        "source": source,
        "database_head": "0058",
        "normalized_catalog_sha256": values[0]["normalized_catalog_sha256"],
        "source58_scope_sha256": values[0]["source58_scope_sha256"],
        "references": values,
        "fresh_fixture_only": True,
        "schema_admission": False,
        "paper_actor_admission": False,
        "startup_admission": False,
        "execution_authority": False,
    }


def verify_reference_pair_v1(root, source, files):
    _need(type(files) is dict and source["source_head"] == "0058")
    values = []
    for label in LABELS:
        payloads = []
        for name in paths(label):
            path = Path(root) / name
            _need(
                path.is_file()
                and not path.is_symlink()
                and 0 < path.stat().st_size <= MAX_CATALOG_BYTES
            )
            payloads.append(path.read_bytes())
        values.append(verify_measurement(*payloads, source=source, label=label))
    return verify_pair_values(values, source=source)
