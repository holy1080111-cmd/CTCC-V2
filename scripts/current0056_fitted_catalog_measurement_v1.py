"""Test-only, role-redacted one-cluster DB0056 physical catalog measurement.

The raw catalog and PostgreSQL system identifier remain in memory. The retained
receipt is a diagnostic from an exact isolated hermetic test, never a reference
seal or production startup authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from scripts import current0056_fitted_catalog_pair_v1 as pair
from scripts.current0056_migration_pin import (
    MIGRATION_INVENTORY_COUNT,
    MIGRATION_INVENTORY_SHA256,
    MIGRATION_PATH,
    MIGRATION_SHA256,
)
from scripts.evidence_json import read_evidence_json

SCHEMA = "ctcc.current0056.fitted_catalog_measurement.redacted.v1"
FILE = "current0056-fitted-catalog-single-redacted.json"
CASE = (
    "tests.integration.test_gate3_fitted_candidate_custody_pg",
    "test_actual0056_two_fresh_fitted_catalogs_and_drift_refusal",
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_RUN = re.compile(r"[0-9a-f]{12}\Z")
_IDENTITY = Path("/ctcc-run-identity.json")
_SOURCE_FILES = Path("/ctcc-source-files.json")
_REQUIRED_FILES = (
    "MANIFEST.sha256",
    "pyproject.toml",
    "requirements/validation-linux-py312.lock",
    "requirements/validation-linux-py312.json",
    MIGRATION_PATH,
    "scripts/current0056_fitted_delta_catalog_v1.py",
    "scripts/current0056_fitted_catalog_pair_v1.py",
    "scripts/current0056_fitted_catalog_measurement_v1.py",
    "tests/integration/test_gate3_fitted_candidate_custody_pg.py",
)


class FittedMeasurementRefusal(ValueError):
    pass


def _need(condition: bool, code: str) -> None:
    if condition is not True:
        raise FittedMeasurementRefusal(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_identity(
    identity: dict, source_files: dict, source_files_raw: bytes
) -> dict:
    """Derive the changing source pins from the runner's admitted archive map."""
    _need(
        type(identity) is dict
        and type(source_files) is dict
        and type(source_files_raw) is bytes
        and 0 < len(source_files_raw) <= 2_000_000
        and _canonical(source_files) == source_files_raw
        and identity.get("runner_contract") == "ctcc.current0056.hermetic.v1"
        and identity.get("source_contract_head") == "0056"
        and identity.get("source_files_sha256") == _sha(source_files_raw)
        and type(identity.get("commit")) is str
        and _GIT.fullmatch(identity["commit"]) is not None
        and type(identity.get("tree")) is str
        and _GIT.fullmatch(identity["tree"]) is not None
        and type(identity.get("archive_sha256")) is str
        and _HEX.fullmatch(identity["archive_sha256"]) is not None
        and type(identity.get("manifest_sha256")) is str
        and _HEX.fullmatch(identity["manifest_sha256"]) is not None
        and type(identity.get("run_id")) is str
        and _RUN.fullmatch(identity["run_id"]) is not None,
        "fitted_measurement_runner_identity",
    )
    _need(
        all(
            type(source_files.get(path)) is dict
            and type(source_files[path].get("sha256")) is str
            and _HEX.fullmatch(source_files[path]["sha256"]) is not None
            for path in _REQUIRED_FILES
        )
        and source_files["MANIFEST.sha256"]["sha256"] == identity["manifest_sha256"]
        and source_files[MIGRATION_PATH]["sha256"] == MIGRATION_SHA256,
        "fitted_measurement_archive_inventory",
    )
    migration = {
        path: row["sha256"]
        for path, row in source_files.items()
        if path.startswith("migrations/") and path.endswith(".py")
    }
    _need(
        len(migration) == MIGRATION_INVENTORY_COUNT
        and _sha(_canonical(migration)) == MIGRATION_INVENTORY_SHA256,
        "fitted_measurement_migration_inventory",
    )
    return {
        "commit": identity["commit"],
        "tree": identity["tree"],
        "archive_sha256": identity["archive_sha256"],
        "source_files_sha256": identity["source_files_sha256"],
        "manifest_sha256": identity["manifest_sha256"],
        "pyproject_sha256": source_files["pyproject.toml"]["sha256"],
        "validation_linux_lock_sha256": source_files[
            "requirements/validation-linux-py312.lock"
        ]["sha256"],
        "validation_linux_lock_record_sha256": source_files[
            "requirements/validation-linux-py312.json"
        ]["sha256"],
        "migration_head": "0056",
        "migration_inventory_sha256": MIGRATION_INVENTORY_SHA256,
        "migration_leaf_sha256": MIGRATION_SHA256,
        "catalog_inspector_sha256": source_files[
            "scripts/current0056_fitted_delta_catalog_v1.py"
        ]["sha256"],
        "measurement_producer_sha256": source_files[
            "scripts/current0056_fitted_catalog_measurement_v1.py"
        ]["sha256"],
    }


def build_measurement(
    *,
    identity: dict,
    source_files: dict,
    source_files_raw: bytes,
    catalog: dict,
    owner: str,
    demo_roles: tuple[str, str, str],
    fitted_roles: dict[str, str],
    cluster_system_identifier: str,
    database_name: str,
    database_oid: int,
) -> bytes:
    source = source_identity(identity, source_files, source_files_raw)
    _need(
        type(cluster_system_identifier) is str
        and bool(re.fullmatch(r"[1-9][0-9]{0,19}", cluster_system_identifier))
        and type(database_name) is str
        and bool(re.fullmatch(r"ctcc_g3fcat_[a-f0-9]{12}", database_name))
        and type(database_oid) is int
        and database_oid > 0,
        "fitted_measurement_database_identity",
    )
    try:
        normalized_catalog = pair.normalized_catalog_document(
            catalog, owner=owner, demo_roles=demo_roles, fitted_roles=fitted_roles
        )
        normalized_sha = pair.verify_normalized_catalog_document(normalized_catalog)
    except (pair.FittedPairRefusal, ValueError, KeyError, TypeError):
        raise FittedMeasurementRefusal("fitted_measurement_catalog_invalid") from None
    _need(catalog["server_build"] == pair.PG_BUILD, "fitted_measurement_pg_runtime")
    receipt = {
        "schema": SCHEMA,
        "case": list(CASE),
        "scope": "one_disposable_linux_pg17_tls_cluster",
        "source": source,
        "pg_image": pair.PG_IMAGE,
        "pg_server_build": pair.PG_BUILD,
        "run_id": identity["run_id"],
        "cluster_system_identifier_sha256": _sha(
            cluster_system_identifier.encode("ascii")
        ),
        "database_identity_sha256": _sha(
            _canonical({"name": database_name, "oid": database_oid})
        ),
        "raw_catalog_sha256": _sha(_canonical(catalog)),
        "normalized_catalog": normalized_catalog,
        "normalized_catalog_sha256": normalized_sha,
        "role_names_retained": False,
        "database_oid_retained": False,
        "raw_catalog_retained": False,
        "actual_measurement_independently_attested": False,
        "native_reference_sealed": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    return _canonical(receipt)


def verify_measurement(raw: bytes, *, identity: dict, source_files: dict) -> dict:
    """Check retained redacted bytes against the independently admitted map."""
    _need(type(raw) is bytes and 0 < len(raw) <= 1_200_000, "fitted_measurement_size")
    try:
        receipt = read_evidence_json(raw)
        _need(
            type(receipt) is dict and _canonical(receipt) == raw,
            "fitted_measurement_encoding",
        )
        expected_source = source_identity(
            identity, source_files, _canonical(source_files)
        )
        _need(
            set(receipt)
            == {
                "schema",
                "case",
                "scope",
                "source",
                "pg_image",
                "pg_server_build",
                "run_id",
                "cluster_system_identifier_sha256",
                "database_identity_sha256",
                "raw_catalog_sha256",
                "normalized_catalog",
                "normalized_catalog_sha256",
                "role_names_retained",
                "database_oid_retained",
                "raw_catalog_retained",
                "actual_measurement_independently_attested",
                "native_reference_sealed",
                "startup_admission",
                "execution_authority",
                "admission",
            }
            and receipt["schema"] == SCHEMA
            and receipt["case"] == list(CASE)
            and receipt["scope"] == "one_disposable_linux_pg17_tls_cluster"
            and receipt["source"] == expected_source
            and receipt["pg_image"] == pair.PG_IMAGE
            and receipt["pg_server_build"] == pair.PG_BUILD
            and receipt["run_id"] == identity["run_id"]
            and type(receipt["normalized_catalog"]) is dict
            and pair.verify_normalized_catalog_document(receipt["normalized_catalog"])
            == receipt["normalized_catalog_sha256"]
            and all(
                type(receipt[key]) is str and _HEX.fullmatch(receipt[key]) is not None
                for key in (
                    "cluster_system_identifier_sha256",
                    "database_identity_sha256",
                    "raw_catalog_sha256",
                    "normalized_catalog_sha256",
                )
            )
            and receipt["role_names_retained"] is False
            and receipt["database_oid_retained"] is False
            and receipt["raw_catalog_retained"] is False
            and receipt["actual_measurement_independently_attested"] is False
            and receipt["native_reference_sealed"] is False
            and receipt["startup_admission"] is False
            and receipt["execution_authority"] is False
            and receipt["admission"] == "DENY",
            "fitted_measurement_identity",
        )
        return receipt
    except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        if type(exc) is FittedMeasurementRefusal:
            raise
        raise FittedMeasurementRefusal("fitted_measurement_invalid") from None


def compare_retained_measurements(
    first_raw: bytes,
    second_raw: bytes,
    *,
    first_identity: dict,
    first_source_files: dict,
    first_expected_sha256: str,
    second_identity: dict,
    second_source_files: dict,
    second_expected_sha256: str,
) -> dict:
    """Require two separately retained external pins; still no authority."""
    _need(
        type(first_expected_sha256) is str
        and _HEX.fullmatch(first_expected_sha256) is not None
        and type(second_expected_sha256) is str
        and _HEX.fullmatch(second_expected_sha256) is not None
        and _sha(first_raw) == first_expected_sha256
        and _sha(second_raw) == second_expected_sha256,
        "fitted_measurement_external_pin",
    )
    first = verify_measurement(
        first_raw, identity=first_identity, source_files=first_source_files
    )
    second = verify_measurement(
        second_raw, identity=second_identity, source_files=second_source_files
    )
    _need(
        first["source"] == second["source"]
        and first["run_id"] != second["run_id"]
        and first["cluster_system_identifier_sha256"]
        != second["cluster_system_identifier_sha256"]
        and first["normalized_catalog_sha256"] == second["normalized_catalog_sha256"],
        "fitted_measurement_pair_mismatch",
    )
    return {
        "schema": "ctcc.current0056.fitted_catalog_pair.redacted.inert.v1",
        "source": first["source"],
        "pg_image": pair.PG_IMAGE,
        "pg_server_build": pair.PG_BUILD,
        "normalized_catalog_sha256": first["normalized_catalog_sha256"],
        "measurement_receipt_sha256": sorted((_sha(first_raw), _sha(second_raw))),
        "actual_measurement_independently_attested": False,
        "native_reference_sealed": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def retain_hermetic_measurement(
    *,
    catalog: dict,
    owner: str,
    demo_roles: tuple[str, str, str],
    fitted_roles: dict[str, str],
    cluster_system_identifier: str,
    database_name: str,
    database_oid: int,
) -> None:
    """Write one no-clobber redacted file in the existing isolated test mount."""
    from app.trade_evidence.storage import _posix_root, _root_path

    _need(sys.platform == "linux", "fitted_measurement_not_linux")
    _need(
        _IDENTITY.is_file() and _SOURCE_FILES.is_file(),
        "fitted_measurement_runner_mount",
    )
    identity_raw = _IDENTITY.read_bytes()
    source_files_raw = _SOURCE_FILES.read_bytes()
    _need(
        0 < len(identity_raw) <= 8_192 and 0 < len(source_files_raw) <= 2_000_000,
        "fitted_measurement_runner_mount",
    )
    identity = read_evidence_json(identity_raw)
    source_files = read_evidence_json(source_files_raw)
    raw = build_measurement(
        identity=identity,
        source_files=source_files,
        source_files_raw=source_files_raw,
        catalog=catalog,
        owner=owner,
        demo_roles=demo_roles,
        fitted_roles=fitted_roles,
        cluster_system_identifier=cluster_system_identifier,
        database_name=database_name,
        database_oid=database_oid,
    )
    verify_measurement(raw, identity=identity, source_files=source_files)
    with _posix_root(_root_path(Path("/validation-results"))) as results:
        results.publish(FILE, raw)
        _need(results.read(FILE, 1_200_000) == raw, "fitted_measurement_readback")
