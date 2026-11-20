"""Strict DB0057 G12 schema observation; supplies no execution authority."""

from __future__ import annotations

import hashlib
import re

from app.domain import native_closed_report_catalog_v55 as table_helpers
from app.domain import native_terminal_catalog_v55 as function_helpers
from app.domain.source_primitives import canonical

SCHEMA = "ctcc.g12_custody0057.actual_catalog.v1"
TABLES = ("g12_invocation_claims", "g12_invocation_publications")
CLAIM_BODY_SHA256 = "2d3b6f48acebd783c10ad31438da9c82dc219cba7b0967e0f311b5d03fbbd449"
PUBLICATION_BODY_SHA256 = (
    "ecf3775f2c78f6fee3a70a06ce0036d037a3b29a46f96c51ddd3263ea5aeab97"
)
IMMUTABLE_BODY_SHA256 = (
    "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649"
)
_OLD_FUNCTIONS = "'qualification_native_plain_v55','qualification_native_canonical_v55','qualification_native_manifest_v55',\n 'qualification_native_rows_v55','qualification_native_number_v55','qualification_native_clock_v55','qualification_native_filled_flat_data_v55'"
_NEW_FUNCTIONS = "'g12_invocation_claim_guard','g12_invocation_publication_guard','qualification_ledger_immutable'"
_FUNCTION_SQL = function_helpers.CATALOG_SQL.replace(
    function_helpers.SCHEMA, SCHEMA
).replace(_OLD_FUNCTIONS, _NEW_FUNCTIONS)
_TABLE_SQL = table_helpers.TABLE_CATALOG_SQL.replace(
    "'qualification_report_spool','qualification_report_projection_receipts'",
    "'g12_invocation_claims','g12_invocation_publications'",
)
_RI_SQL = """
SELECT coalesce(jsonb_agg(jsonb_build_object(
 'table',c.relname,'other',o.relname,'name',t.tgname,'oid',t.oid::bigint,
 'constraint_oid',t.tgconstraint::bigint,'constraint_name',k.conname,
 'function',p.proname,'namespace',pn.nspname,'enabled',t.tgenabled::text,
 'type',t.tgtype::int,'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,
 'args',encode(t.tgargs,'hex'),'nargs',t.tgnargs,'attrs',t.tgattr::text,
 'qual',pg_get_expr(t.tgqual,t.tgrelid),'old_table',t.tgoldtable,'new_table',t.tgnewtable,
 'builtin',p.oid=pg_catalog.to_regprocedure('pg_catalog.'||quote_ident(p.proname)||'()')
) ORDER BY c.relname,p.proname),'[]'::jsonb)
FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_class o ON o.oid=t.tgconstrrelid
JOIN pg_catalog.pg_constraint k ON k.oid=t.tgconstraint
JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
WHERE n.nspname='public' AND c.relname IN('g12_invocation_claims','g12_invocation_publications')
 AND t.tgisinternal
"""
CATALOG_SQL = (
    "SELECT jsonb_build_object('head',(SELECT jsonb_agg(version_num::text) FROM public.alembic_version),"
    "'functions',("
    + _FUNCTION_SQL
    + "),'tables',("
    + _TABLE_SQL
    + "),'ri',("
    + _RI_SQL
    + "))"
)
_COLUMNS = {
    TABLES[0]: (
        ("invocation_id", "character varying(32)"),
        ("environment", "character varying(8)"),
        ("account_uid_sha256", "character varying(64)"),
        ("event_key", "character varying(64)"),
        ("claim_json", "text"),
        ("claim_sha256", "character varying(64)"),
        ("db_claimed_at", "timestamp with time zone"),
    ),
    TABLES[1]: (
        ("invocation_id", "character varying(32)"),
        ("claim_sha256", "character varying(64)"),
        ("publication_json", "text"),
        ("publication_sha256", "character varying(64)"),
        ("db_recorded_at", "timestamp with time zone"),
    ),
}
_CHECKS = {
    TABLES[0]: {
        "ck_g12_invocation_claims_demo_only": "environment = 'demo'",
        "ck_g12_invocation_claims_identity": (
            "invocation_id ~ '^[a-f0-9]{32}$' AND "
            "account_uid_sha256 ~ '^[a-f0-9]{64}$' AND "
            "event_key ~ '^[a-f0-9]{64}$' AND claim_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        "ck_g12_invocation_claims_bounded": "octet_length(claim_json) BETWEEN 1 AND 8192",
    },
    TABLES[1]: {
        "ck_g12_invocation_publications_hashes": (
            "claim_sha256 ~ '^[a-f0-9]{64}$' AND publication_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        "ck_g12_invocation_publications_bounded": "octet_length(publication_json) BETWEEN 1 AND 8192",
    },
}
_FK_NAME = "fk_g12_invocation_publications_invocation_id_g12_invocation_cla"
_TOKEN = re.compile(
    r"\s*(?:(::|>=|<=|=|~|\(|\))|([a-z_][a-z0-9_]*)|([0-9]+)|'((?:''|[^'])*)')",
    re.IGNORECASE,
)


class G12Catalog0057Error(ValueError):
    """Fixed refusal without database, role or raw catalog details."""


def _need(value):
    if value is not True:
        raise G12Catalog0057Error("g12_catalog0057_changed") from None


def _predicate(sql):
    """Parse only conjunctions of the fixed simple CHECK predicates.

    Parentheses and varchar-to-text casts are semantic here. Unknown operators,
    functions, casts, OR, negation and trailing text are refused.
    """
    _need(type(sql) is str and 0 < len(sql) < 4096)
    tokens = []
    pos = 0
    while pos < len(sql.rstrip()):
        match = _TOKEN.match(sql, pos)
        _need(match is not None)
        symbol, name, number, literal = match.groups()
        tokens.append(
            ("literal", literal.replace("''", "'"))
            if literal is not None
            else ("number", int(number))
            if number is not None
            else ("name", name.lower())
            if name is not None
            else ("symbol", symbol)
        )
        pos = match.end()
    offset = 0

    def accept(text):
        nonlocal offset
        if offset < len(tokens) and tokens[offset][1] == text:
            offset += 1
            return True
        return False

    def scalar():
        nonlocal offset
        _need(offset < len(tokens))
        token = tokens[offset]
        offset += 1
        if token == ("symbol", "("):
            result = conjunction()
            _need(accept(")"))
        elif token[0] in {"literal", "number"}:
            result = token
        else:
            _need(
                token[0] == "name"
                and token[1] not in {"and", "or", "not", "between", "check"}
            )
            if accept("("):
                _need(token[1] == "octet_length")
                argument = scalar()
                _need(argument[0] == "name" and accept(")"))
                result = ("octet_length", argument)
            else:
                result = token
        if accept("::"):
            _need(accept("text") and result[0] in {"name", "literal"})
        return result

    def comparison():
        left = scalar()
        if accept("between"):
            lower = scalar()
            _need(accept("and"))
            upper = scalar()
            return ("and", (">=", left, lower), ("<=", left, upper))
        for operator in ("=", "~", ">=", "<="):
            if accept(operator):
                return (operator, left, scalar())
        return left

    def conjunction():
        parts = [comparison()]
        while accept("and"):
            parts.append(comparison())
        return parts[0] if len(parts) == 1 else ("and", *parts)

    accept("check")
    expression = conjunction()
    _need(offset == len(tokens))

    def normalized(node):
        if node[0] != "and":
            _need(node[0] in {"=", "~", ">=", "<="})
            return [node]
        return [part for child in node[1:] for part in normalized(child)]

    return sorted(normalized(expression), key=repr)


def _functions(value, owner):
    _need(
        type(value) is dict
        and set(value) == {"schema", "functions"}
        and value["schema"] == SCHEMA
    )
    rows = value["functions"]
    _need(type(rows) is list and len(rows) == 3)
    expected = {
        "g12_invocation_claim_guard": (
            CLAIM_BODY_SHA256,
            ["search_path=pg_catalog, pg_temp"],
            False,
        ),
        "g12_invocation_publication_guard": (
            PUBLICATION_BODY_SHA256,
            ["search_path=pg_catalog, pg_temp"],
            False,
        ),
        "qualification_ledger_immutable": (IMMUTABLE_BODY_SHA256, None, True),
    }
    seen = set()
    oids = set()
    for row in rows:
        _need(type(row) is dict and set(row) == function_helpers.FUNCTION_KEYS)
        name = row["name"]
        _need(type(name) is str and name in expected and name not in seen)
        body, config, public = expected[name]
        acl = [[owner, owner, "EXECUTE", False]]
        if public:
            acl.insert(0, [0, owner, "EXECUTE", False])
        _need(
            type(row["oid"]) is int
            and 0 < row["oid"] < 2**32
            and row["oid"] not in oids
            and row["owner"] == owner
            and row["namespace"] == "public"
            and row["arguments"] == ""
            and row["returns"] == "trigger"
            and row["language"] == "plpgsql"
            and row["security_definer"] is False
            and row["kind"] == "f"
            and row["volatility"] == "v"
            and row["parallel"] == "u"
            and row["strict"] is False
            and row["leakproof"] is False
            and row["returns_set"] is False
            and type(row["defaults"]) is int
            and row["defaults"] == 0
            and row["support_zero"] is True
            and row["config"] == config
            and type(row["source"]) is str
            and hashlib.sha256(row["source"].encode()).hexdigest() == body
            and type(row["definition"]) is str
            and bool(row["definition"])
            and row["acl_valid"] is True
            and row["acl"] == acl
        )
        seen.add(name)
        oids.add(row["oid"])


def _constraints(rows, table):
    checks = _CHECKS[table]
    primary = "pk_" + table
    unique = "uq_g12_claim_uid_event"
    expected = (
        set(checks) | {primary} | ({unique} if table == TABLES[0] else {_FK_NAME})
    )
    _need(type(rows) is list and len(rows) == len(expected))
    seen = set()
    for row in rows:
        _need(
            type(row) is dict
            and set(row)
            == {
                "name",
                "kind",
                "validated",
                "deferrable",
                "deferred",
                "definition",
                "columns",
                "parent",
                "parent_columns",
                "update",
                "delete",
                "match",
            }
        )
        name = row["name"]
        _need(
            name in expected
            and name not in seen
            and row["validated"] is True
            and row["deferrable"] is False
            and row["deferred"] is False
        )
        if name in checks:
            _need(
                row["kind"] == "c"
                and _predicate(row["definition"]) == _predicate(checks[name])
            )
        else:
            kind = "p" if name == primary else "u" if name == unique else "f"
            columns = (
                ["environment", "account_uid_sha256", "event_key"]
                if name == unique
                else ["invocation_id"]
            )
            _need(row["kind"] == kind and row["columns"] == columns)
        if name == _FK_NAME:
            _need(
                row["parent"] == TABLES[0]
                and row["parent_columns"] == ["invocation_id"]
                and (row["update"], row["delete"], row["match"]) == ("a", "r", "s")
            )
        else:
            _need(row["parent"] is None and row["parent_columns"] is None)
        seen.add(name)


def _indexes(rows, table, owner):
    expected = {"pk_" + table: (True, ["invocation_id"])}
    if table == TABLES[0]:
        expected["uq_g12_claim_uid_event"] = (
            False,
            ["environment", "account_uid_sha256", "event_key"],
        )
    _need(type(rows) is list and len(rows) == len(expected))
    seen = set()
    for row in rows:
        _need(
            type(row) is dict
            and row.get("name") in expected
            and row["name"] not in seen
        )
        primary, columns = expected[row["name"]]
        _need(
            row["owner"] == owner
            and row["method"] == "btree"
            and row["primary"] is primary
            and all(
                row[k] is True
                for k in ("unique", "valid", "ready", "live", "immediate")
            )
            and row["exclusion"] is False
            and row["natts"] == row["keyatts"] == len(columns)
            and row["expressions"] is row["predicate"] is None
            and row["columns"] == columns
        )
        seen.add(row["name"])


def _triggers(rows, table, owner):
    expected = {
        ("g12_claim_guard" if table == TABLES[0] else "g12_publication_guard"): (
            7,
            "g12_invocation_claim_guard"
            if table == TABLES[0]
            else "g12_invocation_publication_guard",
        ),
        table + "_immutable": (27, "qualification_ledger_immutable"),
        table + "_no_truncate": (34, "qualification_ledger_immutable"),
    }
    _need(type(rows) is list and len(rows) == 3)
    seen = set()
    for row in rows:
        _need(
            type(row) is dict
            and row.get("name") in expected
            and row["name"] not in seen
        )
        kind, function = expected[row["name"]]
        _need(
            row["kind"] == kind
            and row["enabled"] == "O"
            and row["function"] == function
            and row["namespace"] == "public"
            and row["owner"] == owner
            and row["arity"] == 0
            and row["constraint"] == 0
            and row["deferrable"] is row["deferred"] is False
            and row["args"] == row["attrs"] == ""
            and row["nargs"] == 0
            and row["qual"] is row["old_table"] is row["new_table"] is None
        )
        seen.add(row["name"])


def _ri(rows):
    expected = {
        (TABLES[0], "RI_FKey_restrict_del"): (TABLES[1], 9),
        (TABLES[0], "RI_FKey_noaction_upd"): (TABLES[1], 17),
        (TABLES[1], "RI_FKey_check_ins"): (TABLES[0], 5),
        (TABLES[1], "RI_FKey_check_upd"): (TABLES[0], 17),
    }
    _need(type(rows) is list and len(rows) == 4)
    seen = set()
    oids = set()
    constraints = set()
    for row in rows:
        _need(type(row) is dict)
        key = (row["table"], row["function"])
        _need(key in expected and key not in seen)
        other, kind = expected[key]
        _need(
            row["other"] == other
            and row["type"] == kind
            and row["enabled"] == "O"
            and row["namespace"] == "pg_catalog"
            and row["builtin"] is True
            and row["constraint_name"] == _FK_NAME
            and row["deferrable"] is row["deferred"] is False
            and row["args"] == row["attrs"] == ""
            and row["nargs"] == 0
            and row["qual"] is row["old_table"] is row["new_table"] is None
            and type(row["oid"]) is int
            and 0 < row["oid"] < 2**32
            and row["oid"] not in oids
            and type(row["constraint_oid"]) is int
            and 0 < row["constraint_oid"] < 2**32
        )
        seen.add(key)
        oids.add(row["oid"])
        constraints.add(row["constraint_oid"])
    _need(len(constraints) == 1)


def observe_catalog_0057(value, *, owner, writer):
    """Validate actual metadata; hashes and expected values never grant authority."""
    try:
        _need(
            type(owner) is type(writer) is int
            and 0 < owner < 2**32
            and 0 < writer < 2**32
            and owner != writer
        )
        _need(
            type(value) is dict
            and set(value) == {"head", "functions", "tables", "ri"}
            and value["head"] == ["0057"]
        )
        _functions(value["functions"], owner)
        rows = value["tables"]
        _need(type(rows) is list and len(rows) == 2)
        seen = set()
        oids = set()
        for row in rows:
            _need(type(row) is dict and set(row) == table_helpers.TABLE_KEYS)
            name = row["name"]
            _need(name in TABLES and name not in seen)
            _need(
                type(row["oid"]) is int
                and 0 < row["oid"] < 2**32
                and row["oid"] not in oids
                and row["owner"] == owner
                and row["kind"] == "r"
                and row["persistence"] == "p"
                and all(
                    row[k] is False
                    for k in (
                        "partition",
                        "rls",
                        "force_rls",
                        "inherited",
                        "rules",
                        "policies",
                        "column_acl",
                    )
                )
                and row["acl_valid"] is True
            )
            acl = {(owner, owner, right, False) for right in table_helpers._RIGHTS} | {
                (writer, owner, right, False) for right in ("SELECT", "INSERT")
            }
            _need(
                type(row["acl"]) is list
                and len(row["acl"]) == len(acl)
                and {tuple(a) for a in row["acl"]} == acl
            )
            columns = row["columns"]
            _need(type(columns) is list and len(columns) == len(_COLUMNS[name]))
            for column, (field, type_name) in zip(columns, _COLUMNS[name], strict=True):
                default = "clock_timestamp()" if field.startswith("db_") else None
                _need(
                    type(column) is list
                    and len(column) == 7
                    and column[:6] == [field, type_name, True, "", "", default]
                    and type(column[6]) is int
                )
                _need(
                    column[6] > 0
                    if type_name.startswith("character") or type_name == "text"
                    else column[6] == 0
                )
            _constraints(row["constraints"], name)
            _indexes(row["indexes"], name, owner)
            _triggers(row["triggers"], name, owner)
            seen.add(name)
            oids.add(row["oid"])
        _ri(value["ri"])
        return {
            "schema": "ctcc.g12_custody0057.observation.v1",
            "database_head": "0057",
            "catalog_sha256": hashlib.sha256(canonical(value)).hexdigest(),
            "publication_body_sha256": PUBLICATION_BODY_SHA256,
            "execution_authority": False,
            "startup_admission": False,
            "admission": "DENY",
        }
    except G12Catalog0057Error:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise G12Catalog0057Error("g12_catalog0057_changed") from None
