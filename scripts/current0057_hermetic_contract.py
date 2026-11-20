"""Exact-source DB0057 G12 smoke evidence; never full release acceptance."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

from scripts import current0056_hermetic_contract as previous
from scripts import current0057_migration_pin as pins
from scripts import verify_final_hermetic as shared
from scripts.evidence_json import read_evidence_json

HEAD = pins.HEAD
PARENT = pins.PARENT
RUNNER_CONTRACT = "ctcc.current0057.g12_hermetic_smoke.v1"
RUNNER_PATH = "scripts/verify_current0057_hermetic.py"
SCOPE = "g12_restricted_writer_smoke_only"
JUNIT = "test-results/g12-successor-smoke.xml"
TEST_MODULE = "tests.integration.test_g12_restricted_writer_0057_pg"
CASES = tuple(
    (TEST_MODULE, name)
    for name in (
        "test_actual0057_restricted_g12_commits_without_update_and_reopens",
        "test_actual0057_profile_denies_each_revocation_extra_grant_and_disabled_guard",
        "test_actual0057_inherited_roles_writer_control_and_frame_keep_actual_head",
        "test_actual0057_native_custody_six_real_sessions_keep_issued_profile",
    )
)
REPORTS = tuple(
    f"test-results/current0057-g12-profile-{i}.json" for i in range(len(CASES))
)
STAGES = (
    "build",
    "image-identity",
    "network-create",
    "network-inspect",
    "postgres-start",
    "redis-start",
    "postgres-ready",
    "redis-ready",
    "copied-manifest",
    "copied-source-exact",
    "dependency-lock",
    "migration-upgrade-parent",
    "migration-parent-readback",
    "migration-upgrade",
    "migration-identity",
    "migration-empty-downgrade",
    "migration-empty-downgrade-readback",
    "migration-empty-reupgrade",
    "migration-empty-reupgrade-identity",
    "migration-drift",
    "g12-successor-smoke",
)
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_GIT = re.compile(r"[a-f0-9]{40}\Z")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _need(value):
    if value is not True:
        raise ValueError("current57_g12_smoke_evidence_invalid") from None


def migration_digest(files):
    migration = {
        name: row["sha256"]
        for name, row in files.items()
        if name.startswith("migrations/") and name.endswith(".py")
    }
    _need(
        len(migration) == pins.MIGRATION_INVENTORY_COUNT
        and migration.get(pins.MIGRATION_PATH) == pins.MIGRATION_SHA256
        and digest(canonical(migration)) == pins.MIGRATION_INVENTORY_SHA256
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
        type(identity) is dict and type(files) is dict and canonical(files) == files_raw
    )
    _need(
        identity.get("runner_contract") == RUNNER_CONTRACT
        and identity.get("source_contract_head") == HEAD
        and identity.get("validation_scope") == SCOPE
    )
    _need(
        all(
            type(identity.get(k)) is str and _GIT.fullmatch(identity[k]) is not None
            for k in ("commit", "tree")
        )
    )
    _need(
        all(
            type(identity.get(k)) is str and _HEX.fullmatch(identity[k]) is not None
            for k in ("archive_sha256", "source_files_sha256", "manifest_sha256")
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
        "producer_sha256": files["scripts/current0057_g12_measurement_v1.py"]["sha256"],
        "runner_sha256": files[RUNNER_PATH]["sha256"],
    }


def _read(root, relative, maximum=64 * 1024 * 1024):
    path = root / relative
    _need(
        not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= maximum
    )
    return path.read_bytes()


def artifacts(steps):
    return {
        "identity.json",
        "source-files.json",
        "steps.json",
        "cleanup.json",
        JUNIT,
        *REPORTS,
        *(step["name"] + ".log" for step in steps),
    }


def _verify_host_code(files):
    for name in (
        "current0057_hermetic_contract.py",
        "current0057_g12_measurement_v1.py",
        "current0057_migration_pin.py",
        "verify_current0057_hermetic.py",
        "verify_current0057_g12_smoke_pair.py",
        "current0056_hermetic_contract.py",
        "current0056_migration_pin.py",
        "current0055_hermetic_contract.py",
        "verify_current0055_hermetic.py",
        "verify_current0056_hermetic.py",
        "verify_final_hermetic.py",
        "devval_hermetic_tls.py",
        "verify_linux_shard.py",
        "evidence_json.py",
    ):
        path = Path(__file__).with_name(name)
        _need(
            not path.is_symlink()
            and digest(path.read_bytes()) == files["scripts/" + name]["sha256"]
        )


def verify_smoke(root: Path, raw: bytes, *, commit, tree):
    identity = read_evidence_json(_read(root, "identity.json"))
    steps = read_evidence_json(_read(root, "steps.json"))
    hashes = read_evidence_json(_read(root, "results.sha256.json"))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        _need(
            archive.pax_headers.get("comment") == commit
            and shared.archive_tree(archive) == tree
        )
        files = shared.archive_files(archive)
    _verify_host_code(files)
    mapping = _read(root, "source-files.json")
    _need(read_evidence_json(mapping) == files)
    source = source_identity(identity, files, mapping)
    _need(
        identity["commit"] == commit
        and identity["tree"] == tree
        and identity["archive_sha256"] == digest(raw)
    )
    _need(
        identity.get("component_validation")
        == identity.get("hermetic_regression")
        == "NOT_RUN"
        and identity.get("smoke_validation") == "PASS"
        and identity.get("cleanup") == "PASS"
        and "failure_type" not in identity
    )
    _need(
        type(steps) is list
        and tuple(s["name"] for s in steps) == STAGES
        and all(
            type(s["exit_code"]) is int
            and s["exit_code"] == 0
            and "failure_type" not in s
            for s in steps
        )
    )
    _need(
        set(hashes) == artifacts(steps)
        and all(digest(_read(root, name)) == sha for name, sha in hashes.items())
    )
    _need(
        migration_readback(
            _read(root, "migration-identity.log"), migration_digest(files)
        )
        == identity["migration"]
    )
    _need(
        migration_readback(
            _read(root, "migration-empty-reupgrade-identity.log"),
            migration_digest(files),
        )
        == identity["migration"]
    )
    _need(
        _read(root, "migration-parent-readback.log").strip() == PARENT.encode()
        and _read(root, "migration-empty-downgrade-readback.log").strip()
        == PARENT.encode()
    )
    _need(b"No new upgrade operations detected" in _read(root, "migration-drift.log"))
    _need(
        b"manifest check passed:" in _read(root, "copied-manifest.log")
        and _read(root, "copied-source-exact.log").strip() == str(len(files)).encode()
    )
    _need(
        _read(root, "redis-ready.log").strip() == b"PONG"
        and previous.postgres_ready_readback(
            _read(root, "postgres-ready.log"), ephemeral_tls=True
        )
    )
    _need(
        _read(root, "g12-successor-smoke.log")
        .splitlines()
        .count(b"HERMETIC_EXECUTION_AUTHORITY=0")
        == 1
    )
    image = read_evidence_json(_read(root, "image-identity.log"))
    network = read_evidence_json(_read(root, "network-inspect.log"))
    prefix = "ctcc-final-" + identity["run_id"] + "-"
    _need(
        type(identity["run_id"]) is str
        and re.fullmatch(r"[a-f0-9]{12}", identity["run_id"]) is not None
    )
    _need(
        image["Id"] == identity["image_id"]
        and re.fullmatch(r"sha256:[a-f0-9]{64}", image["Id"]) is not None
        and image["Config"]["Labels"]["org.opencontainers.image.revision"] == commit
        and image["Config"]["Labels"]["org.ctcc.source.tree"] == tree
    )
    _need(
        network["Name"] == prefix + "network"
        and network["Internal"] is True
        and network["Containers"] in (None, {})
        and network["Labels"]["org.ctcc.validation.run"] == identity["run_id"]
    )
    lock = read_evidence_json(_read(root, "dependency-lock.log"))
    _need(
        lock["target"] == "linux"
        and lock["lock_sha256"]
        == files["requirements/validation-linux-py312.lock"]["sha256"]
        and lock["accepted_full_validation_baseline"] is False
    )
    previous._cleanup(_read(root, "cleanup.json"), prefix, anchor=False)
    junit = shared.verify_pytest_report(root / JUNIT, required_cases=CASES)
    _need(
        junit == identity["tests"]
        and junit["tests"] == junit["passed"] == len(CASES)
        and junit["skipped"] == 0
    )
    from scripts.current0057_g12_measurement_v1 import verify_measurement

    measurements = [
        verify_measurement(_read(root, path, 16_384), source=source, case=case[1])
        for path, case in zip(REPORTS, CASES, strict=True)
    ]
    _need(
        len({m["cluster_system_identifier_sha256"] for m in measurements}) == 1
        and len({m["inherited_native_definitions_sha256"] for m in measurements}) == 1
        and len({m["g12_definitions_sha256"] for m in measurements}) == 1
    )
    return {
        "schema": "ctcc.current0057.g12_smoke_readback.v1",
        "source": source,
        "tests": junit,
        "cluster_system_identifier_sha256": measurements[0][
            "cluster_system_identifier_sha256"
        ],
        "inherited_native_definitions_sha256": measurements[0][
            "inherited_native_definitions_sha256"
        ],
        "g12_definitions_sha256": measurements[0]["g12_definitions_sha256"],
        "smoke_validation": "PASS",
        "hermetic_regression": "NOT_RUN",
        "execution_authority": False,
        "trading_acceptance": False,
    }
