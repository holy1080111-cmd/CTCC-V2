"""Full actual-source58 hermetic contract, with physical56/57 scopes explicit."""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import current0057_full_hermetic_contract_v1 as base
from scripts import current0057_hermetic_contract as historical57
from scripts import current0058_collection_v1 as collection
from scripts import current0058_hermetic_contract_v1 as g12
from scripts import retained0017_reference_measurement_v1 as references17
from scripts import run_db0058_migration_probe_v1 as live_cases

HEAD, PARENT = g12.HEAD, g12.PARENT
RUNNER_CONTRACT, RUNNER_PATH, SCOPE = g12.RUNNER_CONTRACT, g12.RUNNER_PATH, g12.SCOPE
PROFILE_RESULT_KEY = "actual0058_profiles"
EXTRA_RESULT_KEYS = (
    "historical0057",
    "actual0058_g12_custody",
    "retained0017_reference_pair",
)
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
        "test_actual58_physical_disarm_proves_exact_one_step_committed_successor_only",
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
OWNER_UPGRADE_CASES = tuple(
    ("tests.integration.test_native_owner_upgrade_0058_pg", name)
    for name in (
        "test_actual0057_owner58_rehearsal_and_commit_preserve_every_row_actor_and44_acl",
        "test_actual0057_owner58_final_ddl_failure_restores_original_head_rows_oids_and_acl",
        "test_actual0057_owner58_unreviewed_live_access_rolls_back_without_granting_or_commit",
        "test_actual0057_shared_snapshot_rejects_user_domain_before_selecting_its_rows",
        "test_actual0057_shared_snapshot_never_calls_active_public_json_overload",
        *(
            "test_actual0057_owner58_null_bitmap_refuses_rehearsal_or_final_null_flip_and_restores_all_state["
            + stage
            + "]"
            for stage in ("1", "2")
        ),
    )
)
OWNER0056_V2_CASES = tuple(
    ("tests.integration.test_native_owner_upgrade_0056_v2_pg", name)
    for name in (
        "test_actual0055_v2_rehearsal_rollback_then_commit_preserves_all_rows_and42_grants",
        *(
            "test_actual0055_v2_rehearsal_or_final_exception_rolls_back_catalog_rows_and_acl["
            + phase
            + "]"
            for phase in ("1", "2")
        ),
        *(
            "test_actual0055_v2_null_bitmap_detects_json_only_zero_delta_and_restores_original["
            + phase
            + "]"
            for phase in ("1", "2")
        ),
        "test_actual0055_v2_original_metadata_drift_is_not_hidden_by_correct_new_fitted_schema",
        "test_actual0055_v2_wrong_reviewed_role_oid_refuses_without_ddl",
        "test_actual0055_v2_commit_response_loss_is_unknown_and_never_reapplies_or_erases_head",
        "test_actual0055_v2_marker_view_refuses_before_its_select_callback",
    )
)
OWNER0056_EXISTING_CLI_CASE = (
    "tests.integration.test_gate3_fitted_candidate_custody_pg",
    "test_actual0056_non_superuser_owner_upgrades_0055_once_with_fresh_readback",
)
OWNER0057_NULL_CASES = tuple(
    (
        "tests.integration.test_native_owner_upgrade_0057_pg",
        "test_actual0056_owner_upgrade_null_bitmap_refuses_changed_audit_value_and_rolls_back["
        + changed
        + "]",
    )
    for changed in ("False", "True")
)
OWNER_STORAGE_CANONICAL_CASES = (
    (
        "tests.integration.test_native_owner_upgrade_0058_pg",
        "test_actual0057_storage_predicate_parses_and_collation_is_an_independent_fence",
    ),
    (
        "tests.integration.test_native_owner_upgrade_0058_pg",
        "test_actual0057_unused_custom_default_am_refuses_before_head_or_upgrade",
    ),
    (
        "tests.integration.test_native_owner_upgrade_0056_v2_pg",
        "test_actual0055_v2_and_commonroute_custom_marker_am_refuse_before_head_reads",
    ),
    (
        "tests.integration.test_native_owner_upgrade_0058_pg",
        "test_actual0057_shared_snapshot_rejects_custom_table_am_before_row_or_storage_read",
    ),
    *(
        (
            "tests.integration.test_native_owner_upgrade_0058_pg",
            "test_actual0057_native_btree_closure_refuses_before_any_index_callback_or_app_read["
            + changed
            + "]",
        )
        for changed in ("index_am", "operator_class", "predicate")
    ),
)
OWNER_STORAGE_CASES = (
    *OWNER_STORAGE_CANONICAL_CASES,
    (
        "tests.integration.test_retained0017_owner_plan_v1_pg",
        "test_actual0017_custom_marker_am_refuses_before_head_row_or_storage_read",
    ),
)
CANONICAL_OWNER_CASES = (
    *OWNER_STORAGE_CANONICAL_CASES,
    *OWNER0056_V2_CASES,
    *(
        case
        for case in base.SUCCESSOR_CASES
        if case[0].replace(".", "/") + ".py" in collection.CANONICAL_OWNER_PATHS
    ),
    *OWNER0057_NULL_CASES,
    *OWNER_UPGRADE_CASES,
)
RETAINED0017_OWNER_CASES = tuple(
    ("tests.integration.test_retained0017_owner_plan_v1_pg", name)
    for name in (
        "test_actual0017_custom_marker_am_refuses_before_head_row_or_storage_read",
        "test_actual0017_two_readonly_snapshots_preserve_all_rows_oids_sequences_and_unknown_intent",
        "test_actual0017_outer_cli_replays_admitted_source_private_pins_tls_and_receipt",
        "test_actual0017_row_hash_encoding_distinguishes_sql_null_from_json_null",
        *(
            "test_actual0017_readonly_inventory_refuses_changed_actual_head["
            + head
            + "]"
            for head in ("0016", "0018", "0058")
        ),
        "test_actual0017_readonly_inventory_refuses_missing_original_risk_column",
        "test_actual0017_head_view_is_refused_before_its_select_function_runs",
        *(
            "test_actual0017_readonly_inventory_refuses_unobserved_storage_scope["
            + outside
            + "]"
            for outside in ("user_schema", "large_object")
        ),
        *(
            "test_actual0017_independent_reads_refuse_retained_state_or_catalog_drift["
            + drift
            + "]"
            for drift in ("row", "sequence", "function_acl")
        ),
    )
)
ORIGINAL_OCO_REPORT_CASES = tuple(
    ("tests.integration.test_native_original_oco_reporting_0058_pg", name)
    for name in (
        "test_actual0058_original_oco_report_spool_replays_without_own_close_intent",
        "test_actual0058_original_oco_reporting_guard_refuses_foreign_forensics",
        "test_actual0058_original_oco_report_policy_refuses_rehashed_changed_analysis",
    )
)
LIVE_OPERATOR_CASES = tuple(
    ("tests.integration.test_native_live_operator_control_v1_pg", name)
    for name in (
        "test_actual_operator_clear_arm_and_separate_readback",
        "test_actual_two_operator_workers_same_revision_allow_only_one_arm",
        "test_actual_extra_operator_ledger_write_grant_is_refused_before_positive_change",
        "test_actual_operator_cannot_write_intents_or_financial_history",
        "test_actual_startup_negative_inhibits_without_source_or_legacy_flat_claim",
        *(
            "test_actual_live4_catalog_drift_never_becomes_operator_readiness["
            + kind
            + "]"
            for kind in ("column", "check", "foreign_key", "index", "schema_create")
        ),
    )
)
LIVE_LIFECYCLE_CASES = tuple(
    ("tests.integration.test_native_live_lifecycle_v1_pg", name)
    for name in (
        "test_actual58_lifecycle_closed_reconciler_profile_hides_key_and_forbids_entry_writes",
        *(
            "test_actual58_lifecycle_bad_signature_scope_or_chronology_never_resolves["
            + mutation
            + "]"
            for mutation in (
                "signature",
                "uid",
                "intent",
                "sequence",
                "previous",
                "future",
                "stale",
                "authority",
            )
        ),
        "test_actual58_lifecycle_uncertain_proof_is_durable_and_keeps_stop_and_hold",
        "test_actual58_lifecycle_terminal_zero_fill_releases_only_authenticated_original_hold",
        "test_actual58_live_recovery_journal_preserves_db_intent_recorded_time",
        *(
            "test_actual58_original_oco_terminal_source_drives_signed_resolution_or_hold["
            + source_case
            + "]"
            for source_case in (
                "complete",
                "changed_after",
                "partial_exit",
                "missing_terminal",
            )
        ),
    )
)
STOPPED_SETUP_CASES = tuple(
    ("tests.integration.test_native_stopped_setup_0058_pg", name)
    for name in (
        "test_actual0058_stopped_genesis_and_owner_legacy_seed_replay_same_real_incarnation",
        "test_actual0058_wrong_owner_incarnation_refuses_without_legacy_write",
    )
)
LIVE_CONTROLLED_CLOSE_CASES = tuple(
    ("tests.integration.test_native_live_controlled_close_v1_pg", name)
    for name in (
        "test_actual58_controlled_restart_optional_lookup_replays_original_hmac_records",
        *(
            "test_actual58_controlled_restart_complete_fresh_source_resolves_or_retains_hold["
            + source_case
            + "]"
            for source_case in (
                "complete",
                "client_id_reuse",
                "partial_close",
                "protection_still_active",
                "zero_then_oco",
                "zero_partial",
                "zero_reuse",
                "zero_changed_oco",
            )
        ),
        "test_actual58_close_closed_profile_and_immutable_two_action_schema",
        *(
            "test_actual58_close_rejects_unbound_or_changed_original_geometry["
            + mutation
            + "]"
            for mutation in (
                "wrong_domain",
                "wrong_uid",
                "wrong_revision",
                "null_deadline",
                "string_consent_clock",
                "missing_consent_clock",
                "fraction_revision",
                "expired",
                "wrong_algo",
                "wrong_size",
                "changed_stop",
                "market",
                "hedged",
                "not_reduce_only",
                "wrong_original_process",
            )
        ),
        "test_actual58_close_two_workers_one_parent_have_at_most_one_intent",
        "test_actual58_close_claim_burns_once_without_releasing_risk_hold",
        *(
            "test_actual58_close_original_protection_cancel_is_narrow_and_revocable["
            + mutation
            + "]"
            for mutation in (
                "before_close_claim",
                "not_filled",
                "partial_fill",
                "position",
                "ordinary_pending",
                "no_algo",
                "other_algo",
                "missing_fills",
                "changed_revision",
                "new_stop",
            )
        ),
        "test_actual58_close_cancel_has_separate_one_shot_request_and_terminal_proof",
        *(
            "test_actual58_close_prepared_consent_cannot_adopt_later_control["
            + change
            + "]"
            for change in ("revision", "stop")
        ),
        *(
            "test_actual58_close_catalog_and_effective_grants_drift_fail_closed["
            + drift
            + "]"
            for drift in ("column", "write_acl")
        ),
        "test_actual58_close_unresolved_legacy_intent_is_a_global_negative_blocker",
    )
)
SUCCESSOR_CASES = (
    *(
        case
        for case in base.SUCCESSOR_CASES
        if case[0].replace(".", "/") + ".py" not in collection.CANONICAL_OWNER_PATHS
    ),
    *live_cases.REQUIRED_CASES,
    *g12.CASES,
    g12.CUSTODY_CASE,
    *PHYSICAL_LIVE_CASES,
    *BINDING_CASES,
    *ORIGINAL_OCO_CASES,
    *ORIGINAL_OCO_REPORT_CASES,
    *LIVE_OPERATOR_CASES,
    *LIVE_LIFECYCLE_CASES,
    *STOPPED_SETUP_CASES,
    *LIVE_CONTROLLED_CLOSE_CASES,
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
    *OWNER_UPGRADE_CASES,
    *OWNER0056_V2_CASES,
    *OWNER_STORAGE_CANONICAL_CASES,
    *OWNER0057_NULL_CASES,
    OWNER0056_EXISTING_CLI_CASE,
    *ORIGINAL_OCO_REPORT_CASES,
    *LIVE_OPERATOR_CASES,
    *LIVE_LIFECYCLE_CASES,
    *STOPPED_SETUP_CASES,
    *LIVE_CONTROLLED_CLOSE_CASES,
    *RETAINED0017_OWNER_CASES,
    *references17.CASES,
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
    "retained0017_reference_measurement_v1.py",
    "retained0017_catalog_baseline_v1.py",
    "retained0017_source_contract_v1.py",
    "retained0017_catalog_sql_types_v1.py",
    "native_owner_heap_am_v1.py",
    "native_owner_index_catalog_v1.py",
    "native_owner_pg17_btree_facts_v1.json",
)
digest, canonical, _need = g12.digest, g12.canonical, g12._need
_SHA, _GIT = g12._HEX, g12._GIT
migration_digest, migration_readback = g12.migration_digest, g12.migration_readback
previous = base.previous
component_report_paths = collection.report_paths
_read, _read_archive = base._read, base._read_archive


def component_is_anchor(mode, index):
    collection._mode(mode, index)
    return mode == "full" or mode == "postgres" and index == 0


def requires_historical(mode, index):
    collection._mode(mode, index)
    return mode == "full" or mode == "postgres" and index == 1


def requires_g12(mode, index):
    collection._mode(mode, index)
    return mode == "full" or mode == "postgres" and index == 2


def requires_reference_pair17(mode, index):
    collection._mode(mode, index)
    return mode == "full" or mode == "postgres" and index == 3


def component_test_stage(mode, index):
    collection._mode(mode, index)
    return "pytest-" + mode + ("" if index is None else "-" + str(index))


def component_stage_names(mode, index):
    collection._mode(mode, index)
    # New historical case-only workers use the unchanged non-anchor stages,
    # named with their actual index. No old profile is relabelled or admitted.
    template_index = 2 if mode == "postgres" and index >= 3 else index
    old_stage = base.component_test_stage(mode, template_index)
    return tuple(
        component_test_stage(mode, index) if name == old_stage else name
        for name in base.component_stage_names(mode, template_index)
    )


def cleanup_inventory(mode, index, prefix):
    collection._mode(mode, index)
    template_index = 2 if mode == "postgres" and index >= 3 else index
    old_name = prefix + base.component_test_stage(mode, template_index)
    return {
        (kind, prefix + component_test_stage(mode, index) if name == old_name else name)
        for kind, name in base.cleanup_inventory(mode, template_index, prefix)
    }


def require_host_code(files):
    base._require_host_code(files, HOST_HELPERS, need=_need)
    _require_loaded_app_sources(files)


def _require_loaded_app_sources(files):
    """Bind late-imported pure SPEC dependencies to the same exact archive."""
    root = Path(__file__).resolve().parents[1]
    for name, module in tuple(sys.modules.items()):
        if name == "app" or name.startswith("app."):
            filename = getattr(module, "__file__", None)
            _need(type(filename) is str)
            path = Path(filename)
            _need(
                path.is_absolute()
                and path.suffix == ".py"
                and path.is_relative_to(root / "app")
                and not path.is_symlink()
                and path.is_file()
            )
            relative = path.relative_to(root).as_posix()
            _need(digest(path.read_bytes()) == files.get(relative, {}).get("sha256"))


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
    if requires_reference_pair17(mode, index):
        names.update(references17.REPORTS)
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
        "retained0017_reference_pair": references17.verify_reference_pair(
            root,
            source=references17.source_identity(identity, files, canonical(files)),
        )
        if requires_reference_pair17(identity["suite"], index)
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
    pair = verified["retained0017_reference_pair"]
    extras = {}
    if requires_reference_pair17(mode, index):
        _need(
            type(pair) is dict
            and type(pair.get("source")) is dict
            and pair["source"].get("source_head") == HEAD
        )
        _need(
            references17.verify_pair_values(pair["references"], source=pair["source"])
            == pair
        )
        extras["retained0017_reference_pair"] = pair
    else:
        _need(pair is None)
    if not requires_g12(mode, index):
        _need(historical is None and custody is None)
        return extras
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
    return extras | {"historical0057": historical, "actual0058_g12_custody": custody}


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
    ) + (
        ("-e", references17.ENV + "=/validation-results")
        if requires_reference_pair17(mode, index)
        else ()
    )


def verify_component(root, archive, *, commit, tree, manifest_sha256):
    verified = base._verify_component(
        sys.modules[__name__],
        root,
        archive,
        commit=commit,
        tree=tree,
        manifest_sha256=manifest_sha256,
    )
    # Measurement replay may import source-owned pure SQL SPEC modules late.
    # Check their actual host bytes as well; never adopt a different checkout.
    mapping = _read(root, "source-files.json")
    _need(digest(mapping) == verified["identity"]["source_files_sha256"])
    from scripts.evidence_json import read_evidence_json

    require_host_code(read_evidence_json(mapping))
    return verified
