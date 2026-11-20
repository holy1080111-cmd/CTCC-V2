"""Exact physical58 report metadata; the frozen55 observer stays untouched.

The actual58 outer graph selects this DATA validator. It checks unchanged
report tables/indexes/ACLs and the exact new eligible-function body, without
rewriting observed rows or projecting a database head.
"""

from app.domain import native_closed_report_catalog_v55 as ancestor
from app.domain import native_protective_report_schema_v58 as current

schema = ancestor.schema
helpers = ancestor.helpers
SCHEMA = ancestor.SCHEMA
CATALOG_SQL = ancestor.CATALOG_SQL
TABLE_KEYS = ancestor.TABLE_KEYS
CONSTRAINT_KEYS = ancestor.CONSTRAINT_KEYS
INDEX_KEYS = ancestor.INDEX_KEYS
TRIGGER_KEYS = ancestor.TRIGGER_KEYS
_COLUMNS = ancestor._COLUMNS
_CONSTRAINTS = ancestor._CONSTRAINTS
_INDEXES = ancestor._INDEXES
_RIGHTS = ancestor._RIGHTS
_deny = ancestor._deny


def observe_catalog(value, *, owner, actor, owner_mode):
    legacy = False  # Physical58 observes the installed native report policy only.
    schema.statements()
    if (
        type(owner) is not int
        or not 0 < owner < 2**32
        or type(actor) is not int
        or not 0 < actor < 2**32
        or type(value) is not dict
        or set(value) != {"functions", "tables"}
        or type(owner_mode) is not bool
        or type(legacy) is not bool
    ):
        _deny()
    functions = value["functions"]
    if (
        type(functions) is not dict
        or set(functions) != {"schema", "functions"}
        or functions["schema"] != SCHEMA
    ):
        _deny()
    specs = tuple(
        (
            name,
            args,
            returns,
            current.ELIGIBLE_SQL
            if name == "qualification_native_report_eligible_v55"
            else sql,
            delimiter,
            public,
        )
        for name, args, returns, sql, delimiter, public in schema.FUNCTIONS
    )
    expected = {
        name: (args, returns, sql.split(delimiter)[1], public)
        for name, args, returns, sql, delimiter, public in specs
    }
    rows = functions["functions"]
    if type(rows) is not list or len(rows) != len(expected):
        _deny()
    definitions = {}
    seen_oids = set()
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != helpers.FUNCTION_KEYS
            or row["name"] not in expected
            or row["name"] in definitions
        ):
            _deny()
        args, returns, body, public = expected[row["name"]]
        acl = [[owner, owner, "EXECUTE", False]]
        if public:
            acl.insert(0, [0, owner, "EXECUTE", False])
        config = ["search_path=pg_catalog, pg_temp"]
        if (
            type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["oid"] in seen_oids
            or row["namespace"] != "public"
            or row["owner"] != owner
            or row["arguments"] != args
            or row["returns"] != returns
            or row["language"] != "plpgsql"
            or row["security_definer"] is not False
            or row["kind"] != "f"
            or row["volatility"] != "v"
            or row["parallel"] != "u"
            or row["strict"] is not False
            or row["leakproof"] is not False
            or row["returns_set"] is not False
            or type(row["defaults"]) is not int
            or row["defaults"] != 0
            or row["support_zero"] is not True
            or row["config"] != config
            or row["source"] != body
            or row["acl_valid"] is not True
            or row["acl"] != acl
            or type(row["definition"]) is not str
            or not row["definition"]
        ):
            _deny()
        seen_oids.add(row["oid"])
        definitions[row["name"]] = row["definition"]
    tables = value["tables"]
    if (
        type(tables) is not list
        or len(tables) != 2
        or {row["name"] for row in tables} != set(_COLUMNS)
    ):
        _deny()
    normalized = {}
    for table in tables:
        if type(table) is not dict or set(table) != TABLE_KEYS:
            _deny()
        name = table["name"]
        if (
            type(table["oid"]) is not int
            or not 0 < table["oid"] < 2**32
            or table["owner"] != owner
            or table["kind"] != "r"
            or table["persistence"] != "p"
            or table["acl_valid"] is not True
            or any(
                table[key] is not False
                for key in (
                    "partition",
                    "rls",
                    "force_rls",
                    "inherited",
                    "rules",
                    "policies",
                    "column_acl",
                )
            )
        ):
            _deny()
        owner_acl = {(owner, owner, right, False) for right in _RIGHTS}
        actual_acl = {tuple(row) for row in table["acl"]}
        expected_acl = owner_acl
        if not owner_mode:
            expected_acl |= {
                (actor, owner, right, False) for right in ("SELECT", "INSERT")
            }
        if len(actual_acl) != len(table["acl"]) or actual_acl != expected_acl:
            _deny()
        columns = table["columns"]
        if (
            type(columns) is not list
            or any(type(row) is not list or len(row) != 7 for row in columns)
            or [tuple(row[:2]) for row in columns] != _COLUMNS[name]
            or any(
                row[2:6] != [True, "", "", None]
                or type(row[6]) is not int
                or row[6] < 0
                or (
                    (row[1].startswith("character varying") or row[1] == "text")
                    != (row[6] > 0)
                )
                for row in columns
            )
        ):
            _deny()
        constraints = table["constraints"]
        if (
            type(constraints) is not list
            or len(constraints) != len(_CONSTRAINTS[name])
            or {row["name"] for row in constraints} != set(_CONSTRAINTS[name])
        ):
            _deny()
        for row in constraints:
            if type(row) is not dict or set(row) != CONSTRAINT_KEYS:
                _deny()
            kind, columns, parent, parent_columns = _CONSTRAINTS[name][row["name"]]
            if (
                row["kind"] != kind
                or row["validated"] is not True
                or row["deferrable"] is not False
                or row["deferred"] is not False
                or not row["definition"]
            ):
                _deny()
            actual_parent = (
                row["parent"].removeprefix("public.")
                if type(row["parent"]) is str
                else row["parent"]
            )
            if kind != "c" and (
                row["columns"],
                actual_parent,
                row["parent_columns"],
            ) != (columns, parent, parent_columns):
                _deny()
            if kind == "f" and (row["update"], row["delete"], row["match"]) != (
                "a",
                "r",
                "s",
            ):
                _deny()
            if row["name"] in {
                "ck_qualification_report_spool_transport_unverified",
                "ck_qualification_report_projection_receipts_transport_unverifie",
            }:
                expected_call = (
                    "qualification_native_report_eligible_v55"
                    if name == "qualification_report_spool"
                    else "qualification_native_projection_eligible_v55"
                )
                if legacy:
                    if row["definition"] not in (
                        {"CHECK (eligible = false)", "CHECK ((eligible = false))"}
                        if name == "qualification_report_spool"
                        else {"CHECK (false)"}
                    ):
                        _deny()
                elif expected_call not in row["definition"]:
                    _deny()
        indexes = table["indexes"]
        if (
            type(indexes) is not list
            or len(indexes) != len(_INDEXES[name])
            or {row["name"] for row in indexes} != set(_INDEXES[name])
        ):
            _deny()
        for row in indexes:
            if type(row) is not dict or set(row) != INDEX_KEYS:
                _deny()
            unique, primary, columns, predicate = _INDEXES[name][row["name"]]
            if (
                row["owner"] != owner
                or row["method"] != "btree"
                or row["unique"] is not unique
                or row["primary"] is not primary
                or any(
                    row[key] is not True
                    for key in ("valid", "ready", "live", "immediate")
                )
                or row["exclusion"] is not False
                or row["natts"] != len(columns)
                or row["keyatts"] != len(columns)
                or row["expressions"] is not None
                or row["columns"] != columns
                or row["predicate"] != predicate
                or not row["definition"]
            ):
                _deny()
        expected_triggers = {
            name + "_immutable": (27, "qualification_ledger_immutable"),
            "qualification_spool_binding_guard"
            if name == "qualification_report_spool"
            else "qualification_projection_binding_guard": (
                7,
                "qualification_spool_binding"
                if name == "qualification_report_spool"
                else "qualification_projection_binding",
            ),
        }
        triggers = table["triggers"]
        if (
            type(triggers) is not list
            or len(triggers) != 2
            or {row["name"] for row in triggers} != set(expected_triggers)
        ):
            _deny()
        for row in triggers:
            if type(row) is not dict or set(row) != TRIGGER_KEYS:
                _deny()
            if (
                (row["kind"], row["function"]) != expected_triggers[row["name"]]
                or row["enabled"] != "O"
                or row["args"] != ""
                or row["nargs"] != 0
                or row["attrs"] != ""
                or row["qual"] is not None
                or row["constraint"] != 0
                or row["deferrable"] is not False
                or row["deferred"] is not False
                or row["old_table"] is not None
                or row["new_table"] is not None
                or row["namespace"] != "public"
                or row["owner"] != owner
                or row["arity"] != 0
                or not row["definition"]
            ):
                _deny()
        # Exact ACLs and relation/function owners are validated above. Keep
        # database-local role OIDs out of the shared definitions fingerprint.
        normalized[name] = {
            "columns": table["columns"],
            "constraints": constraints,
            "indexes": [
                {key: value for key, value in row.items() if key != "owner"}
                for row in indexes
            ],
            "triggers": [
                {key: value for key, value in row.items() if key != "owner"}
                for row in triggers
            ],
        }
    return {"functions": definitions, "tables": normalized}
