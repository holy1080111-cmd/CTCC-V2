"""Actual DB0058 protective predicate/index/trigger metadata; DATA only.

Legacy55 catalog functions and their semantic own-close witness remain unchanged.
Every captured head is physical58; this module creates no Arm or risk authority.
"""

import hashlib

from app.domain import collector_current_0055_catalog_v1 as inherited
from app.domain import native_close_intent_catalog_v55 as index55
from app.domain import native_protective_hold_schema_v58 as schema
from app.domain import native_protective_report_catalog_v58 as report_catalog
from app.domain import native_protective_report_schema_v58 as report
from app.domain import native_submission_binding_schema_v58 as submission
from app.domain import native_terminal_catalog_v55 as functions55
from app.domain.source_primitives import canonical

SCHEMA = "ctcc.native_protective58.actual_catalog.v1"
REPORT_ELIGIBILITY_BODY_SHA256 = report.ELIGIBLE_BODY_SHA256
REPORT_CATALOG_SQL = report_catalog.CATALOG_SQL
_FUNCTION_LIST = "'qualification_native_plain_v55','qualification_native_canonical_v55','qualification_native_manifest_v55',\n 'qualification_native_rows_v55','qualification_native_number_v55','qualification_native_clock_v55','qualification_native_filled_flat_data_v55'"
FUNCTION_SQL = functions55.CATALOG_SQL.replace(functions55.SCHEMA, SCHEMA).replace(
    _FUNCTION_LIST, "'qualification_native_protective_flat_data_v58'"
)
INDEX_SQL = index55.CATALOG_SQL.replace(
    "ic.relname='qualification_native_close_intent_once_v55'",
    "ic.relname IN ('qualification_native_protected_hold_once_v58','qualification_native_protective_flat_once_v58')",
)
CATALOG_SQL = (
    "SELECT jsonb_build_object('head',(SELECT jsonb_agg(version_num::text) FROM public.alembic_version),'functions',("
    + FUNCTION_SQL
    + "),'indexes',("
    + INDEX_SQL
    + "))"
)
_SUBMISSION_KEYS = inherited._SUBMISSION_KEYS
_CLOSURE_TABLE_KEYS = inherited._CLOSURE_TABLE_KEYS
_CLOSURE_TRIGGER_KEYS = inherited._CLOSURE_TRIGGER_KEYS
_CLOSURE_TRIGGERS = inherited._CLOSURE_TRIGGERS
configuration_enforcement = inherited.configuration_enforcement
_oid = inherited._oid
_acl = inherited._acl


def _deny():
    raise ValueError("native_protective58_actual_catalog_changed") from None


def observe_submission58(rows, *, owner):
    if type(rows) is not list or len(rows) != 1:
        _deny()
    row = rows[0]
    if (
        type(row) is not dict
        or set(row) != _SUBMISSION_KEYS
        or row["name"] != submission.FUNCTION
        or row["namespace"] != "public"
        or row["owner"] != owner
        or row["arguments"] != ""
        or row["returns"] != "trigger"
        or row["language"] != "plpgsql"
        or row["security_definer"] is not False
        or row["kind"] != "f"
        or row["volatility"] != "v"
        or row["parallel"] != "u"
        or row["strict"] is not False
        or row["leakproof"] is not False
        or row["config"] is not None
        or row["acl_valid"] is not True
        or type(row["source"]) is not str
        or row["source"] != submission.CURRENT_SQL.split("$$")[1]
        or hashlib.sha256(row["source"].encode("utf-8")).hexdigest()
        != submission.CURRENT_BODY_SHA256
        or type(row["definition"]) is not str
        or not row["definition"]
    ):
        _deny()
    _oid(row["oid"])
    _acl(row["acl"], owner=owner, actor=owner, owner_mode=True, rights=(), public=True)
    return row["definition"]


def observe_closure58(value, *, owner, configuration):
    """Exact physical metadata only; source/closure authority is never a DATA field."""
    if type(value) is not dict or set(value) != {"functions", "tables", "triggers"}:
        _deny()
    functions, tables, triggers = value["functions"], value["tables"], value["triggers"]
    if type(functions) is not list or len(functions) != 1:
        _deny()
    row = functions[0]
    if (
        type(row) is not dict
        or set(row) != _SUBMISSION_KEYS
        or row["name"] != "qualification_reservation_update"
        or row["namespace"] != "public"
        or row["owner"] != owner
        or row["arguments"] != ""
        or row["returns"] != "trigger"
        or row["language"] != "plpgsql"
        or row["security_definer"] is not False
        or row["kind"] != "f"
        or row["volatility"] != "v"
        or row["parallel"] != "u"
        or row["strict"] is not False
        or row["leakproof"] is not False
        or row["config"] != ["search_path=pg_catalog, pg_temp"]
        or row["acl_valid"] is not True
        or type(row["source"]) is not str
        or row["source"] != schema.CURRENT_SQL.split("$$")[1]
        or hashlib.sha256(row["source"].encode("utf-8")).hexdigest()
        != schema.CURRENT_BODY_SHA256
        or type(row["definition"]) is not str
        or not row["definition"]
    ):
        _deny()
    function_oid = _oid(row["oid"])
    _acl(row["acl"], owner=owner, actor=owner, owner_mode=True, rights=(), public=True)
    if type(tables) is not list or len(tables) != 1:
        _deny()
    table = tables[0]
    if (
        type(table) is not dict
        or set(table) != _CLOSURE_TABLE_KEYS
        or table["namespace"] != "public"
        or table["name"] != "qualification_reservations"
        or table["owner"] != owner
        or table["kind"] != "r"
        or table["persistence"] != "p"
        or any(
            table[key] is not False
            for key in (
                "partition",
                "has_subclass",
                "rules",
                "row_security",
                "force_row_security",
            )
        )
    ):
        _deny()
    table_oid = _oid(table["oid"])
    if type(triggers) is not list or len(triggers) != len(_CLOSURE_TRIGGERS):
        _deny()
    seen = set()
    definitions = []
    for trigger in triggers:
        if type(trigger) is not dict or set(trigger) != _CLOSURE_TRIGGER_KEYS:
            _deny()
        name = trigger["name"]
        if type(name) is not str or name in seen or name not in _CLOSURE_TRIGGERS:
            _deny()
        kind, function = _CLOSURE_TRIGGERS[name]
        if (
            trigger["table_oid"] != table_oid
            or trigger["enabled"] != "O"
            or type(trigger["kind"]) is not int
            or trigger["kind"] != kind
            or trigger["internal"] is not False
            or type(trigger["arguments"]) is not int
            or trigger["arguments"] != 0
            or trigger["argument_bytes"] != ""
            or trigger["columns"] != ""
            or trigger["qual_is_null"] is not True
            or type(trigger["constraint_oid"]) is not int
            or trigger["constraint_oid"] != 0
            or trigger["old_table"] is not None
            or trigger["new_table"] is not None
            or trigger["function_namespace"] != "public"
            or trigger["function_name"] != function
            or trigger["function_owner"] != owner
            or type(trigger["function_arguments"]) is not int
            or trigger["function_arguments"] != 0
            or trigger["function_kind"] != "f"
            or type(trigger["definition"]) is not str
            or not trigger["definition"]
        ):
            _deny()
        _oid(trigger["oid"])
        _oid(trigger["function_oid"])
        if (
            name == "qualification_reservation_update_guard"
            and trigger["function_oid"] != function_oid
        ):
            _deny()
        if (
            name == configuration_enforcement.TRIGGER
            and trigger["function_oid"]
            != configuration["function_oids"][configuration_enforcement.FUNCTION]
        ):
            _deny()
        seen.add(name)
        definitions.append([name, trigger["definition"]])
    if seen != set(_CLOSURE_TRIGGERS):
        _deny()
    return {"function": row["definition"], "triggers": sorted(definitions)}


def _index(row, *, owner, table_oid, name, version):
    spec = {
        "index_name": name,
        "table": "qualification_submission_outcomes",
        "access_method": "btree",
        "column": "reservation_id",
        "predicate": "(((capture_json)::jsonb ->> 'version'::text) = '"
        + version
        + "'::text)",
    }
    if (
        type(row) is not dict
        or set(row) != index55.INDEX_KEYS
        or row["namespace"] != "public"
        or row["name"] != spec["index_name"]
        or row["owner"] != owner
        or row["kind"] != "i"
        or row["persistence"] != "p"
        or row["options"] is not None
        or row["partition"] is not False
        or row["table_oid"] != table_oid
        or row["table_namespace"] != "public"
        or row["table_name"] != spec["table"]
        or row["table_owner"] != owner
        or row["access_method"] != spec["access_method"]
        or type(row["natts"]) is not int
        or row["natts"] != 1
        or type(row["keyatts"]) is not int
        or row["keyatts"] != 1
        or any(
            row[key] is not True
            for key in (
                "unique",
                "valid",
                "ready",
                "live",
                "immediate",
                "expressions_is_null",
            )
        )
        or any(
            row[key] is not False
            for key in (
                "primary",
                "exclusion",
                "replica_identity",
                "nulls_not_distinct",
                "clustered",
            )
        )
        or row["predicate"] != spec["predicate"]
        or row["columns"] != [spec["column"]]
        or type(row["key_attnums"]) is not list
        or row["key_attnums"] != [2]
        or any(type(item) is not int for item in row["key_attnums"])
        or type(row["key_options"]) is not list
        or row["key_options"] != [0]
        or any(type(item) is not int for item in row["key_options"])
        or type(row["definition"]) is not str
        or not row["definition"]
        or type(row["opclasses"]) is not list
        or len(row["opclasses"]) != 1
        or type(row["collations"]) is not list
        or len(row["collations"]) != 1
    ):
        _deny()
    _oid(row["oid"])
    opclass = row["opclasses"][0]
    collation = row["collations"][0]
    if (
        type(opclass) is not dict
        or set(opclass)
        != {"oid", "namespace", "name", "default", "input_type", "access_method"}
        or opclass["namespace"] != "pg_catalog"
        or opclass["name"] != "text_ops"
        or opclass["default"] is not True
        or opclass["input_type"] != "text"
        or opclass["access_method"] != "btree"
        or type(collation) is not dict
        or set(collation) != {"oid", "namespace", "name", "deterministic"}
        or collation["namespace"] != "pg_catalog"
        or collation["name"] != "default"
        or collation["deterministic"] is not True
    ):
        _deny()
    _oid(opclass["oid"])
    _oid(collation["oid"])
    return [name, row["definition"], row["predicate"]]


def observe_catalog(value, *, owner, original_close):
    if (
        type(value) is not dict
        or set(value) != {"head", "functions", "indexes"}
        or value["head"] != ["0058"]
        or type(owner) is not int
        or not 0 < owner < 2**32
    ):
        _deny()
    functions = value["functions"]
    if (
        type(functions) is not dict
        or set(functions) != {"schema", "functions"}
        or functions["schema"] != SCHEMA
        or type(functions["functions"]) is not list
        or len(functions["functions"]) != 1
    ):
        _deny()
    row = functions["functions"][0]
    if (
        type(row) is not dict
        or set(row) != functions55.FUNCTION_KEYS
        or row["name"] != schema.FUNCTION
        or row["namespace"] != "public"
        or row["owner"] != owner
        or row["arguments"] != schema.FUNCTION_IDENTITY
        or row["returns"] != "boolean"
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
        or row["config"] != ["search_path=pg_catalog, pg_temp"]
        or row["source"] != schema.PREDICATE_SQL.split("$ctcc$")[1]
        or hashlib.sha256(row["source"].encode()).hexdigest()
        != schema.PREDICATE_BODY_SHA256
        or type(row["definition"]) is not str
        or not row["definition"]
        or row["acl_valid"] is not True
        or row["acl"]
        != [[0, owner, "EXECUTE", False], [owner, owner, "EXECUTE", False]]
    ):
        _deny()
    _oid(row["oid"])
    indexes = value["indexes"]
    if (
        type(indexes) is not dict
        or set(indexes) != {"tables", "columns", "indexes", "triggers"}
        or any(
            indexes[k] != original_close[k] for k in ("tables", "columns", "triggers")
        )
        or type(indexes["indexes"]) is not list
        or len(indexes["indexes"]) != 2
    ):
        _deny()
    expected = {
        "qualification_native_protected_hold_once_v58": schema.HOLD_VERSION,
        "qualification_native_protective_flat_once_v58": schema.TERMINAL_VERSION,
    }
    if len({x["name"] for x in indexes["indexes"]}) != 2 or {
        x["name"] for x in indexes["indexes"]
    } != set(expected):
        _deny()
    table_oid = _oid(original_close["tables"][0]["oid"])
    seen = set()
    definitions = []
    for index in indexes["indexes"]:
        oid = _oid(index["oid"])
        if oid in seen:
            _deny()
        seen.add(oid)
        definitions.append(
            _index(
                index,
                owner=owner,
                table_oid=table_oid,
                name=index["name"],
                version=expected[index["name"]],
            )
        )
    return {
        "predicate_body_sha256": schema.PREDICATE_BODY_SHA256,
        "predicate_definition": row["definition"],
        "indexes": sorted(definitions),
        "definitions_sha256": hashlib.sha256(
            canonical({"predicate": row["definition"], "indexes": sorted(definitions)})
        ).hexdigest(),
        "execution_authority": False,
    }
