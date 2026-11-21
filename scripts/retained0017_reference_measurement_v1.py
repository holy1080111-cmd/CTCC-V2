"""Exact-source58 custody of two fresh isolated source17 schema references.

Measured JSON never installs the literal product reference or grants admission.
The pair is a required post-test artifact gate, independently replayed later.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from scripts import current0058_hermetic_contract_v1 as source_contract
from scripts.evidence_json import read_evidence_json

ENV = "CTCC_RETAINED0017_REFERENCE_EVIDENCE_DIR"
LABELS = ("reference_a", "reference_b")
MODULE = "tests.integration.test_retained0017_catalog_references_v1_pg"
CASES = tuple(
    (
        MODULE,
        "test_actual0017_fresh_independent_catalog_reference_is_complete["
        + label
        + "]",
    )
    for label in LABELS
)
_PRODUCER = "scripts/retained0017_reference_measurement_v1.py"
_COMPARATOR = "scripts/retained0017_catalog_baseline_v1.py"
PROBE_RUNNER = "scripts/run_retained0017_reference_probe_v1.py"
PROBE_CONTRACT = "ctcc.source58.retained0017_reference_probe.v1"
PROBE_SCOPE = "two_fresh_source0017_catalogs_only_no_full_regression"
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_NAME = re.compile(r"ctcc_ref17_[a-z0-9_]{1,48}\Z", re.ASCII)
MAX_CATALOG_BYTES = 16 * 1024 * 1024
_FLAGS = {
    "rolsuper": False,
    "rolinherit": False,
    "rolcreaterole": False,
    "rolcreatedb": False,
    "rolcanlogin": True,
    "rolreplication": False,
    "rolbypassrls": False,
}
_PHYSICAL_KEYS = {
    "database",
    "database_oid",
    "owner",
    "owner_oid",
    "owner_flags",
    "owner_memberships",
    "marker_relation_oid",
    "server_version_num",
    "cluster_system_identifier_sha256",
    "actual_tls",
}
canonical, digest = source_contract.canonical, source_contract.digest


def _need(value):
    if value is not True:
        raise ValueError("retained0017_reference_evidence_invalid") from None


def paths(label):
    _need(label in LABELS)
    prefix = "test-results/retained0017-" + label.replace("_", "-")
    return tuple(
        prefix + suffix for suffix in ("-raw.json", "-normalized.json", "-receipt.json")
    )


REPORTS = tuple(name for label in LABELS for name in paths(label))


def source_identity(identity, files, files_raw):
    if identity.get("runner_contract") == PROBE_CONTRACT:
        source = _probe_source_identity(identity, files, files_raw)
    else:
        source = source_contract.source_identity(identity, files, files_raw)
        _need(
            identity.get("suite") == "full"
            or identity.get("suite") == "postgres"
            and identity.get("postgres_component_index") == 3
        )
    _need(
        all(
            type(files.get(name)) is dict
            and type(files[name].get("sha256")) is str
            and _HEX.fullmatch(files[name]["sha256"]) is not None
            for name in (_PRODUCER, _COMPARATOR)
        )
    )
    return source | {
        "producer_sha256": files[_PRODUCER]["sha256"],
        "comparator_sha256": files[_COMPARATOR]["sha256"],
    }


def _probe_source_identity(identity, files, files_raw):
    _need(
        type(identity) is dict
        and type(files) is dict
        and type(files_raw) is bytes
        and canonical(files) == files_raw
        and 0 < len(files_raw) <= 2_000_000
    )
    _need(
        identity.get("validation_scope") == PROBE_SCOPE
        and identity.get("suite") == "retained0017-reference-probe"
        and identity.get("source_contract_head") == "0058"
        and identity.get("build_context") == "admitted_archive_bytes_v1"
        and identity.get("hermetic_regression") == "NOT_RUN"
        and identity.get("full_suite_acceptance") is False
        and identity.get("host_credentials") is False
        and identity.get("host_env") is False
        and type(identity.get("external_order_writes")) is int
        and identity["external_order_writes"] == 0
        and identity.get("trading_acceptance") is False
    )
    _need(
        all(
            type(identity.get(key)) is str
            and re.fullmatch(pattern, identity[key], re.ASCII) is not None
            for key, pattern in (
                ("commit", r"[0-9a-f]{40}"),
                ("tree", r"[0-9a-f]{40}"),
                *(
                    (key, r"[0-9a-f]{64}")
                    for key in (
                        "archive_sha256",
                        "source_files_sha256",
                        "manifest_sha256",
                        "runner_sha256",
                    )
                ),
            )
        )
    )
    _need(
        identity["source_files_sha256"] == digest(files_raw)
        and identity["manifest_sha256"] == files["MANIFEST.sha256"]["sha256"]
        and identity["runner_sha256"] == files[PROBE_RUNNER]["sha256"]
    )
    source_contract.migration_digest(files)
    return {
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
        "diagnostic_scope": PROBE_SCOPE,
    }


def _physical(physical, document):
    _need(type(physical) is dict and set(physical) == _PHYSICAL_KEYS)
    _need(
        all(
            type(physical[name]) is str and _NAME.fullmatch(physical[name]) is not None
            for name in ("database", "owner")
        )
        and physical["database"] != physical["owner"]
        and all(
            type(physical[name]) is int and 16384 <= physical[name] < 2**32
            for name in ("database_oid", "owner_oid", "marker_relation_oid")
        )
        and type(physical["owner_flags"]) is dict
        and physical["owner_flags"] == _FLAGS
        and all(type(value) is bool for value in physical["owner_flags"].values())
        and type(physical["owner_memberships"]) is int
        and physical["owner_memberships"] == 0
        and physical["server_version_num"] == "170011"
        and physical["actual_tls"] is True
        and type(physical["cluster_system_identifier_sha256"]) is str
        and _HEX.fullmatch(physical["cluster_system_identifier_sha256"]) is not None
    )
    _need(
        type(document) is dict
        and document.get("schema") == "ctcc.retained0017.raw_catalog.v1"
        and document.get("head") == ["0017"]
        and type(document.get("profile")) is dict
    )
    profile = document["profile"]
    _need(
        profile.get("owner") == profile.get("session_owner") == physical["owner"]
        and profile.get("database") == physical["database"]
        and profile.get("read_only") == "on"
        and profile.get("role") == "none"
        and profile.get("search_path") == "pg_catalog"
        and profile.get("timezone") == "UTC"
        and profile.get("server_version") == physical["server_version_num"]
        and profile.get("tls") is True
    )
    markers = [
        row[1]
        for row in document["entries"]
        if row[0] == "relation" and row[1].get("relname") == "alembic_version"
    ]
    databases = [row[1] for row in document["entries"] if row[0] == "database"]
    _need(
        len(markers) == len(databases) == 1
        and markers[0]["oid"] == physical["marker_relation_oid"]
        and markers[0]["relowner"] == physical["owner_oid"]
        and markers[0]["relkind"] == "r"
        and markers[0]["relam"] == 2
        and databases[0]["oid"] == physical["database_oid"]
        and databases[0]["datname"] == physical["database"]
        and databases[0]["datdba"] == physical["owner_oid"]
    )
    references = {(row[0], row[1]): row[2] for row in document["references"]}
    _need(
        references["pg_authid", physical["owner_oid"]]["identity"] == physical["owner"]
        and references["pg_database", physical["database_oid"]]["identity"]
        == physical["database"]
    )
    actor = document.get("actor_physical")
    _need(
        type(actor) is dict
        and actor.get("database_oid") == physical["database_oid"]
        and actor.get("owner")
        == [
            physical["owner_oid"],
            physical["owner"],
            *(physical["owner_flags"][name] for name in _FLAGS),
        ]
        and type(actor.get("membership_count")) is int
        and actor["membership_count"] == physical["owner_memberships"]
    )


def verify_measurement(raw, normalized, receipt, *, source, label):
    from scripts import retained0017_catalog_baseline_v1 as comparator

    _need(
        label in LABELS
        and type(source) is dict
        and source.get("source_head") == "0058"
        and all(
            type(source.get(key)) is str and _HEX.fullmatch(source[key]) is not None
            for key in ("producer_sha256", "comparator_sha256")
        )
    )
    _need(
        all(
            type(value) is bytes and 0 < len(value) <= MAX_CATALOG_BYTES
            for value in (raw, normalized)
        )
        and type(receipt) is bytes
        and 0 < len(receipt) <= 32_768
    )
    document, normal, value = (
        read_evidence_json(item) for item in (raw, normalized, receipt)
    )
    _need(
        all(
            canonical(item) == data
            for item, data in ((document, raw), (normal, normalized), (value, receipt))
        )
    )
    _need(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "source",
            "reference",
            "database_head",
            "physical",
            "source17_prefix_sha256",
            "raw_catalog_sha256",
            "normalized_catalog_sha256",
            "catalog_scope",
            "authentication_scope",
            "reference_seal_installed",
            "schema_upgrade_authorized",
            "startup_admission",
            "execution_authority",
        }
        and value["schema"] == "ctcc.retained0017.reference_measurement.v1"
        and value["source"] == source
        and value["reference"] == label
        and value["database_head"] == "0017"
        and value["catalog_scope"] == "fresh_source17_schema_only"
        and value["authentication_scope"]
        == "isolated_tls_fixture_no_password_auth_acceptance"
        and all(
            value[name] is False
            for name in (
                "reference_seal_installed",
                "schema_upgrade_authorized",
                "startup_admission",
                "execution_authority",
            )
        )
        and value["raw_catalog_sha256"] == digest(raw)
        and value["normalized_catalog_sha256"] == digest(normalized)
    )
    _physical(value["physical"], document)
    expected = comparator.normalize_catalog17_v1(document)
    contract = comparator.source17_contract_v1()
    _need(
        normal == expected
        and normal["schema"] == "ctcc.retained0017.normalized_catalog.v1"
        and normal["source17_prefix_sha256"]
        == value["source17_prefix_sha256"]
        == contract["source17_prefix_sha256"]
        and type(value["source17_prefix_sha256"]) is str
        and _HEX.fullmatch(value["source17_prefix_sha256"]) is not None
    )
    return value


def verify_pair_values(values, *, source):
    """Pair gate over independently verified receipts; never a product seal."""
    _need(type(source) is dict and source.get("source_head") == "0058")
    _need(type(values) is list and len(values) == 2)
    _need([value["reference"] for value in values] == list(LABELS))
    _need(
        all(
            value["source"] == source and value["database_head"] == "0017"
            for value in values
        )
    )
    for key in ("normalized_catalog_sha256", "source17_prefix_sha256"):
        _need(
            type(values[0][key]) is str
            and _HEX.fullmatch(values[0][key]) is not None
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
        "schema": "ctcc.retained0017.independent_reference_pair.v1",
        "source": source,
        "database_head": "0017",
        "source17_prefix_sha256": values[0]["source17_prefix_sha256"],
        "normalized_catalog_sha256": values[0]["normalized_catalog_sha256"],
        "references": values,
        "reference_seal_installed": False,
        "schema_upgrade_authorized": False,
        "startup_admission": False,
        "execution_authority": False,
    }


def _read(root, name, maximum):
    _need(isinstance(root, Path) and root.is_dir() and not root.is_symlink())
    path = root / name
    _need(
        path.parent.is_dir()
        and not path.parent.is_symlink()
        and path.is_file()
        and not path.is_symlink()
    )
    raw = path.read_bytes()
    _need(0 < len(raw) <= maximum)
    return raw


def verify_reference_pair(root, *, source):
    values = []
    for label in LABELS:
        raw_path, normalized_path, receipt_path = paths(label)
        values.append(
            verify_measurement(
                _read(root, raw_path, MAX_CATALOG_BYTES),
                _read(root, normalized_path, MAX_CATALOG_BYTES),
                _read(root, receipt_path, 32_768),
                source=source,
                label=label,
            )
        )
    return verify_pair_values(values, source=source)


def record_reference_if_requested(*, label, document, physical):
    output = os.environ.get(ENV)
    if output is None:
        return
    from scripts import retained0017_catalog_baseline_v1 as comparator

    _need(output == "/validation-results" and label in LABELS)
    root = Path(output)
    identity = read_evidence_json(Path("/ctcc-run-identity.json").read_bytes())
    files_raw = Path("/ctcc-source-files.json").read_bytes()
    source = source_identity(identity, read_evidence_json(files_raw), files_raw)
    normal = comparator.normalize_catalog17_v1(document)
    raw, normalized = canonical(document), canonical(normal)
    receipt = canonical(
        {
            "schema": "ctcc.retained0017.reference_measurement.v1",
            "source": source,
            "reference": label,
            "database_head": "0017",
            "physical": physical,
            "source17_prefix_sha256": normal["source17_prefix_sha256"],
            "raw_catalog_sha256": digest(raw),
            "normalized_catalog_sha256": digest(normalized),
            "catalog_scope": "fresh_source17_schema_only",
            "authentication_scope": "isolated_tls_fixture_no_password_auth_acceptance",
            "reference_seal_installed": False,
            "schema_upgrade_authorized": False,
            "startup_admission": False,
            "execution_authority": False,
        }
    )
    verify_measurement(raw, normalized, receipt, source=source, label=label)
    _write_reference_files(root, label, (raw, normalized, receipt))


def _write_reference_files(root, label, payloads):
    _need(root.is_dir() and not root.is_symlink())
    for name, data in zip(paths(label), payloads, strict=True):
        # The container root is already the host's test-results bind mount.
        # Artifact readers retain their test-results/ relative paths.
        path = root / Path(name).name
        _need(path.parent.is_dir() and not path.parent.is_symlink())
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _need(path.read_bytes() == data)
