"""Actual finite current55 configuration catalog, without native admission.

All new physical objects are observed. Simple CHECK atoms use only finite
PostgreSQL IN/BETWEEN deparser equivalences; no boolean expression, field,
operator, literal or function can be omitted. Full actual definitions remain in
the native fingerprint. Original54/old scope predicates are never rewritten.
"""

import hashlib
import re
from collections import Counter

from app.domain import native_closed_report_catalog_v55 as table_source
from app.domain import native_leverage_configuration_enforcement_v55 as enforcement
from app.domain import native_leverage_configuration_schema_v55 as schema
from app.domain import native_terminal_catalog_v55 as function_source

SCHEMA = "ctcc.native_leverage_configuration55.actual_catalog.v1"
DDL_SHA256 = hashlib.sha256("\n".join(schema.statements()).encode()).hexdigest()


def _deny():
    raise ValueError("native_leverage_configuration55_actual_catalog_changed") from None


def _once(value, before, after):
    if value.count(before) != 1:
        _deny()
    return value.replace(before, after, 1)


_FUNCTION_NAMES = ",".join("'" + row[0] + "'" for row in schema.FUNCTION_IDENTITIES)
_FUNCTION_NAMES += ",'" + enforcement.FUNCTION + "'"
_FUNCTION_OLD_NAMES = "'qualification_native_plain_v55','qualification_native_canonical_v55','qualification_native_manifest_v55',\n 'qualification_native_rows_v55','qualification_native_number_v55','qualification_native_clock_v55','qualification_native_filled_flat_data_v55'"
FUNCTION_CATALOG_SQL = _once(
    function_source.CATALOG_SQL, _FUNCTION_OLD_NAMES, _FUNCTION_NAMES
).replace(function_source.SCHEMA, SCHEMA)
TABLE_CATALOG_SQL = _once(
    table_source.TABLE_CATALOG_SQL,
    "IN('qualification_report_spool','qualification_report_projection_receipts')",
    "IN(" + ",".join("'" + name + "'" for name in schema.TABLES) + ")",
)
TABLE_CATALOG_SQL = _once(
    TABLE_CATALOG_SQL,
    "'name',k.conname,'kind',",
    "'oid',k.oid::bigint,'name',k.conname,'kind',",
)
TABLE_CATALOG_SQL = _once(
    TABLE_CATALOG_SQL,
    "'name',ic.relname,'owner',ic.relowner::bigint,'method',am.amname,",
    "'name',ic.relname,'owner',ic.relowner::bigint,'namespace',icn.nspname,'kind',ic.relkind::text,'persistence',ic.relpersistence::text,'method',am.amname,",
)
TABLE_CATALOG_SQL = _once(
    TABLE_CATALOG_SQL,
    "JOIN pg_am am ON am.oid=ic.relam",
    "JOIN pg_namespace icn ON icn.oid=ic.relnamespace JOIN pg_am am ON am.oid=ic.relam",
)
TABLE_CATALOG_SQL = _once(
    TABLE_CATALOG_SQL,
    "'columns',(SELECT jsonb_agg(a.attname ORDER BY s.n) FROM unnest(i.indkey)",
    "'opclasses',(SELECT jsonb_agg(jsonb_build_array(onsp.nspname,op.opcname,format_type(op.opcintype,NULL)) ORDER BY k.ordinality) FROM unnest(i.indclass) WITH ORDINALITY k(opc_oid,ordinality) JOIN pg_opclass op ON op.oid=k.opc_oid JOIN pg_namespace onsp ON onsp.oid=op.opcnamespace),"
    "'collations',(SELECT jsonb_agg(k.collation_oid::bigint ORDER BY k.ordinality) FROM unnest(i.indcollation) WITH ORDINALITY k(collation_oid,ordinality)),"
    "'columns',(SELECT jsonb_agg(a.attname ORDER BY s.n) FROM unnest(i.indkey)",
)
SHARED_TRIGGER_CATALOG_SQL = """
SELECT coalesce(jsonb_agg(jsonb_build_object('name',c.relname,'oid',c.oid::bigint,'owner',c.relowner::bigint,
 'namespace',n.nspname,'kind',c.relkind::text,'persistence',c.relpersistence::text,
 'triggers',(SELECT jsonb_agg(jsonb_build_object('name',t.tgname,'kind',t.tgtype::integer,'enabled',t.tgenabled::text,
  'args',encode(t.tgargs,'hex'),'nargs',t.tgnargs,'attrs',t.tgattr::text,'qual',pg_get_expr(t.tgqual,t.tgrelid),
  'constraint',t.tgconstraint::bigint,'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,'old_table',t.tgoldtable,'new_table',t.tgnewtable,
  'function',p.proname,'namespace',pn.nspname,'owner',p.proowner::bigint,'arity',p.pronargs,
  'definition',pg_get_triggerdef(t.oid,true)) ORDER BY t.tgname) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
  JOIN pg_namespace pn ON pn.oid=p.pronamespace WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
) ORDER BY c.relname),'[]'::jsonb) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN('demo_control_journal','qualification_reservations',
 'qualification_reservation_transitions','qualification_submission_outcomes')
"""
CATALOG_SQL = (
    "SELECT jsonb_build_object('functions',("
    + FUNCTION_CATALOG_SQL
    + "),'tables',("
    + TABLE_CATALOG_SQL
    + "),'shared_triggers',("
    + SHARED_TRIGGER_CATALOG_SQL
    + "))"
)
_SHARED_TRIGGERS = {
    "demo_control_journal": {
        "demo_control_journal_guard": (7, "demo_control_journal_guard"),
        "demo_control_journal_immutable": (27, "qualification_ledger_immutable"),
        "demo_control_journal_no_truncate": (34, "qualification_ledger_immutable"),
    },
    "qualification_reservations": {
        "qualification_reservation_no_delete": (11, "qualification_ledger_immutable"),
        "qualification_reservation_no_truncate": (34, "qualification_ledger_immutable"),
        "qualification_reservation_update_guard": (
            19,
            "qualification_reservation_update",
        ),
    },
    "qualification_reservation_transitions": {
        "qualification_transition_immutable": (27, "qualification_ledger_immutable"),
        "qualification_transition_no_truncate": (34, "qualification_ledger_immutable"),
    },
    "qualification_submission_outcomes": {
        "qualification_submission_outcomes_immutable": (
            27,
            "qualification_ledger_immutable",
        ),
        "qualification_submission_binding_guard": (
            7,
            "qualification_submission_binding",
        ),
    },
}
for _table, (_kind, _) in enforcement.TRIGGERS.items():
    _SHARED_TRIGGERS[_table][enforcement.TRIGGER] = (_kind, enforcement.FUNCTION)

_I, _A, _R = schema.TABLES
_COLUMNS = {
    _I: [
        ("intent_id", "character varying(64)"),
        ("environment", "character varying(8)"),
        ("account_id", "character varying(32)"),
        ("settlement_currency", "character varying(16)"),
        ("account_revision", "bigint"),
        ("previous_ledger_revision", "bigint"),
        ("ledger_revision", "bigint"),
        ("claims_sha256", "character varying(64)"),
        ("control_revision", "bigint"),
        ("control_event_sha256", "character varying(64)"),
        ("core_json", "text"),
        ("source_json", "text"),
        ("source_sha256", "character varying(64)"),
        ("plan_json", "text"),
        ("plan_sha256", "character varying(64)"),
        ("request_json", "text"),
        ("request_sha256", "character varying(64)"),
        ("original_expires_at", "timestamp with time zone"),
        ("phase", "character varying(16)"),
        ("latest_result_sequence", "bigint"),
        ("db_role_oid", "oid"),
        ("db_backend_pid", "integer"),
        ("db_transaction_id", "character varying(20)"),
        ("db_recorded_at", "timestamp with time zone"),
    ],
    _A: [
        ("intent_id", "character varying(64)"),
        ("attempt_id", "character varying(64)"),
        ("environment", "character varying(8)"),
        ("account_id", "character varying(32)"),
        ("settlement_currency", "character varying(16)"),
        ("account_revision", "bigint"),
        ("previous_ledger_revision", "bigint"),
        ("ledger_revision", "bigint"),
        ("attempt_json", "text"),
        ("db_role_oid", "oid"),
        ("db_backend_pid", "integer"),
        ("db_transaction_id", "character varying(20)"),
        ("db_recorded_at", "timestamp with time zone"),
    ],
    _R: [
        ("result_id", "character varying(64)"),
        ("intent_id", "character varying(64)"),
        ("attempt_id", "character varying(64)"),
        ("environment", "character varying(8)"),
        ("account_id", "character varying(32)"),
        ("settlement_currency", "character varying(16)"),
        ("account_revision", "bigint"),
        ("previous_ledger_revision", "bigint"),
        ("ledger_revision", "bigint"),
        ("result_sequence", "bigint"),
        ("previous_phase", "character varying(16)"),
        ("phase", "character varying(16)"),
        ("result_json", "text"),
        ("db_role_oid", "oid"),
        ("db_backend_pid", "integer"),
        ("db_transaction_id", "character varying(20)"),
        ("db_recorded_at", "timestamp with time zone"),
    ],
}
_COMMON_CHECKS = (
    "environment='demo'",
    "settlement_currency='USDT'",
    "account_revision>=0",
    "previous_ledger_revision>=account_revision",
    "ledger_revision=previous_ledger_revision+1",
    "db_backend_pid>0",
    "db_transaction_id ~ '^[0-9]{1,20}$'",
    "isfinite(db_recorded_at)",
)
_CHECKS = {
    _I: _COMMON_CHECKS
    + (
        "intent_id ~ '^[a-f0-9]{64}$'",
        "account_id ~ '^[0-9]{1,32}$'",
        "control_revision>0",
        "control_event_sha256 ~ '^[a-f0-9]{64}$'",
        "octet_length(core_json) BETWEEN 1 AND 16384",
        "octet_length(source_json) BETWEEN 1 AND 1048576",
        "source_sha256 ~ '^[a-f0-9]{64}$'",
        "octet_length(plan_json) BETWEEN 1 AND 32768",
        "plan_sha256 ~ '^[a-f0-9]{64}$'",
        "octet_length(request_json) BETWEEN 1 AND 2048",
        "request_sha256 ~ '^[a-f0-9]{64}$'",
        "isfinite(original_expires_at)",
        "phase IN ('prepared','attempted','verified','unknown','settled','no_change','not_sent')",
        "latest_result_sequence>=0",
    ),
    _A: _COMMON_CHECKS
    + (
        "attempt_id ~ '^[a-f0-9]{64}$'",
        "octet_length(attempt_json) BETWEEN 1 AND 16384",
    ),
    _R: _COMMON_CHECKS
    + (
        "result_id ~ '^[a-f0-9]{64}$'",
        "result_sequence>0",
        "octet_length(result_json) BETWEEN 1 AND 262144",
        "phase IN ('unknown','verified','settled','no_change','not_sent')",
    ),
}
_TOKEN = re.compile(
    r"'(?:[^']|'')*'|::|>=|<=|<>|>|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[(),.=~+\[\]]"
)


def _signature(definition):
    """Exact finite atoms only; quoted literals are never rewritten."""
    if type(definition) is not str or not 0 < len(definition) <= 8192:
        _deny()
    tokens, last = [], 0
    for match in _TOKEN.finditer(definition):
        if definition[last : match.start()].strip():
            _deny()
        token = match.group()
        tokens.append(token if token.startswith("'") else token.lower())
        last = match.end()
    if definition[last:].strip():
        _deny()
    if tokens and tokens[0] == "check":
        tokens = tokens[1:]
    clean, index = [], 0
    while index < len(tokens):
        if tokens[index] == "::":
            index += 1
            if index >= len(tokens) or tokens[index] not in {
                "text",
                "character",
                "bigint",
                "integer",
            }:
                _deny()
            if tokens[index] in {"bigint", "integer"} and (
                not clean or not clean[-1].isdigit()
            ):
                _deny()
            if tokens[index] == "character":
                index += 1
                if index >= len(tokens) or tokens[index] != "varying":
                    _deny()
            index += 1
            if tokens[index : index + 2] == ["[", "]"]:
                index += 2
            continue
        if tokens[index] not in {"(", ")"}:
            clean.append(tokens[index])
        index += 1
    # IN is emitted as equality ANY with the SAME exact ordered string array.
    if len(clean) > 5 and clean[1:5] == ["=", "any", "array", "["] and clean[-1] == "]":
        clean = [clean[0], "in", *clean[5:-1]]
    if len(clean) > 2 and clean[1] == "in":
        values = clean[2:]
        if (
            len(values) % 2 != 1
            or any(not value.startswith("'") for value in values[::2])
            or any(value != "," for value in values[1::2])
        ):
            _deny()
        return (clean[0], "in", *values[::2])
    # BETWEEN's two conjuncts must repeat the SAME exact builtin and column.
    if (
        len(clean) == 9
        and clean[0] == clean[5] == "octet_length"
        and clean[1] == clean[6]
        and clean[2] == ">="
        and clean[4] == "and"
        and clean[7] == "<="
    ):
        return ("between", clean[1], clean[3], clean[8])
    if (
        len(clean) == 6
        and clean[0] == "octet_length"
        and clean[2] == "between"
        and clean[4] == "and"
    ):
        return ("between", clean[1], clean[3], clean[5])
    return tuple(clean)


def _oid(value):
    if type(value) is not int or not 0 < value < 2**32:
        _deny()
    return value


def _acl(rows, expected):
    if type(rows) is not list or any(
        type(row) is not list
        or len(row) != 4
        or type(row[0]) is not int
        or type(row[1]) is not int
        or type(row[2]) is not str
        or type(row[3]) is not bool
        for row in rows
    ):
        _deny()
    actual = {tuple(row) for row in rows}
    if len(actual) != len(rows) or actual != expected:
        _deny()


def observe_catalog(value, *, owner, actor, owner_mode):
    schema.statements()
    enforcement.statements()
    _oid(owner)
    _oid(actor)
    if (
        type(owner_mode) is not bool
        or type(value) is not dict
        or set(value) != {"functions", "tables", "shared_triggers"}
    ):
        _deny()
    specs = {
        name: (
            ", ".join(part.strip() for part in arguments.split(",")),
            returns,
            language,
            volatility,
            sql.split("$f$")[1],
            True,
        )
        for name, arguments, returns, language, volatility, sql in schema.FUNCTION_IDENTITIES
    }
    specs[enforcement.FUNCTION] = (
        "",
        "trigger",
        "plpgsql",
        "v",
        enforcement.CURRENT_SQL.split("$ctcc$")[1],
        False,
    )
    functions = value["functions"]
    if (
        type(functions) is not dict
        or set(functions) != {"schema", "functions"}
        or functions["schema"] != SCHEMA
        or type(functions["functions"]) is not list
        or len(functions["functions"]) != 6
    ):
        _deny()
    definitions, function_oids, seen = {}, {}, set()
    for row in functions["functions"]:
        if (
            type(row) is not dict
            or set(row) != function_source.FUNCTION_KEYS
            or type(row["name"]) is not str
            or row["name"] not in specs
            or row["name"] in definitions
        ):
            _deny()
        args, returns, language, volatility, body, public = specs[row["name"]]
        oid = _oid(row["oid"])
        if (
            oid in seen
            or row["namespace"] != "public"
            or row["owner"] != owner
            or row["arguments"] != args
            or row["returns"] != returns
            or row["language"] != language
            or row["security_definer"] is not False
            or row["kind"] != "f"
            or row["volatility"] != volatility
            or row["parallel"] != "u"
            or row["strict"] is not False
            or row["leakproof"] is not False
            or row["returns_set"] is not False
            or type(row["defaults"]) is not int
            or row["defaults"] != 0
            or row["support_zero"] is not True
            or row["config"]
            != (
                ["search_path=pg_catalog, public, pg_temp"]
                if public
                else ["search_path=pg_catalog, pg_temp"]
            )
            or row["source"] != body
            or row["acl_valid"] is not True
            or type(row["definition"]) is not str
            or not row["definition"]
        ):
            _deny()
        acl = {(owner, owner, "EXECUTE", False)}
        if public:
            acl.add((0, owner, "EXECUTE", False))
        _acl(row["acl"], acl)
        seen.add(oid)
        function_oids[row["name"]] = oid
        definitions[row["name"]] = {
            "definition": row["definition"],
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
    tables = value["tables"]
    if type(tables) is not list or len(tables) != 3:
        _deny()
    normalized, table_oids, constraints_by_oid, fk_specs = {}, {}, {}, {}
    expected_foreign = {
        (child, tuple(columns), parent, tuple(parent_columns))
        for child, columns, parent, parent_columns in schema.FOREIGN_KEYS
    }
    actual_foreign = set()
    for table in tables:
        if (
            type(table) is not dict
            or set(table) != table_source.TABLE_KEYS
            or type(table["name"]) is not str
            or table["name"] not in _COLUMNS
            or table["name"] in normalized
        ):
            _deny()
        name, oid = table["name"], _oid(table["oid"])
        if (
            oid in table_oids.values()
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
        rights = {
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
            "MAINTAIN",
        }
        acl = {(owner, owner, right, False) for right in rights}
        if not owner_mode:
            acl |= {
                (actor, owner, right, False)
                for right in (
                    {"SELECT", "INSERT", "UPDATE"}
                    if name == _I
                    else {"SELECT", "INSERT"}
                )
            }
        _acl(table["acl"], acl)
        columns = table["columns"]
        if (
            type(columns) is not list
            or any(type(row) is not list or len(row) != 7 for row in columns)
            or [tuple(row[:2]) for row in columns] != _COLUMNS[name]
        ):
            _deny()
        column_map = {}
        for row in columns:
            nullable = (name == _I and row[0] == "claims_sha256") or (
                name == _R and row[0] == "attempt_id"
            )
            textual = row[1] == "text" or row[1].startswith("character varying")
            default = "0" if row[0] == "latest_result_sequence" else None
            if (
                row[2] is not (not nullable)
                or row[3:6] != ["", "", default]
                or type(row[6]) is not int
                or row[6] != (100 if textual else 0)
            ):
                _deny()
            column_map[row[0]] = row
        constraints = table["constraints"]
        if type(constraints) is not list:
            _deny()
        checks, key_shapes, seen_names = [], set(), set()
        expected_keys = {("p", ("intent_id" if name != _R else "result_id",))}
        if name == _A:
            expected_keys.add(("u", ("attempt_id",)))
        if name == _R:
            expected_keys.add(("u", ("intent_id", "result_sequence")))
        for row in constraints:
            if (
                type(row) is not dict
                or set(row) != table_source.CONSTRAINT_KEYS | {"oid"}
                or type(row["name"]) is not str
                or not row["name"]
                or row["name"] in seen_names
            ):
                _deny()
            cid = _oid(row["oid"])
            if (
                cid in constraints_by_oid
                or row["validated"] is not True
                or row["deferrable"] is not False
                or row["deferred"] is not False
                or type(row["definition"]) is not str
                or not row["definition"]
            ):
                _deny()
            seen_names.add(row["name"])
            constraints_by_oid[cid] = row
            if row["kind"] == "c":
                checks.append(_signature(row["definition"]))
            elif row["kind"] in {"p", "u"}:
                shape = (row["kind"], tuple(row["columns"]))
                if (
                    shape in key_shapes
                    or row["parent"] is not None
                    or row["parent_columns"] is not None
                ):
                    _deny()
                key_shapes.add(shape)
            elif row["kind"] == "f":
                parent = (
                    row["parent"].removeprefix("public.")
                    if type(row["parent"]) is str
                    else None
                )
                shape = (
                    name,
                    tuple(row["columns"]),
                    parent,
                    tuple(row["parent_columns"]),
                )
                if (
                    shape not in expected_foreign
                    or shape in actual_foreign
                    or (row["update"], row["delete"], row["match"]) != ("a", "r", "s")
                    or row["name"] in fk_specs
                ):
                    _deny()
                actual_foreign.add(shape)
                fk_specs[row["name"]] = (name, parent, "r", False)
            else:
                _deny()
        if (
            Counter(checks)
            != Counter(_signature(expression) for expression in _CHECKS[name])
            or key_shapes != expected_keys
        ):
            _deny()
        indexes = table["indexes"]
        if type(indexes) is not list:
            _deny()
        expected_indexes = {
            (True, True, ("intent_id" if name != _R else "result_id",), None)
        }
        if name == _I:
            expected_indexes.add(
                (
                    True,
                    False,
                    ("environment", "account_id"),
                    _signature(
                        "phase IN ('prepared','attempted','verified','unknown')"
                    ),
                )
            )
        elif name == _A:
            expected_indexes.add((True, False, ("attempt_id",), None))
        else:
            expected_indexes.add((True, False, ("intent_id", "result_sequence"), None))
        found_indexes, index_names = set(), set()
        for row in indexes:
            if (
                type(row) is not dict
                or set(row)
                != table_source.INDEX_KEYS
                | {"namespace", "kind", "persistence", "opclasses", "collations"}
                or type(row["name"]) is not str
                or not row["name"]
                or row["name"] in index_names
            ):
                _deny()
            cols = row["columns"]
            if (
                type(cols) is not list
                or not cols
                or any(col not in column_map for col in cols)
            ):
                _deny()
            shape = (
                row["unique"],
                row["primary"],
                tuple(cols),
                None if row["predicate"] is None else _signature(row["predicate"]),
            )
            expected_ops = [
                ["pg_catalog", "int8_ops", "bigint"]
                if column_map[col][1] == "bigint"
                else ["pg_catalog", "text_ops", "text"]
                for col in cols
            ]
            if (
                shape not in expected_indexes
                or shape in found_indexes
                or row["owner"] != owner
                or (row["namespace"], row["kind"], row["persistence"], row["method"])
                != ("public", "i", "p", "btree")
                or any(
                    row[key] is not True
                    for key in ("valid", "ready", "live", "immediate")
                )
                or row["exclusion"] is not False
                or row["natts"] != len(cols)
                or row["keyatts"] != len(cols)
                or row["expressions"] is not None
                or row["opclasses"] != expected_ops
                or row["collations"] != [column_map[col][6] for col in cols]
                or type(row["definition"]) is not str
                or not row["definition"]
            ):
                _deny()
            found_indexes.add(shape)
            index_names.add(row["name"])
        if found_indexes != expected_indexes:
            _deny()
        expected_triggers = {
            name + "_guard": (31, "qualification_leverage_configuration_guard_v55"),
            name + "_fence": (5, "qualification_leverage_configuration_fence_v55"),
            name + "_no_truncate": (
                34,
                "qualification_leverage_configuration_guard_v55",
            ),
        }
        triggers = table["triggers"]
        if (
            type(triggers) is not list
            or len(triggers) != 3
            or {row["name"] for row in triggers} != set(expected_triggers)
        ):
            _deny()
        for row in triggers:
            if (
                type(row) is not dict
                or set(row) != table_source.TRIGGER_KEYS
                or (row["kind"], row["function"]) != expected_triggers[row["name"]]
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
                or type(row["definition"]) is not str
                or not row["definition"]
            ):
                _deny()
        table_oids[name] = oid
        # ACLs/OIDs are authenticated above and joined separately. The native
        # definition fingerprint remains identical for owner/writer profiles.
        normalized[name] = {
            "columns": columns,
            "constraints": [[row["name"], row["definition"]] for row in constraints],
            "indexes": [[row["name"], row["definition"]] for row in indexes],
            "triggers": [[row["name"], row["definition"]] for row in triggers],
        }
    if actual_foreign != expected_foreign or len(fk_specs) != 6:
        _deny()
    shared = value["shared_triggers"]
    if type(shared) is not list or len(shared) != 4:
        _deny()
    shared_oids, shared_definitions = {}, {}
    for table in shared:
        if (
            type(table) is not dict
            or set(table)
            != {"name", "oid", "owner", "namespace", "kind", "persistence", "triggers"}
            or table["name"] not in _SHARED_TRIGGERS
            or table["name"] in shared_oids
        ):
            _deny()
        name, oid = table["name"], _oid(table["oid"])
        if (
            oid in shared_oids.values()
            or oid in table_oids.values()
            or table["owner"] != owner
            or (table["namespace"], table["kind"], table["persistence"])
            != ("public", "r", "p")
        ):
            _deny()
        triggers, expected = table["triggers"], _SHARED_TRIGGERS[name]
        if (
            type(triggers) is not list
            or len(triggers) != len(expected)
            or {row["name"] for row in triggers} != set(expected)
        ):
            _deny()
        for row in triggers:
            if (
                type(row) is not dict
                or set(row) != table_source.TRIGGER_KEYS
                or (row["kind"], row["function"]) != expected[row["name"]]
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
                or type(row["definition"]) is not str
                or not row["definition"]
            ):
                _deny()
        shared_oids[name] = oid
        shared_definitions[name] = [
            [row["name"], row["definition"]] for row in triggers
        ]
    return {
        "definitions": {
            "functions": definitions,
            "tables": normalized,
            "shared_triggers": shared_definitions,
        },
        "table_oids": table_oids,
        "shared_table_oids": shared_oids,
        "function_oids": function_oids,
        "fk_specs": fk_specs,
        "constraints_by_oid": constraints_by_oid,
    }
