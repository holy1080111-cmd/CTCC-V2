"""Synthetic codec/denial tests only; these do not accept a real PG run."""

import copy
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import current0057_g12_measurement_v1 as measurement
from scripts import current0057_hermetic_contract as contract
from scripts import current0057_migration_pin as pins
from scripts import verify_current0057_g12_smoke_pair as pair
from scripts import verify_current0057_hermetic as runner

ROOT = Path(__file__).resolve().parents[2]


def _files():
    paths = [*ROOT.joinpath("migrations").rglob("*.py")]
    paths.extend(
        ROOT / name
        for name in (
            "MANIFEST.sha256",
            "scripts/current0057_g12_measurement_v1.py",
            contract.RUNNER_PATH,
        )
    )
    return {
        path.relative_to(ROOT).as_posix(): {
            "sha256": contract.digest(path.read_bytes()),
            "size": path.stat().st_size,
        }
        for path in paths
    }


def _source_identity(files):
    return {
        "runner_contract": contract.RUNNER_CONTRACT,
        "source_contract_head": "0057",
        "validation_scope": contract.SCOPE,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "archive_sha256": "c" * 64,
        "source_files_sha256": contract.digest(contract.canonical(files)),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "runner_sha256": files[contract.RUNNER_PATH]["sha256"],
        "build_context": "admitted_archive_bytes_v1",
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
    }


def _measurement(source):
    return {
        "schema": "ctcc.current0057.g12_profile_measurement.v1",
        "source": source,
        "case": contract.CASES[0][1],
        "database_head": "0057",
        "server_version_num": "170011",
        "cluster_system_identifier_sha256": "1" * 64,
        "actual_tls": True,
        "role_oids": {"writer": 101, "custody": 102, "frame_reader": 103},
        "grant_statements": 44,
        "acl_shape_sha256": "2" * 64,
        "actual_g12_catalog_sha256": "3" * 64,
        "g12_definitions_sha256": "4" * 64,
        "inherited_native_definitions_sha256": "5" * 64,
        "startup_admission": False,
        "execution_authority": False,
        "trading_acceptance": False,
    }


def test_exact_actual57_migration_inventory_retains_distinct_parent56_prefix():
    files = _files()
    assert contract.migration_digest(files) == pins.MIGRATION_INVENTORY_SHA256
    parent = {name: row for name, row in files.items() if name != pins.MIGRATION_PATH}
    assert contract.previous.migration_digest(parent) == pins.PARENT_INVENTORY_SHA256
    with pytest.raises(ValueError):
        contract.migration_digest(parent)
    with pytest.raises(ValueError):
        contract.previous.migration_digest(files)


@pytest.mark.parametrize("change", ["leaf", "old_prefix", "extra", "missing"])
def test_successor_migration_inventory_drift_is_not_a_warning(change):
    files = _files()
    if change == "leaf":
        files[pins.MIGRATION_PATH]["sha256"] = "0" * 64
    elif change == "old_prefix":
        files["migrations/env.py"]["sha256"] = "0" * 64
    elif change == "extra":
        files["migrations/versions/0058_DECLARED_SYNTHETIC.py"] = {"sha256": "0" * 64}
    else:
        files.pop("migrations/env.py")
    with pytest.raises(ValueError):
        contract.migration_digest(files)


def test_actual_source_receipt_has57_head_and_no_trading_authority():
    files = _files()
    value = contract.source_identity(
        _source_identity(files), files, contract.canonical(files)
    )
    assert value["source_head"] == "0057"
    assert value["migration_inventory_sha256"] == pins.MIGRATION_INVENTORY_SHA256
    assert value["migration_leaf_sha256"] == pins.MIGRATION_SHA256


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_contract_head", "0056"),
        ("validation_scope", "full"),
        ("host_credentials", True),
        ("host_env", True),
        ("external_order_writes", False),
        ("external_order_writes", 1),
        ("trading_acceptance", True),
        ("source_files_sha256", "0" * 64),
        ("manifest_sha256", "0" * 64),
        ("runner_sha256", "0" * 64),
        ("build_context", "mutable_directory"),
    ],
)
def test_source_receipt_rejects_relabel_and_host_or_execution_authority(key, value):
    files = _files()
    identity = _source_identity(files)
    identity[key] = value
    with pytest.raises(ValueError):
        contract.source_identity(identity, files, contract.canonical(files))


def test_full_regression_source_context_keeps_actual_runner_identity_distinct():
    files = _files()
    path = ROOT / contract.FULL_RUNNER_PATH
    files[contract.FULL_RUNNER_PATH] = {
        "sha256": contract.digest(path.read_bytes()),
        "size": path.stat().st_size,
    }
    identity = _source_identity(files)
    identity.update(
        runner_contract=contract.FULL_RUNNER_CONTRACT,
        validation_scope=contract.FULL_SCOPE,
        runner_sha256=files[contract.FULL_RUNNER_PATH]["sha256"],
    )
    source = contract.source_identity(identity, files, contract.canonical(files))
    assert (
        source["source_head"] == "0057"
        and source["runner_sha256"] == files[contract.FULL_RUNNER_PATH]["sha256"]
    )
    identity["runner_sha256"] = files[contract.RUNNER_PATH]["sha256"]
    with pytest.raises(ValueError):
        contract.source_identity(identity, files, contract.canonical(files))


def test_full_runner_identity_cannot_be_validated_as_smoke_by_reusing_four_cases(
    tmp_path,
):
    (tmp_path / "identity.json").write_bytes(
        contract.canonical(
            {
                "runner_contract": contract.FULL_RUNNER_CONTRACT,
                "validation_scope": contract.FULL_SCOPE,
            }
        )
    )
    with pytest.raises(ValueError):
        contract.verify_smoke(
            tmp_path, b"DECLARED_SYNTHETIC_ARCHIVE", commit="a" * 40, tree="b" * 40
        )


def test_migration_readback_never_projects_real57_back_to56():
    value = {
        "schema": "ctcc.migration_identity.v1",
        "status": "PASS",
        "source_head": "0057",
        "database_head": "0057",
        "migration_sha256": pins.MIGRATION_INVENTORY_SHA256,
        "execution_authority": False,
    }
    assert (
        contract.migration_readback(
            contract.canonical(value), pins.MIGRATION_INVENTORY_SHA256
        )
        == value
    )
    for key in ("source_head", "database_head"):
        changed = dict(value, **{key: "0056"})
        with pytest.raises(ValueError):
            contract.migration_readback(
                contract.canonical(changed), pins.MIGRATION_INVENTORY_SHA256
            )


def test_measurement_codec_retains_exact_role_and_source_binding():
    source = {"DECLARED_SYNTHETIC_CODEC_SOURCE": True}
    value = _measurement(source)
    assert (
        measurement.verify_measurement(
            contract.canonical(value), source=source, case=contract.CASES[0][1]
        )
        == value
    )
    with pytest.raises(ValueError):
        measurement.verify_measurement(
            json.dumps(value).encode(), source=source, case=value["case"]
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("source", {"wrong": True}),
        ("case", "wrong"),
        ("database_head", "0056"),
        ("server_version_num", "170010"),
        ("actual_tls", False),
        ("grant_statements", 42),
        ("grant_statements", True),
        ("role_oids", {"writer": True, "custody": 102, "frame_reader": 103}),
        ("role_oids", {"writer": 101, "custody": 101, "frame_reader": 103}),
        ("startup_admission", True),
        ("execution_authority", True),
        ("trading_acceptance", True),
        ("g12_definitions_sha256", "g" * 64),
    ],
)
def test_measurement_denies_changed_profile_and_boolean_integer_substitutes(key, value):
    source = {"DECLARED_SYNTHETIC_CODEC_SOURCE": True}
    changed = _measurement(source)
    changed[key] = value
    with pytest.raises(ValueError):
        measurement.verify_measurement(
            contract.canonical(changed), source=source, case=contract.CASES[0][1]
        )


def _argv(tmp_path):
    return [
        "runner",
        "--archive",
        str(tmp_path / "source.tar"),
        "--archive-sha256",
        "c" * 64,
        "--commit",
        "a" * 40,
        "--tree",
        "b" * 40,
        "--output",
        str(tmp_path / "results"),
    ]


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--suite", "full"],
        ["--suite", "shard"],
        ["--g12-successor-smoke", "--postgres-component-index", "0"],
    ],
)
def test_smoke_is_explicit_and_cannot_stand_in_for_full_component(
    tmp_path, monkeypatch, extra
):
    monkeypatch.setattr(sys, "argv", [*_argv(tmp_path), *extra])
    monkeypatch.setattr(
        runner,
        "_admit_archive",
        lambda *_: pytest.fail("denied arguments reached archive/build"),
    )
    with pytest.raises((ValueError, SystemExit)):
        runner.main()
    assert not (tmp_path / "results").exists()


def test_explicit_smoke_cli_preserves_actual57_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [*_argv(tmp_path), "--g12-successor-smoke"])
    args = runner._arguments()
    assert args.g12_successor_smoke is True and args.suite == "postgres"
    assert args.postgres_component_index == 1


def test_changed_archive_digest_denies_before_docker(tmp_path):
    args = SimpleNamespace(archive_sha256="0" * 64)
    with pytest.raises(ValueError, match="archive_identity_changed"):
        runner._admit_archive(args, b"DECLARED_SYNTHETIC_ARCHIVE")


def test_pair_codec_refuses_two_copies_of_one_cluster(tmp_path, monkeypatch):
    for name in ("cluster1", "cluster2"):
        report = tmp_path / name / "ctcc-g12-smoke"
        report.mkdir(parents=True)
        (report / "identity.json").write_text("{}")
        (report.parent / "ctcc-source.tar").write_bytes(b"DECLARED_SYNTHETIC_ARCHIVE")
    value = {
        "source": {"synthetic": True},
        "cluster_system_identifier_sha256": "1" * 64,
        "inherited_native_definitions_sha256": "2" * 64,
        "g12_definitions_sha256": "3" * 64,
    }
    monkeypatch.setattr(
        contract, "verify_smoke", lambda *_args, **_kw: copy.deepcopy(value)
    )
    with pytest.raises(ValueError):
        pair.verify_pair(tmp_path, commit="a" * 40, tree="b" * 40)


def test_scoped_artifact_inventory_cannot_omit_any_of_four_measurements():
    expected = contract.artifacts([{"name": name} for name in contract.STAGES])
    assert (
        set(contract.REPORTS) <= expected
        and len(contract.REPORTS) == len(contract.CASES) == 4
    )
    assert contract.JUNIT in expected
    assert len(contract.STAGES) == len(set(contract.STAGES)) == 21


@pytest.mark.asyncio
async def test_optional_measurement_has_no_database_effect_without_explicit_mount(
    monkeypatch,
):
    monkeypatch.delenv(measurement.ENV, raising=False)
    await measurement.record_profile_if_requested(object(), object(), case="unused")


def test_archive_head_selection_does_not_admit_a_malformed_git_tree(tmp_path):
    archive = tmp_path / "DECLARED_SYNTHETIC_SOURCE.tar"
    with tarfile.open(
        archive, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": "a" * 40}
    ) as stream:
        member = tarfile.TarInfo(pins.MIGRATION_PATH)
        member.size = 1
        stream.addfile(member, io.BytesIO(b"x"))
    args = SimpleNamespace(
        archive_sha256=contract.digest(archive.read_bytes()),
        commit="a" * 40,
        tree="0" * 40,
    )
    with pytest.raises(ValueError, match="git_identity_changed"):
        runner._admit_archive(args, archive.read_bytes())
