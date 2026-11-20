"""Closed exact-source58/physical58 metadata, never exchange acceptance."""

from __future__ import annotations

import re

from scripts import current0057_hermetic_contract as previous
from scripts import current0058_migration_pin as pins
from scripts.evidence_json import read_evidence_json

HEAD, PARENT = pins.HEAD, pins.PARENT
RUNNER_CONTRACT = "ctcc.current0058.hermetic.v1"
RUNNER_PATH = "scripts/verify_current0058_full_hermetic.py"
SCOPE = "hermetic_regression_component"
FULL_RUNNER_CONTRACT, FULL_RUNNER_PATH, FULL_SCOPE = RUNNER_CONTRACT, RUNNER_PATH, SCOPE
CASES = (
    (
        "tests.integration.test_native_demo_profile_0058_pg",
        "test_actual0058_restricted_demo_profile_retains_full_graph_and_protective_guards",
    ),
)
REPORTS = ("test-results/current0058-profile-0.json",)
CUSTODY_CASE = (
    "tests.integration.test_native_demo_profile_0058_pg",
    "test_actual0058_native_g12_custody_six_sessions_keep_real_head",
)
CUSTODY_REPORT = "test-results/current0058-g12-custody.json"
digest, canonical = previous.digest, previous.canonical
_HEX, _GIT = re.compile(r"[a-f0-9]{64}\Z"), re.compile(r"[a-f0-9]{40}\Z")


def _need(value):
    if value is not True:
        raise ValueError("current58_hermetic_evidence_invalid") from None


def migration_digest(files):
    inventory = {
        name: row["sha256"]
        for name, row in files.items()
        if name.startswith("migrations/") and name.endswith(".py")
    }
    _need(
        len(inventory) == pins.MIGRATION_INVENTORY_COUNT
        and inventory.get(pins.MIGRATION_PATH) == pins.MIGRATION_SHA256
        and digest(canonical(inventory)) == pins.MIGRATION_INVENTORY_SHA256
    )
    parent = {name: row for name, row in files.items() if name != pins.MIGRATION_PATH}
    _need(previous.migration_digest(parent) == pins.PARENT_INVENTORY_SHA256)
    return pins.MIGRATION_INVENTORY_SHA256


def migration_readback(raw, expected):
    value = read_evidence_json(raw)
    _need(
        value
        == {
            "schema": "ctcc.migration_identity.v1",
            "status": "PASS",
            "source_head": HEAD,
            "database_head": HEAD,
            "migration_sha256": expected,
            "execution_authority": False,
        }
        and value["execution_authority"] is False
    )
    return value


def source_identity(identity, files, files_raw):
    _need(
        type(identity) is dict
        and type(files) is dict
        and type(files_raw) is bytes
        and 0 < len(files_raw) <= 2_000_000
        and canonical(files) == files_raw
    )
    _need(
        identity.get("runner_contract") == RUNNER_CONTRACT
        and identity.get("validation_scope") == SCOPE
        and identity.get("source_contract_head") == HEAD
    )
    _need(
        all(
            type(identity.get(key)) is str
            and pattern.fullmatch(identity[key]) is not None
            for keys, pattern in (
                (("commit", "tree"), _GIT),
                (("archive_sha256", "source_files_sha256", "manifest_sha256"), _HEX),
            )
            for key in keys
        )
    )
    _need(
        identity["source_files_sha256"] == digest(files_raw)
        and identity["manifest_sha256"] == files["MANIFEST.sha256"]["sha256"]
        and identity.get("build_context") == "admitted_archive_bytes_v1"
        and identity.get("runner_sha256") == files[RUNNER_PATH]["sha256"]
    )
    _need(
        identity.get("host_credentials") is False
        and identity.get("host_env") is False
        and type(identity.get("external_order_writes")) is int
        and identity["external_order_writes"] == 0
        and identity.get("trading_acceptance") is False
    )
    migration_digest(files)
    return {
        key: identity[key]
        for key in (
            "commit",
            "tree",
            "archive_sha256",
            "source_files_sha256",
            "manifest_sha256",
        )
    } | {
        "source_head": HEAD,
        "migration_inventory_sha256": pins.MIGRATION_INVENTORY_SHA256,
        "migration_leaf_sha256": pins.MIGRATION_SHA256,
        "producer_sha256": files["scripts/current0058_profile_measurement_v1.py"][
            "sha256"
        ],
        "runner_sha256": files[RUNNER_PATH]["sha256"],
    }
