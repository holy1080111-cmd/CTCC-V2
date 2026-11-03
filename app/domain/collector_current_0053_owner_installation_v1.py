"""Current0053 owner observation and separate strict native installation gate.

This reads the real migration owner. It never calls the restricted actor
catalog verifier, rewrites an observed actor, grants permissions, or issues an
account/custody/reader capability. The new native baseline is deliberately
pending until a genuine PG17 capture is reviewed.
"""

import hashlib

from app.domain import account_revision_collector_lineage_schema_v1 as schema
from app.domain.source_primitives import canonical

CATALOG_SCHEMA = "ctcc.collector_current0053.actual_owner_catalog.v1"
PG17_NATIVE_BASELINE_STATUS = "SEALED_FROM_REVIEWED_NATIVE_PG17"
PG17_NATIVE_BASELINE_SHA256 = (
    "4f9083b6261b961eccf6a34595828944c4d60850f7ed7927de046b71041c81f7"
)
COLUMNS, KEYS, CHECKS = schema.COLUMNS, schema.KEYS, schema.CHECKS
GUARD, IMMUTABLE = schema.GUARD, schema.IMMUTABLE
GUARD_BODY, IMMUTABLE_BODY = schema.GUARD_BODY, schema.IMMUTABLE_BODY
TRIGGERS = schema.TRIGGERS
PARENTS = frozenset(
    (
        "qualification_account_scopes",
        "demo_account_controls",
        "demo_control_journal",
        "demo_account_capture_events",
        "public_runtime_raw_bundles_v1",
    )
)
EXTRA_KEYS = frozenset(
    (
        "schema_version",
        "database_head",
        "table_oid",
        "server",
        "parent_facts",
        "native_definitions",
        "ri_triggers",
        "custody_incoming",
    )
)


class Current0053OwnerError(ValueError):
    pass


def _deny(_reason):
    raise Current0053OwnerError("current0053_owner_catalog_unavailable") from None


def _once(source, original, replacement):
    if source.count(original) != 1:
        raise RuntimeError("current0053_owner_source_template_changed")
    return source.replace(original, replacement)


_OWNER_META = r"""
 'schema_version','ctcc.collector_current0053.actual_owner_catalog.v1',
 'database_head',(SELECT version_num FROM public.alembic_version),
 'table_oid',c.oid::bigint,
 'server',jsonb_build_object(
    'version_num',current_setting('server_version_num'),
    'current_user',current_user,'session_user',session_user,
    'role_setting',current_setting('role'),'database',current_database(),
    'timezone',current_setting('TimeZone'),'search_path',current_setting('search_path'),
    'isolation',current_setting('transaction_isolation'),
    'read_only',current_setting('transaction_read_only'),
    'synchronous_commit',current_setting('synchronous_commit'),
    'fsync',current_setting('fsync'),'full_page_writes',current_setting('full_page_writes'),
    'replication_role',current_setting('session_replication_role'),
    'recovery',pg_is_in_recovery(),
    'role_oid',(SELECT oid::bigint FROM pg_roles WHERE rolname=session_user),
    'superuser',(SELECT rolsuper FROM pg_roles WHERE rolname=session_user),
    'can_login',(SELECT rolcanlogin FROM pg_roles WHERE rolname=session_user)),
 'parent_facts',(SELECT jsonb_agg(jsonb_build_object(
    'name',p.relname,'oid',p.oid::bigint,'owner',p.relowner::bigint,
    'kind',p.relkind::text,'persistence',p.relpersistence::text,
    'rls',p.relrowsecurity,'force_rls',p.relforcerowsecurity)
    ORDER BY p.relname COLLATE "C")
    FROM pg_class p JOIN pg_namespace n ON n.oid=p.relnamespace
    WHERE n.nspname='public' AND p.relname IN(
      'qualification_account_scopes','demo_account_controls','demo_control_journal',
      'demo_account_capture_events','public_runtime_raw_bundles_v1')),
 'native_definitions',jsonb_build_object(
    'functions',(SELECT jsonb_agg(jsonb_build_array(p.proname,pg_get_functiondef(p.oid))
      ORDER BY p.proname COLLATE "C")
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname IN(
        'collector_current_revision_lineage_guard_v1','collector_current_revision_lineage_immutable_v1')),
    'constraints',(SELECT jsonb_agg(jsonb_build_array(k.conname,pg_get_constraintdef(k.oid,true))
      ORDER BY k.conname COLLATE "C") FROM pg_constraint k WHERE k.conrelid=c.oid),
    'indexes',(SELECT jsonb_agg(jsonb_build_array(i.relname,pg_get_indexdef(i.oid))
      ORDER BY i.relname COLLATE "C") FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
      WHERE x.indrelid=c.oid),
    'triggers',(SELECT jsonb_agg(jsonb_build_array(t.tgname,pg_get_triggerdef(t.oid,true))
      ORDER BY t.tgname COLLATE "C") FROM pg_trigger t
      WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
    'custody_triggers',(SELECT jsonb_agg(jsonb_build_array(t.tgname,pg_get_triggerdef(t.oid,true))
      ORDER BY t.tgname COLLATE "C") FROM pg_trigger t
      WHERE t.tgrelid='public.public_runtime_raw_bundles_v1'::regclass AND NOT t.tgisinternal)),
 'ri_triggers',(SELECT jsonb_agg(jsonb_build_object(
    'constraint',k.conname,'constraint_oid',k.oid::bigint,
    'child_oid',k.conrelid::bigint,'parent_oid',k.confrelid::bigint,
    'trigger_oid',t.oid::bigint,'name',t.tgname,'relation_oid',t.tgrelid::bigint,
    'other_oid',t.tgconstrrelid::bigint,'function',p.proname,
    'namespace',n.nspname,'function_oid',p.oid::bigint,
    'expected_function_oid',to_regprocedure('pg_catalog.'||quote_ident(p.proname)||'()')::oid::bigint,
    'type',t.tgtype,'enabled',t.tgenabled::text,'internal',t.tgisinternal,
    'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,
    'parent_trigger_oid',t.tgparentid::bigint,'args_count',t.tgnargs,
    'args',encode(t.tgargs,'hex'),'attributes',t.tgattr::text,
    'when',pg_get_expr(t.tgqual,t.tgrelid),
    'old_table',t.tgoldtable,'new_table',t.tgnewtable,
    'delete_action',k.confdeltype::text,'update_action',k.confupdtype::text,
    'match',k.confmatchtype::text,'validated',k.convalidated,
    'fk_deferrable',k.condeferrable,'fk_deferred',k.condeferred)
    ORDER BY k.conname COLLATE "C",p.proname COLLATE "C",t.oid)
    FROM pg_trigger t JOIN pg_constraint k ON k.oid=t.tgconstraint
    JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE (k.conrelid=c.oid AND k.contype='f' AND t.tgisinternal)
       OR (t.tgrelid=c.oid AND t.tgisinternal)),
 'custody_incoming',(SELECT jsonb_agg(jsonb_build_array(
    k.conname,k.conrelid::regclass::text,k.conkey::text,k.confkey::text,
    k.confdeltype::text,k.confupdtype::text,k.confmatchtype::text,
    k.convalidated,k.condeferrable,k.condeferred)
    ORDER BY k.conname COLLATE "C",k.oid)
    FROM pg_constraint k WHERE k.confrelid='public.public_runtime_raw_bundles_v1'::regclass
      AND k.contype='f'),
"""

CATALOG_SQL = _once(
    _once(
        schema.CATALOG_SQL,
        "SELECT jsonb_build_object(\n",
        "SELECT jsonb_build_object(\n" + _OWNER_META,
    ),
    "NOT pg_has_role(session_user,c.relowner,'MEMBER')",
    "current_user=session_user AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=session_user) "
    "AND NOT c.relispartition "
    "AND NOT EXISTS(SELECT 1 FROM pg_inherits h WHERE h.inhrelid=c.oid OR h.inhparent=c.oid) "
    "AND NOT EXISTS(SELECT 1 FROM pg_rewrite r WHERE r.ev_class=c.oid) "
    "AND NOT EXISTS(SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid)",
)


def _owner_context(value):
    if value["schema_version"] != CATALOG_SCHEMA or value["database_head"] != "0053":
        _deny("head")
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
        _deny("server")
    version = server["version_num"]
    if (
        type(version) is not str
        or not version.isascii()
        or not version.isdecimal()
        or not 170000 <= int(version) < 180000
        or type(server["role_oid"]) is not int
        or server["role_oid"] != value["owner"]
        or server["superuser"] is not True
        or server["can_login"] is not True
        or server["recovery"] is not False
        or type(server["database"]) is not str
        or server["read_only"] not in {"on", "off"}
        or not server["database"]
        or type(server["current_user"]) is not str
        or not server["current_user"]
        or server["current_user"] != server["session_user"]
    ):
        _deny("owner")
    for name, expected in (
        ("role_setting", "none"),
        ("timezone", "UTC"),
        ("search_path", "pg_catalog, public, pg_temp"),
        ("isolation", "read committed"),
        ("synchronous_commit", "on"),
        ("fsync", "on"),
        ("full_page_writes", "on"),
        ("replication_role", "origin"),
    ):
        if server[name] != expected:
            _deny("session")
    parents = value["parent_facts"]
    if type(parents) is not list or len(parents) != len(PARENTS):
        _deny("parents")
    names = set()
    for row in parents:
        if (
            type(row) is not dict
            or set(row)
            != {"name", "oid", "owner", "kind", "persistence", "rls", "force_rls"}
            or type(row["name"]) is not str
            or row["name"] in names
            or row["name"] not in PARENTS
            or type(row["oid"]) is not int
            or row["oid"] <= 0
            or type(row["owner"]) is not int
            or row["owner"] != value["owner"]
            or row["kind"] != "r"
            or row["persistence"] != "p"
            or row["rls"] is not False
            or row["force_rls"] is not False
        ):
            _deny("parents")
        names.add(row["name"])


def _native_observation(value):
    if type(value["table_oid"]) is not int or not 0 < value["table_oid"] < 2**32:
        _deny("table_identity")
    definitions = value["native_definitions"]
    expected_names = {
        "functions": {GUARD, IMMUTABLE},
        "constraints": set(KEYS) | CHECKS,
        "indexes": {name for name, spec in KEYS.items() if spec[0] in {"p", "u"}},
        "triggers": {row[0] for row in TRIGGERS},
    }
    if type(definitions) is not dict or set(definitions) != set(expected_names) | {
        "custody_triggers"
    }:
        _deny("native")
    for name, names in expected_names.items():
        rows = definitions[name]
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
            _deny("native")
    if (
        type(definitions["custody_triggers"]) is not list
        or len(definitions["custody_triggers"]) != 3
        or any(
            type(row) is not list
            or len(row) != 2
            or any(type(v) is not str or not v for v in row)
            for row in definitions["custody_triggers"]
        )
        or len({row[0] for row in definitions["custody_triggers"]}) != 3
    ):
        _deny("native")
    foreign_keys = {name for name, spec in KEYS.items() if spec[0] == "f"}
    routines = {
        "RI_FKey_check_ins": (5, "c", True),
        "RI_FKey_check_upd": (17, "c", True),
        "RI_FKey_restrict_del": (9, "a", False),
        "RI_FKey_noaction_upd": (17, "a", False),
    }
    rows = value["ri_triggers"]
    if type(rows) is not list or len(rows) != len(foreign_keys) * len(routines):
        _deny("RI")
    seen = set()
    constraint_identities = {}
    parent_identities = {row["name"]: row["oid"] for row in value["parent_facts"]}
    row_fields = {
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
    }
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != row_fields
            or row["constraint"] not in foreign_keys
            or row["function"] not in routines
        ):
            _deny("RI")
        key = row["constraint"], row["function"]
        if key in seen:
            _deny("RI")
        seen.add(key)
        identity = row["constraint_oid"], row["child_oid"], row["parent_oid"]
        previous_identity = constraint_identities.setdefault(
            row["constraint"], identity
        )
        if identity != previous_identity:
            _deny("RI_identity")
        if any(
            type(row[n]) is not int or not 0 < row[n] < 2**32
            for n in (
                "constraint_oid",
                "child_oid",
                "parent_oid",
                "trigger_oid",
                "relation_oid",
                "other_oid",
                "function_oid",
                "expected_function_oid",
            )
        ):
            _deny("RI")
        kind, prefix, child = routines[row["function"]]
        if (
            row["child_oid"] != value["table_oid"]
            or row["parent_oid"] != parent_identities[KEYS[row["constraint"]][2]]
            or any(
                type(row[name]) is not int
                for name in ("type", "parent_trigger_oid", "args_count")
            )
            or row["name"]
            != "RI_ConstraintTrigger_" + prefix + "_" + str(row["trigger_oid"])
            or row["type"] != kind
            or row["enabled"] != "O"
            or row["internal"] is not True
            or row["namespace"] != "pg_catalog"
            or row["function_oid"] != row["expected_function_oid"]
            or row["relation_oid"] != row["child_oid" if child else "parent_oid"]
            or row["other_oid"] != row["parent_oid" if child else "child_oid"]
            or row["deferrable"] is not False
            or row["deferred"] is not False
            or row["parent_trigger_oid"] != 0
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
        ):
            _deny("RI")
    if len({row["trigger_oid"] for row in rows}) != len(rows):
        _deny("RI_identity")
    if len({item[0] for item in constraint_identities.values()}) != len(foreign_keys):
        _deny("RI_identity")
    incoming = value["custody_incoming"]
    if (
        type(incoming) is not list
        or len(incoming) != 1
        or type(incoming[0]) is not list
        or len(incoming[0]) != 10
        or incoming[0][0] != "fk_collector_current_custody"
        or incoming[0][1]
        not in {
            "demo_collector_current_revision_lineages",
            "public.demo_collector_current_revision_lineages",
        }
        or incoming[0][2:7] != ["{15}", "{1}", "r", "a", "s"]
        or incoming[0][7] is not True
        or incoming[0][8] is not False
        or incoming[0][9] is not False
    ):
        _deny("incoming")
    return hashlib.sha256(canonical(definitions)).hexdigest()


def observe_owner_installation_catalog(value):
    """Validate actual owner/source structure; retain an unqualified native observation."""
    try:
        _verify_owner_structure(value)
        _owner_context(value)
        native_sha256 = _native_observation(value)
        return {
            "schema": "ctcc.collector_current0053.owner_observation.v1",
            "database_head": "0053",
            "ddl_sha256": schema.DDL_SHA256,
            "native_definitions_sha256": native_sha256,
            "native_baseline_admission": False,
            "runtime_admission": False,
            "account_complete": False,
            "execution_authority": False,
        }
    except Current0053OwnerError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        _deny("catalog")


def _required_native_baseline():
    if (
        PG17_NATIVE_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
        or type(PG17_NATIVE_BASELINE_SHA256) is not str
        or len(PG17_NATIVE_BASELINE_SHA256) != 64
        or any(c not in "0123456789abcdef" for c in PG17_NATIVE_BASELINE_SHA256)
    ):
        raise Current0053OwnerError(
            "current0053_genuine_native_baseline_pending_or_changed"
        ) from None
    return PG17_NATIVE_BASELINE_SHA256


def expected_owner_installation_schema():
    """A declaration is unavailable while the new genuine native seal is pending."""
    return {
        "schema": "ctcc.collector_current0053.owner_installation.v1",
        "database_head": "0053",
        "ddl_sha256": schema.DDL_SHA256,
        "native_definitions_sha256": _required_native_baseline(),
        "native_baseline_admission": True,
        "runtime_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }


def verify_owner_installation_catalog(value):
    """The formal gate has no seal-off flag and cannot borrow the0052 baseline."""
    observed = observe_owner_installation_catalog(value)
    if value["server"]["read_only"] != "on":
        raise Current0053OwnerError(
            "current0053_formal_owner_read_only_required"
        ) from None
    if observed["native_definitions_sha256"] != _required_native_baseline():
        raise Current0053OwnerError(
            "current0053_genuine_native_baseline_pending_or_changed"
        ) from None
    result = {
        **observed,
        "schema": "ctcc.collector_current0053.owner_installation.v1",
        "native_baseline_admission": True,
    }
    if canonical(result) != canonical(expected_owner_installation_schema()):
        _deny("native_installation")
    return result


def _verify_owner_structure(value):
    deny = _deny

    if (
        type(value) is not dict
        or set(value)
        != {
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
            "schema_version",
            "database_head",
            "table_oid",
            "server",
            "parent_facts",
            "native_definitions",
            "ri_triggers",
            "custody_incoming",
        }
        or type(value["owner"]) is not int
        or type(value["actor"]) is not int
        or value["owner"] <= 0
        or value["actor"] <= 0
        or value["owner"] != value["actor"]
        or value["table_ok"] is not True
        or value["parent_owners"] is not True
        or value["bad_table_acl"] is not False
        or value["column_acl"] is not False
        or value["public_schema_create"] is not False
        or value["columns"] != COLUMNS
        or value["triggers"] != TRIGGERS
    ):
        deny("collector_lineage_schema_or_role_changed")
    functions = value["functions"]
    if type(functions) is not list or len(functions) != 2:
        deny("collector_lineage_schema_or_role_changed")
    for item, name, body, security in zip(
        functions,
        (GUARD, IMMUTABLE),
        (GUARD_BODY, IMMUTABLE_BODY),
        (True, False),
        strict=True,
    ):
        if (
            type(item) is not dict
            or set(item)
            != {
                "name",
                "owner",
                "body",
                "security_definer",
                "language",
                "args",
                "returns",
                "volatility",
                "parallel",
                "config",
                "other_acl",
            }
            or item
            != {
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
            }
        ):
            deny("collector_lineage_schema_or_role_changed")
    constraints = value["constraints"]
    if type(constraints) is not list or len(constraints) != len(KEYS) + len(CHECKS):
        deny("collector_lineage_schema_or_role_changed")
    seen = set()
    for item in constraints:
        if (
            type(item) is not dict
            or set(item)
            != {
                "name",
                "kind",
                "validated",
                "deferrable",
                "columns",
                "parent",
                "parent_columns",
            }
            or type(item["name"]) is not str
            or item["name"] in seen
            or item["validated"] is not True
            or item["deferrable"] is not False
        ):
            deny("collector_lineage_schema_or_role_changed")
        seen.add(item["name"])
        if item["name"] in KEYS:
            kind, columns, parent, parent_columns = KEYS[item["name"]]
            actual_parent = item["parent"]
            if type(actual_parent) is str and actual_parent.startswith("public."):
                actual_parent = actual_parent[7:]
            if (
                item["kind"],
                item["columns"],
                actual_parent,
                item["parent_columns"],
            ) != (kind, columns, parent, parent_columns):
                deny("collector_lineage_schema_or_role_changed")
        elif item["name"] not in CHECKS or item["kind"] != "c":
            deny("collector_lineage_schema_or_role_changed")
    if seen != set(KEYS) | CHECKS:
        deny("collector_lineage_schema_or_role_changed")
