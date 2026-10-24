"""Pure DB0048 contract falsification. No PostgreSQL/native acceptance claim."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.database.base import Base
from app.database.repositories import controlled_writer_coverage as coverage
from scripts import verify_controlled_writer_coverage_schema_v1 as adapter
from tests.controlled_writer_coverage_fixtures import (
    coverage_observation_fixture,
    coverage_record_fixture,
)
from tests.durable_migration_fixtures import RecordedDowngrade, load_migration

_ROOT = Path(__file__).resolve().parents[2]


def test_registered_models_use_actual_single_metadata_graph():
    from app.database import models

    for name, cls in (
        (coverage.COVERAGE_TABLES[0], models.DemoControlledWriterCoverageScope),
        (coverage.COVERAGE_TABLES[1], models.DemoControlledWriterCoverageJournal),
    ):
        assert cls.metadata is Base.metadata
        assert cls.__table__ is Base.metadata.tables[name]
        assert [(col.name, col.nullable) for col in cls.__table__.columns] == [
            (column, nullable)
            for column, _, nullable in coverage.COVERAGE_TABLE_SPECS[name]["columns"]
        ]


def test_migration_is_additive_0048_with_exact_0047_parent_and_no_new_role():
    module = load_migration("0048")
    assert module.revision == "0048" and module.down_revision == "0047"
    commands = []

    class Recorder:
        def execute(self, statement):
            commands.append(str(statement))

    original = module.op
    try:
        module.op = Recorder()
        module.upgrade()
    finally:
        module.op = original
    assert sum(statement.startswith("CREATE TABLE") for statement in commands) == 2
    assert sum(statement.startswith("CREATE FUNCTION") for statement in commands) == 6
    assert sum("ENABLE ALWAYS TRIGGER" in statement for statement in commands) == 8
    assert all(
        "CREATE ROLE" not in statement and "GRANT " not in statement
        for statement in commands
    )
    for name, signature in coverage.COVERAGE_FUNCTION_SIGNATURES.items():
        assert (
            f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC" in commands
        )


def test_downgrade_locks_both_tables_before_read_and_never_deletes_evidence():
    commands = [command[1] for command in RecordedDowngrade("0048").commands]
    assert (
        commands[0]
        == "LOCK TABLE public.demo_account_controls, public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert "controlled_writer_coverage_downgrade_requires_empty" in commands[1]
    assert all(
        "DELETE FROM" not in command
        and "TRUNCATE" not in command
        and "CASCADE" not in command
        for command in commands
    )
    assert (
        "DROP FUNCTION public.controlled_writer_coverage_v1_read(text,text,bigint,text,text)"
        in commands
    )
    journal = commands.index(
        "DROP TABLE public.demo_controlled_writer_coverage_journal"
    )
    scope = commands.index("DROP TABLE public.demo_controlled_writer_coverage_scopes")
    assert 1 < journal < scope


def test_downgrade_parent_lock_precedes_both_child_locks_before_any_read_or_drop():
    commands = [command[1] for command in RecordedDowngrade("0048").commands]
    first = commands[0]
    assert first.startswith("LOCK TABLE ") and first.endswith(
        " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert first.removeprefix("LOCK TABLE ").removesuffix(
        " IN ACCESS EXCLUSIVE MODE NOWAIT"
    ).split(", ") == [
        "public.demo_account_controls",
        "public.demo_controlled_writer_coverage_scopes",
        "public.demo_controlled_writer_coverage_journal",
    ]
    assert "SELECT" not in first and "DROP" not in first
    assert "SELECT 1 FROM public.demo_controlled_writer_coverage_scopes" in commands[1]
    assert all(
        "DROP TABLE public.demo_account_controls" not in command for command in commands
    )


def test_sql_previous_hash_check_explicitly_rejects_null_for_noninitial_sequence():
    expression = coverage.COVERAGE_TABLE_SPECS[coverage.COVERAGE_TABLES[1]]["checks"][
        "ck_cwc_journal_previous"
    ]
    assert "journal_sequence>1 AND previous_sha256 IS NOT NULL" in expression
    assert "journal_sequence=1 AND previous_sha256 IS NULL" in expression


def test_sql_wire_checks_original_raw_uniqueness_before_jsonb_cast():
    body = coverage.COVERAGE_FUNCTION_SQL["controlled_writer_coverage_v1_journal_guard"]
    for name in ("state_json", "event_json", "audit_json"):
        assert f"NEW.{name} IS JSON OBJECT WITH UNIQUE KEYS" in body
    assert body.index("IS JSON OBJECT WITH UNIQUE KEYS") < body.index(
        "s:=NEW.state_json::jsonb"
    )
    assert "rebuilt IS DISTINCT FROM raw" in body
    assert "node_count>65536" in body and "level>24" in body
    assert "lpad(to_hex(55296+(code/1024)),4,'0')" in body


def test_r5_read_function_has_internal_row_and_late_mode_table_locks():
    body = coverage.COVERAGE_FUNCTION_SQL["controlled_writer_coverage_v1_read"]
    assert (
        coverage.COVERAGE_FUNCTION_SIGNATURES["controlled_writer_coverage_v1_read"]
        == "text,text,bigint,text,text"
    )
    assert "LANGUAGE plpgsql VOLATILE SECURITY DEFINER" in body
    assert "p_lock_mode NOT IN ('discover','append','readback')" in body
    assert (
        "WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE" in body
    )
    assert "IN ROW EXCLUSIVE MODE NOWAIT" in body and "IN SHARE MODE NOWAIT" in body
    assert (
        body.index("pg_advisory_xact_lock")
        < body.index("demo_account_controls")
        < body.index("SELECT * INTO h")
        < body.index("LOCK TABLE")
    )
    assert "UPDATE public." not in body and "INSERT INTO public." not in body


def test_adapter_uses_sole_policy_and_does_not_learn_observed_baseline():
    source = (
        _ROOT / "scripts/verify_controlled_writer_coverage_schema_v1.py"
    ).read_text()
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert any(
        node.module == "app.database.repositories.controlled_writer_coverage"
        for node in imports
    )
    assert (
        adapter.expected_controlled_writer_coverage_schema
        is coverage.expected_controlled_writer_coverage_schema
    )
    expected = coverage.expected_controlled_writer_coverage_schema()
    assert expected["native_source_acceptance"] is False
    assert expected["native_deparser_baseline"] == coverage.PG17_DEPARSED_BASELINE
    assert expected["native_baseline_status"] == coverage.PG17_DEPARSED_BASELINE_STATUS


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", True),
        ("journal_sequence", True),
        ("control_owner_epoch", True),
        ("control_revision", True),
        ("execution_authority", 0),
        ("account_complete", 0),
        ("control_emergency_stop", 1),
        ("account_id", 730048),
    ],
)
def test_wire_bool_int_alias_and_uid_coercion_are_rejected(field, value):
    fixture = coverage_record_fixture()
    state = copy.deepcopy(fixture["state"])
    state[field] = value
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage._validate_state(coverage.canonical(state))


@pytest.mark.parametrize(
    "raw",
    [
        "{} ",
        '{"a":1,"a":1}',
        '{"a":{"x":1,"x":1}}',
        '{"a":[{"x":1,"x":1}]}',
        '{"a":1,"\\u0061":1}',
        '{"a":NaN}',
        '{"a":1.0}',
    ],
)
def test_raw_noncanonical_or_duplicate_wire_does_not_normalize_to_acceptance(raw):
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage._decode(raw, coverage.MAX_STATE_BYTES)


def test_source_record_and_saved_observation_are_non_authorizing():
    fixture = coverage_record_fixture()
    observed = coverage_observation_fixture(
        fixture["state_raw"], fixture["event_raw"], fixture["audit_raw"]
    )
    assert observed.execution_authority is False
    assert all(
        observed.state_document[name] is False
        for name in coverage.FALSE_AUTHORITY_FIELDS
    )
    assert (
        observed.state_document["audit_sha256"]
        == hashlib.sha256(fixture["audit_raw"].encode("ascii")).hexdigest()
    )
    # Ordinary replay/serialization yields data, without touching issuer registry.
    assert json.loads(observed.records_json)["head"]["state"] == "prepared"


def test_pending_deparser_policy_cannot_be_admitted_by_a_caller_projection():
    projection = coverage.expected_controlled_writer_coverage_schema()
    with pytest.raises(coverage.ControlledWriterCoverageError):
        adapter.verify_catalog(projection)


def test_all_sql_functions_have_fixed_search_path_and_public_revoke_contract():
    assert len(coverage.COVERAGE_FUNCTION_SQL) == 6
    for source in coverage.COVERAGE_FUNCTION_SQL.values():
        assert "SET search_path=pg_catalog,public,pg_temp" in source
    assert (
        sum(
            "SECURITY DEFINER" in source
            for source in coverage.COVERAGE_FUNCTION_SQL.values()
        )
        == 2
    )
    assert all(
        spec[5] for spec in coverage.COVERAGE_TRIGGER_SPECS if spec[0].endswith("_pair")
    )


def _synthetic_runtime_role_catalog():
    """Only private role-validator unit data; never a schema/deparser baseline."""
    return {
        "role": {
            "oid": 200,
            "name": "synthetic_runtime",
            "can_login": True,
            **{
                key: False
                for key in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "other_membership",
                    "can_create",
                )
            },
        },
        "server": {
            "current_user": "synthetic_runtime",
            "session_user": "synthetic_runtime",
            "role_setting": "none",
            "timezone": "UTC",
            "search_path": "pg_catalog, public, pg_temp",
            "isolation": "read committed",
            "synchronous_commit": "on",
            "fsync": "on",
            "full_page_writes": "on",
            "replication_role": "origin",
            "in_recovery": False,
        },
        "tables": [{"owner_oid": 100, "table_access": False} for _ in range(2)],
        "functions": [
            {
                "name": name,
                "owner_oid": 100,
                "can_execute": name.endswith(("_append", "_read")),
            }
            for name in coverage.COVERAGE_FUNCTION_SQL
        ],
    }


def test_synthetic_restricted_role_observation_alone_grants_no_native_admission():
    catalog = _synthetic_runtime_role_catalog()
    assert coverage._verify_native_role_catalog(catalog) is None
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage.verify_controlled_writer_coverage_catalog(catalog)


@pytest.mark.parametrize(
    "mutation",
    [
        "owner",
        "superuser",
        "set-role",
        "missing-role",
        "table-read",
        "missing-setting",
        "bool-privilege",
        "other-membership",
    ],
)
def test_native_role_gate_rejects_privilege_set_role_and_missing_facts(mutation):
    catalog = _synthetic_runtime_role_catalog()
    if mutation == "owner":
        catalog["tables"][0]["owner_oid"] = catalog["role"]["oid"]
    elif mutation == "superuser":
        catalog["role"]["superuser"] = True
    elif mutation == "set-role":
        catalog["server"]["role_setting"] = "synthetic_runtime"
    elif mutation == "missing-role":
        del catalog["role"]
    elif mutation == "table-read":
        catalog["tables"][0]["table_access"] = True
    elif mutation == "missing-setting":
        del catalog["server"]["synchronous_commit"]
    elif mutation == "bool-privilege":
        catalog["role"]["can_create"] = 0
    else:
        catalog["role"]["other_membership"] = True
    with pytest.raises(coverage.ControlledWriterCoverageError, match="role_denied"):
        coverage._verify_native_role_catalog(catalog)


def test_safety_revocation_retains_old_boot_stamps_and_all_original_deadlines():
    original = coverage_record_fixture()["state"]
    later = datetime.fromisoformat(original["updated_at"]) + timedelta(seconds=90)
    simulated_new_boot_monotonic = 0
    assert simulated_new_boot_monotonic < original["transition_stamp"]["monotonic_ns"]
    revoked = coverage._revocation_document(
        original, now=later, reason="restart_reconciliation_required", observed=None
    )
    for name in (
        "original_native_anchor",
        "transition_stamp",
        "process_domain_nonce",
        "source_current_expires_at",
        "source_current_monotonic_deadline_ns",
    ):
        assert coverage.canonical(revoked[name]) == coverage.canonical(original[name])
    assert revoked["updated_at"] == later.isoformat() and revoked["state"] == "revoked"
    assert all(revoked[name] is False for name in coverage.FALSE_AUTHORITY_FIELDS)


def test_safety_revocation_refuses_regressed_or_non_native_utc_without_mutating_old_epoch():
    original = coverage_record_fixture()["state"]
    retained = coverage.canonical(original)
    old = datetime.fromisoformat(original["updated_at"])
    for malformed in (
        old - timedelta(microseconds=1),
        old.replace(tzinfo=None),
        "2026-10-24T00:00:00+00:00",
    ):
        with pytest.raises(
            coverage.ControlledWriterCoverageError, match="revocation_time"
        ):
            coverage._revocation_document(
                original,
                now=malformed,
                reason="restart_reconciliation_required",
                observed=None,
            )
    assert coverage.canonical(original) == retained


def test_normal_transition_utc_keeps_existing_conservative_ns_conversion():
    original = coverage_record_fixture()["state"]
    state = copy.deepcopy(original)
    state["transition_stamp"] = {
        name: value + 1 for name, value in original["transition_stamp"].items()
    }
    state["updated_at"] = coverage.utc_from_ns(
        state["transition_stamp"]["utc_ns"]
    ).isoformat()
    assert (
        coverage._validate_state(coverage.canonical(state))["transition_stamp"][
            "utc_ns"
        ]
        == original["transition_stamp"]["utc_ns"] + 1
    )
    state["updated_at"] = original["updated_at"]
    with pytest.raises(coverage.ControlledWriterCoverageError, match="transition_time"):
        coverage._validate_state(coverage.canonical(state))


def _synthetic_schema_catalog_for_identity_mutations():
    """Synthetic schema-validator input only, never a native catalog baseline."""
    catalog = {
        "schema_version": "ctcc.controlled_writer_coverage_actual_catalog.v1",
        "server": {"version_num": "170000", "version": "synthetic_unit_fixture"},
        "role": {},
        "tables": [],
        "columns": [],
        "constraints": [],
        "indexes": [],
        "functions": [],
        "triggers": [],
    }
    for table, spec in coverage.COVERAGE_TABLE_SPECS.items():
        catalog["tables"].append(
            {
                "name": table,
                "owner_oid": 900000,
                "kind": "r",
                "persistence": "p",
                "rls": False,
                "force_rls": False,
                "public_access": False,
            }
        )
        for column, kind, nullable in spec["columns"]:
            catalog["columns"].append(
                {
                    "table": table,
                    "name": column,
                    "type": kind,
                    "nullable": nullable,
                    "identity": "",
                    "generated": "",
                    "default": "clock_timestamp()" if column == "recorded_at" else None,
                }
            )
        base = {
            "table": table,
            "validated": True,
            "deferrable": False,
            "deferred": False,
            "definition": "SYNTHETIC_UNSEALED_NOT_A_POSTGRES_DEFINITION",
            "columns": None,
            "target": None,
            "target_columns": None,
            "delete_action": " ",
        }
        catalog["constraints"].append(
            {
                **base,
                "name": f"pk_{table}",
                "kind": "p",
                "columns": list(spec["primary_key"]),
            }
        )
        catalog["constraints"].extend(
            {**base, "name": name, "kind": "c"} for name in spec["checks"]
        )
        for name, columns, target, target_columns, deferrable, deferred in spec[
            "foreign_keys"
        ]:
            catalog["constraints"].append(
                {
                    **base,
                    "name": name,
                    "kind": "f",
                    "columns": list(columns),
                    "target": target,
                    "target_columns": list(target_columns),
                    "delete_action": "r",
                    "deferrable": deferrable,
                    "deferred": deferred,
                }
            )
        catalog["constraints"].extend(
            {**base, "name": name, "kind": "u", "columns": list(columns)}
            for name, columns in spec["unique"]
        )
        indexes = [
            (f"pk_{table}", spec["primary_key"], True, True),
            *((name, columns, True, False) for name, columns in spec["unique"]),
            *(
                (name, columns, unique, False)
                for name, columns, unique in spec["indexes"]
            ),
        ]
        catalog["indexes"].extend(
            {
                "table": table,
                "name": name,
                "columns": list(columns),
                "unique": unique,
                "primary": primary,
                "method": "btree",
                "valid": True,
                "ready": True,
                "predicate": None,
                "expressions": None,
                "definition": "SYNTHETIC_UNSEALED_NOT_A_POSTGRES_DEFINITION",
            }
            for name, columns, unique, primary in indexes
        )
    for index, (name, _, returns, language, definer, volatility, body) in enumerate(
        coverage._FUNCTIONS
    ):
        catalog["functions"].append(
            {
                "name": name,
                "oid": 500000 + index,
                "namespace_oid": 400000,
                "namespace": "public",
                "owner_oid": 900000,
                "arguments": coverage.COVERAGE_FUNCTION_SIGNATURES[name],
                "returns": returns,
                "source": body,
                "language": language,
                "security_definer": definer,
                "volatility": "v" if volatility == "VOLATILE" else "s",
                "config": ["search_path=pg_catalog, public, pg_temp"],
                "kind": "f",
                "leakproof": False,
                "strict": False,
                "parallel": "u",
                "public_execute": False,
            }
        )
    functions = {row["name"]: row for row in catalog["functions"]}
    for (
        name,
        table,
        events,
        level,
        function,
        deferred,
    ) in coverage.COVERAGE_TRIGGER_SPECS:
        kind = (1 if level == "ROW" else 0) + (2 if events.startswith("BEFORE") else 0)
        kind += sum(
            bit
            for event, bit in (
                ("INSERT", 4),
                ("DELETE", 8),
                ("UPDATE", 16),
                ("TRUNCATE", 32),
            )
            if event in events
        )
        catalog["triggers"].append(
            {
                "table": table,
                "name": name,
                "function": function,
                "function_oid": functions[function]["oid"],
                "function_namespace_oid": 400000,
                "function_namespace": "public",
                "enabled": "A",
                "type": kind,
                "deferrable": deferred,
                "deferred": deferred,
                "constraint": deferred,
                "arguments": "",
                "when": None,
            }
        )
    return catalog


@pytest.mark.parametrize(
    "field,value",
    [
        ("function_oid", 700000),
        ("function_namespace_oid", 700001),
        ("function_namespace", "synthetic_other_schema"),
        ("function_oid", True),
        ("function_namespace_oid", True),
    ],
)
def test_catalog_rejects_trigger_oid_or_namespace_substitution_before_native_baseline(
    field, value
):
    catalog = _synthetic_schema_catalog_for_identity_mutations()
    assert (
        adapter.verify_catalog(catalog, require_native_baseline=False)
        == coverage.expected_controlled_writer_coverage_schema()
    )
    original_functions = coverage.canonical(catalog["functions"])
    target = next(
        row for row in catalog["triggers"] if row["name"] == "cwc_scope_guard"
    )
    original_name = target["function"]
    target[field] = value
    assert target["function"] == original_name
    assert coverage.canonical(catalog["functions"]) == original_functions
    for native_schema_gate in (False, True):
        with pytest.raises(
            coverage.ControlledWriterCoverageError,
            match="controlled_writer_coverage_trigger_identity_drift",
        ):
            adapter.verify_catalog(catalog, require_native_baseline=native_schema_gate)


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "synthetic_other_schema"),
        ("namespace_oid", True),
        ("namespace_oid", 700001),
        ("oid", True),
    ],
)
def test_catalog_rejects_unverified_public_function_namespace_identity(field, value):
    catalog = _synthetic_schema_catalog_for_identity_mutations()
    catalog["functions"][0][field] = value
    with pytest.raises(
        coverage.ControlledWriterCoverageError,
        match="controlled_writer_coverage_function_namespace_drift",
    ):
        adapter.verify_catalog(catalog, require_native_baseline=False)
