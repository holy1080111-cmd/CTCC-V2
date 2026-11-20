"""Synthetic receipt tampering controls; no PostgreSQL or trade acceptance."""

import copy
import runpy
from pathlib import Path

import pytest

from scripts import current0056_fitted_catalog_measurement_v1 as fitted
from scripts import current0057_hermetic_contract as current
from scripts import historical0056_on_current0057_observation_v1 as observation

ROOT = Path(__file__).resolve().parents[2]


def _inputs():
    paths = {
        *fitted._REQUIRED_FILES,
        observation._PRODUCER,
        current.FULL_RUNNER_PATH,
        "scripts/current0057_g12_measurement_v1.py",
        *(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "migrations").rglob("*.py")
        ),
    }
    files = {
        name: {"sha256": current.digest((ROOT / name).read_bytes())} for name in paths
    }
    raw = current.canonical(files)
    identity = {
        "runner_contract": current.FULL_RUNNER_CONTRACT,
        "validation_scope": current.FULL_SCOPE,
        "source_contract_head": "0057",
        "commit": "1" * 40,
        "tree": "2" * 40,
        "archive_sha256": "3" * 64,
        "source_files_sha256": current.digest(raw),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "runner_sha256": files[current.FULL_RUNNER_PATH]["sha256"],
        "build_context": "admitted_archive_bytes_v1",
        "host_credentials": False,
        "host_env": False,
        "external_order_writes": 0,
        "trading_acceptance": False,
        "run_id": "123456abcdef",
    }
    return identity, files, raw


def test_source57_and_physical56_remain_distinct_without_log_or_row_projection():
    identity, files, raw = _inputs()
    result = fitted.source_identity(identity, files, raw)
    assert result["source_head"] == result["migration_head"] == "0057"
    assert result["database_head"] == "0056"
    assert (
        result["migration_inventory_sha256"] == current.pins.MIGRATION_INVENTORY_SHA256
    )
    assert result["historical_prefix_sha256"] == fitted.MIGRATION_INVENTORY_SHA256
    assert observation.historical_source_identity(identity, files, raw) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_contract_head", "0056"),
        ("runner_contract", current.RUNNER_CONTRACT),
        ("runner_sha256", "f" * 64),
        ("source_files_sha256", "f" * 64),
        ("run_id", "not-a-run"),
        ("host_credentials", True),
        ("host_env", True),
        ("external_order_writes", True),
        ("external_order_writes", 1),
        ("trading_acceptance", True),
    ],
)
def test_historical_observation_rejects_relabelled_source_and_unsafe_runner(
    field, value
):
    identity, files, raw = _inputs()
    identity[field] = value
    with pytest.raises(ValueError):
        observation.historical_source_identity(identity, files, raw)


@pytest.mark.parametrize(
    "change", ["old_leaf", "new_leaf", "extra_migration", "missing_producer"]
)
def test_changed_migration_prefix_or_missing_producer_is_rejected_even_rehashed(change):
    identity, files, _ = _inputs()
    if change == "old_leaf":
        files[fitted.MIGRATION_PATH]["sha256"] = "f" * 64
    elif change == "new_leaf":
        files[current.pins.MIGRATION_PATH]["sha256"] = "f" * 64
    elif change == "extra_migration":
        files["migrations/versions/extra.py"] = {"sha256": "f" * 64}
    else:
        del files[observation._PRODUCER]
    raw = current.canonical(files)
    identity["source_files_sha256"] = current.digest(raw)
    with pytest.raises(ValueError):
        observation.historical_source_identity(identity, files, raw)


def test_source57_historical_receipt_roundtrip_is_inert_and_refuses_database_relabel():
    identity, files, source_raw = _inputs()
    fixture = runpy.run_path(
        str(ROOT / "tests/unit/mie/test_current0056_fitted_delta_catalog_v1.py")
    )
    catalog = fixture["synthetic_catalog"]()
    catalog["server_build"] = fitted.pair.PG_BUILD
    raw = fitted.build_measurement(
        identity=identity,
        source_files=files,
        source_files_raw=source_raw,
        catalog=catalog,
        **fixture["ROLES"],
        cluster_system_identifier="12345678901234567890",
        database_name="ctcc_g3fcat_123456789abc",
        database_oid=12990,
    )
    result = fitted.verify_measurement(raw, identity=identity, source_files=files)
    assert result["source"]["database_head"] == "0056"
    assert result["execution_authority"] is result["startup_admission"] is False
    assert result["admission"] == "DENY"
    changed = copy.deepcopy(result)
    changed["source"]["database_head"] = "0057"
    with pytest.raises(ValueError):
        fitted.verify_measurement(
            current.canonical(changed), identity=identity, source_files=files
        )
