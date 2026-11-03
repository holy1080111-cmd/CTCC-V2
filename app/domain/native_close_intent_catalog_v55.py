"""Exact current55 once-index/append-only Outcome metadata; DATA only.

No connection, grants, native seal or lifecycle authority is accepted or issued.
The full owner/writer catalog supplies actual role/table facts and seals the
complete normalized graph separately. Historical54 validators are untouched.
"""

from app.domain import native_close_intent_schema_v55 as native_close_intent
from app.domain import (
    native_leverage_configuration_enforcement_v55 as configuration_enforcement,
)
from app.domain.source_primitives import decode

_PRIVILEGES = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
}


class NativeCloseIntentCatalogError(ValueError):
    pass


def _deny():
    raise NativeCloseIntentCatalogError(
        "native_close_intent55_actual_index_changed"
    ) from None


def _oid(value):
    if type(value) is not int or not 0 < value < 2**32:
        _deny()
    return value


def _acl(rows, *, owner, actor, owner_mode, rights, public=False):
    if type(rows) is not list:
        _deny()
    expected = {
        (owner, owner, "EXECUTE" if public is not None else right, False)
        for right in ({"EXECUTE"} if public is not None else _PRIVILEGES)
    }
    if public:
        expected.add((0, owner, "EXECUTE", False))
    if public is None and not owner_mode:
        expected |= {(actor, owner, right, False) for right in rights}
    found = set()
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 4
            or type(row[0]) is not int
            or type(row[1]) is not int
            or type(row[2]) is not str
            or type(row[3]) is not bool
            or tuple(row) in found
        ):
            _deny()
        found.add(tuple(row))
    if found != expected:
        _deny()


TABLE_KEYS = {
    "oid",
    "namespace",
    "name",
    "owner",
    "kind",
    "persistence",
    "partition",
    "has_subclass",
    "rules",
    "row_security",
    "force_row_security",
}

TRIGGER_KEYS = {
    "oid",
    "table_oid",
    "name",
    "enabled",
    "kind",
    "internal",
    "arguments",
    "argument_bytes",
    "columns",
    "qual_is_null",
    "constraint_oid",
    "old_table",
    "new_table",
    "definition",
    "function_oid",
    "function_namespace",
    "function_name",
    "function_owner",
    "function_arguments",
    "function_kind",
}

CATALOG_SQL = """
SELECT jsonb_build_object(
 'tables',coalesce((SELECT jsonb_agg(jsonb_build_object(
  'oid',c.oid::bigint,'namespace',n.nspname,'name',c.relname,'owner',c.relowner::bigint,
  'kind',c.relkind,'persistence',c.relpersistence,'partition',c.relispartition,
  'has_subclass',c.relhassubclass,'rules',c.relhasrules,
  'row_security',c.relrowsecurity,'force_row_security',c.relforcerowsecurity,
  'column_acl',EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND a.attacl IS NOT NULL),
  'acl_valid',CASE WHEN c.relacl IS NULL OR cardinality(c.relacl)=0 THEN true
    WHEN array_ndims(c.relacl)<>1 OR array_lower(c.relacl,1)<>1 THEN false
    ELSE array_position(c.relacl,NULL) IS NULL END,
  'acl',coalesce((SELECT jsonb_agg(jsonb_build_array(x.grantee::bigint,x.grantor::bigint,x.privilege_type,x.is_grantable)
    ORDER BY x.grantee,x.privilege_type) FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x),'[]'::jsonb)
 ) ORDER BY c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname='qualification_submission_outcomes'),'[]'::jsonb),
 'columns',coalesce((SELECT jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod),
  a.attnotnull,a.attgenerated::text,a.attidentity::text,pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE n.nspname='public' AND c.relname='qualification_submission_outcomes' AND a.attnum>0 AND NOT a.attisdropped),'[]'::jsonb),
 'indexes',coalesce((SELECT jsonb_agg(jsonb_build_object(
  'oid',ic.oid::bigint,'namespace',ins.nspname,'name',ic.relname,'owner',ic.relowner::bigint,
  'kind',ic.relkind,'persistence',ic.relpersistence,'options',ic.reloptions,'partition',ic.relispartition,
  'table_oid',tc.oid::bigint,'table_namespace',tn.nspname,'table_name',tc.relname,'table_owner',tc.relowner::bigint,
  'access_method',am.amname,'natts',i.indnatts,'keyatts',i.indnkeyatts,
  'unique',i.indisunique,'valid',i.indisvalid,'ready',i.indisready,'live',i.indislive,
  'immediate',i.indimmediate,'primary',i.indisprimary,'exclusion',i.indisexclusion,
  'replica_identity',i.indisreplident,'nulls_not_distinct',i.indnullsnotdistinct,'clustered',i.indisclustered,
  'expressions_is_null',i.indexprs IS NULL,'predicate',pg_get_expr(i.indpred,i.indrelid),
  'definition',pg_get_indexdef(ic.oid),'key_attnums',to_jsonb(i.indkey::smallint[]),
  'key_options',to_jsonb(i.indoption::smallint[]),
  'columns',(SELECT jsonb_agg(a.attname ORDER BY k.ordinality)
   FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ordinality)
   JOIN pg_attribute a ON a.attrelid=tc.oid AND a.attnum=k.attnum),
  'opclasses',(SELECT jsonb_agg(jsonb_build_object('oid',o.oid::bigint,'namespace',ns.nspname,
   'name',o.opcname,'default',o.opcdefault,'input_type',format_type(o.opcintype,NULL),'access_method',oa.amname)
   ORDER BY k.ordinality) FROM unnest(i.indclass) WITH ORDINALITY k(opclass,ordinality)
   JOIN pg_opclass o ON o.oid=k.opclass JOIN pg_namespace ns ON ns.oid=o.opcnamespace
   JOIN pg_am oa ON oa.oid=o.opcmethod),
  'collations',(SELECT jsonb_agg(jsonb_build_object('oid',o.oid::bigint,'namespace',ns.nspname,
   'name',o.collname,'deterministic',o.collisdeterministic) ORDER BY k.ordinality)
   FROM unnest(i.indcollation) WITH ORDINALITY k(collation_oid,ordinality)
   JOIN pg_collation o ON o.oid=k.collation_oid JOIN pg_namespace ns ON ns.oid=o.collnamespace)
 ) ORDER BY ic.oid)
 FROM pg_class ic JOIN pg_namespace ins ON ins.oid=ic.relnamespace
 JOIN pg_index i ON i.indexrelid=ic.oid JOIN pg_class tc ON tc.oid=i.indrelid
 JOIN pg_namespace tn ON tn.oid=tc.relnamespace JOIN pg_am am ON am.oid=ic.relam
 WHERE ins.nspname='public' AND ic.relname='qualification_native_close_intent_once_v55'),'[]'::jsonb),
 'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object(
  'oid',t.oid::bigint,'table_oid',c.oid::bigint,'name',t.tgname,
  'enabled',t.tgenabled,'kind',t.tgtype::integer,'internal',t.tgisinternal,
  'arguments',t.tgnargs,'argument_bytes',encode(t.tgargs,'hex'),'columns',t.tgattr::text,
  'qual_is_null',t.tgqual IS NULL,'constraint_oid',t.tgconstraint::bigint,
  'old_table',t.tgoldtable,'new_table',t.tgnewtable,'definition',pg_get_triggerdef(t.oid),
  'function_oid',p.oid::bigint,'function_namespace',pn.nspname,'function_name',p.proname,
  'function_owner',p.proowner::bigint,'function_arguments',p.pronargs,'function_kind',p.prokind
 ) ORDER BY t.tgname,t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
 JOIN pg_namespace pn ON pn.oid=p.pronamespace
 WHERE n.nspname='public' AND c.relname='qualification_submission_outcomes' AND NOT t.tgisinternal),'[]'::jsonb)
)
"""
COLUMNS = [
    ["outcome_id", "character varying(64)", True, "", "", None],
    ["reservation_id", "character varying(64)", True, "", "", None],
    ["intent_transition_id", "bigint", True, "", "", None],
    ["sequence", "bigint", True, "", "", None],
    ["previous_sha256", "character varying(64)", False, "", "", None],
    ["observation_kind", "character varying(24)", True, "", "", None],
    ["status", "character varying(24)", True, "", "", None],
    ["intent_sha256", "character varying(64)", True, "", "", None],
    ["exchange_request_sha256", "character varying(64)", True, "", "", None],
    ["capture_json", "text", True, "", "", None],
    ["capture_sha256", "character varying(64)", True, "", "", None],
    ["outcome_json", "text", True, "", "", None],
    ["ledger_revision", "bigint", True, "", "", None],
    ["observed_at", "timestamp with time zone", True, "", "", None],
    ["recorded_at", "timestamp with time zone", True, "", "", None],
]
INDEX_KEYS = {
    "oid",
    "namespace",
    "name",
    "owner",
    "kind",
    "persistence",
    "options",
    "partition",
    "table_oid",
    "table_namespace",
    "table_name",
    "table_owner",
    "access_method",
    "natts",
    "keyatts",
    "unique",
    "valid",
    "ready",
    "live",
    "immediate",
    "primary",
    "exclusion",
    "replica_identity",
    "nulls_not_distinct",
    "clustered",
    "expressions_is_null",
    "predicate",
    "definition",
    "key_attnums",
    "key_options",
    "columns",
    "opclasses",
    "collations",
}
TRIGGERS = {
    "qualification_submission_binding_guard": (7, "qualification_submission_binding"),
    "qualification_submission_outcomes_immutable": (
        27,
        "qualification_ledger_immutable",
    ),
}


def observe_catalog(value, *, owner, actor, owner_mode, configuration_guard_oid=None):
    owner, actor = _oid(owner), _oid(actor)
    if type(owner_mode) is not bool:
        _deny()
    native_close_intent.statements()
    spec = decode(native_close_intent.INDEX_SPEC_JSON.encode("ascii"), 4096)
    if (
        type(value) is not dict
        or set(value) != {"tables", "columns", "indexes", "triggers"}
        or type(value["tables"]) is not list
        or len(value["tables"]) != 1
        or type(value["columns"]) is not list
        or value["columns"] != COLUMNS
        or any(
            type(item) is not list or len(item) != 6 or type(item[2]) is not bool
            for item in value["columns"]
        )
        or type(value["indexes"]) is not list
        or len(value["indexes"]) != 1
    ):
        _deny()
    table = value["tables"][0]
    if (
        type(table) is not dict
        or set(table) != TABLE_KEYS | {"column_acl", "acl_valid", "acl"}
        or table["namespace"] != "public"
        or table["name"] != "qualification_submission_outcomes"
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
                "column_acl",
            )
        )
        or table["acl_valid"] is not True
    ):
        _deny()
    table_oid = _oid(table["oid"])
    _acl(
        table["acl"],
        owner=owner,
        actor=actor,
        owner_mode=owner_mode,
        rights={"SELECT", "INSERT"},
        public=None,
    )
    row = value["indexes"][0]
    if (
        type(row) is not dict
        or set(row) != INDEX_KEYS
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
    expected_triggers = TRIGGERS
    if configuration_guard_oid is not None:
        configuration_guard_oid = _oid(configuration_guard_oid)
        kind, _ = configuration_enforcement.TRIGGERS[
            "qualification_submission_outcomes"
        ]
        expected_triggers = {
            **TRIGGERS,
            configuration_enforcement.TRIGGER: (
                kind,
                configuration_enforcement.FUNCTION,
            ),
        }
    triggers = value["triggers"]
    if type(triggers) is not list or len(triggers) != len(expected_triggers):
        _deny()
    definitions = {}
    seen = set()
    for trigger in triggers:
        if (
            type(trigger) is not dict
            or set(trigger) != TRIGGER_KEYS
            or trigger["name"] not in expected_triggers
            or trigger["name"] in definitions
            or trigger["table_oid"] != table_oid
            or trigger["enabled"] != "O"
            or trigger["internal"] is not False
            or type(trigger["kind"]) is not int
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
            or trigger["function_owner"] != owner
            or type(trigger["function_arguments"]) is not int
            or trigger["function_arguments"] != 0
            or trigger["function_kind"] != "f"
            or type(trigger["definition"]) is not str
            or not trigger["definition"]
        ):
            _deny()
        oid = _oid(trigger["oid"])
        _oid(trigger["function_oid"])
        if oid in seen:
            _deny()
        seen.add(oid)
        kind, function = expected_triggers[trigger["name"]]
        if trigger["kind"] != kind or trigger["function_name"] != function:
            _deny()
        if (
            trigger["name"] == configuration_enforcement.TRIGGER
            and trigger["function_oid"] != configuration_guard_oid
        ):
            _deny()
        definitions[trigger["name"]] = trigger["definition"]
    return {
        "index": row["definition"],
        "predicate": row["predicate"],
        "triggers": definitions,
    }
