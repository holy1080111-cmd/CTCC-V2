"""Declared synthetic collection/denial mechanics; no Docker or DB admission."""

import io
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import current0057_collection_v1 as collection
from scripts import current0057_full_hermetic_contract_v1 as contract
from scripts import hermetic_workflow_profile_v1 as workflow
from scripts import verify_current0057_full_hermetic as runner
from scripts import versioned_hermetic_engine_v1 as engine
from scripts import versioned_hermetic_union_v1 as union
from tests.historical_source_prefix_v1 import historical_migration_paths


def _nodes():
    nodes = [
        min(collection.postgres_paths(0)) + "::test_declared_synthetic_pg0",
        min(collection.postgres_paths(1)) + "::test_declared_synthetic_pg1",
    ]
    nodes.extend(
        module.replace(".", "/") + ".py::" + name
        for module, name in contract.SUCCESSOR_CASES
    )
    for index in range(8):
        ordinal = 0
        while True:
            path = f"tests/unit/test_DECLARED_SYNTHETIC_{ordinal}.py"
            if collection.previous.module_shard(path) == index:
                nodes.append(path + "::test_declared_synthetic")
                break
            ordinal += 1
    return nodes


def test_all_nodes_are_owned_exactly_once_and_actual57_cases_have_their_own_pg_owner():
    full, owned = _nodes(), []
    for mode, index, _ in union.specs_for(contract, "a" * 40):
        selected = [node for node in full if collection.selected_for(mode, index, node)]
        assert selected
        assert not set(owned) & set(selected)
        owned.extend(selected)
        raw = contract.canonical(
            collection._record(mode, index, full, selected, selected, 0)
        )
        assert (
            collection.verify_collection(
                raw, mode=mode, index=index, report={"tests": len(selected)}
            )["selected_nodeids"]
            == selected
        )
    assert set(owned) == set(full) and len(owned) == len(full)
    assert collection.postgres_paths(2) == collection.SUCCESSOR_PATHS
    assert not collection.postgres_paths(0) & collection.SUCCESSOR_PATHS
    assert not collection.postgres_paths(1) & collection.SUCCESSOR_PATHS


@pytest.mark.parametrize(
    "mode,index",
    [
        ("full", 0),
        ("postgres", None),
        ("postgres", True),
        ("postgres", 3),
        ("shard", None),
        ("shard", 8),
        ("wrong", 0),
    ],
)
def test_closed_collection_indices_cannot_reinterpret_old56_or_unknown_owners(
    mode, index
):
    with pytest.raises(ValueError):
        collection.report_paths(mode, index)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_head",
        "wrong_count",
        "boolean_count",
        "missing_node",
        "duplicate_node",
        "wrong_owner",
        "not_observed",
        "failed_pytest",
        "extra_key",
        "wrong_schema",
    ],
)
def test_collection_refuses_dropped_duplicate_misclassified_or_unexecuted_cases(change):
    full = _nodes()
    selected = [node for node in full if collection.selected_for("postgres", 2, node)]
    record = collection._record("postgres", 2, full, selected, selected, 0)
    if change == "wrong_head":
        record["source_contract_head"] = "0056"
    elif change == "wrong_count":
        record["postgres_component_count"] = 2
    elif change == "boolean_count":
        record["pytest_exitstatus"] = False
    elif change == "missing_node":
        record["selected_nodeids"] = selected[:-1]
    elif change == "duplicate_node":
        record["full_nodeids"] = full + full[:1]
    elif change == "wrong_owner":
        record["postgres_component_index"] = 1
    elif change == "not_observed":
        record["observed_nodeids"] = []
    elif change == "failed_pytest":
        record["pytest_exitstatus"] = 1
    elif change == "extra_key":
        record["passed"] = True
    else:
        record["schema"] = "ctcc.linux_postgres_component_collection.v1"
    with pytest.raises(ValueError):
        collection.verify_collection(
            contract.canonical(record),
            mode="postgres",
            index=2,
            report={"tests": len(selected)},
        )


def test_full_collection_retains_all_nodes_and_source57_without_projection():
    nodes = _nodes()
    record = collection._record("full", None, nodes, nodes, nodes, 0)
    assert (
        collection.verify_collection(
            contract.canonical(record),
            mode="full",
            index=None,
            report={"tests": len(nodes)},
        )
        == record
    )
    assert (
        record["source_contract_head"] == "0057"
        and record["postgres_component_count"] == 3
    )


def test_closed_profile_never_accepts_a_string_or_a_caller_dto():
    assert engine.contract_for(engine.HermeticProfileV1.CURRENT0057) is contract
    for value in ("0057", SimpleNamespace(head="0057"), object(), True, None):
        with pytest.raises(ValueError):
            engine.contract_for(value)


@pytest.mark.parametrize(
    "mode,index",
    [
        ("full", None),
        *(("postgres", i) for i in range(3)),
        *(("shard", i) for i in range(8)),
    ],
)
def test_exact_stage_and_artifact_contract_keeps_migrations_restart_and_required_source_receipts(
    mode, index
):
    stages = contract.component_stage_names(mode, index)
    artifacts = contract.component_artifacts(mode, index)
    assert len(stages) == len(set(stages))
    assert {name + ".log" for name in stages} <= artifacts
    assert {
        "copied-manifest",
        "copied-source-exact",
        "dependency-lock",
        "ruff",
        "format",
        "migration-upgrade",
        "migration-identity",
        "migration-drift",
    } <= set(stages)
    if contract.component_is_anchor(mode, index):
        assert {
            "migration-upgrade-parent",
            "migration-parent-readback",
            "migration-empty-downgrade",
            "migration-empty-reupgrade",
            "api-restart",
            "postgres-restart",
            "redis-restart",
            "migration-identity-after-restart",
            "migration-drift-after-restart",
        } <= set(stages)
    assert (
        set(contract.HISTORICAL_REPORTS) <= artifacts
        if contract.requires_historical(mode, index)
        else not set(contract.HISTORICAL_REPORTS) & artifacts
    )
    assert (
        set(contract.g12.REPORTS) <= artifacts
        if contract.requires_g12(mode, index)
        else not set(contract.g12.REPORTS) & artifacts
    )
    assert ("test-results/postgres-progress.log" in artifacts) == (mode == "postgres")
    command = engine.test_command(contract, mode, index)
    assert (
        "scripts.hermetic_pytest" in command
        and "scripts.current0057_collection_v1" in command
    )
    assert "--ctcc-current57-suite=" + mode in command


def test_required_cases_keep_old_safety_cases_and_all_new_actual57_g12_cases():
    assert set(contract.previous.REQUIRED_CURRENT56_CASES) < set(
        contract.REQUIRED_CASES
    )
    assert contract.required_cases("postgres", 2) == contract.SUCCESSOR_CASES
    assert not set(contract.required_cases("postgres", 0)) & set(contract.g12.CASES)
    assert not set(contract.required_cases("postgres", 1)) & set(contract.g12.CASES)
    assert len(set(contract.REQUIRED_CASES)) == len(contract.REQUIRED_CASES)


def _args(tmp_path, extra):
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
        str(tmp_path / "out"),
        *extra,
    ]


@pytest.mark.parametrize(
    "extra",
    [
        ["--suite", "postgres"],
        ["--suite", "postgres", "--postgres-component-index", "3"],
        ["--suite", "shard", "--shard-index", "8"],
        ["--suite", "full", "--shard-index", "0"],
        ["--g12-successor-smoke"],
    ],
)
def test_full_runner_argument_denials_happen_before_archive_or_docker(
    tmp_path, monkeypatch, extra
):
    monkeypatch.setattr(sys, "argv", _args(tmp_path, extra))
    monkeypatch.setattr(
        runner,
        "admit_archive",
        lambda *_: pytest.fail("invalid arguments reached archive admission"),
    )
    with pytest.raises((ValueError, SystemExit)):
        runner.main()
    assert not (tmp_path / "out").exists()


def test_full_runner_accepts_explicit_third_component_without_claiming_execution(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        sys,
        "argv",
        _args(tmp_path, ["--suite", "postgres", "--postgres-component-index", "2"]),
    )
    args = runner._arguments()
    assert (
        args.suite == "postgres"
        and args.postgres_component_index == 2
        and args.shard_index is None
    )


def test_union_refuses_missing_or_smoke_only_artifacts_before_component_replay(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        contract,
        "verify_component",
        lambda *_args, **_kw: pytest.fail("incomplete set reached component replay"),
    )
    (tmp_path / "ctcc-db0057-g12-smoke-1").mkdir()
    with pytest.raises(ValueError):
        union.verify_union(
            engine.HermeticProfileV1.CURRENT0057,
            tmp_path,
            commit="a" * 40,
            tree="b" * 40,
            manifest_sha256="c" * 64,
        )


def _archive(head, migrations=None):
    raw = io.BytesIO()
    with tarfile.open(
        fileobj=raw,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "a" * 40},
    ) as stream:
        paths = migrations or {
            f"migrations/versions/{head}_declared_synthetic.py": b"DECLARED_SYNTHETIC_ROUTING_ONLY"
        }
        for name, data in paths.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            stream.addfile(info, io.BytesIO(data))
    value = raw.getvalue()
    with tarfile.open(fileobj=io.BytesIO(value)) as stream:
        tree = workflow.archive_tree(stream)
    return value, tree


def test_workflow_routing_preserves56_two_components_and57_three_after_exact_inventory():
    raw56, tree56 = _archive("0056")
    old = workflow.profile(
        raw56, commit="a" * 40, tree=tree56, archive_sha256=contract.digest(raw56)
    )
    assert old["postgres_components"] == [0, 1] and old["validation_admission"] is False
    root = Path(__file__).resolve().parents[2]
    migrations = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in historical_migration_paths(root, target="0057")
    }
    raw57, tree57 = _archive("0057", migrations)
    current = workflow.profile(
        raw57, commit="a" * 40, tree=tree57, archive_sha256=contract.digest(raw57)
    )
    assert (
        current["postgres_components"] == [0, 1, 2] and current["source_head"] == "0057"
    )
    assert current["validation_admission"] is False


@pytest.mark.parametrize(
    "change", ["archive", "tree", "commit", "unknown58", "invented57"]
)
def test_workflow_routing_is_not_an_archive_or_migration_bypass(change):
    raw, tree = _archive(
        "0056"
        if change not in {"unknown58", "invented57"}
        else "0058"
        if change == "unknown58"
        else "0057"
    )
    args = {"commit": "a" * 40, "tree": tree, "archive_sha256": contract.digest(raw)}
    if change in {"archive", "tree", "commit"}:
        args[
            {"archive": "archive_sha256", "tree": "tree", "commit": "commit"}[change]
        ] = "0" * (64 if change == "archive" else 40)
    with pytest.raises(ValueError):
        workflow.profile(raw, **args)
