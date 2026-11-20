"""Full actual-source58 hermetic contract, with physical56/57 scopes explicit."""

from __future__ import annotations

import sys

from scripts import current0057_full_hermetic_contract_v1 as base
from scripts import current0057_hermetic_contract as historical57
from scripts import current0058_collection_v1 as collection
from scripts import current0058_hermetic_contract_v1 as g12
from scripts import run_db0058_migration_probe_v1 as live_cases

HEAD, PARENT = g12.HEAD, g12.PARENT
RUNNER_CONTRACT, RUNNER_PATH, SCOPE = g12.RUNNER_CONTRACT, g12.RUNNER_PATH, g12.SCOPE
PROFILE_RESULT_KEY = "actual0058_profiles"
EXTRA_RESULT_KEYS = ("historical0057", "actual0058_g12_custody")
PROFILE_METADATA_KEYS = (
    *base.PROFILE_METADATA_KEYS,
    "native_closure_body_sha256",
    "native_protective_flat_body_sha256",
    "native_protective_indexes_sha256",
    "actual_protective_catalog_sha256",
    "native_submission_body_sha256",
)
PHYSICAL_LIVE_CASES = tuple(
    ("tests.integration.test_native_live_physical_v1_pg", name)
    for name in (
        "test_actual58_physical_negative_stop_commits_and_independently_reads_back",
        "test_actual58_physical_disarm_preserves_stop_without_any_new_authority",
    )
)
BINDING_CASES = tuple(
    ("tests.integration.test_native_submission_binding_0058_pg", f"{name}[{parameter}]")
    for name, parameters in (
        (
            "test_real0058_binding_allows_only_exact_original_oco_postflat_raw_data",
            (
                "ctcc.native_read_page_metadata.v1",
                "ctcc.native_read_raw_part.v1",
                "ctcc.native_read_manifest.v1",
            ),
        ),
        (
            "test_real0058_binding_rejects_forged_postflat_lineage_or_unrelated_data",
            (
                "wrong_witness",
                "authority",
                "wrong_reason",
                "wrong_version",
                "request_hash",
                "intent_hash",
                "body_hash",
                "risk_retained",
                "ownclose_payload",
                "unrelated_payload",
            ),
        ),
    )
    for parameter in parameters
)
ORIGINAL_OCO_CASES = tuple(
    (
        "tests.integration.test_native_original_oco_public_0058_pg",
        f"{name}[{parameter}]",
    )
    for name, parameters in (
        (
            "test_actual0058_public_original_oco_closure_and_new_postflat_journal_preserve_lineage",
            ("_template0", "_template1", "_template2", "_template3"),
        ),
        (
            "test_actual0058_public_changed_or_incomplete_oco_remains_durably_held",
            ("changed_protection", "partial_fill", "ambiguous_child"),
        ),
    )
    for parameter in parameters
)
SUCCESSOR_CASES = (
    *base.SUCCESSOR_CASES,
    *live_cases.REQUIRED_CASES,
    *g12.CASES,
    g12.CUSTODY_CASE,
    *PHYSICAL_LIVE_CASES,
    *BINDING_CASES,
    *ORIGINAL_OCO_CASES,
    (
        "tests.integration.test_native_protective_hold_schema_v58_pg",
        "test_actual0058_original_oco_predicate_compiles_and_missing_targets_never_resolve",
    ),
)
REQUIRED_CASES = (
    *base.REQUIRED_CASES,
    *live_cases.REQUIRED_CASES,
    *g12.CASES,
    g12.CUSTODY_CASE,
    *PHYSICAL_LIVE_CASES,
    *BINDING_CASES,
    *ORIGINAL_OCO_CASES,
    SUCCESSOR_CASES[-1],
)
HISTORICAL_REPORTS = base.HISTORICAL_REPORTS
ROOT_TRUST_STAGE, ROOT_TRUST_REPORT = base.ROOT_TRUST_STAGE, base.ROOT_TRUST_REPORT
HOST_HELPERS = (
    *base.HOST_HELPERS,
    "current0058_migration_pin.py",
    "current0058_hermetic_contract_v1.py",
    "current0058_full_hermetic_contract_v1.py",
    "current0058_collection_v1.py",
    "current0058_profile_measurement_v1.py",
    "verify_current0058_full_hermetic.py",
    "verify_current0058_linux_union.py",
    "historical0056_on_current0058_observation_v1.py",
    "historical0057_on_current0058_observation_v1.py",
    "historical0056_owner_source_on_0058_v1.py",
    "run_db0058_migration_probe_v1.py",
    "historical0056_owner_reference_dispatch_v1.py",
)
digest, canonical, _need = g12.digest, g12.canonical, g12._need
_SHA, _GIT = g12._HEX, g12._GIT
migration_digest, migration_readback = g12.migration_digest, g12.migration_readback
previous = base.previous
component_report_paths = collection.report_paths
component_is_anchor, requires_historical, requires_g12 = (
    base.component_is_anchor,
    base.requires_historical,
    base.requires_g12,
)
component_test_stage, component_stage_names = (
    base.component_test_stage,
    base.component_stage_names,
)
cleanup_inventory, _read, _read_archive = (
    base.cleanup_inventory,
    base._read,
    base._read_archive,
)


def require_host_code(files):
    base._require_host_code(files, HOST_HELPERS, need=_need)


def required_cases(mode, index):
    collection._mode(mode, index)
    return (
        REQUIRED_CASES
        if mode == "full"
        else ()
        if mode == "shard"
        else tuple(
            case
            for case in REQUIRED_CASES
            if case[0].replace(".", "/") + ".py" in collection.postgres_paths(index)
        )
    )


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
        names.update((*historical57.REPORTS, *g12.REPORTS))
        names.add(g12.CUSTODY_REPORT)
    if mode == "postgres":
        names.add("test-results/postgres-progress.log")
    return names


def historical_observation(root, identity, files):
    from scripts.historical0056_on_current0058_observation_v1 import (
        verify_historical0056_components,
    )

    return verify_historical0056_components(root, identity, files)


def current_observations(root, source):
    from scripts.current0058_profile_measurement_v1 import verify_measurement

    return [
        verify_measurement(_read(root, path, 32_768), source=source, case=case[1])
        for path, case in zip(g12.REPORTS, g12.CASES, strict=True)
    ]


def extra_observations(root, identity, files):
    from scripts.current0058_profile_measurement_v1 import verify_custody_measurement
    from scripts.historical0057_on_current0058_observation_v1 import (
        verify_historical0057_components,
    )

    index = (
        identity.get("postgres_component_index")
        if identity.get("suite") == "postgres"
        else identity.get("shard_index")
    )
    return {
        "historical0057": verify_historical0057_components(root, identity, files)
        if requires_g12(identity["suite"], index)
        else None,
        "actual0058_g12_custody": verify_custody_measurement(
            _read(root, g12.CUSTODY_REPORT, 32_768),
            source=g12.source_identity(identity, files, canonical(files)),
        )
        if requires_g12(identity["suite"], index)
        else None,
    }


def union_extras(verified, mode, index):
    historical = verified["historical0057"]
    custody = verified["actual0058_g12_custody"]
    if not requires_g12(mode, index):
        _need(historical is None and custody is None)
        return {}
    _need(
        type(historical) is dict
        and historical.get("source_head") == HEAD
        and historical.get("database_head") == "0057"
        and historical.get("startup_admission") is False
        and historical.get("execution_authority") is False
        and historical.get("admission") == "DENY"
        and type(historical.get("source")) is dict
        and historical["source"].get("source_head") == HEAD
        and historical["source"].get("database_head") == "0057"
    )
    _need(
        type(custody) is dict
        and custody.get("database_head") == HEAD
        and type(custody.get("source")) is dict
        and custody["source"].get("source_head") == HEAD
        and custody.get("execution_authority") is False
        and custody.get("trading_acceptance") is False
    )
    return {"historical0057": historical, "actual0058_g12_custody": custody}


def measurement_environment(mode, index):
    from scripts.current0057_g12_measurement_v1 import ENV as previous_env
    from scripts.current0058_profile_measurement_v1 import ENV

    return (
        (
            "-e",
            previous_env + "=/validation-results",
            "-e",
            ENV + "=/validation-results",
        )
        if requires_g12(mode, index)
        else ()
    )


def verify_component(root, archive, *, commit, tree, manifest_sha256):
    return base._verify_component(
        sys.modules[__name__],
        root,
        archive,
        commit=commit,
        tree=tree,
        manifest_sha256=manifest_sha256,
    )
