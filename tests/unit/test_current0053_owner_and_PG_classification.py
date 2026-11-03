"""Synthetic contract vectors; no native/role/account acceptance credit."""

import copy
from pathlib import Path

import pytest

from app.domain import account_revision_collector_lineage_schema_v1 as schema
from app.domain import collector_current_0053_owner_installation_v1 as owner
from scripts import collector_current_0053_toolchain as current
from scripts import verify_final_hermetic as historical
from scripts import verify_linux_shard as linux
from scripts import verify_windows_regression as windows
from tests.unit.test_account_revision_collector_lineage_v1 import catalog

CURRENT_PG = (
    "test_account_history_original_financial_native_pg_v2.py",
    "test_account_history_original_financial_native_pg_v3.py",
    "test_account_revision_collector_lineage_pg_v1.py",
    "test_original_frame_current_0053_fixture.py",
)


def synthetic_owner_observation():
    # This is declared unit data. No SQL response, runtime actor, native seal
    # or authority-bearing object is transformed by the implementation.
    value = copy.deepcopy(catalog())
    value["actor"] = value["owner"]
    value["table_ok"] = True
    value.update(
        schema_version=owner.CATALOG_SCHEMA,
        database_head="0053",
        table_oid=900,
        server={
            "version_num": "170011",
            "current_user": "unit_owner",
            "session_user": "unit_owner",
            "role_setting": "none",
            "database": "unit_only",
            "timezone": "UTC",
            "search_path": "pg_catalog, public, pg_temp",
            "isolation": "read committed",
            "read_only": "on",
            "synchronous_commit": "on",
            "fsync": "on",
            "full_page_writes": "on",
            "replication_role": "origin",
            "recovery": False,
            "role_oid": value["owner"],
            "superuser": True,
            "can_login": True,
        },
        parent_facts=[
            {
                "name": name,
                "oid": 1000 + i,
                "owner": value["owner"],
                "kind": "r",
                "persistence": "p",
                "rls": False,
                "force_rls": False,
            }
            for i, name in enumerate(sorted(owner.PARENTS))
        ],
        native_definitions={
            "functions": [
                [name, "unit-only function " + name]
                for name in (schema.GUARD, schema.IMMUTABLE)
            ],
            "constraints": [
                [name, "unit-only constraint " + name]
                for name in sorted(set(schema.KEYS) | schema.CHECKS)
            ],
            "indexes": [
                [name, "unit-only index " + name]
                for name, spec in schema.KEYS.items()
                if spec[0] in {"p", "u"}
            ],
            "triggers": [
                [row[0], "unit-only trigger " + row[0]] for row in schema.TRIGGERS
            ],
            "custody_triggers": [
                [name, "unit-only custody trigger " + name] for name in ("a", "b", "c")
            ],
        },
        ri_triggers=[],
        custody_incoming=[
            [
                "fk_collector_current_custody",
                "demo_collector_current_revision_lineages",
                "{15}",
                "{1}",
                "r",
                "a",
                "s",
                True,
                False,
                False,
            ]
        ],
    )
    parent_ids = {row["name"]: row["oid"] for row in value["parent_facts"]}
    routines = (
        ("RI_FKey_check_ins", 5, "c", True),
        ("RI_FKey_check_upd", 17, "c", True),
        ("RI_FKey_restrict_del", 9, "a", False),
        ("RI_FKey_noaction_upd", 17, "a", False),
    )
    foreign = [(name, spec) for name, spec in schema.KEYS.items() if spec[0] == "f"]
    for i, (name, spec) in enumerate(foreign):
        parent = parent_ids[spec[2]]
        for j, (routine, kind, prefix, child) in enumerate(routines):
            trigger = 3000 + i * 4 + j
            value["ri_triggers"].append(
                {
                    "constraint": name,
                    "constraint_oid": 2000 + i,
                    "child_oid": value["table_oid"],
                    "parent_oid": parent,
                    "trigger_oid": trigger,
                    "name": "RI_ConstraintTrigger_" + prefix + "_" + str(trigger),
                    "relation_oid": value["table_oid"] if child else parent,
                    "other_oid": parent if child else value["table_oid"],
                    "function": routine,
                    "namespace": "pg_catalog",
                    "function_oid": 4000 + j,
                    "expected_function_oid": 4000 + j,
                    "type": kind,
                    "enabled": "O",
                    "internal": True,
                    "deferrable": False,
                    "deferred": False,
                    "parent_trigger_oid": 0,
                    "args_count": 0,
                    "args": "",
                    "attributes": "",
                    "when": None,
                    "old_table": None,
                    "new_table": None,
                    "delete_action": "r",
                    "update_action": "a",
                    "match": "s",
                    "validated": True,
                    "fk_deferrable": False,
                    "fk_deferred": False,
                }
            )
    return value


def test_declared_owner_observation_remains_inert_and_has_no0052_native_credit():
    value = synthetic_owner_observation()
    observed = owner.observe_owner_installation_catalog(value)
    assert observed["database_head"] == "0053"
    assert (
        observed["native_baseline_admission"] is observed["runtime_admission"] is False
    )
    assert observed["account_complete"] is observed["execution_authority"] is False
    with pytest.raises(
        owner.Current0053OwnerError, match="genuine_native_baseline_pending_or_changed"
    ):
        owner.verify_owner_installation_catalog(value)


@pytest.mark.parametrize(
    "change",
    (
        "historical_head",
        "actor",
        "missing_native",
        "extra_catalog_key",
        "missing_parent",
        "parent_owner",
        "set_role",
        "superuser_unknown",
        "future_major",
        "lineage_access",
        "missing_RI",
        "duplicate_RI",
        "builtin_oid",
        "foreign_schema",
        "wrong_relation",
        "wrong_parent",
        "wrong_constraint_identity",
        "bool_count",
        "cascade",
        "extra_incoming",
    ),
)
def test_owner_observation_rejects_unknown_role_native_and_RI_bindings(change):
    value = synthetic_owner_observation()
    if change == "historical_head":
        value["database_head"] = "0052"
    elif change == "actor":
        value["actor"] += 1
    elif change == "missing_native":
        value["native_definitions"] = None
    elif change == "extra_catalog_key":
        value["passed"] = True
    elif change == "missing_parent":
        value["parent_facts"].pop()
    elif change == "parent_owner":
        value["parent_facts"][0]["owner"] += 1
    elif change == "set_role":
        value["server"]["role_setting"] = "unit_owner"
    elif change == "superuser_unknown":
        value["server"]["superuser"] = None
    elif change == "future_major":
        value["server"]["version_num"] = "180000"
    elif change == "lineage_access":
        value["bad_table_acl"] = True
    elif change == "missing_RI":
        value["ri_triggers"].pop()
    elif change == "duplicate_RI":
        value["ri_triggers"][-1] = copy.deepcopy(value["ri_triggers"][0])
    elif change == "builtin_oid":
        value["ri_triggers"][0]["expected_function_oid"] += 1
    elif change == "foreign_schema":
        value["ri_triggers"][0]["namespace"] = "public"
    elif change == "wrong_relation":
        value["ri_triggers"][0]["relation_oid"] += 1
    elif change == "wrong_parent":
        value["ri_triggers"][0]["parent_oid"] += 1
    elif change == "wrong_constraint_identity":
        value["ri_triggers"][1]["constraint_oid"] += 1
    elif change == "bool_count":
        value["ri_triggers"][0]["args_count"] = False
    elif change == "cascade":
        value["ri_triggers"][0]["delete_action"] = "c"
    else:
        value["custody_incoming"].append(copy.deepcopy(value["custody_incoming"][0]))
    with pytest.raises(owner.Current0053OwnerError):
        owner.observe_owner_installation_catalog(value)


@pytest.mark.parametrize("name", CURRENT_PG)
def test_new_PG_modules_have_one_component_and_never_run_as_offline_shards(name):
    path = "tests/integration/" + name
    node = path + "::declared_case"
    assert (
        sum(
            linux.selected_for("postgres", index, node)
            for index in range(linux.POSTGRES_COMPONENT_COUNT)
        )
        == 1
    )
    assert not any(
        linux.selected_for("shard", index, node) for index in range(linux.SHARD_COUNT)
    )
    assert name in windows.POSTGRES_INTEGRATION
    assert name not in windows.NO_DB_INTEGRATION


def test_windows_classification_is_exhaustive_and_new_unknown_module_still_fails(
    tmp_path: Path,
):
    integration = tmp_path / "tests/integration"
    integration.mkdir(parents=True)
    (tmp_path / "tests/unit").mkdir()
    names = (
        set(windows.NO_DB_INTEGRATION)
        | set(windows.POSTGRES_INTEGRATION)
        | set(CURRENT_PG)
    )
    for name in names:
        (integration / name).write_text("", encoding="utf-8")
    selected = windows.test_selection(tmp_path)
    assert all("tests/integration/" + name not in selected for name in CURRENT_PG)
    (integration / "test_unreviewed_current0053.py").write_text("", encoding="utf-8")
    with pytest.raises(
        windows.WindowsRegressionError,
        match="integration_module_classification_changed",
    ):
        windows.test_selection(tmp_path)


def test_current0053_readback_does_not_relabel_a_real0052_database():
    with pytest.raises(current.Current0053CaptureError):
        current.current0053_migration_readback(
            "0052",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            coverage_catalog=None,
            partition_catalog=None,
            cutover_catalog=None,
            original_frame_catalog=None,
            current0053_owner_catalog=None,
        )


def test_new_current_declaration_never_mutates_the_historical0052_declaration():
    before = copy.deepcopy(historical.expected_migration_revision_readback("0052"))
    try:
        declared = current.expected_current0053_migration_readback()
    except owner.Current0053OwnerError:
        assert owner.PG17_NATIVE_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
    else:
        assert declared["expected"] == declared["observed"] == "0053"
        assert (
            declared["original_history_frame_owner_installation"]["revision"] == "0052"
        )
    assert historical.expected_migration_revision_readback("0052") == before


def test_caller_passed_flag_cannot_replace_a_current_owner_capture():
    with pytest.raises(current.Current0053CaptureError):
        current.verify_current0053_owner_capture(
            {"schema": current.CAPTURE_SCHEMA, "passed": True},
            migration_sha256="0" * 64,
            manifest_sha256="0" * 64,
            source_files={},
        )
