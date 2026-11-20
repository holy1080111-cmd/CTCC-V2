"""Full source57/physical57 hermetic evidence; historical56 remains explicit."""

from __future__ import annotations

import io
import math
import re
import sys
import tarfile
from pathlib import Path

from scripts import current0056_hermetic_contract as previous
from scripts import current0057_collection_v1 as collection
from scripts import current0057_hermetic_contract as g12
from scripts import verify_final_hermetic as shared
from scripts.evidence_json import read_evidence_json

HEAD, PARENT = "0057", "0056"
RUNNER_CONTRACT, RUNNER_PATH, SCOPE = (
    g12.FULL_RUNNER_CONTRACT,
    g12.FULL_RUNNER_PATH,
    g12.FULL_SCOPE,
)
REQUIRED_CASES = (*previous.REQUIRED_CURRENT56_CASES, *g12.CASES)
HISTORICAL_REPORTS = (
    "test-results/devval-campaign-native-catalogs.json",
    "test-results/current0056-fitted-catalog-single-redacted.json",
    "test-results/current0056-inherited-native-catalog-redacted.json",
    "test-results/current0056-postgrant-acl-redacted.json",
)
ROOT_TRUST_STAGE, ROOT_TRUST_REPORT = (
    previous.ROOT_TRUST_STAGE,
    previous.ROOT_TRUST_REPORT,
)
HOST_HELPERS = (
    "current0057_full_hermetic_contract_v1.py",
    "current0057_collection_v1.py",
    "versioned_hermetic_engine_v1.py",
    "verify_current0057_full_hermetic.py",
    "versioned_hermetic_union_v1.py",
    "verify_current0057_linux_union.py",
    "current0057_hermetic_contract.py",
    "current0057_g12_measurement_v1.py",
    "current0057_migration_pin.py",
    "current_hermetic_contract_dispatch_v1.py",
    "hermetic_workflow_profile_v1.py",
    "historical0056_on_current0057_observation_v1.py",
    "current0056_hermetic_contract.py",
    "current0056_migration_pin.py",
    "current0056_fitted_catalog_measurement_v1.py",
    "current0056_fitted_catalog_pair_v1.py",
    "current0056_postgrant_acl_measurement_v1.py",
    "current0056_fitted_delta_catalog_v1.py",
    "current0055_hermetic_contract.py",
    "current0055_catalog_receipt.py",
    "verify_current0055_hermetic.py",
    "verify_current0055_linux_union.py",
    "verify_current0056_hermetic.py",
    "devval_campaign_physical_catalog_v1.py",
    "devval_hermetic_tls.py",
    "verify_final_hermetic.py",
    "verify_linux_shard.py",
    "evidence_json.py",
)
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_GIT = re.compile(r"[a-f0-9]{40}\Z")
digest, canonical, _need = g12.digest, g12.canonical, g12._need
migration_digest, migration_readback = g12.migration_digest, g12.migration_readback
component_report_paths = collection.report_paths


def component_is_anchor(mode, index):
    collection._mode(mode, index)
    return mode == "full" or mode == "postgres" and index == 0


def requires_historical(mode, index):
    collection._mode(mode, index)
    return mode == "full" or mode == "postgres" and index == 1


def requires_g12(mode, index):
    collection._mode(mode, index)
    return mode == "full" or mode == "postgres" and index == 2


def component_test_stage(mode, index):
    collection._mode(mode, index)
    return "pytest-" + mode + ("" if index is None else "-" + str(index))


def component_stage_names(mode, index):
    collection._mode(mode, index)
    stages = [
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
        "ruff",
        "format",
    ]
    if component_is_anchor(mode, index):
        stages.extend(
            (
                "migration-initial-supported",
                "migration-upgrade-supported",
                "migration-downgrade-supported",
                "migration-supported-readback",
                "migration-upgrade-parent",
                "migration-parent-readback",
            )
        )
    stages.extend(("migration-upgrade", "migration-identity"))
    if component_is_anchor(mode, index):
        stages.extend(
            (
                "migration-empty-downgrade",
                "migration-empty-downgrade-readback",
                "migration-empty-reupgrade",
                "migration-empty-reupgrade-identity",
            )
        )
    stages.extend(("migration-reservation-unique", "migration-drift"))
    if previous.requires_root_trust(mode, index):
        stages.append(ROOT_TRUST_STAGE)
    stages.append(component_test_stage(mode, index))
    if component_is_anchor(mode, index):
        stages.extend(
            (
                "api-start",
                "api-before-restart",
                "api-instance-before",
                "postgres-restart",
                "postgres-ready-after-restart",
                "redis-restart",
                "redis-ready-after-restart",
                "api-restart",
                "api-after-restart",
                "api-instance-after",
                "migration-identity-after-restart",
                "migration-drift-after-restart",
            )
        )
    return tuple(stages)


def component_artifacts(mode, index):
    junit, selected = component_report_paths(mode, index)
    names = {
        "identity.json",
        "source-files.json",
        "steps.json",
        "cleanup.json",
        junit,
        selected,
        *(name + ".log" for name in component_stage_names(mode, index)),
    }
    if previous.requires_root_trust(mode, index):
        names.add(ROOT_TRUST_REPORT)
    if requires_historical(mode, index):
        names.update(HISTORICAL_REPORTS)
    if requires_g12(mode, index):
        names.update(g12.REPORTS)
    if mode == "postgres":
        names.add("test-results/postgres-progress.log")
    return names


def cleanup_inventory(mode, index, prefix):
    persistent = {
        ("network", prefix + "network"),
        ("container", prefix + "postgres"),
        ("container", prefix + "redis"),
    }
    if component_is_anchor(mode, index):
        persistent.add(("container", prefix + "api"))
    host_or_exec = {
        "build",
        "image-identity",
        "network-create",
        "network-inspect",
        "postgres-start",
        "redis-start",
        "postgres-ready",
        "redis-ready",
        "migration-parent-readback",
        "migration-empty-downgrade-readback",
        "migration-reservation-unique",
        "api-start",
        "api-before-restart",
        "api-instance-before",
        "postgres-restart",
        "postgres-ready-after-restart",
        "redis-restart",
        "redis-ready-after-restart",
        "api-restart",
        "api-after-restart",
        "api-instance-after",
    }
    return persistent | {
        ("container", prefix + stage)
        for stage in component_stage_names(mode, index)
        if stage not in host_or_exec
    }


def require_host_code(files):
    for name in HOST_HELPERS:
        path = Path(__file__).with_name(name)
        _need(
            not path.is_symlink()
            and path.is_file()
            and digest(path.read_bytes()) == files["scripts/" + name]["sha256"]
        )
    # Include the actual transitive script verifier closure, rather than
    # allowing an unlisted imported helper to differ from the admitted source.
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith("scripts.") and path is not None:
            path = Path(path)
            _need(
                path.suffix == ".py"
                and path.parent == Path(__file__).parent
                and not path.is_symlink()
            )
            _need(digest(path.read_bytes()) == files["scripts/" + path.name]["sha256"])


def required_cases(mode, index):
    collection._mode(mode, index)
    if mode == "full":
        return REQUIRED_CASES
    if mode == "shard":
        return ()
    return tuple(
        case
        for case in REQUIRED_CASES
        if case[0].replace(".", "/") + ".py" in collection.postgres_paths(index)
    )


def _read(root, name, maximum=64 * 1024 * 1024):
    from scripts.verify_current0055_linux_union import _read as bounded

    return bounded(root, name, maximum=maximum)


def _read_archive(archive):
    if type(archive) is bytes:
        raw = archive
    else:
        _need(
            isinstance(archive, Path)
            and not archive.is_symlink()
            and archive.is_file()
            and 0 < archive.stat().st_size <= 128 * 1024 * 1024
        )
        raw = archive.read_bytes()
    _need(type(raw) is bytes and 0 < len(raw) <= 128 * 1024 * 1024)
    return raw


def verify_component(root, archive, *, commit, tree, manifest_sha256):
    _need(isinstance(root, Path) and root.is_dir() and not root.is_symlink())
    _need(
        type(commit) is type(tree) is type(manifest_sha256) is str
        and _GIT.fullmatch(commit) is not None
        and _GIT.fullmatch(tree) is not None
        and _SHA.fullmatch(manifest_sha256) is not None
    )
    raw = _read_archive(archive)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as stream:
        _need(
            stream.pax_headers.get("comment") == commit
            and shared.archive_tree(stream) == tree
        )
        files = shared.archive_files(stream)
    require_host_code(files)
    identity = read_evidence_json(_read(root, "identity.json"))
    mode = identity["suite"]
    index = (
        identity["postgres_component_index"]
        if mode == "postgres"
        else identity["shard_index"]
    )
    collection._mode(mode, index)
    mapping = _read(root, "source-files.json")
    _need(read_evidence_json(mapping) == files)
    source = g12.source_identity(identity, files, mapping)
    _need(
        identity["runner_contract"] == RUNNER_CONTRACT
        and identity["validation_scope"] == SCOPE
    )
    _need(
        identity["commit"] == commit
        and identity["tree"] == tree
        and identity["archive_sha256"] == digest(raw)
        and identity["manifest_sha256"] == manifest_sha256
    )
    _need(
        identity["shard_index"] == (index if mode == "shard" else None)
        and identity["postgres_component_index"]
        == (index if mode == "postgres" else None)
    )
    _need(
        identity.get("component_validation") == "PASS"
        and identity.get("hermetic_regression")
        == ("PASS" if mode == "full" else "PENDING_UNION")
        and identity.get("cleanup") == "PASS"
        and "failure_type" not in identity
    )
    hashes = read_evidence_json(_read(root, "results.sha256.json"))
    _need(type(hashes) is dict and set(hashes) == component_artifacts(mode, index))
    _need(
        all(
            type(pin) is str
            and _SHA.fullmatch(pin) is not None
            and digest(_read(root, name)) == pin
            for name, pin in hashes.items()
        )
    )
    steps = read_evidence_json(_read(root, "steps.json"))
    _need(
        type(steps) is list
        and tuple(row["name"] for row in steps) == component_stage_names(mode, index)
    )
    _need(
        all(
            type(row["exit_code"]) is int
            and row["exit_code"] == 0
            and type(row["elapsed_seconds"]) in (int, float)
            and math.isfinite(row["elapsed_seconds"])
            and row["elapsed_seconds"] >= 0
            and "failure_type" not in row
            and "policy_refusal" not in row
            for row in steps
        )
    )
    run_id = identity["run_id"]
    _need(type(run_id) is str and re.fullmatch(r"[a-f0-9]{12}", run_id) is not None)
    prefix = "ctcc-final-" + run_id + "-"
    image = read_evidence_json(_read(root, "image-identity.log"))
    network = read_evidence_json(_read(root, "network-inspect.log"))
    lock = read_evidence_json(_read(root, "dependency-lock.log"))
    _need(
        image["Id"] == identity["image_id"]
        and re.fullmatch(r"sha256:[a-f0-9]{64}", image["Id"]) is not None
        and image["Config"]["Labels"]["org.opencontainers.image.revision"] == commit
        and image["Config"]["Labels"]["org.ctcc.source.tree"] == tree
    )
    _need(
        network["Name"] == prefix + "network"
        and network["Internal"] is True
        and network["Containers"] in ({}, None)
        and network["Labels"]["org.ctcc.validation.run"] == run_id
    )
    _need(
        lock["target"] == "linux"
        and lock["lock_sha256"]
        == files["requirements/validation-linux-py312.lock"]["sha256"]
        and lock["accepted_full_validation_baseline"] is False
    )
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
        _read(root, "migration-reservation-unique.log").strip()
        == previous.RESERVATION_UNIQUE
        and b"No new upgrade operations detected" in _read(root, "migration-drift.log")
    )
    _need(
        _read(root, component_test_stage(mode, index) + ".log")
        .splitlines()
        .count(b"HERMETIC_EXECUTION_AUTHORITY=0")
        == 1
    )
    migration = migration_readback(
        _read(root, "migration-identity.log"), migration_digest(files)
    )
    _need(identity["migration"] == migration)
    previous._cleanup(
        _read(root, "cleanup.json"), prefix, anchor=component_is_anchor(mode, index)
    )
    cleanup_rows = read_evidence_json(_read(root, "cleanup.json"))
    expected_cleanup = cleanup_inventory(mode, index, prefix)
    _need(
        len(cleanup_rows) == len(expected_cleanup)
        and {(row["kind"], row["name"]) for row in cleanup_rows} == expected_cleanup
    )
    trusted = (
        previous.verify_root_trust(root, mode, index)
        if previous.requires_root_trust(mode, index)
        else ()
    )
    junit, selected = component_report_paths(mode, index)
    tests = shared.verify_pytest_report(
        root / junit,
        required_cases=required_cases(mode, index),
        allow_unrelated_skips=mode != "postgres",
    )
    _need(tests == identity["tests"] and (mode != "postgres" or tests["skipped"] == 0))
    previous._verify_platform_skips(
        _read(root, junit),
        mode=mode,
        expected_skipped=tests["skipped"],
        root_trust_verified=trusted,
    )
    selection = collection.verify_collection(
        _read(root, selected), mode=mode, index=index, report=tests
    )
    shared.verify_selected_junit_nodes(
        root / junit, selection["selected_nodeids"], expected_sha256=tests["sha256"]
    )
    historical = None
    if requires_historical(mode, index):
        from scripts.historical0056_on_current0057_observation_v1 import (
            verify_historical0056_components,
        )

        historical = verify_historical0056_components(root, identity, files)
    g12_measurements = []
    if requires_g12(mode, index):
        from scripts.current0057_g12_measurement_v1 import verify_measurement

        g12_measurements = [
            verify_measurement(_read(root, path, 16_384), source=source, case=case[1])
            for path, case in zip(g12.REPORTS, g12.CASES, strict=True)
        ]
        _need(
            all(
                len({row[key] for row in g12_measurements}) == 1
                for key in (
                    "cluster_system_identifier_sha256",
                    "inherited_native_definitions_sha256",
                    "g12_definitions_sha256",
                )
            )
        )
    if component_is_anchor(mode, index):
        first_id, first_time = previous._restart_time(
            _read(root, "api-instance-before.log"), prefix + "api"
        )
        last_id, last_time = previous._restart_time(
            _read(root, "api-instance-after.log"), prefix + "api"
        )
        previous._api_readback(_read(root, "api-before-restart.log"))
        previous._api_readback(_read(root, "api-after-restart.log"))
        _need(first_id == last_id and first_time < last_time)
        _need(
            _read(root, "redis-ready-after-restart.log").strip() == b"PONG"
            and previous.postgres_ready_readback(
                _read(root, "postgres-ready-after-restart.log"), ephemeral_tls=True
            )
        )
        _need(
            migration_readback(
                _read(root, "migration-identity-after-restart.log"),
                migration_digest(files),
            )
            == migration
            and b"No new upgrade operations detected"
            in _read(root, "migration-drift-after-restart.log")
        )
        _need(
            _read(root, "migration-parent-readback.log").strip() == PARENT.encode()
            and _read(root, "migration-empty-downgrade-readback.log").strip()
            == PARENT.encode()
        )
        _need(
            migration_readback(
                _read(root, "migration-empty-reupgrade-identity.log"),
                migration_digest(files),
            )
            == migration
        )
        _need(
            read_evidence_json(_read(root, "migration-supported-readback.log"))
            == shared.expected_migration_revision_readback("0001")
        )
    require_host_code(files)
    return {
        "identity": identity,
        "full_nodeids": selection["full_nodeids"],
        "selected_nodeids": selection["selected_nodeids"],
        "historical0056": historical,
        "actual0057_g12": g12_measurements,
    }
