"""Review independently measured fitted catalogs on disposable bootstrap databases."""

from __future__ import annotations

import hashlib
import json
import re

from scripts.evidence_json import read_evidence_json

LABELS = ("a", "b")
CASE = (
    "tests.integration.test_retained0056_fitted_reference_pair_v1_pg",
    "test_actual_unscoped0056_bootstrap_owner_reference",
)
NODE = CASE[0].replace(".", "/") + ".py::" + CASE[1]
MEASUREMENT = "test-results/unscoped-fitted56-reference.json"
REPORT = "test-results/unscoped-fitted56-reference.xml"
SCHEMA = "ctcc.retained0056.fitted_bootstrap_owner_reference.v1"
PAIR_SCHEMA = "ctcc.retained0056.fitted_bootstrap_owner_reference_pair.v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def need(condition):
    if condition is not True:
        raise ValueError("retained_fitted0056_reference_refused") from None


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def measurement(raw, *, label, manifest):
    need(label in LABELS and type(raw) is bytes and 0 < len(raw) <= 8192)
    row = read_evidence_json(raw)
    salts = {
        "schema": SCHEMA,
        "reference": label,
        "database_head": "0056",
        "source_manifest_sha256": manifest,
        "postgres_version_num": "170011",
        "bootstrap_owner_oid": 10,
        "independent_readbacks": 2,
        "fitted_tables": 3,
        "fitted_routines": 20,
        "fitted_shared_dependencies": 0,
        "fitted_initial_privileges": 0,
        "external_role_grants_installed": False,
        "startup_admission": False,
        "execution_authority": False,
    }
    hashes = {
        "cluster_system_identifier_sha256",
        "structural_sha256",
        "role_catalog_sha256",
        "logical_dependency_sha256",
        "physical_allocation_sha256",
    }
    numbers = {"database_oid", "marker_relation_oid", "fitted_dependency_count"}
    need(type(row) is dict and set(row) == set(salts) | hashes | numbers)
    need(raw == canonical(row))
    need(type(manifest) is str and _HEX.fullmatch(manifest) is not None)
    need(
        all(
            type(row[name]) is type(value) and row[name] == value
            for name, value in salts.items()
        )
    )
    need(
        all(
            type(row[name]) is str and _HEX.fullmatch(row[name]) is not None
            for name in hashes
        )
    )
    need(
        all(
            type(row[name]) is int and 16384 <= row[name] < 2**32
            for name in ("database_oid", "marker_relation_oid")
        )
    )
    need(
        type(row["fitted_dependency_count"]) is int
        and 0 < row["fitted_dependency_count"] <= 512
    )
    return {"sha256": digest(raw), "measurement": row}


def compare(first, second, *, manifest):
    """Compare verified DATA for two distinct initdb and object allocations."""
    reads = [
        measurement(raw, label=label, manifest=manifest)
        for label, raw in zip(LABELS, (first, second), strict=True)
    ]
    before, after = (item["measurement"] for item in reads)
    same = (
        "structural_sha256",
        "role_catalog_sha256",
        "logical_dependency_sha256",
        "fitted_dependency_count",
    )
    different = (
        "cluster_system_identifier_sha256",
        "database_oid",
        "marker_relation_oid",
        "physical_allocation_sha256",
    )
    need(all(before[name] == after[name] for name in same))
    need(all(before[name] != after[name] for name in different))
    return {
        "schema": PAIR_SCHEMA,
        "source_manifest_sha256": manifest,
        **{name: before[name] for name in same},
        "references": reads,
        "reference_seal_installed": False,
        "schema_upgrade_authorized": False,
        "startup_admission": False,
        "execution_authority": False,
    }
