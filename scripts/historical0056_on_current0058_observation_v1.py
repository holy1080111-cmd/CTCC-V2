"""Exact source58 observations of separately migrated physical DB0056.

These diagnostic receipts retain both identities. They do not project SQL rows
or logs onto another version and never grant startup or trading authority.
"""

from __future__ import annotations

import re

from scripts import current0056_fitted_catalog_measurement_v1 as fitted
from scripts import current0056_postgrant_acl_measurement_v1 as acl
from scripts import current0058_migration_pin as current
from scripts import devval_campaign_physical_catalog_v1 as campaign
from scripts.evidence_json import read_evidence_json

_PRODUCER = "scripts/historical0056_on_current0058_observation_v1.py"
_HEX = re.compile(r"[a-f0-9]{64}\Z")


def _need(condition):
    if condition is not True:
        raise ValueError("source58_historical56_observation_invalid") from None


def historical_source_identity(identity, files, files_raw):
    """Bind actual source58 and the unchanged physical56 migration prefix."""
    from scripts import current0058_hermetic_contract_v1 as source

    source.source_identity(identity, files, files_raw)
    _need(
        identity.get("runner_contract") == source.FULL_RUNNER_CONTRACT
        and identity.get("validation_scope") == source.FULL_SCOPE
        and type(identity.get("run_id")) is str
        and re.fullmatch(r"[a-f0-9]{12}", identity["run_id"]) is not None
    )
    paths = (*fitted._REQUIRED_FILES, _PRODUCER)
    _need(
        all(
            type(files.get(path)) is dict
            and type(files[path].get("sha256")) is str
            and _HEX.fullmatch(files[path]["sha256"]) is not None
            for path in paths
        )
    )
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
        "source_head": current.HEAD,
        "database_head": "0056",
        "pyproject_sha256": files["pyproject.toml"]["sha256"],
        "validation_linux_lock_sha256": files[
            "requirements/validation-linux-py312.lock"
        ]["sha256"],
        "validation_linux_lock_record_sha256": files[
            "requirements/validation-linux-py312.json"
        ]["sha256"],
        "migration_head": current.HEAD,
        "migration_inventory_sha256": current.MIGRATION_INVENTORY_SHA256,
        "migration_leaf_sha256": current.MIGRATION_SHA256,
        "historical_prefix_sha256": fitted.MIGRATION_INVENTORY_SHA256,
        "historical_leaf_sha256": fitted.MIGRATION_SHA256,
        "catalog_inspector_sha256": files[
            "scripts/current0056_fitted_delta_catalog_v1.py"
        ]["sha256"],
        "measurement_producer_sha256": files[
            "scripts/current0056_fitted_catalog_measurement_v1.py"
        ]["sha256"],
        "historical_observation_producer_sha256": files[_PRODUCER]["sha256"],
        "runner_sha256": files[source.FULL_RUNNER_PATH]["sha256"],
    }


def verify_historical0056_components(root, identity, files):
    """Replay retained raw receipts, original catalogs and selected real cases."""
    from scripts import current0056_hermetic_contract as previous
    from scripts import current0058_full_hermetic_contract_v1 as full

    source = historical_source_identity(identity, files, full.canonical(files))
    mode, index = identity.get("suite"), identity.get("postgres_component_index")
    _need(full.requires_historical(mode, index))
    junit, _ = full.component_report_paths(mode, index)
    # Component admission and historical receipts request different case sets.
    # Replay the SAME complete component summary for identity equality, then
    # independently demand every historical case in those original XML bytes.
    _verified_component_tests(
        root / junit,
        identity.get("tests"),
        component_cases=full.required_cases(mode, index),
        historical_cases=tuple(
            dict.fromkeys(
                (
                    campaign.HERMETIC_CATALOG_CASE,
                    fitted.CASE,
                    fitted.INHERITED_CASE,
                    acl.CASE,
                )
            )
        ),
        allow_unrelated_skips=mode == "full",
    )
    _need(
        all(
            files.get(path, {}).get("sha256") == pin
            for path, pin in campaign.SQL_PINS.items()
        )
    )
    paths = full.HISTORICAL_REPORTS
    raw = {path: full._read(root, path) for path in paths}
    result_hashes = read_evidence_json(full._read(root, "results.sha256.json"))
    _need(
        all(
            result_hashes.get(path) == full.digest(value) for path, value in raw.items()
        )
    )
    campaign.verify_hermetic_linux_catalogs(raw[paths[0]])
    observation = previous.verify_fitted_and_inherited_catalogs(root, identity, files)
    _need(observation["source"] == source)
    return {
        "schema": "ctcc.source0058_historical0056.observation.inert.v1",
        "source": source,
        "source_head": current.HEAD,
        "database_head": "0056",
        "reports_sha256": {path: full.digest(value) for path, value in raw.items()},
        "cluster_system_identifier_sha256": observation[
            "cluster_system_identifier_sha256"
        ],
        "account_complete": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def _verified_component_tests(
    path, expected, *, component_cases, historical_cases, allow_unrelated_skips
):
    from scripts import verify_final_hermetic as shared

    checked = shared.verify_pytest_report(
        path,
        required_cases=component_cases,
        allow_unrelated_skips=allow_unrelated_skips,
    )
    _need(checked == expected)
    historical = shared.verify_pytest_report(
        path,
        required_cases=historical_cases,
        allow_unrelated_skips=allow_unrelated_skips,
    )
    # Both demand sets must refer to exactly the same original complete report.
    _need(
        all(
            checked[key] == historical[key]
            for key in ("tests", "failures", "errors", "skipped", "passed", "sha256")
        )
    )
    return checked


def _pair_sources(kwargs):
    sources = []
    for prefix in ("first", "second"):
        files = kwargs[prefix + "_source_files"]
        sources.append(
            historical_source_identity(
                kwargs[prefix + "_identity"], files, fitted._canonical(files)
            )
        )
    _need(sources[0] == sources[1])


def compare_retained_native_reference_evidence(*args, **kwargs):
    _pair_sources(kwargs)
    return fitted.compare_retained_native_reference_evidence(*args, **kwargs)


def compare_retained_measurements(*args, **kwargs):
    _pair_sources(kwargs)
    return acl.compare_retained_measurements(*args, **kwargs)
