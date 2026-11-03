"""Separate actual DB0054 catalog profile; native admission remains unsealed.

The immutable0052/0053 profiles are unchanged. This profile independently
accounts for issuer16, lineage18, custody4 native RI triggers and the complete
scope-parent user-trigger/function population. A catalog observation cannot
authenticate source history or issue trading authority.
"""

import hashlib

from app.domain import account_issued_revision_schema_v1 as issued
from app.domain import account_revision_collector_lineage_schema_v1 as lineage
from app.domain import collector_current_0053_owner_installation_v1 as old53
from app.domain.source_primitives import canonical

SCHEMA = "ctcc.collector_current0054.actual_catalog.v1"
PG17_NATIVE_BASELINE_STATUS = "SEALED_FROM_REVIEWED_NATIVE_PG17"
PG17_NATIVE_BASELINE_SHA256 = (
    "8b78a104dafd5969fa454ef0a8edc2492ac291c794ccd2b81a3d52686f279459"
)
BASE_KEYS = frozenset(
    (
        "owner",
        "actor",
        "table_ok",
        "parent_owners",
        "columns",
        "constraints",
        "triggers",
        "functions",
        "bad_table_acl",
        "column_acl",
        "public_schema_create",
    )
)
PARENTS = old53.PARENTS | {"demo_collector_current_revision_lineages"}
_ISSUED_COLUMNS = [
    ["issuer_sha256", "character varying(64)", True],
    ["environment", "character varying(8)", True],
    ["account_id", "character varying(32)", True],
    ["settlement_currency", "character varying(16)", True],
    ["previous_account_revision", "bigint", True],
    ["previous_ledger_revision", "bigint", True],
    ["previous_claims_sha256", "character varying(64)", False],
    ["account_revision", "bigint", True],
    ["ledger_revision", "bigint", True],
    ["claims_sha256", "character varying(64)", True],
    ["claims_json", "text", True],
    ["control_revision", "bigint", True],
    ["control_event_sha256", "character varying(64)", True],
    ["control_state_sha256", "character varying(64)", True],
    ["source_lineage_sha256", "character varying(64)", True],
    ["hwm_custody_id", "character varying(32)", True],
    ["hwm_bundle_sha256", "character varying(64)", True],
    ["proof_json", "text", True],
    ["original_expires_at", "timestamp with time zone", True],
    ["db_recorded_at", "timestamp with time zone", True],
]
_SCOPE = ["environment", "account_id", "settlement_currency"]
_ISSUED_KEYS = {
    "demo_collector_issued_account_revisions_pkey": (
        "p",
        ["issuer_sha256"],
        None,
        None,
    ),
    "fk_collector_issued_scope": ("f", _SCOPE, "qualification_account_scopes", _SCOPE),
    "fk_collector_issued_control": (
        "f",
        ["environment", "account_id", "control_revision"],
        "demo_control_journal",
        ["environment", "account_id", "control_revision"],
    ),
    "fk_collector_issued_source_lineage": (
        "f",
        ["source_lineage_sha256"],
        lineage.TABLE,
        ["lineage_sha256"],
    ),
    "fk_collector_issued_hwm_custody": (
        "f",
        ["hwm_custody_id"],
        "public_runtime_raw_bundles_v1",
        ["bundle_id"],
    ),
    "uq_collector_issued_scope_revision": (
        "u",
        [*_SCOPE, "account_revision"],
        None,
        None,
    ),
    "uq_collector_issued_once_per_current": (
        "u",
        ["source_lineage_sha256"],
        None,
        None,
    ),
}
_ISSUED_CHECKS = frozenset(
    (
        "collector_issued_scope",
        "collector_issued_revision_step",
        "collector_issued_digests",
        "collector_issued_bounds",
    )
)
_ISSUED_TRIGGERS = [
    ["collector_issued_revision_immutable", 27, "O", issued.IMMUTABLE],
    ["collector_issued_revision_insert", 7, "O", issued.GUARD],
    ["collector_issued_revision_no_truncate", 34, "O", issued.IMMUTABLE],
]


class Current0054CatalogError(ValueError):
    pass


def _deny():
    raise Current0054CatalogError("current0054_actual_catalog_unavailable") from None


def _once(source, old, new):
    if type(source) is not str or source.count(old) != 1:
        _deny()
    return source.replace(old, new, 1)


def _issued_template(source):
    for old, new in (
        ("collector_current_revision_lineage_guard_v1", issued.GUARD),
        ("collector_current_revision_lineage_immutable_v1", issued.IMMUTABLE),
        (
            "'public.demo_collector_current_revision_lineages'::regclass",
            "'public.demo_collector_issued_account_revisions'::regclass",
        ),
    ):
        source = _once(source, old, new)
    return source


_ISSUED_BASE = _issued_template(lineage.CATALOG_SQL)
_ISSUED_BASE = _once(
    _ISSUED_BASE,
    "'public.public_runtime_raw_bundles_v1'::regclass) AND p.relowner<>c.relowner)",
    "'public.public_runtime_raw_bundles_v1'::regclass,"
    "'public.demo_collector_current_revision_lineages'::regclass) AND p.relowner<>c.relowner)",
)
_ISSUED_META = old53._OWNER_META
for _old, _new in (
    (
        "ctcc.collector_current0053.actual_owner_catalog.v1",
        "ctcc.collector_current0054.actual_issued_catalog.v1",
    ),
    ("collector_current_revision_lineage_guard_v1", issued.GUARD),
    ("collector_current_revision_lineage_immutable_v1", issued.IMMUTABLE),
    (
        "'demo_account_capture_events','public_runtime_raw_bundles_v1'))",
        "'demo_account_capture_events','public_runtime_raw_bundles_v1','demo_collector_current_revision_lineages'))",
    ),
):
    _ISSUED_META = _once(_ISSUED_META, _old, _new)
del _old, _new
_ISSUED_ACTOR = _once(
    _ISSUED_BASE,
    "SELECT jsonb_build_object(\n",
    "SELECT jsonb_build_object(\n" + _ISSUED_META,
)
_ISSUED_OWNER = _once(
    _ISSUED_ACTOR,
    "NOT pg_has_role(session_user,c.relowner,'MEMBER')",
    "current_user=session_user AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=session_user)",
)
_LINEAGE_ACTOR = _once(
    lineage.CATALOG_SQL,
    "SELECT jsonb_build_object(\n",
    "SELECT jsonb_build_object(\n" + old53._OWNER_META,
)
_LINEAGE_OWNER = old53.CATALOG_SQL


# Exact source identities already reviewed for the unchanged0017 scope guards.
# This new current54 profile closes its own parent-trigger population; the
# historical ledger/51/52/53 contracts and their native hashes stay unchanged.
_SCOPE_PARENT_TRIGGERS = {
    "qualification_scope_no_delete": (11, "qualification_ledger_immutable"),
    "qualification_scope_no_truncate": (34, "qualification_ledger_immutable"),
    "qualification_scope_update_guard": (19, "qualification_scope_update"),
}
_SCOPE_PARENT_FUNCTIONS = {
    "qualification_ledger_immutable": "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649",
    "qualification_scope_update": "5702017eafabb70375bb0495e521d2632e090b739f257a2ae4cbaeb77f55ab6d",
}
_SCOPE_PARENT_CATALOG_SQL = """
WITH relation AS (
 SELECT c.* FROM pg_catalog.pg_class c
 WHERE c.oid='public.qualification_account_scopes'::regclass
), actual_triggers AS (
 SELECT t.* FROM pg_catalog.pg_trigger t JOIN relation c ON c.oid=t.tgrelid
 WHERE NOT t.tgisinternal
), actual_functions AS (
 SELECT p.*,n.nspname,l.lanname FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_catalog.pg_language l ON l.oid=p.prolang
 WHERE p.oid IN(SELECT tgfoid FROM actual_triggers)
)
SELECT jsonb_build_object(
 'table',(SELECT jsonb_build_object(
   'oid',c.oid::bigint,'owner',c.relowner::bigint,'namespace',n.nspname,
   'name',c.relname,'kind',c.relkind::text,'persistence',c.relpersistence::text,
   'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,'partition',c.relispartition,
   'inherited',EXISTS(SELECT 1 FROM pg_catalog.pg_inherits x WHERE x.inhrelid=c.oid OR x.inhparent=c.oid),
   'rules',EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite x WHERE x.ev_class=c.oid),
   'policies',EXISTS(SELECT 1 FROM pg_catalog.pg_policy x WHERE x.polrelid=c.oid))
   FROM relation c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace),
 'triggers',(SELECT jsonb_agg(jsonb_build_object(
   'oid',t.oid::bigint,'name',t.tgname,'relation_oid',t.tgrelid::bigint,
   'function_oid',t.tgfoid::bigint,'enabled',t.tgenabled::text,'type',t.tgtype::int,
   'internal',t.tgisinternal,'constraint_oid',t.tgconstraint::bigint,
   'parent_oid',t.tgparentid::bigint,'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,
   'args_count',t.tgnargs::int,'args',encode(t.tgargs,'hex'),'attributes',t.tgattr::text,
   'when',pg_catalog.pg_get_expr(t.tgqual,t.tgrelid),
   'old_table',t.tgoldtable,'new_table',t.tgnewtable)
   ORDER BY t.tgname COLLATE "C") FROM actual_triggers t),
 'functions',(SELECT jsonb_agg(jsonb_build_object(
   'oid',p.oid::bigint,'name',p.proname,'namespace',p.nspname,'owner',p.proowner::bigint,
   'language',p.lanname,'arguments',pg_catalog.oidvectortypes(p.proargtypes),
   'arity',p.pronargs::int,'arg_defaults',p.pronargdefaults::int,
   'returns_trigger',p.prorettype='pg_catalog.trigger'::regtype,'returns_set',p.proretset,
   'volatility',p.provolatile::text,'security_definer',p.prosecdef,'strict',p.proisstrict,
   'leakproof',p.proleakproof,'parallel',p.proparallel::text,'kind',p.prokind::text,
   'support_zero',p.prosupport=0,'config',p.proconfig,
   'source_sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'))
   ORDER BY p.proname COLLATE "C") FROM actual_functions p),
 'native_definitions',jsonb_build_object(
   'triggers',(SELECT jsonb_agg(jsonb_build_array(t.tgname,pg_catalog.pg_get_triggerdef(t.oid,true))
     ORDER BY t.tgname COLLATE "C") FROM actual_triggers t),
   'functions',(SELECT jsonb_agg(jsonb_build_array(p.proname,pg_catalog.pg_get_functiondef(p.oid))
     ORDER BY p.proname COLLATE "C") FROM actual_functions p))
)
"""


def _wrap(issued_sql, lineage_sql):
    return f"""
SELECT jsonb_build_object(
 'schema','{SCHEMA}','issued',({issued_sql}),'lineage',({lineage_sql}),
 'scope_parent',({_SCOPE_PARENT_CATALOG_SQL}),
 'lineage_base',({lineage.CATALOG_SQL}),
 'role',(SELECT jsonb_build_object(
   'oid',r.oid::bigint,'name',r.rolname,'current_user',current_user,'session_user',session_user,
   'setting',current_setting('role'),'login',r.rolcanlogin,'superuser',r.rolsuper,
   'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,'replication',r.rolreplication,
   'bypassrls',r.rolbypassrls,
   'membership',EXISTS(SELECT 1 FROM pg_roles x WHERE x.oid<>r.oid AND pg_has_role(current_user,x.oid,'MEMBER')),
   'create',has_schema_privilege(current_user,'public','CREATE')
      OR has_database_privilege(current_user,current_database(),'CREATE'))
   FROM pg_roles r WHERE r.rolname=current_user),
 'table_guards',(SELECT jsonb_agg(jsonb_build_array(c.relname,NOT c.relispartition,
   NOT EXISTS(SELECT 1 FROM pg_inherits h WHERE h.inhrelid=c.oid OR h.inhparent=c.oid),
   NOT EXISTS(SELECT 1 FROM pg_rewrite r WHERE r.ev_class=c.oid),
   NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid))
   ORDER BY c.relname COLLATE "C") FROM pg_class c
   WHERE c.oid IN('public.demo_collector_current_revision_lineages'::regclass,
                  'public.demo_collector_issued_account_revisions'::regclass)),
 'defaults',(SELECT jsonb_agg(jsonb_build_array(c.relname,a.attnum,a.attname,
   pg_get_expr(d.adbin,d.adrelid),a.attgenerated,a.attidentity)
   ORDER BY c.relname COLLATE "C",a.attnum)
   FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
   LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
   WHERE c.oid IN('public.demo_collector_current_revision_lineages'::regclass,
                  'public.demo_collector_issued_account_revisions'::regclass)
     AND a.attnum>0 AND NOT a.attisdropped),
 'acl',(SELECT jsonb_agg(jsonb_build_array(c.relname,x.grantee::bigint,x.grantor::bigint,
   x.privilege_type,x.is_grantable) ORDER BY c.relname COLLATE "C",x.grantee,x.privilege_type COLLATE "C")
   FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
   WHERE c.oid IN('public.demo_collector_current_revision_lineages'::regclass,
                  'public.demo_collector_issued_account_revisions'::regclass)),
 'custody_internal',(SELECT jsonb_agg(jsonb_build_array(t.oid::bigint,t.tgname,t.tgfoid::bigint,
   t.tgconstraint::bigint,t.tgrelid::bigint,t.tgconstrrelid::bigint) ORDER BY t.oid)
   FROM pg_trigger t WHERE t.tgrelid='public.public_runtime_raw_bundles_v1'::regclass AND t.tgisinternal))
"""


OWNER_CATALOG_SQL = _wrap(_ISSUED_OWNER, _LINEAGE_OWNER)
ACTOR_CATALOG_SQL = _wrap(_ISSUED_ACTOR, _LINEAGE_ACTOR)


def _source_bodies():
    if (
        issued.DDL_SHA256
        != "5f82b31ffe168f803d65e485c8296d1df49d3fb2cda6701b0216fea0d6be24ee"
    ):
        _deny()
    statements = issued.statements()
    if len(statements) != 9:
        _deny()
    result = []
    for statement in statements[1:3]:
        if statement.count("AS $ctcc$") != 1 or statement.count("$ctcc$ LANGUAGE") != 1:
            _deny()
        result.append(statement.split("AS $ctcc$", 1)[1].split("$ctcc$ LANGUAGE", 1)[0])
    return tuple(result)


def _structure(value, *, owner_mode, columns, keys, checks, triggers, functions):
    if (
        type(value) is not dict
        or set(value) != BASE_KEYS | old53.EXTRA_KEYS
        or type(value["owner"]) is not int
        or not 0 < value["owner"] < 2**32
        or type(value["actor"]) is not int
        or not 0 < value["actor"] < 2**32
        or ((value["owner"] == value["actor"]) is not owner_mode)
        or value["table_ok"] is not True
        or value["parent_owners"] is not True
        or value["bad_table_acl"] is not False
        or value["column_acl"] is not False
        or value["public_schema_create"] is not False
        or value["columns"] != columns
        or value["triggers"] != triggers
    ):
        _deny()
    rows = value["functions"]
    if type(rows) is not list or len(rows) != 2:
        _deny()
    for row, (name, body, security) in zip(rows, functions, strict=True):
        if row != {
            "name": name,
            "owner": value["owner"],
            "body": body,
            "security_definer": security,
            "language": "plpgsql",
            "args": 0,
            "returns": "trigger",
            "volatility": "v",
            "parallel": "u",
            "config": ["search_path=pg_catalog, pg_temp"],
            "other_acl": False,
        }:
            _deny()
    rows = value["constraints"]
    if type(rows) is not list or len(rows) != len(keys) + len(checks):
        _deny()
    seen = set()
    for row in rows:
        if (
            type(row) is not dict
            or set(row)
            != {
                "name",
                "kind",
                "validated",
                "deferrable",
                "columns",
                "parent",
                "parent_columns",
            }
            or type(row["name"]) is not str
            or row["name"] in seen
            or row["validated"] is not True
            or row["deferrable"] is not False
        ):
            _deny()
        seen.add(row["name"])
        if row["name"] in keys:
            parent = row["parent"]
            if type(parent) is str and parent.startswith("public."):
                parent = parent[7:]
            if (row["kind"], row["columns"], parent, row["parent_columns"]) != keys[
                row["name"]
            ]:
                _deny()
        elif row["name"] not in checks or row["kind"] != "c":
            _deny()
    if seen != set(keys) | set(checks):
        _deny()


def _context(value, *, role, read_only, parents, schema_name):
    if (
        value["schema_version"] != schema_name
        or value["database_head"] != "0054"
        or value["actor"] != role["oid"]
        or type(value["table_oid"]) is not int
        or not 0 < value["table_oid"] < 2**32
    ):
        _deny()
    server = value["server"]
    if type(server) is not dict or set(server) != {
        "version_num",
        "current_user",
        "session_user",
        "role_setting",
        "database",
        "timezone",
        "search_path",
        "isolation",
        "read_only",
        "synchronous_commit",
        "fsync",
        "full_page_writes",
        "replication_role",
        "recovery",
        "role_oid",
        "superuser",
        "can_login",
    }:
        _deny()
    version = server["version_num"]
    if (
        type(version) is not str
        or not version.isascii()
        or not version.isdecimal()
        or not 170000 <= int(version) < 180000
        or server["role_oid"] != role["oid"]
        or server["current_user"] != role["name"]
        or server["session_user"] != role["name"]
        or server["superuser"] is not role["superuser"]
        or server["can_login"] is not True
        or server["recovery"] is not False
        or type(server["database"]) is not str
        or not server["database"]
    ):
        _deny()
    for name, expected in (
        ("role_setting", "none"),
        ("timezone", "UTC"),
        ("search_path", "pg_catalog, public, pg_temp"),
        ("isolation", "read committed"),
        ("read_only", read_only),
        ("synchronous_commit", "on"),
        ("fsync", "on"),
        ("full_page_writes", "on"),
        ("replication_role", "origin"),
    ):
        if server[name] != expected:
            _deny()
    rows = value["parent_facts"]
    if type(rows) is not list or len(rows) != len(parents):
        _deny()
    identities = {}
    for row in rows:
        if (
            type(row) is not dict
            or set(row)
            != {"name", "oid", "owner", "kind", "persistence", "rls", "force_rls"}
            or row["name"] not in parents
            or row["name"] in identities
            or type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["owner"] != value["owner"]
            or row["kind"] != "r"
            or row["persistence"] != "p"
            or row["rls"] is not False
            or row["force_rls"] is not False
        ):
            _deny()
        identities[row["name"]] = row["oid"]
    return identities


_RI_FIELDS = frozenset(
    (
        "constraint",
        "constraint_oid",
        "child_oid",
        "parent_oid",
        "trigger_oid",
        "name",
        "relation_oid",
        "other_oid",
        "function",
        "namespace",
        "function_oid",
        "expected_function_oid",
        "type",
        "enabled",
        "internal",
        "deferrable",
        "deferred",
        "parent_trigger_oid",
        "args_count",
        "args",
        "attributes",
        "when",
        "old_table",
        "new_table",
        "delete_action",
        "update_action",
        "match",
        "validated",
        "fk_deferrable",
        "fk_deferred",
    )
)
_ROUTINES = {
    "RI_FKey_check_ins": (5, "c", True),
    "RI_FKey_check_upd": (17, "c", True),
    "RI_FKey_restrict_del": (9, "a", False),
    "RI_FKey_noaction_upd": (17, "a", False),
}


def _scope_parent(value, *, owner, table_oid):
    if type(value) is not dict or set(value) != {
        "table",
        "triggers",
        "functions",
        "native_definitions",
    }:
        _deny()
    table = value["table"]
    if (
        type(table) is not dict
        or set(table)
        != {
            "oid",
            "owner",
            "namespace",
            "name",
            "kind",
            "persistence",
            "rls",
            "force_rls",
            "partition",
            "inherited",
            "rules",
            "policies",
        }
        or type(table["oid"]) is not int
        or table["oid"] != table_oid
        or type(table["owner"]) is not int
        or table["owner"] != owner
        or (table["namespace"], table["name"], table["kind"], table["persistence"])
        != ("public", "qualification_account_scopes", "r", "p")
        or any(
            table[name] is not False
            for name in (
                "rls",
                "force_rls",
                "partition",
                "inherited",
                "rules",
                "policies",
            )
        )
    ):
        _deny()
    functions = value["functions"]
    if type(functions) is not list or len(functions) != len(_SCOPE_PARENT_FUNCTIONS):
        _deny()
    by_oid, by_name = {}, {}
    fields = {
        "oid",
        "name",
        "namespace",
        "owner",
        "language",
        "arguments",
        "arity",
        "arg_defaults",
        "returns_trigger",
        "returns_set",
        "volatility",
        "security_definer",
        "strict",
        "leakproof",
        "parallel",
        "kind",
        "support_zero",
        "config",
        "source_sha256",
    }
    for row in functions:
        if (
            type(row) is not dict
            or set(row) != fields
            or type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["oid"] in by_oid
            or type(row["name"]) is not str
            or row["name"] not in _SCOPE_PARENT_FUNCTIONS
            or row["name"] in by_name
            or row["namespace"] != "public"
            or type(row["owner"]) is not int
            or row["owner"] != owner
            or row["language"] != "plpgsql"
            or row["arguments"] != ""
            or any(
                type(row[name]) is not int or row[name] != 0
                for name in ("arity", "arg_defaults")
            )
            or row["returns_trigger"] is not True
            or row["returns_set"] is not False
            or row["volatility"] != "v"
            or row["parallel"] != "u"
            or row["kind"] != "f"
            or any(
                row[name] is not False
                for name in ("security_definer", "strict", "leakproof")
            )
            or row["support_zero"] is not True
            or row["config"] is not None
            or row["source_sha256"] != _SCOPE_PARENT_FUNCTIONS[row["name"]]
        ):
            _deny()
        by_oid[row["oid"]], by_name[row["name"]] = row, row
    triggers = value["triggers"]
    if type(triggers) is not list or len(triggers) != len(_SCOPE_PARENT_TRIGGERS):
        _deny()
    names, identities = set(), set()
    fields = {
        "oid",
        "name",
        "relation_oid",
        "function_oid",
        "enabled",
        "type",
        "internal",
        "constraint_oid",
        "parent_oid",
        "deferrable",
        "deferred",
        "args_count",
        "args",
        "attributes",
        "when",
        "old_table",
        "new_table",
    }
    for row in triggers:
        if (
            type(row) is not dict
            or set(row) != fields
            or type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["oid"] in identities
            or type(row["name"]) is not str
            or row["name"] not in _SCOPE_PARENT_TRIGGERS
            or row["name"] in names
            or type(row["relation_oid"]) is not int
            or row["relation_oid"] != table_oid
            or type(row["function_oid"]) is not int
            or row["function_oid"] not in by_oid
            or row["enabled"] != "O"
            or row["internal"] is not False
            or any(
                type(row[name]) is not int or row[name] != 0
                for name in ("constraint_oid", "parent_oid", "args_count")
            )
            or row["deferrable"] is not False
            or row["deferred"] is not False
            or row["args"] != ""
            or row["attributes"] != ""
            or any(row[name] is not None for name in ("when", "old_table", "new_table"))
        ):
            _deny()
        expected_type, expected_function = _SCOPE_PARENT_TRIGGERS[row["name"]]
        if (
            type(row["type"]) is not int
            or row["type"] != expected_type
            or by_oid[row["function_oid"]]["name"] != expected_function
        ):
            _deny()
        names.add(row["name"])
        identities.add(row["oid"])
    definitions = value["native_definitions"]
    if type(definitions) is not dict or set(definitions) != {"triggers", "functions"}:
        _deny()
    for name, expected in (
        ("triggers", _SCOPE_PARENT_TRIGGERS),
        ("functions", _SCOPE_PARENT_FUNCTIONS),
    ):
        rows = definitions[name]
        if (
            type(rows) is not list
            or len(rows) != len(expected)
            or any(
                type(row) is not list
                or len(row) != 2
                or type(row[0]) is not str
                or row[0] not in expected
                or type(row[1]) is not str
                or not row[1]
                for row in rows
            )
            or {row[0] for row in rows} != set(expected)
        ):
            _deny()
    return definitions


def _definitions(value, *, keys, checks, functions, triggers):
    definitions = value["native_definitions"]
    expected = {
        "functions": set(functions),
        "constraints": set(keys) | set(checks),
        "indexes": {name for name, spec in keys.items() if spec[0] in {"p", "u"}},
        "triggers": {row[0] for row in triggers},
    }
    if type(definitions) is not dict or set(definitions) != set(expected) | {
        "custody_triggers"
    }:
        _deny()
    for key, names in expected.items():
        rows = definitions[key]
        if (
            type(rows) is not list
            or len(rows) != len(names)
            or any(
                type(row) is not list
                or len(row) != 2
                or type(row[0]) is not str
                or type(row[1]) is not str
                or not row[1]
                for row in rows
            )
            or {row[0] for row in rows} != names
        ):
            _deny()
    custody = definitions["custody_triggers"]
    if (
        type(custody) is not list
        or len(custody) != 3
        or any(
            type(row) is not list
            or len(row) != 2
            or any(type(part) is not str or not part for part in row)
            for row in custody
        )
        or len({row[0] for row in custody}) != 3
    ):
        _deny()
    return definitions


def _ri(value, *, keys, parent_oids, incoming):
    specs = {}
    for name, spec in keys.items():
        if spec[0] == "f":
            specs[name] = value["table_oid"], parent_oids[spec[2]], frozenset(_ROUTINES)
    for name, child, parent in incoming:
        if name in specs:
            _deny()
        specs[name] = (
            child,
            parent,
            frozenset(("RI_FKey_restrict_del", "RI_FKey_noaction_upd")),
        )
    rows = value["ri_triggers"]
    if type(rows) is not list or len(rows) != sum(
        len(spec[2]) for spec in specs.values()
    ):
        _deny()
    seen, result, constraints = set(), {}, {}
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != _RI_FIELDS
            or row["constraint"] not in specs
        ):
            _deny()
        child, parent, routines = specs[row["constraint"]]
        if (
            row["function"] not in routines
            or (row["constraint"], row["function"]) in seen
        ):
            _deny()
        seen.add((row["constraint"], row["function"]))
        for name in (
            "constraint_oid",
            "child_oid",
            "parent_oid",
            "trigger_oid",
            "relation_oid",
            "other_oid",
            "function_oid",
            "expected_function_oid",
        ):
            if type(row[name]) is not int or not 0 < row[name] < 2**32:
                _deny()
        identity = row["constraint_oid"], row["child_oid"], row["parent_oid"]
        if constraints.setdefault(row["constraint"], identity) != identity:
            _deny()
        kind, prefix, on_child = _ROUTINES[row["function"]]
        if (
            row["child_oid"] != child
            or row["parent_oid"] != parent
            or row["name"]
            != "RI_ConstraintTrigger_" + prefix + "_" + str(row["trigger_oid"])
            or type(row["type"]) is not int
            or row["type"] != kind
            or row["relation_oid"] != (child if on_child else parent)
            or row["other_oid"] != (parent if on_child else child)
            or row["namespace"] != "pg_catalog"
            or row["function_oid"] != row["expected_function_oid"]
            or row["enabled"] != "O"
            or row["internal"] is not True
            or row["deferrable"] is not False
            or row["deferred"] is not False
            or type(row["parent_trigger_oid"]) is not int
            or row["parent_trigger_oid"] != 0
            or type(row["args_count"]) is not int
            or row["args_count"] != 0
            or row["args"] != ""
            or row["attributes"] != ""
            or row["when"] is not None
            or row["old_table"] is not None
            or row["new_table"] is not None
            or (row["delete_action"], row["update_action"], row["match"])
            != ("r", "a", "s")
            or row["validated"] is not True
            or row["fk_deferrable"] is not False
            or row["fk_deferred"] is not False
            or row["trigger_oid"] in result
        ):
            _deny()
        result[row["trigger_oid"]] = row
    if len({item[0] for item in constraints.values()}) != len(specs):
        _deny()
    return result


def _incoming_custody(value):
    rows = value["custody_incoming"]
    expected = {
        "fk_collector_current_custody": (lineage.TABLE, "{15}"),
        "fk_collector_issued_hwm_custody": (issued.TABLE, "{16}"),
    }
    if type(rows) is not list or len(rows) != 2:
        _deny()
    seen = set()
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 10
            or row[0] not in expected
            or row[0] in seen
            or row[1] not in {expected[row[0]][0], "public." + expected[row[0]][0]}
            or row[2:7] != [expected[row[0]][1], "{1}", "r", "a", "s"]
            or row[7] is not True
            or row[8] is not False
            or row[9] is not False
        ):
            _deny()
        seen.add(row[0])


def _acl(rows, *, owner, actor, owner_mode):
    if type(rows) is not list:
        _deny()
    seen = set()
    privileges = {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
        "MAINTAIN",
    }
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 5
            or row[0] not in {lineage.TABLE, issued.TABLE}
            or type(row[1]) is not int
            or row[2] != owner
            or type(row[4]) is not bool
            or (row[0], row[1], row[3]) in seen
        ):
            _deny()
        seen.add((row[0], row[1], row[3]))
        if row[1] == owner:
            if row[3] not in privileges:
                _deny()
        elif (
            owner_mode
            or row[1] != actor
            or row[3] not in {"SELECT", "INSERT"}
            or row[4] is not False
        ):
            _deny()
    expected = {
        (table, owner, right)
        for table in (lineage.TABLE, issued.TABLE)
        for right in privileges
    }
    if not owner_mode:
        expected |= {
            (table, actor, right)
            for table in (lineage.TABLE, issued.TABLE)
            for right in ("SELECT", "INSERT")
        }
    if seen != expected:
        _deny()


def _observe(value, *, owner_mode, read_only):
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema",
            "issued",
            "lineage",
            "lineage_base",
            "role",
            "table_guards",
            "defaults",
            "acl",
            "custody_internal",
            "scope_parent",
        }
        or value["schema"] != SCHEMA
    ):
        _deny()
    role = value["role"]
    if (
        type(role) is not dict
        or set(role)
        != {
            "oid",
            "name",
            "current_user",
            "session_user",
            "setting",
            "login",
            "superuser",
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "membership",
            "create",
        }
        or type(role["oid"]) is not int
        or not 0 < role["oid"] < 2**32
        or type(role["name"]) is not str
        or not role["name"]
        or role["current_user"] != role["name"]
        or role["session_user"] != role["name"]
        or role["setting"] != "none"
        or role["login"] is not True
        or role["superuser"] is not owner_mode
    ):
        _deny()
    if not owner_mode and any(
        role[key] is not False
        for key in (
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "membership",
            "create",
        )
    ):
        _deny()
    guard_body, immutable_body = _source_bodies()
    current, previous = value["issued"], value["lineage"]
    _structure(
        current,
        owner_mode=owner_mode,
        columns=_ISSUED_COLUMNS,
        keys=_ISSUED_KEYS,
        checks=_ISSUED_CHECKS,
        triggers=_ISSUED_TRIGGERS,
        functions=(
            (issued.GUARD, guard_body, True),
            (issued.IMMUTABLE, immutable_body, False),
        ),
    )
    _structure(
        previous,
        owner_mode=owner_mode,
        columns=lineage.COLUMNS,
        keys=lineage.KEYS,
        checks=lineage.CHECKS,
        triggers=lineage.TRIGGERS,
        functions=(
            (lineage.GUARD, lineage.GUARD_BODY, True),
            (lineage.IMMUTABLE, lineage.IMMUTABLE_BODY, False),
        ),
    )
    if (
        current["owner"] != previous["owner"]
        or current["table_oid"] == previous["table_oid"]
    ):
        _deny()
    if owner_mode:
        if role["oid"] != current["owner"]:
            _deny()
    else:
        # Unchanged restricted actor verifier, actual writer query only. The
        # complete independently measured lineage_base remains untouched.
        lineage.verify_catalog(value["lineage_base"])
        if any(value["lineage_base"][key] != previous[key] for key in BASE_KEYS):
            _deny()
    current_parents = _context(
        current,
        role=role,
        read_only=read_only,
        parents=PARENTS,
        schema_name="ctcc.collector_current0054.actual_issued_catalog.v1",
    )
    previous_parents = _context(
        previous,
        role=role,
        read_only=read_only,
        parents=old53.PARENTS,
        schema_name="ctcc.collector_current0053.actual_owner_catalog.v1",
    )
    if (
        current_parents[lineage.TABLE] != previous["table_oid"]
        or any(current_parents[key] != previous_parents[key] for key in old53.PARENTS)
        or current["server"]["database"] != previous["server"]["database"]
    ):
        _deny()
    if value["table_guards"] != [
        [lineage.TABLE, True, True, True, True],
        [issued.TABLE, True, True, True, True],
    ]:
        _deny()
    expected_defaults = []
    for table, columns in (
        (lineage.TABLE, lineage.COLUMNS),
        (issued.TABLE, _ISSUED_COLUMNS),
    ):
        for number, column in enumerate(columns, 1):
            expected_defaults.append(
                [
                    table,
                    number,
                    column[0],
                    "clock_timestamp()" if column[0] == "db_recorded_at" else None,
                    "",
                    "",
                ]
            )
    if value["defaults"] != expected_defaults:
        _deny()
    _acl(value["acl"], owner=current["owner"], actor=role["oid"], owner_mode=owner_mode)
    current_defs = _definitions(
        current,
        keys=_ISSUED_KEYS,
        checks=_ISSUED_CHECKS,
        functions=(issued.GUARD, issued.IMMUTABLE),
        triggers=_ISSUED_TRIGGERS,
    )
    previous_defs = _definitions(
        previous,
        keys=lineage.KEYS,
        checks=lineage.CHECKS,
        functions=(lineage.GUARD, lineage.IMMUTABLE),
        triggers=lineage.TRIGGERS,
    )
    scope_defs = _scope_parent(
        value["scope_parent"],
        owner=current["owner"],
        table_oid=current_parents["qualification_account_scopes"],
    )
    current_ri = _ri(
        current, keys=_ISSUED_KEYS, parent_oids=current_parents, incoming=()
    )
    previous_ri = _ri(
        previous,
        keys=lineage.KEYS,
        parent_oids=previous_parents,
        incoming=(
            (
                "fk_collector_issued_source_lineage",
                current["table_oid"],
                previous["table_oid"],
            ),
        ),
    )
    if len(current_ri) != 16 or len(previous_ri) != 18:
        _deny()
    combined = dict(current_ri)
    for oid, row in previous_ri.items():
        if oid in combined and combined[oid] != row:
            _deny()
        combined[oid] = row
    if len(combined) != 32:
        _deny()
    _incoming_custody(current)
    _incoming_custody(previous)
    if current["custody_incoming"] != previous["custody_incoming"]:
        _deny()
    custody_oid = current_parents["public_runtime_raw_bundles_v1"]
    expected_internal = sorted(
        [
            row["trigger_oid"],
            row["name"],
            row["function_oid"],
            row["constraint_oid"],
            row["relation_oid"],
            row["other_oid"],
        ]
        for row in combined.values()
        if row["relation_oid"] == custody_oid
    )
    if len(expected_internal) != 4 or value["custody_internal"] != expected_internal:
        _deny()
    if current_defs["custody_triggers"] != previous_defs["custody_triggers"]:
        _deny()
    native_sha256 = hashlib.sha256(
        canonical(
            {
                "issued": current_defs,
                "lineage": previous_defs,
                "scope_parent": scope_defs,
            }
        )
    ).hexdigest()
    return {
        "schema": "ctcc.collector_current0054.catalog_observation.v1",
        "database_head": "0054",
        "issuer_ddl_sha256": issued.DDL_SHA256,
        "lineage_ddl_sha256": lineage.DDL_SHA256,
        "native_definitions_sha256": native_sha256,
        "role_oid": role["oid"],
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }


def _observed(value, *, owner_mode, read_only):
    try:
        return _observe(value, owner_mode=owner_mode, read_only=read_only)
    except Current0054CatalogError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        _deny()


def observe_actual_owner_catalog(value):
    return _observed(value, owner_mode=True, read_only="on")


def _required_baseline():
    if (
        PG17_NATIVE_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
        or type(PG17_NATIVE_BASELINE_SHA256) is not str
        or len(PG17_NATIVE_BASELINE_SHA256) != 64
        or any(c not in "0123456789abcdef" for c in PG17_NATIVE_BASELINE_SHA256)
    ):
        raise Current0054CatalogError(
            "current0054_genuine_native_baseline_pending_or_changed"
        ) from None
    return PG17_NATIVE_BASELINE_SHA256


def _admit(observed, schema_name):
    if observed["native_definitions_sha256"] != _required_baseline():
        _deny()
    return {**observed, "schema": schema_name, "native_baseline_admission": True}


def verify_actual_owner_catalog(value):
    return _admit(
        observe_actual_owner_catalog(value),
        "ctcc.collector_current0054.owner_installation.v1",
    )


def verify_actual_writer_catalog(value):
    return _admit(
        _observed(value, owner_mode=False, read_only="off"),
        "ctcc.collector_current0054.actual_writer_catalog.v1",
    )


def verify_actual_readonly_writer_catalog(value):
    return _admit(
        _observed(value, owner_mode=False, read_only="on"),
        "ctcc.collector_current0054.actual_readonly_writer_catalog.v1",
    )
