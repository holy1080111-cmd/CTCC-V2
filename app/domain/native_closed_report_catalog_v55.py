"""Actual report tables/functions/ACL/indexes in the unsealed sole55 graph."""

from app.domain import native_closed_report_schema_v55 as schema
from app.domain import native_terminal_catalog_v55 as helpers

SCHEMA = "ctcc.native_closed_report55.actual_catalog.v1"
_OLD_FUNCTIONS = "'qualification_native_plain_v55','qualification_native_canonical_v55','qualification_native_manifest_v55',\n 'qualification_native_rows_v55','qualification_native_number_v55','qualification_native_clock_v55','qualification_native_filled_flat_data_v55'"
_NEW_FUNCTIONS = "'qualification_native_report_eligible_v55','qualification_native_projection_eligible_v55','qualification_spool_binding','qualification_projection_binding'"
FUNCTION_CATALOG_SQL = helpers.CATALOG_SQL.replace(helpers.SCHEMA, SCHEMA).replace(
    _OLD_FUNCTIONS, _NEW_FUNCTIONS
)
TABLE_CATALOG_SQL = """
SELECT coalesce(jsonb_agg(jsonb_build_object(
 'name',c.relname,'oid',c.oid::bigint,'owner',c.relowner::bigint,'kind',c.relkind::text,'persistence',c.relpersistence::text,
 'partition',c.relispartition,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
 'inherited',EXISTS(SELECT 1 FROM pg_inherits x WHERE x.inhrelid=c.oid OR x.inhparent=c.oid),
 'rules',EXISTS(SELECT 1 FROM pg_rewrite x WHERE x.ev_class=c.oid),'policies',EXISTS(SELECT 1 FROM pg_policy x WHERE x.polrelid=c.oid),
 'column_acl',EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND a.attacl IS NOT NULL),
 'acl_valid',CASE WHEN c.relacl IS NULL OR cardinality(c.relacl)=0 THEN true WHEN array_ndims(c.relacl)<>1 OR array_lower(c.relacl,1)<>1 THEN false ELSE array_position(c.relacl,NULL) IS NULL END,
 'acl',coalesce((SELECT jsonb_agg(jsonb_build_array(a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable) ORDER BY a.grantee,a.privilege_type)
  FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a),'[]'::jsonb),
 'columns',(SELECT jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
   a.attgenerated::text,a.attidentity::text,pg_get_expr(d.adbin,d.adrelid),a.attcollation::bigint) ORDER BY a.attnum)
   FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
 'constraints',(SELECT jsonb_agg(jsonb_build_object('name',k.conname,'kind',k.contype::text,'validated',k.convalidated,
   'deferrable',k.condeferrable,'deferred',k.condeferred,'definition',pg_get_constraintdef(k.oid,true),
   'columns',(SELECT jsonb_agg(a.attname ORDER BY s.n) FROM unnest(k.conkey) WITH ORDINALITY s(attnum,n) JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=s.attnum),
   'parent',CASE WHEN k.confrelid=0 THEN NULL ELSE k.confrelid::regclass::text END,
   'parent_columns',(SELECT jsonb_agg(a.attname ORDER BY s.n) FROM unnest(k.confkey) WITH ORDINALITY s(attnum,n) JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=s.attnum),
   'update',k.confupdtype::text,'delete',k.confdeltype::text,'match',k.confmatchtype::text) ORDER BY k.conname) FROM pg_constraint k WHERE k.conrelid=c.oid),
 'indexes',(SELECT jsonb_agg(jsonb_build_object('name',ic.relname,'owner',ic.relowner::bigint,'method',am.amname,
   'unique',i.indisunique,'primary',i.indisprimary,'valid',i.indisvalid,'ready',i.indisready,'live',i.indislive,'immediate',i.indimmediate,
   'exclusion',i.indisexclusion,'natts',i.indnatts,'keyatts',i.indnkeyatts,'expressions',pg_get_expr(i.indexprs,i.indrelid),
   'predicate',pg_get_expr(i.indpred,i.indrelid),'definition',pg_get_indexdef(i.indexrelid),
   'columns',(SELECT jsonb_agg(a.attname ORDER BY s.n) FROM unnest(i.indkey) WITH ORDINALITY s(attnum,n) JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=s.attnum)) ORDER BY ic.relname)
   FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_am am ON am.oid=ic.relam WHERE i.indrelid=c.oid),
 'triggers',(SELECT jsonb_agg(jsonb_build_object('name',t.tgname,'kind',t.tgtype::integer,'enabled',t.tgenabled::text,
   'args',encode(t.tgargs,'hex'),'nargs',t.tgnargs,'attrs',t.tgattr::text,'qual',pg_get_expr(t.tgqual,t.tgrelid),
   'constraint',t.tgconstraint::bigint,'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,'old_table',t.tgoldtable,'new_table',t.tgnewtable,
   'function',p.proname,'namespace',pn.nspname,'owner',p.proowner::bigint,'arity',p.pronargs,
   'definition',pg_get_triggerdef(t.oid,true)) ORDER BY t.tgname) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
   WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
) ORDER BY c.relname),'[]'::jsonb) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN('qualification_report_spool','qualification_report_projection_receipts')
"""
CATALOG_SQL = (
    "SELECT jsonb_build_object('functions',("
    + FUNCTION_CATALOG_SQL
    + "),'tables',("
    + TABLE_CATALOG_SQL
    + "))"
)
_COLUMNS = {
    "qualification_report_spool": [
        ("spool_id", "character varying(64)"),
        ("outcome_id", "character varying(64)"),
        ("reservation_id", "character varying(64)"),
        ("report_id", "character varying(96)"),
        ("report_bytes", "bytea"),
        ("report_sha256", "character varying(64)"),
        ("policy_json", "text"),
        ("policy_sha256", "character varying(64)"),
        ("queue_namespace", "character varying(64)"),
        ("eligible", "boolean"),
        ("created_at", "timestamp with time zone"),
    ],
    "qualification_report_projection_receipts": [
        ("spool_id", "character varying(64)"),
        ("report_sha256", "character varying(64)"),
        ("policy_sha256", "character varying(64)"),
        ("envelope_sha256", "character varying(64)"),
        ("queue_namespace", "character varying(64)"),
        ("verified_at", "timestamp with time zone"),
    ],
}
_INDEXES = {
    "qualification_report_spool": {
        "pk_qualification_report_spool": (True, True, ["spool_id"], None),
        "uq_qualification_report_spool_outcome_id": (True, False, ["outcome_id"], None),
        "ix_submission_pending_spool": (
            False,
            False,
            ["queue_namespace", "created_at", "spool_id"],
            "eligible",
        ),
        "uq_submission_eligible_report": (
            True,
            False,
            ["queue_namespace", "report_id"],
            "eligible",
        ),
        "uq_submission_eligible_reservation": (
            True,
            False,
            ["reservation_id"],
            "eligible",
        ),
    },
    "qualification_report_projection_receipts": {
        "pk_qualification_report_projection_receipts": (True, True, ["spool_id"], None)
    },
}
_CONSTRAINTS = {
    "qualification_report_spool": {
        "pk_qualification_report_spool": ("p", ["spool_id"], None, None),
        "uq_qualification_report_spool_outcome_id": ("u", ["outcome_id"], None, None),
        "ck_qualification_report_spool_digests": ("c", None, None, None),
        "ck_qualification_report_spool_names": ("c", None, None, None),
        "ck_qualification_report_spool_bounds": ("c", None, None, None),
        "ck_qualification_report_spool_transport_unverified": ("c", None, None, None),
        "fk_qualification_report_spool_outcome_id_qualification__df7a": (
            "f",
            ["outcome_id"],
            "qualification_submission_outcomes",
            ["outcome_id"],
        ),
        "fk_qualification_report_spool_reservation_id_qualificat_4ccb": (
            "f",
            ["reservation_id"],
            "qualification_reservations",
            ["reservation_id"],
        ),
    },
    "qualification_report_projection_receipts": {
        "pk_qualification_report_projection_receipts": ("p", ["spool_id"], None, None),
        "ck_qualification_report_projection_receipts_digests": ("c", None, None, None),
        "ck_qualification_report_projection_receipts_transport_unverifie": (
            "c",
            None,
            None,
            None,
        ),
        "fk_qualification_report_projection_receipts_spool_id_qu_b1e6": (
            "f",
            ["spool_id"],
            "qualification_report_spool",
            ["spool_id"],
        ),
    },
}
_RIGHTS = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
}
TABLE_KEYS = {
    "name",
    "oid",
    "owner",
    "kind",
    "persistence",
    "partition",
    "rls",
    "force_rls",
    "inherited",
    "rules",
    "policies",
    "column_acl",
    "acl_valid",
    "acl",
    "columns",
    "constraints",
    "indexes",
    "triggers",
}
CONSTRAINT_KEYS = {
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
INDEX_KEYS = {
    "name",
    "owner",
    "method",
    "unique",
    "primary",
    "valid",
    "ready",
    "live",
    "immediate",
    "exclusion",
    "natts",
    "keyatts",
    "expressions",
    "predicate",
    "definition",
    "columns",
}
TRIGGER_KEYS = {
    "name",
    "kind",
    "enabled",
    "args",
    "nargs",
    "attrs",
    "qual",
    "constraint",
    "deferrable",
    "deferred",
    "old_table",
    "new_table",
    "function",
    "namespace",
    "owner",
    "arity",
    "definition",
}


def _deny():
    raise helpers.NativeTerminalCatalogError(
        "native_closed_report55_actual_catalog_changed"
    ) from None


def observe_catalog(value, *, owner, actor, owner_mode, legacy=False):
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
    specs = (
        schema.FUNCTIONS
        if not legacy
        else (
            (
                "qualification_spool_binding",
                "",
                "trigger",
                schema.LEGACY_SPOOL_SQL,
                "$$",
                False,
            ),
            (
                "qualification_projection_binding",
                "",
                "trigger",
                schema.LEGACY_PROJECTION_SQL,
                "$$",
                False,
            ),
        )
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
