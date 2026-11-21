"""Custody of one genuine canonical55 full-catalog DATA reference.

This is a diagnostic artifact contract, not a Paper/startup/schema admission
API. The actual test first passes the existing sealed native55 observer.
The complete typed metadata query is shared SQL, without any source17
normalizer, source17 admission, head rewrite, or observed baseline install.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from scripts import current0058_hermetic_contract_v1 as source_contract
from scripts.evidence_json import read_evidence_json

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "ctcc.canonical0055.full_catalog_data.v1"
RECEIPT_SCHEMA = "ctcc.canonical0055.full_catalog_reference_receipt.v1"
FAILED_SCHEMA = "ctcc.canonical0055.failed_capture_data.v1"
MODULE = "tests/integration/test_canonical0055_catalog_reference_v1_pg.py"
CASE = (
    MODULE.removesuffix(".py").replace("/", "."),
    "test_actual0055_canonical_full_catalog_reference_has_native_source_and_tls_proof",
)
FILES = tuple(
    "test-results/canonical0055-" + suffix + ".json"
    for suffix in ("raw", "native-owner", "receipt")
)
FAILED_FILES = tuple(
    "test-results/FAILED_CAPTURE_RAW-canonical0055-" + suffix + ".json"
    for suffix in ("raw", "native-owner", "receipt")
)
ENV = "CTCC_DEVVAL_NATIVE_STORAGE_DIAGNOSTIC_DIR"
MAX_BYTES = 32 * 1024 * 1024
_NATIVE_MODULE = "app/domain/collector_current_0055_catalog_v1.py"
_PRODUCER = "scripts/canonical0055_catalog_reference_v1.py"
_HEX = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
canonical, digest = source_contract.canonical, source_contract.digest


def _need(value):
    if value is not True:
        raise ValueError("canonical0055_reference_evidence_invalid") from None


def catalog_query_v1():
    # The shared query captures the actual head/profile and all public/TOAST
    # catalog objects. Only its DATA schema tag changes for this new purpose.
    from scripts.retained0017_catalog_baseline_v1 import CATALOG_SQL

    old = "'ctcc.retained0017.raw_catalog.v1'"
    _need(CATALOG_SQL.count(old) == 1)
    return CATALOG_SQL.replace(old, "'" + SCHEMA + "'")


def raw_predicates_v1():
    """Closed55 safety context after the unchanged native index/AST gate.

    Source17 has no partial indexes. Canonical55 has reviewed partial btree
    predicates, whose metadata, nodes, operators and dependencies are already
    proved by require_native_owner_storage_v1 before these clauses run.
    Expression-key indexes remain forbidden; every other safety byte stays.
    """
    from scripts import retained0017_catalog_baseline_v1 as source

    old = "(i.indexprs IS NOT NULL OR i.indpred IS NOT NULL)"
    new = "(i.indexprs IS NOT NULL)"
    _need(source.SAFE_CATALOG_SQL.count(old) == 1)
    fixed = source.SAFE_CATALOG_SQL.replace(old, new)
    _need(fixed.count(new) == 1 and fixed.replace(new, old) == source.SAFE_CATALOG_SQL)
    return (
        fixed,
        "SELECT " + source._LANGUAGE_ADMISSION_SQL,
        "SELECT " + source._unknown_nonpublic_objects_predicate(),
    )


def native_baseline_v1():
    # Host archived replay is stdlib-only. This reads the exact source-owned
    # literal; the real owner observer is called inside the actual SQL case.
    tree = ast.parse((ROOT / _NATIVE_MODULE).read_bytes())
    values = {}
    for node in tree.body:
        if type(node) is ast.Assign and len(node.targets) == 1:
            target = node.targets[0]
            if type(target) is ast.Name and target.id in {
                "PG17_NATIVE_BASELINE_STATUS",
                "PG17_NATIVE_BASELINE_SHA256",
            }:
                _need(type(node.value) is ast.Constant)
                values[target.id] = node.value.value
    _need(
        values.get("PG17_NATIVE_BASELINE_STATUS") == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    )
    result = values.get("PG17_NATIVE_BASELINE_SHA256")
    _need(type(result) is str and _HEX.fullmatch(result) is not None)
    return result


def _document(raw, native, receipt):
    _need(
        type(raw) is dict
        and raw.get("schema") == SCHEMA
        and raw.get("head") == ["0055"]
    )
    profile = raw["profile"]
    _need(
        type(profile) is dict
        and profile["owner"] == profile["session_owner"]
        and type(profile["owner"]) is str
        and re.fullmatch(r"ctcc_ref55_owner_[a-f0-9]{16}", profile["owner"], re.ASCII)
        is not None
        and re.fullmatch(
            r"ctcc_ref55_database_[a-f0-9]{16}", profile["database"], re.ASCII
        )
        is not None
        and profile["role"] == "none"
        and profile["read_only"] == "on"
        and profile["search_path"] == "pg_catalog"
        and profile["timezone"] == "UTC"
        and profile["tls"] is True
        and profile["datum_abi"] == "pg17.amd64.little.datum8.signedchar"
        and type(profile["server_version"]) is str
        and re.fullmatch(r"17[0-9]{4}", profile["server_version"], re.ASCII) is not None
    )
    _need(
        type(native) is dict
        and native.get("schema") == "ctcc.collector_current0055.actual_catalog.v1"
    )
    role = native["role"]
    physical = receipt["physical"]
    _need(
        role["name"] == role["current_user"] == role["session_user"] == profile["owner"]
        and role["setting"] == profile["role"]
        and role["superuser"] is True
        and role["login"] is True
        and type(role["oid"]) is int
        and 0 < role["oid"] < 2**32
        and physical["owner"] == profile["owner"]
        and physical["owner_oid"] == role["oid"]
        and physical["database"] == profile["database"]
        and physical["owner_is_database_owner"] is True
        and physical["actual_tls"] is True
        and physical["server_version_num"] == profile["server_version"]
        and all(
            type(physical[key]) is int and 0 < physical[key] < 2**32
            for key in ("database_oid", "marker_relation_oid")
        )
        and _HEX.fullmatch(physical["cluster_system_identifier_sha256"]) is not None
    )
    actor = raw["actor_physical"]
    flags = physical["owner_flags"]
    _need(
        type(flags) is list
        and len(flags) == 7
        and all(type(value) is bool for value in flags)
    )
    _need(flags == [True, False, False, False, True, False, False])
    _need(
        type(actor["owner"]) is list
        and len(actor["owner"]) == 9
        and all(type(value) is bool for value in actor["owner"][2:])
        and type(actor["membership_count"]) is int
        and type(physical["owner_memberships"]) is int
        and actor["database_oid"] == physical["database_oid"]
        and actor["owner"] == [role["oid"], role["name"], *flags]
        and actor["membership_count"] == physical["owner_memberships"] == 0
    )
    _need(
        all(
            role[key] is False
            for key in (
                "createdb",
                "createrole",
                "replication",
                "bypassrls",
            )
        )
    )
    # Frozen0054._wrap, inherited unchanged by55, uses EXISTS(pg_has_role
    # MEMBER): SUPER has semantic membership even with zero physical edges.
    # The independent pg_auth_members COUNTs above remain exact integer zero.
    _need(role["membership"] is True and role["create"] is True)
    entries = raw["entries"]
    _need(type(entries) is list and 0 < len(entries) <= 40000)
    _need(
        all(
            type(row) is list
            and len(row) == 3
            and type(row[0]) is str
            and type(row[1]) is dict
            for row in entries
        )
    )
    public_tables = [
        row[1]["relname"]
        for row in entries
        if row[0] == "relation" and row[1]["relkind"] == "r"
    ]
    constraints = [row[1] for row in entries if row[0] == "constraint"]
    indexes = [row[1] for row in entries if row[0] == "index"]
    _need(
        "alembic_version" in public_tables
        and len(public_tables) == len(set(public_tables))
    )
    # This is a nonvacuous proof of actual Alembic naming-convention context,
    # absent from the legacy boolean diagnostic fixture. No names are repaired.
    _need(
        any(
            row["conname"] == "pk_audit_logs" and row["contype"] == "p"
            for row in constraints
        )
    )
    _need(any(row["indpred"] is not None for row in indexes))
    _need(all(row["indexprs"] is None for row in indexes))
    _need(receipt["public_table_names"] == sorted(public_tables))
    observation = receipt["native55_observation"]
    _need(
        observation["schema"] == "ctcc.collector_current0055.owner_installation.v1"
        and observation["database_head"] == "0055"
        and observation["role_oid"] == role["oid"]
        and observation["native_definitions_sha256"] == native_baseline_v1()
        and observation["native_baseline_admission"] is True
        and observation["execution_authority"] is False
        and observation["account_complete"] is False
    )
    _need(
        receipt["schema"] == RECEIPT_SCHEMA
        and receipt["database_target"] == "0055"
        and receipt["canonical_naming_convention"] is True
        and receipt["full_typed_catalog_query_sha256"]
        == digest(catalog_query_v1().encode("utf-8"))
        and receipt["raw_sha256"] == digest(canonical(raw))
        and receipt["native_owner_sha256"] == digest(canonical(native))
        and type(receipt["native_owner_query_sha256"]) is str
        and _HEX.fullmatch(receipt["native_owner_query_sha256"]) is not None
        and all(
            receipt[key] is False
            for key in (
                "schema_admission",
                "startup_admission",
                "paper_admission",
                "execution_authority",
                "saved_reference_is_authority",
            )
        )
    )


def _record_context_v1(receipt):
    directory = os.environ.get(ENV)
    if directory is None:
        return None, receipt
    root = Path(directory)
    _need(
        root == Path("/validation-results") and root.is_dir() and not root.is_symlink()
    )
    identity = read_evidence_json(Path("/ctcc-run-identity.json").read_bytes())
    files = read_evidence_json(Path("/ctcc-source-files.json").read_bytes())
    from scripts import native_owner_storage_diagnostic_v1 as diagnostic

    _need(
        identity["schema"] == diagnostic.SCHEMA
        and identity["runner_contract"] == diagnostic.CONTRACT
        and identity["validation_scope"] == diagnostic.SCOPE
    )
    receipt = dict(
        receipt,
        source_commit=identity["commit"],
        source_tree=identity["tree"],
        source_manifest_sha256=identity["manifest_sha256"],
        producer_sha256=files[_PRODUCER]["sha256"],
        native_source_sha256=files[_NATIVE_MODULE]["sha256"],
    )
    return root, receipt


def record_if_requested_v1(raw, native, receipt):
    # The fixed actual case reaches this function only after real native55
    # baseline admission and complete typed capture. Bind its source context
    # before the new codec so an honest refusal can retain diagnostic DATA.
    root, receipt = _record_context_v1(receipt)
    try:
        _document(raw, native, receipt)
    except Exception as error:
        if root is not None:
            failed = dict(
                receipt,
                schema=FAILED_SCHEMA,
                attempted_reference_schema=RECEIPT_SCHEMA,
                status="FAILED_CAPTURE_RAW",
                stage="after_native55_admission_before_reference_codec",
                codec_error_type=type(error).__name__,
                diagnostic_only=True,
                accepted_reference=False,
                schema_admission=False,
                startup_admission=False,
                paper_admission=False,
                execution_authority=False,
                saved_reference_is_authority=False,
                expected_full_typed_catalog_query_sha256=digest(
                    catalog_query_v1().encode("utf-8")
                ),
            )
            _write_exact_files_v1(root, FAILED_FILES, (raw, native, failed))
        raise
    if root is not None:
        _write_files_v1(root, (raw, native, receipt))


def _write_files_v1(root, documents):
    _write_exact_files_v1(root, FILES, documents)


def _write_exact_files_v1(root, names, documents):
    _need(type(names) is tuple and names in (FILES, FAILED_FILES))
    _need(type(documents) is tuple and len(documents) == len(FILES))
    for name, value in zip(names, documents, strict=True):
        payload = canonical(value)
        _need(0 < len(payload) <= MAX_BYTES)
        target = root / Path(name).name
        with target.open("xb") as output:
            _need(output.write(payload) == len(payload))
            output.flush()
            os.fsync(output.fileno())
        _need(target.read_bytes() == payload)


def verify_reference_v1(root, source, files):
    # This app module is read as AST, not imported by the stdlib host. Bind
    # those late-read literal bytes to the admitted archive before using them.
    _need(
        digest((ROOT / _NATIVE_MODULE).read_bytes()) == files[_NATIVE_MODULE]["sha256"]
    )
    documents = []
    for name in FILES:
        path = Path(root) / name
        _need(
            path.is_file()
            and not path.is_symlink()
            and 0 < path.stat().st_size <= MAX_BYTES
        )
        payload = path.read_bytes()
        value = read_evidence_json(payload)
        _need(canonical(value) == payload)
        documents.append(value)
    raw, native, receipt = documents
    _document(raw, native, receipt)
    _need(
        all(
            receipt[key] == source[name]
            for key, name in (
                ("source_commit", "commit"),
                ("source_tree", "tree"),
                ("source_manifest_sha256", "manifest_sha256"),
            )
        )
    )
    _need(
        receipt["producer_sha256"] == files[_PRODUCER]["sha256"]
        and receipt["native_source_sha256"] == files[_NATIVE_MODULE]["sha256"]
    )
    return {
        "database_target": "0055",
        "raw_sha256": receipt["raw_sha256"],
        "native_owner_sha256": receipt["native_owner_sha256"],
        "canonical_naming_convention": True,
        "admission": "DENY",
    }
