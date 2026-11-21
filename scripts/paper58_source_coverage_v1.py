"""Source inventory for the separate, non-executing Paper58 startup work.

This is an engineering coverage map, not a catalog observer or admission API.
It never opens a database, provisions a role, starts an application, or imports
the Paper service singleton. Definition sites include historical overrides;
their presence is not proof of the effective installed definition.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "ctcc.paper58.source_coverage_data.v1"
HEAD = "0058"

# Reviewed source roster. A source-only audit must not silently absorb a new
# relation into the future startup observer's expected object inventory.
RELATIONS = (
    "account_snapshots",
    "analysis_runs",
    "audit_logs",
    "configuration_versions",
    "demo_account_bill_archive_claims",
    "demo_account_capture_events",
    "demo_account_controls",
    "demo_account_current_observations",
    "demo_account_history_native_members",
    "demo_account_history_partition_checkpoints",
    "demo_account_measured_hwm_v2",
    "demo_account_observation_batches",
    "demo_account_observation_coverage",
    "demo_account_observation_facts",
    "demo_account_observation_findings",
    "demo_account_observation_typed_batches",
    "demo_account_observation_typed_coverage",
    "demo_account_observation_typed_facts",
    "demo_account_observation_typed_findings",
    "demo_account_revision_source_witnesses",
    "demo_automation_fingerprints",
    "demo_automation_runs",
    "demo_automation_state",
    "demo_collector_current_revision_lineages",
    "demo_collector_issued_account_revisions",
    "demo_control_clear_acknowledgements_v2",
    "demo_control_journal",
    "demo_controlled_writer_coverage_cutovers",
    "demo_controlled_writer_coverage_journal",
    "demo_controlled_writer_coverage_scopes",
    "demo_daily_performance_reports",
    "demo_event_history_epoch_events",
    "demo_observability_events",
    "demo_performance_snapshots",
    "demo_policy_counter_initializations_v1",
    "demo_soak_sessions",
    "demo_strategy_controls",
    "fills",
    "g12_invocation_claims",
    "g12_invocation_publications",
    "gate3_capture_schedule_claim_acks",
    "gate3_capture_schedule_key_claims",
    "gate3_capture_schedule_legacy_inventory",
    "gate3_capture_schedule_pins",
    "gate3_capture_schedule_publication_acks",
    "gate3_fitted_candidate_acks",
    "gate3_fitted_candidate_bindings",
    "gate3_fitted_candidate_evaluations",
    "gate3_preregistration_seal_acks",
    "gate3_preregistration_seals",
    "market_snapshots",
    "native_live_controlled_close_claims_v1",
    "native_live_controlled_close_intents_v1",
    "native_live_controls_v1",
    "native_live_financial_epochs_v1",
    "native_live_financial_samples_v1",
    "native_live_intents_v1",
    "native_live_lifecycle_keys_v1",
    "native_live_lifecycle_proofs_v1",
    "native_live_reservations_v1",
    "native_live_transitions_v1",
    "okx_demo_algo_order_state",
    "okx_demo_balance_state",
    "okx_demo_order_state",
    "okx_demo_position_state",
    "okx_demo_sync_checkpoints",
    "okx_live_account_config_state",
    "okx_live_algo_order_state",
    "okx_live_balance_state",
    "okx_live_execution_intents",
    "okx_live_order_state",
    "okx_live_position_state",
    "okx_live_sync_checkpoints",
    "orchestrator_fingerprint_state",
    "orchestrator_run_state",
    "orders",
    "paper_account_state",
    "paper_order_state",
    "paper_position_state",
    "portfolio_snapshots",
    "positions",
    "protective_orders",
    "public_receipt_post_read_observations",
    "public_receipt_publication_acks",
    "public_receipt_witness_revisions",
    "public_runtime_raw_bundles_v1",
    "qualification_account_scopes",
    "qualification_leverage_configuration_attempts_v1",
    "qualification_leverage_configuration_intents_v1",
    "qualification_leverage_configuration_results_v1",
    "qualification_report_projection_receipts",
    "qualification_report_spool",
    "qualification_reservation_transitions",
    "qualification_reservations",
    "qualification_submission_outcomes",
    "recovery_checkpoints",
    "risk_decisions",
    "safety_incidents",
    "strategy_evaluations",
    "system_events",
    "timeframe_analyses",
    "trade_candidates",
    "trade_lifecycles",
    "trades",
)

TABLE_PRIVILEGES = (
    ("paper_account_state", ("SELECT", "INSERT", "UPDATE")),
    ("paper_order_state", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("paper_position_state", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("recovery_checkpoints", ("SELECT", "INSERT", "UPDATE")),
    ("audit_logs", ("SELECT", "INSERT")),
    ("system_events", ("SELECT", "INSERT")),
    ("orchestrator_run_state", ("SELECT",)),
    ("orchestrator_fingerprint_state", ("SELECT",)),
)

# These are source pointers for composition review, never interchangeable role
# witnesses. The frozen owner/native actor observers remain unchanged.
SOURCE_VALIDATORS = (
    (
        "physical_read_safety",
        "scripts/native_owner_heap_am_v1.py",
        "require_native_owner_storage_v1",
        "not_schema_equality",
    ),
    (
        "native_index_callbacks",
        "scripts/native_owner_index_catalog_v1.py",
        "_build_source_predicate",
        "not_schema_equality",
    ),
    (
        "live_ledger_bodies",
        "app/database/repositories/native_live_ledger_v1.py",
        "_catalog",
        "actor_adapter_required",
    ),
    (
        "live_financial_columns",
        "app/database/repositories/native_live_financial_epoch_v1.py",
        "_column_profile",
        "held_safe_catalog_required",
    ),
    (
        "live_financial_checks",
        "app/database/repositories/native_live_financial_epoch_v1.py",
        "_constraint_profile",
        "held_safe_catalog_before_deparse_required",
    ),
    (
        "live_financial_keys",
        "app/database/repositories/native_live_financial_epoch_v1.py",
        "_key_profile",
        "held_safe_catalog_required",
    ),
    (
        "live_lifecycle_bodies",
        "app/database/repositories/native_live_lifecycle_v1.py",
        "_catalog",
        "actor_adapter_required",
    ),
    (
        "live_lifecycle_close_structure",
        "app/database/repositories/native_live_lifecycle_v1.py",
        "_structural_catalog",
        "held_safe_catalog_required",
    ),
)

RECOVERY_SOURCES = (
    ("app/paper/execution_service.py", "PaperExecutionService", "recover"),
    ("app/paper/execution_service.py", "PaperExecutionService", "recovery_status"),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService",
        "require_confirmed_state",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository",
        "save_paper_state",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository",
        "load_paper_state",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository",
        "mark_recovered",
    ),
    ("app/database/repositories/persistence.py", "PersistenceRepository", "counts"),
)

_CREATE = re.compile(
    r"\A\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+)?"
    r"(FUNCTION|(?:CONSTRAINT\s+)?TRIGGER|TYPE|SEQUENCE|INDEX|TABLE)\s+"
    r"(?:(?:public|pg_catalog)\.)?(?:\"([a-z_][a-z0-9_]*)\"|([a-z_][a-z0-9_]*))?",
    re.IGNORECASE,
)


def _need(value):
    if value is not True:
        raise ValueError("paper58_source_coverage_incomplete") from None


def _source(path):
    _need(
        type(path) is str
        and not Path(path).is_absolute()
        and ".." not in Path(path).parts
    )
    file = ROOT / path
    _need(file.is_file() and not file.is_symlink())
    raw = file.read_bytes()
    _need(0 < len(raw) <= 2_000_000)
    return raw, ast.parse(raw, filename=path)


def _migration_domain_sources():
    """Only the fixed 58-file migration graph and its source domain imports."""
    files, domain, heads = {}, set(), {}
    for file in sorted((ROOT / "migrations/versions").glob("*.py")):
        if file.name == "__init__.py":
            continue
        path = file.relative_to(ROOT).as_posix()
        raw, tree = _source(path)
        identity = {}
        for node in tree.body:
            target = (
                node.targets[0]
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                else node.target
                if isinstance(node, ast.AnnAssign)
                else None
            )
            if isinstance(target, ast.Name) and target.id in {
                "revision",
                "down_revision",
            }:
                identity[target.id] = ast.literal_eval(node.value)
        _need(set(identity) == {"revision", "down_revision"})
        revision = identity["revision"]
        _need(revision not in heads)
        heads[revision] = identity["down_revision"]
        files[path] = (raw, tree)
    _need(set(heads) == {f"{number:04d}" for number in range(1, 59)})
    _need(
        all(
            heads[f"{number:04d}"] == (None if number == 1 else f"{number - 1:04d}")
            for number in range(1, 59)
        )
    )
    pending = list(files)
    while pending:
        path = pending.pop()
        for node in ast.walk(files[path][1]):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imports = (
                    tuple("app/domain/" + alias.name + ".py" for alias in node.names)
                    if node.module == "app.domain"
                    else (node.module.replace(".", "/") + ".py",)
                    if node.module.startswith("app.domain.")
                    else ()
                )
                for imported in imports:
                    if imported not in files:
                        _need(len(domain) < 128)
                        files[imported] = _source(imported)
                        domain.add(imported)
                        pending.append(imported)
    return files


def _ddl_sites(files):
    sites = []
    for path, (_raw, tree) in sorted(files.items()):
        joined_children = {
            id(value)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for value in node.values
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and type(node.value) is str
                and id(node) not in joined_children
            ):
                prefix, dynamic = node.value, False
            elif isinstance(node, ast.JoinedStr):
                values = []
                for value in node.values:
                    if (
                        not isinstance(value, ast.Constant)
                        or type(value.value) is not str
                    ):
                        break
                    values.append(value.value)
                prefix, dynamic = "".join(values), True
            else:
                continue
            match = _CREATE.match(prefix)
            if match is None:
                continue
            name = match.group(2) or match.group(3)
            if dynamic and match.end() == len(prefix) and not prefix[-1:].isspace():
                name = None
            sites.append(
                {
                    "kind": " ".join(match.group(1).upper().split()),
                    "name": name,
                    "source": path,
                    "line": node.lineno,
                    "source_expression_sha256": hashlib.sha256(
                        ast.dump(node, include_attributes=False).encode()
                    ).hexdigest(),
                    "dynamic_name_requires_spec_expansion": name is None,
                    "effective_override_resolution_required": True,
                }
            )
    return sorted(sites, key=lambda item: (item["source"], item["line"], item["kind"]))


def _server_default(value, compiler):
    from sqlalchemy.schema import FetchedValue

    if value is None:
        return None
    if type(value) is FetchedValue:
        # A source ORM annotation is not an expected SQL default definition.
        return {"fetched_value_source_marker": True, "native_spec_required": True}
    value = value.arg
    if type(value) is str:
        return value
    return str(value.compile(dialect=compiler, compile_kwargs={"literal_binds": True}))


def source_coverage_v1():
    # Import only the genuine source registry. In particular, neither session,
    # settings, app.main nor the service singleton is part of this audit.
    from sqlalchemy import MetaData
    from sqlalchemy.dialects.postgresql import dialect
    from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

    import app.database.models  # noqa: F401 -- source registry only
    from app.database.base import Base

    _need(tuple(sorted(Base.metadata.tables)) == RELATIONS)
    compiler = dialect()
    # AddConstraint changes its target constraint's _create_rule. Compile
    # throwaway copies; this audit must never change the genuine registry.
    copied = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(copied)
    mappers = {
        mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers
    }
    _need(
        set(mappers) == set(RELATIONS)
        and len(mappers) == len(tuple(Base.registry.mappers))
    )
    relations = []
    for name in RELATIONS:
        table, model = Base.metadata.tables[name], mappers[name]
        model_path = model.__module__.replace(".", "/") + ".py"
        _need(model_path.startswith("app/database/models/"))
        model_raw, model_tree = _source(model_path)
        _need(
            any(
                isinstance(node, ast.ClassDef) and node.name == model.__name__
                for node in model_tree.body
            )
        )
        columns = [
            {
                "name": column.name,
                "type": str(column.type.compile(dialect=compiler)),
                "nullable": column.nullable,
                "primary_key": column.primary_key,
                "server_default": _server_default(column.server_default, compiler),
                "server_onupdate": _server_default(column.server_onupdate, compiler),
                "python_default_present": column.default is not None,
                "python_onupdate_present": column.onupdate is not None,
            }
            for column in table.columns
        ]
        relations.append(
            {
                "name": name,
                "model": model.__module__ + "." + model.__name__,
                "source": model_path,
                "source_sha256": hashlib.sha256(model_raw).hexdigest(),
                "columns": columns,
                "constraints": sorted(
                    str(AddConstraint(item).compile(dialect=compiler))
                    for item in copied.tables[name].constraints
                ),
                "indexes": sorted(
                    str(CreateIndex(item).compile(dialect=compiler))
                    for item in copied.tables[name].indexes
                ),
                "source_create_table": str(
                    CreateTable(table).compile(dialect=compiler)
                ),
            }
        )
    files = _migration_domain_sources()
    return {
        "schema": SCHEMA,
        "source_head": HEAD,
        "relations": relations,
        "column_type_spellings": sorted(
            {column["type"] for relation in relations for column in relation["columns"]}
        ),
        "migration_and_domain_sources": {
            path: hashlib.sha256(raw).hexdigest()
            for path, (raw, _tree) in sorted(files.items())
        },
        "sql_definition_sites": _ddl_sites(files),
        "table_privileges": TABLE_PRIVILEGES,
        "table_privilege_count": 20,
        "schema_usage_required": ("public",),
        "schema_create_allowed": False,
        "sequence_usage_allowed": False,
        "native_execution_grants_allowed": False,
        "source_validator_references": SOURCE_VALIDATORS,
        "original_recovery_references": RECOVERY_SOURCES,
        "composition_work_remaining": (
            "resolve exact effective migration/SPEC overrides, including dynamic names",
            "compare complete catalog bodies/ACL/types/triggers and reject unknown objects",
            "prove separate NOSUPER actor's exact20 table rights, no memberships/extra/column/native rights",
            "closed settings must refuse unsafe inherited OS environment before generic imports",
            "same owned engine/context: recover, last_error=None, consistent=True, unknown-commit fence",
        ),
        "runtime_observer_implemented": False,
        "startup_admitted": False,
        "provisioning_performed": False,
        "database_connections": 0,
        "execution_authority": False,
    }


if __name__ == "__main__":
    print(json.dumps(source_coverage_v1(), sort_keys=True, separators=(",", ":")))
