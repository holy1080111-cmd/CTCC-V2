"""DB0057 source and refusal contracts; synthetic, never venue acceptance."""

import ast
import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

from app.domain import g12_custody_catalog_0057_v1 as catalog
from app.domain import native_closed_report_catalog_v55 as helpers
from scripts import current0057_native_g12_acl_v1 as profile

ROOT = Path(__file__).resolve().parents[2]


def _migration():
    path = ROOT / "migrations/versions/0057_g12_restricted_writer.py"
    spec = importlib.util.spec_from_file_location("g12_migration57_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_bodies():
    tree = ast.parse(
        (ROOT / "migrations/versions/0039_g12_invocation_custody.py").read_text()
    )
    result = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "CREATE FUNCTION" in node.value
        ):
            sql = node.value
            result[sql.split("public.")[1].split("(")[0]] = sql.split("$$")[1]
    return result


def test_successor_changes_only_immutable_claim_lock_and_preserves_previous():
    migration = _migration()
    old = _old_bodies()["g12_invocation_publication_guard"]
    assert migration.down_revision == "0056" and migration.revision == "0057"
    assert old.count(" FOR KEY SHARE;") == 1
    assert migration.PUBLICATION_BODY == old.replace(" FOR KEY SHARE;", ";")
    assert hashlib.sha256(old.encode()).hexdigest() == migration.PREVIOUS_BODY_SHA256
    assert (
        hashlib.sha256(migration.PUBLICATION_BODY.encode()).hexdigest()
        == migration.PUBLICATION_BODY_SHA256
        == catalog.PUBLICATION_BODY_SHA256
    )


def test_migration_has_no_grant_row_mutation_or_evidence_deletion(monkeypatch):
    migration = _migration()
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    assert statements[0].endswith("ACCESS EXCLUSIVE MODE NOWAIT")
    assert len([s for s in statements if "CREATE OR REPLACE FUNCTION" in s]) == 1
    assert all(
        not s.lstrip().startswith(
            ("GRANT ", "DELETE ", "UPDATE ", "TRUNCATE ", "DROP ")
        )
        for s in statements
    )
    assert "SECURITY INVOKER SET search_path=pg_catalog,pg_temp" in statements[2]
    assert migration.PREVIOUS_BODY_SHA256 in statements[1]
    assert migration.PUBLICATION_BODY_SHA256 in statements[-1]
    statements.clear()
    migration.downgrade()
    assert "g12_successor_downgrade_requires_empty_ungranted" in statements[2]
    assert " FOR KEY SHARE;" in statements[3]
    assert migration.PREVIOUS_BODY_SHA256 in statements[-1]


def test_fixed_44_grant_profile_only_adds_writer_select_insert():
    previous, additions = profile._plans("demo_writer", "demo_custody", "demo_frame")
    assert len(previous) == 42 and len(additions) == 2
    assert {(g.kind, g.target, g.rights, g.login) for g in additions} == {
        ("TABLE", table, ("SELECT", "INSERT"), "demo_writer")
        for table in catalog.TABLES
    }
    assert profile.current57_native_g12_grant_sql_v1(
        writer="demo_writer", custody="demo_custody", frame_reader="demo_frame"
    ) == (
        'GRANT SELECT, INSERT ON TABLE public.g12_invocation_claims TO "demo_writer"',
        'GRANT SELECT, INSERT ON TABLE public.g12_invocation_publications TO "demo_writer"',
    )


@pytest.mark.parametrize(
    "names",
    [
        ("w", "w", "f"),
        ("public", "c", "f"),
        ("pg_owner", "c", "f"),
        ('w";DROP ROLE x;', "c", "f"),
    ],
)
def test_no_arbitrary_or_duplicate_grant_role(names):
    with pytest.raises(ValueError):
        profile._plans(*names)


@pytest.mark.parametrize(
    "actual, expected",
    [
        ("CHECK ((environment)::text = 'demo'::text)", "environment = 'demo'"),
        (
            "CHECK ((octet_length(claim_json) >= 1) AND (octet_length(claim_json) <= 8192))",
            "octet_length(claim_json) BETWEEN 1 AND 8192",
        ),
        (
            "CHECK (((event_key)::text ~ '^[a-f0-9]{64}$'::text) AND ((invocation_id)::text ~ '^[a-f0-9]{32}$'::text))",
            "invocation_id ~ '^[a-f0-9]{32}$' AND event_key ~ '^[a-f0-9]{64}$'",
        ),
    ],
)
def test_predicate_accepts_only_equivalent_parentheses_cast_and_conjunction(
    actual, expected
):
    assert catalog._predicate(actual) == catalog._predicate(expected)


@pytest.mark.parametrize(
    "actual",
    [
        "environment = 'demo' OR true",
        "NOT environment = 'live'",
        "environment::integer = 1",
        "octet_length(claim_json) <= 8192; SELECT 1",
        "environment = 'demo' -- comment",
        "custom_length(claim_json) >= 1",
    ],
)
def test_predicate_refuses_unknown_sql_semantics(actual):
    with pytest.raises(catalog.G12Catalog0057Error):
        catalog._predicate(actual)


def _function(name, body, oid, config, public=False):
    owner = 101
    return {
        "oid": oid,
        "name": name,
        "namespace": "public",
        "owner": owner,
        "arguments": "",
        "returns": "trigger",
        "language": "plpgsql",
        "security_definer": False,
        "kind": "f",
        "volatility": "v",
        "parallel": "u",
        "strict": False,
        "leakproof": False,
        "returns_set": False,
        "defaults": 0,
        "support_zero": True,
        "config": config,
        "source": body,
        "definition": "synthetic fixture definition",
        "acl_valid": True,
        "acl": ([[0, owner, "EXECUTE", False]] if public else [])
        + [[owner, owner, "EXECUTE", False]],
    }


def _fixture():
    bodies = _old_bodies()
    immutable_tree = ast.parse(
        (ROOT / "migrations/versions/0017_qualification_reservations.py").read_text()
    )
    immutable = next(
        node.value.split("$$")[1]
        for node in ast.walk(immutable_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "CREATE FUNCTION qualification_ledger_immutable" in node.value
    )
    owner, writer = 101, 201
    value = {
        "head": ["0057"],
        "functions": {
            "schema": catalog.SCHEMA,
            "functions": [
                _function(
                    "g12_invocation_claim_guard",
                    bodies["g12_invocation_claim_guard"],
                    301,
                    ["search_path=pg_catalog, pg_temp"],
                ),
                _function(
                    "g12_invocation_publication_guard",
                    _migration().PUBLICATION_BODY,
                    302,
                    ["search_path=pg_catalog, pg_temp"],
                ),
                _function("qualification_ledger_immutable", immutable, 303, None, True),
            ],
        },
        "tables": [],
        "ri": [],
    }
    for ti, table in enumerate(catalog.TABLES):
        constraints = [
            {
                "name": name,
                "kind": "c",
                "validated": True,
                "deferrable": False,
                "deferred": False,
                "definition": "CHECK (" + sql + ")",
                "columns": [],
                "parent": None,
                "parent_columns": None,
                "update": " ",
                "delete": " ",
                "match": " ",
            }
            for name, sql in catalog._CHECKS[table].items()
        ]
        for name, kind, columns in [("pk_" + table, "p", ["invocation_id"])] + (
            [
                (
                    "uq_g12_claim_uid_event",
                    "u",
                    ["environment", "account_uid_sha256", "event_key"],
                )
            ]
            if ti == 0
            else [(catalog._FK_NAME, "f", ["invocation_id"])]
        ):
            constraints.append(
                {
                    "name": name,
                    "kind": kind,
                    "validated": True,
                    "deferrable": False,
                    "deferred": False,
                    "definition": "synthetic key definition",
                    "columns": columns,
                    "parent": catalog.TABLES[0] if kind == "f" else None,
                    "parent_columns": ["invocation_id"] if kind == "f" else None,
                    "update": "a" if kind == "f" else " ",
                    "delete": "r" if kind == "f" else " ",
                    "match": "s" if kind == "f" else " ",
                }
            )
        indexes = []
        for c in constraints:
            if c["kind"] not in {"p", "u"}:
                continue
            indexes.append(
                {
                    "name": c["name"],
                    "owner": owner,
                    "method": "btree",
                    "unique": True,
                    "primary": c["kind"] == "p",
                    "valid": True,
                    "ready": True,
                    "live": True,
                    "immediate": True,
                    "exclusion": False,
                    "natts": len(c["columns"]),
                    "keyatts": len(c["columns"]),
                    "expressions": None,
                    "predicate": None,
                    "definition": "synthetic index definition",
                    "columns": c["columns"],
                }
            )
        triggers = []
        for name, kind, function in [
            (
                "g12_claim_guard" if ti == 0 else "g12_publication_guard",
                7,
                "g12_invocation_claim_guard"
                if ti == 0
                else "g12_invocation_publication_guard",
            ),
            (table + "_immutable", 27, "qualification_ledger_immutable"),
            (table + "_no_truncate", 34, "qualification_ledger_immutable"),
        ]:
            triggers.append(
                {
                    "name": name,
                    "kind": kind,
                    "enabled": "O",
                    "args": "",
                    "nargs": 0,
                    "attrs": "",
                    "qual": None,
                    "constraint": 0,
                    "deferrable": False,
                    "deferred": False,
                    "old_table": None,
                    "new_table": None,
                    "function": function,
                    "namespace": "public",
                    "owner": owner,
                    "arity": 0,
                    "definition": "synthetic trigger definition",
                }
            )
        value["tables"].append(
            {
                "name": table,
                "oid": 401 + ti,
                "owner": owner,
                "kind": "r",
                "persistence": "p",
                **{
                    k: False
                    for k in (
                        "partition",
                        "rls",
                        "force_rls",
                        "inherited",
                        "rules",
                        "policies",
                        "column_acl",
                    )
                },
                "acl_valid": True,
                "acl": [[owner, owner, r, False] for r in sorted(helpers._RIGHTS)]
                + [[writer, owner, r, False] for r in ("SELECT", "INSERT")],
                "columns": [
                    [
                        name,
                        type_name,
                        True,
                        "",
                        "",
                        "clock_timestamp()" if name.startswith("db_") else None,
                        100
                        if type_name.startswith("character") or type_name == "text"
                        else 0,
                    ]
                    for name, type_name in catalog._COLUMNS[table]
                ],
                "constraints": constraints,
                "indexes": indexes,
                "triggers": triggers,
            }
        )
    for i, (ti, function, kind) in enumerate(
        [
            (0, "RI_FKey_restrict_del", 9),
            (0, "RI_FKey_noaction_upd", 17),
            (1, "RI_FKey_check_ins", 5),
            (1, "RI_FKey_check_upd", 17),
        ]
    ):
        value["ri"].append(
            {
                "table": catalog.TABLES[ti],
                "other": catalog.TABLES[1 - ti],
                "name": f"RI_ConstraintTrigger_synthetic_{i}",
                "oid": 501 + i,
                "constraint_oid": 601,
                "constraint_name": catalog._FK_NAME,
                "function": function,
                "namespace": "pg_catalog",
                "enabled": "O",
                "type": kind,
                "deferrable": False,
                "deferred": False,
                "args": "",
                "nargs": 0,
                "attrs": "",
                "qual": None,
                "old_table": None,
                "new_table": None,
                "builtin": True,
            }
        )
    return value


def test_catalog_is_exact0057_and_observation_never_authorizes():
    observed = catalog.observe_catalog_0057(_fixture(), owner=101, writer=201)
    assert observed["database_head"] == "0057" and observed["admission"] == "DENY"
    assert observed["startup_admission"] is observed["execution_authority"] is False
    assert len(observed["catalog_sha256"]) == 64


@pytest.mark.parametrize(
    "kind",
    [
        "old_head",
        "unknown_head",
        "function_body",
        "security_definer",
        "function_acl",
        "function_path",
        "missing_table",
        "table_acl",
        "column_acl",
        "nullable",
        "changed_default",
        "missing_constraint",
        "weaker_bound",
        "changed_fk",
        "disabled_guard",
        "missing_guard",
        "disabled_ri",
        "fake_builtin",
        "missing_index",
        "nonunique",
    ],
)
def test_catalog_refuses_schema_acl_guard_and_identity_drift(kind):
    value = copy.deepcopy(_fixture())
    table = value["tables"][0]
    if kind == "old_head":
        value["head"] = ["0056"]
    elif kind == "unknown_head":
        value["head"] = ["0058"]
    elif kind == "function_body":
        value["functions"]["functions"][1]["source"] += "\n"
    elif kind == "security_definer":
        value["functions"]["functions"][1]["security_definer"] = True
    elif kind == "function_acl":
        value["functions"]["functions"][1]["acl"].insert(0, [0, 101, "EXECUTE", False])
    elif kind == "function_path":
        value["functions"]["functions"][1]["config"] = None
    elif kind == "missing_table":
        value["tables"].pop()
    elif kind == "table_acl":
        table["acl"].append([201, 101, "UPDATE", False])
    elif kind == "column_acl":
        table["column_acl"] = True
    elif kind == "nullable":
        table["columns"][0][2] = False
    elif kind == "changed_default":
        table["columns"][-1][5] = "now()"
    elif kind == "missing_constraint":
        table["constraints"].pop()
    elif kind == "weaker_bound":
        table["constraints"][2]["definition"] = (
            "CHECK (octet_length(claim_json) BETWEEN 0 AND 8192)"
        )
    elif kind == "changed_fk":
        value["tables"][1]["constraints"][-1]["delete"] = "c"
    elif kind == "disabled_guard":
        table["triggers"][0]["enabled"] = "D"
    elif kind == "missing_guard":
        table["triggers"].pop()
    elif kind == "disabled_ri":
        value["ri"][0]["enabled"] = "D"
    elif kind == "fake_builtin":
        value["ri"][0]["builtin"] = False
    elif kind == "missing_index":
        table["indexes"].pop()
    elif kind == "nonunique":
        table["indexes"][0]["unique"] = False
    with pytest.raises(catalog.G12Catalog0057Error):
        catalog.observe_catalog_0057(value, owner=101, writer=201)
