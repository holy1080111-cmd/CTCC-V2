"""Declared synthetic codec/routing only; no Docker, SQL or native admission."""

import copy
import io
import tarfile
from pathlib import Path

import pytest

from scripts import current0056_fitted_catalog_measurement_v1 as fitted
from scripts import current0057_hermetic_contract as previous
from scripts import current0058_full_hermetic_contract_v1 as full
from scripts import current0058_hermetic_contract_v1 as contract
from scripts import hermetic_workflow_profile_v1 as workflow
from scripts import historical0056_on_current0058_observation_v1 as historical56
from scripts import historical0057_on_current0058_observation_v1 as historical57
from scripts import versioned_hermetic_engine_v1 as engine
from scripts import versioned_hermetic_union_v1 as union
from tests.historical_source_prefix_v1 import historical_migration_paths

ROOT = Path(__file__).resolve().parents[2]


def _files():
    paths = {
        *historical_migration_paths(ROOT, target="0058"),
        *(
            ROOT / name
            for name in (
                *fitted._REQUIRED_FILES,
                contract.RUNNER_PATH,
                "scripts/current0058_profile_measurement_v1.py",
                "scripts/current0057_g12_measurement_v1.py",
                historical56._PRODUCER,
                historical57._PRODUCER,
            )
        ),
    }
    return {
        path.relative_to(ROOT).as_posix(): {
            "sha256": contract.digest(path.read_bytes()),
            "size": path.stat().st_size,
        }
        for path in paths
    }


def _identity(files):
    return {
        "runner_contract": contract.RUNNER_CONTRACT,
        "source_contract_head": "0058",
        "validation_scope": contract.SCOPE,
        "commit": "a" * 40,
        "tree": "b" * 40,
        "archive_sha256": "c" * 64,
        "source_files_sha256": contract.digest(contract.canonical(files)),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "runner_sha256": files[contract.RUNNER_PATH]["sha256"],
        "build_context": "admitted_archive_bytes_v1",
        "run_id": "123456abcdef",
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
    }


def test_actual58_source_inventory_is_distinct_from_strict57_and_every_historical_prefix():
    files = _files()
    assert contract.migration_digest(files) == contract.pins.MIGRATION_INVENTORY_SHA256
    with pytest.raises(ValueError):
        previous.migration_digest(files)
    identity = _identity(files)
    source = contract.source_identity(identity, files, contract.canonical(files))
    old56 = fitted.source_identity(identity, files, contract.canonical(files))
    old57 = historical57.historical_source_identity(
        identity, files, contract.canonical(files)
    )
    assert (
        source["source_head"] == old56["source_head"] == old57["source_head"] == "0058"
    )
    assert old56["database_head"] == "0056" and old57["database_head"] == "0057"
    assert (
        old56["historical_prefix_sha256"]
        != old57["historical_prefix_sha256"]
        != source["migration_inventory_sha256"]
    )
    assert (
        source["producer_sha256"]
        == files["scripts/current0058_profile_measurement_v1.py"]["sha256"]
    )


@pytest.mark.parametrize(
    "change",
    [
        "head",
        "runner",
        "credentials",
        "env",
        "order",
        "boolean_order",
        "trading",
        "manifest",
        "map",
        "leaf",
        "missing",
        "extra",
    ],
)
def test_source58_identity_cannot_be_relabelled_or_hidden_by_a_new_hash(change):
    files = _files()
    identity = _identity(files)
    if change in {
        "head",
        "runner",
        "credentials",
        "env",
        "order",
        "boolean_order",
        "trading",
        "manifest",
        "map",
    }:
        key, value = {
            "head": ("source_contract_head", "0057"),
            "runner": ("runner_contract", previous.FULL_RUNNER_CONTRACT),
            "credentials": ("host_credentials", True),
            "env": ("host_env", True),
            "order": ("external_order_writes", 1),
            "boolean_order": ("external_order_writes", False),
            "trading": ("trading_acceptance", True),
            "manifest": ("manifest_sha256", "0" * 64),
            "map": ("source_files_sha256", "0" * 64),
        }[change]
        identity[key] = value
    else:
        if change == "leaf":
            files[contract.pins.MIGRATION_PATH]["sha256"] = "0" * 64
        elif change == "missing":
            del files[contract.pins.MIGRATION_PATH]
        else:
            files["migrations/versions/0059_synthetic.py"] = {"sha256": "0" * 64}
        identity["source_files_sha256"] = contract.digest(contract.canonical(files))
    with pytest.raises(ValueError):
        contract.source_identity(identity, files, contract.canonical(files))


def test_source58_selection_executes_every_old_and_new_required_case_exactly_once():
    nodes = [
        module.replace(".", "/") + ".py::" + name
        for module, name in full.REQUIRED_CASES
    ]
    for index in range(8):
        suffix = next(
            n
            for n in range(1000)
            if full.collection.previous.module_shard(
                f"tests/unit/test_synthetic58_{n}.py"
            )
            == index
        )
        nodes.append(f"tests/unit/test_synthetic58_{suffix}.py::test_join")
    owned = []
    for mode, index, _ in union.specs_for(full, "a" * 40):
        selected = [
            node for node in nodes if full.collection.selected_for(mode, index, node)
        ]
        assert selected and not set(selected) & set(owned)
        owned.extend(selected)
        raw = contract.canonical(
            full.collection._record(mode, index, nodes, selected, selected, 0)
        )
        assert (
            full.collection.verify_collection(
                raw, mode=mode, index=index, report={"tests": len(selected)}
            )["selected_nodeids"]
            == selected
        )
    assert len(owned) == len(nodes) == len(set(nodes)) and set(owned) == set(nodes)
    assert set(previous.CASES) <= set(full.REQUIRED_CASES)
    assert set(full.base.REQUIRED_CASES) < set(full.REQUIRED_CASES)
    assert len(full.live_cases.REQUIRED_CASES) == 28
    assert len(full.live_cases.REQUIRED_FINANCIAL_CASES) == 11
    assert set(full.live_cases.REQUIRED_FINANCIAL_CASES) <= set(full.REQUIRED_CASES)
    assert len(full.BINDING_CASES) == 13
    assert len(full.ORIGINAL_OCO_REPORT_CASES) == 3
    assert len(full.LIVE_OPERATOR_CASES) == 10
    assert len(full.PHYSICAL_LIVE_CASES) == 3
    assert len(full.LIVE_LIFECYCLE_CASES) == 16
    assert len(full.LIVE_CONTROLLED_CLOSE_CASES) == 43
    assert set(full.LIVE_CONTROLLED_CLOSE_CASES) <= set(full.REQUIRED_CASES)
    assert set(full.LIVE_CONTROLLED_CLOSE_CASES) <= set(full.SUCCESSOR_CASES)
    assert len(full.STOPPED_SETUP_CASES) == 2
    assert set(full.STOPPED_SETUP_CASES) <= set(full.REQUIRED_CASES)
    assert set(full.LIVE_LIFECYCLE_CASES) <= set(full.REQUIRED_CASES)
    assert set(full.LIVE_OPERATOR_CASES) <= set(full.REQUIRED_CASES)
    assert set(full.ORIGINAL_OCO_REPORT_CASES) <= set(full.REQUIRED_CASES)
    assert all(
        full.collection.selected_for(
            "postgres", 2, module.replace(".", "/") + ".py::" + name
        )
        for module, name in full.SUCCESSOR_CASES
    )
    assert engine.contract_for(engine.HermeticProfileV1.CURRENT0058) is full
    command = engine.test_command(full, "postgres", 2)
    assert (
        "scripts.current0058_collection_v1" in command
        and "--ctcc-current58-index=2" in command
    )
    with pytest.raises(ValueError):
        engine.contract_for("0058")


def test_topology_uses_actual58_inventory_and_old57_prefix_stays_distinct():
    for target in ("0057", "0058"):
        raw = io.BytesIO()
        with tarfile.open(
            fileobj=raw,
            mode="w",
            format=tarfile.PAX_FORMAT,
            pax_headers={"comment": "a" * 40},
        ) as stream:
            for path in historical_migration_paths(ROOT, target=target):
                data = path.read_bytes()
                info = tarfile.TarInfo(path.relative_to(ROOT).as_posix())
                info.size = len(data)
                stream.addfile(info, io.BytesIO(data))
        archive = raw.getvalue()
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            tree = workflow.archive_tree(stream)
        result = workflow.profile(
            archive, commit="a" * 40, tree=tree, archive_sha256=contract.digest(archive)
        )
        assert result["source_head"] == target
        assert result["postgres_components"] == list(
            range(5 if target == "0058" else 3)
        )
        assert result["validation_admission"] is False


def test_five_pg_owners_preserve_every_whole_module_and_historical_producer():
    from scripts import verify_linux_shard as legacy
    from scripts import verify_windows_regression as windows

    owners = [full.collection.postgres_paths(index) for index in range(5)]
    assert all(owners)
    assert set.union(*(set(paths) for paths in owners)) == (
        legacy.POSTGRES_TEST_PATHS
        | full.collection.SUCCESSOR_PATHS
        | full.collection.OWNER_PATHS
        | full.collection.PAPER58_REFERENCE_PATHS
    )
    for index, paths in enumerate(owners):
        assert not paths.intersection(set().union(*owners[:index]))
    assert legacy.POSTGRES_ANCHOR_PATHS <= owners[0]
    assert full.collection.HISTORICAL_PRODUCER_PATHS <= owners[1]
    assert owners[2] == full.collection.SUCCESSOR_PATHS
    assert owners[3] - full.collection.OWNER_PATHS <= legacy.postgres_paths(0)
    assert full.collection.OWNER_PATHS <= owners[3]
    assert owners[4] <= legacy.postgres_paths(1)
    assert {Path(path).name for path in owners[2]} <= set(windows.POSTGRES_INTEGRATION)
    assert {
        Path(path).name for path in full.collection.RETAINED0017_OWNER_PATHS
    } <= set(windows.POSTGRES_INTEGRATION)


@pytest.mark.parametrize("index", [3, 4])
def test_extra_pg_workers_keep_actual_names_and_no_borrowed_receipts(index):
    assert not full.component_is_anchor("postgres", index)
    assert not full.requires_historical("postgres", index)
    assert not full.requires_g12("postgres", index)
    stages = full.component_stage_names("postgres", index)
    assert stages[-1] == "pytest-postgres-" + str(index)
    assert "pytest-postgres-2" not in stages
    assert "migration-drift" in stages and "migration-upgrade" in stages
    cleanup = full.cleanup_inventory("postgres", index, "ctcc-test-")
    assert ("container", "ctcc-test-pytest-postgres-" + str(index)) in cleanup
    assert ("container", "ctcc-test-pytest-postgres-2") not in cleanup
    assert not set(full.HISTORICAL_REPORTS).intersection(
        full.component_artifacts("postgres", index)
    )
    command = engine.test_command(full, "postgres", index)
    assert "--ctcc-current58-index=" + str(index) in command


@pytest.mark.parametrize("index", [-1, 5, True, "3"])
def test_five_pg_topology_refuses_invalid_or_coerced_index(index):
    with pytest.raises(ValueError):
        full.collection.postgres_paths(index)


def test_both_real_migration_heads_are_required_in_source58_readback():
    raw = {
        "schema": "ctcc.migration_identity.v1",
        "status": "PASS",
        "source_head": "0058",
        "database_head": "0058",
        "migration_sha256": contract.pins.MIGRATION_INVENTORY_SHA256,
        "execution_authority": False,
    }
    assert (
        contract.migration_readback(contract.canonical(raw), raw["migration_sha256"])
        == raw
    )
    for key in ("source_head", "database_head"):
        changed = copy.deepcopy(raw)
        changed[key] = "0057"
        with pytest.raises(ValueError):
            contract.migration_readback(
                contract.canonical(changed), raw["migration_sha256"]
            )
