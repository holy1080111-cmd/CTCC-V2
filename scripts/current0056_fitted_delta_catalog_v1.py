"""Inert DB0056 fitted-delta physical catalog observation, never startup authority.

The exact native reference is deliberately unsealed. A future version may
compare two independent fresh PostgreSQL 17.11 installations and pin a
reviewed digest. This module does not install roles, run migrations or open a
database while the reference is unsealed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MIGRATION_SHA256 = "82dacd7797d7e5f2286317fb2445c1a5e4986599733617d2ff8f3b8553b3ce3b"
EXPECTED_NATIVE_REFERENCE_SHA256 = ""  # Must be established by two real fresh installs.
SCHEMA = "ctcc.current0056.fitted_delta.physical_catalog.inert.v1"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_TABLE_BY_KIND = {
    "binding": "gate3_fitted_candidate_bindings",
    "ack": "gate3_fitted_candidate_acks",
    "evaluation": "gate3_fitted_candidate_evaluations",
}
TABLES = frozenset(_TABLE_BY_KIND.values())
INTERNAL = frozenset(
    {
        "gate3_fitted_role(integer)",
        "gate3_fitted_durability(boolean)",
        "gate3_fitted_hash(text)",
        "gate3_fitted_json_hash(jsonb)",
        "gate3_fitted_lock(text)",
        "gate3_fitted_seal(text)",
        "gate3_fitted_holdout_access(jsonb,timestamp with time zone)",
        "gate3_fitted_binding_matches(text,text,text,public.gate3_preregistration_seals)",
        "gate3_fitted_binding_insert_guard()",
        "gate3_fitted_ack_insert_guard()",
        "gate3_fitted_evaluation_insert_guard()",
        "gate3_fitted_immutable()",
        "gate3_fitted_commit_guard()",
        "gate3_fitted_candidate_claim_guard()",
    }
)
APIS = {
    "gate3_fitted_binding_append(text,text,text)": ("publisher",),
    "gate3_fitted_binding_read(text)": ("publisher", "observer", "reader", "evaluator"),
    "gate3_fitted_binding_ack_append(text,text)": ("observer",),
    "gate3_fitted_binding_ack_read(text)": ("observer", "reader", "evaluator"),
    "gate3_fitted_evaluation_append(text,text,text,text,text,text)": ("evaluator",),
    "gate3_fitted_evaluation_read(text)": ("evaluator",),
}
TRIGGERS = frozenset(
    {
        *(
            f"gate3_fitted_{kind}_{suffix}"
            for kind in ("binding", "ack", "evaluation")
            for suffix in ("insert_guard", "immutable", "no_truncate", "commit_guard")
        ),
        "gate3_fitted_candidate_claim_guard",
    }
)
INDEXES = frozenset(
    {
        "pk_gate3_fitted_candidate_bindings",
        "uq_gate3_fitted_candidate_bindings_binding_sha256",
        "pk_gate3_fitted_candidate_acks",
        "pk_gate3_fitted_candidate_evaluations",
    }
)
_CONSTRAINT_TYPES_BY_KIND = {
    "binding": {
        "pk_gate3_fitted_candidate_bindings": "p",
        "fk_gate3_fitted_binding_seal": "f",
        "uq_gate3_fitted_candidate_bindings_binding_sha256": "u",
        "ck_gate3_fitted_candidate_bindings_hashes": "c",
        "ck_gate3_fitted_candidate_bindings_payload": "c",
        "ck_gate3_fitted_candidate_bindings_xids": "c",
    },
    "ack": {
        "pk_gate3_fitted_candidate_acks": "p",
        "fk_gate3_fitted_ack_binding": "f",
        "ck_gate3_fitted_candidate_acks_hashes": "c",
        "ck_gate3_fitted_candidate_acks_xids": "c",
    },
    "evaluation": {
        "pk_gate3_fitted_candidate_evaluations": "p",
        "fk_gate3_fitted_evaluation_ack": "f",
        "ck_gate3_fitted_candidate_evaluations_hashes": "c",
        "ck_gate3_fitted_candidate_evaluations_xids": "c",
    },
}
# PostgreSQL records every user-defined constraint trigger in both pg_trigger
# and pg_constraint. DB0056 declares each commit guard initially deferred.
CONSTRAINTS = {
    name: (_TABLE_BY_KIND[kind], constraint_type)
    for kind, types in _CONSTRAINT_TYPES_BY_KIND.items()
    for name, constraint_type in types.items()
} | {
    f"gate3_fitted_{kind}_commit_guard": (table, "t")
    for kind, table in _TABLE_BY_KIND.items()
}


class CatalogRefusal(ValueError):
    pass


def _need(condition: bool, code: str) -> None:
    if condition is not True:
        raise CatalogRefusal(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def pinned_migration(source: Path) -> None:
    path = source / "migrations/versions/0056_gate3_fitted_candidate_custody.py"
    _need(
        path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 65_536,
        "fitted_migration_scope",
    )
    _need(
        hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_SHA256,
        "fitted_migration_identity",
    )


# A fixed PostgreSQL catalog SELECT. The default profile is REPEATABLE READ
# READ ONLY with a pg_catalog/pg_temp search path. The isolated owner grant
# test explicitly validates its distinct READ COMMITTED READ WRITE context.
CATALOG_SQL = """
SELECT pg_catalog.jsonb_build_object(
 'schema','ctcc.current0056.fitted_delta.physical_catalog.inert.v1',
 'head',(SELECT version_num FROM public.alembic_version),
 'server_version',pg_catalog.current_setting('server_version_num'),
 'server_build',pg_catalog.version(),
 'actor',CURRENT_USER::text,'login',SESSION_USER::text,
 'search_path',pg_catalog.current_setting('search_path'),
 'tls',EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl
              WHERE pid=pg_catalog.pg_backend_pid() AND ssl),
 'read_only',pg_catalog.current_setting('transaction_read_only')='on',
 'repeatable',pg_catalog.current_setting('transaction_isolation')='repeatable read',
 'durable',pg_catalog.current_setting('fsync')='on'
   AND pg_catalog.current_setting('full_page_writes')='on'
   AND pg_catalog.current_setting('synchronous_commit')='on'
   AND pg_catalog.current_setting('session_replication_role')='origin'
   AND NOT pg_catalog.pg_is_in_recovery(),
 'tables',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
    'name',c.relname,'kind',c.relkind::text,
    'owner',pg_catalog.pg_get_userbyid(c.relowner),
    'persistence',c.relpersistence::text,'rls',c.relrowsecurity,
    'force_rls',c.relforcerowsecurity,'acl',c.relacl::text[],
    'columns',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
       a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
       pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity::text,
       a.attgenerated::text,a.attacl::text[]) ORDER BY a.attnum),'[]'::jsonb)
       FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
       ON d.adrelid=c.oid AND d.adnum=a.attnum
       WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
    ) ORDER BY c.relname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname LIKE 'gate3_fitted_candidate_%'
      AND c.relkind='r'),
 'functions',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
    'name',p.proname,'signature',p.oid::pg_catalog.regprocedure::text,
    'result',pg_catalog.pg_get_function_result(p.oid),
    'owner',pg_catalog.pg_get_userbyid(p.proowner),'language',l.lanname,
    'definer',p.prosecdef,'volatility',p.provolatile::text,
    'kind',p.prokind::text,'config',p.proconfig,'acl',p.proacl::text[],
    'definition_sha256',pg_catalog.encode(pg_catalog.sha256(
       pg_catalog.convert_to(pg_catalog.pg_get_functiondef(p.oid),'UTF8')),'hex'),
    'source_sha256',pg_catalog.encode(pg_catalog.sha256(
       pg_catalog.convert_to(p.prosrc,'UTF8')),'hex'))
    ORDER BY p.proname COLLATE "C",p.oid::pg_catalog.regprocedure::text
    ),'[]'::jsonb)
    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
    JOIN pg_catalog.pg_language l ON l.oid=p.prolang
    WHERE n.nspname='public' AND p.proname LIKE 'gate3_fitted_%'),
 'function_grants',(SELECT COALESCE(pg_catalog.jsonb_agg(
    pg_catalog.jsonb_build_array(p.oid::pg_catalog.regprocedure::text,
       CASE WHEN a.grantee=0 THEN 'PUBLIC'
         ELSE pg_catalog.pg_get_userbyid(a.grantee) END,
       a.privilege_type,a.is_grantable,pg_catalog.pg_get_userbyid(a.grantor))
    ORDER BY p.oid::pg_catalog.regprocedure::text,
       CASE WHEN a.grantee=0 THEN 'PUBLIC'
         ELSE pg_catalog.pg_get_userbyid(a.grantee) END COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
    WHERE n.nspname='public' AND p.proname LIKE 'gate3_fitted_%'
      AND a.grantee<>p.proowner),
 'table_grants',(SELECT COALESCE(pg_catalog.jsonb_agg(
    pg_catalog.jsonb_build_array(c.relname,
       CASE WHEN a.grantee=0 THEN 'PUBLIC'
         ELSE pg_catalog.pg_get_userbyid(a.grantee) END,
       a.privilege_type,a.is_grantable)
    ORDER BY c.relname COLLATE "C",a.privilege_type COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
    WHERE n.nspname='public' AND c.relname LIKE 'gate3_fitted_candidate_%'
      AND c.relkind='r' AND a.grantee<>c.relowner),
 'triggers',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
    'name',g.tgname,'table',c.relname,'internal',g.tgisinternal,
    'enabled',g.tgenabled::text,'type',g.tgtype,'deferrable',g.tgdeferrable,
    'deferred',g.tginitdeferred,'function',p.proname,
    'definition',pg_catalog.pg_get_triggerdef(g.oid,true))
    ORDER BY c.relname COLLATE "C",g.tgname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_class c ON c.oid=g.tgrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
    WHERE n.nspname='public' AND g.tgname LIKE 'gate3_fitted_%'),
 'constraints',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
    'name',k.conname,'table',c.relname,'type',k.contype::text,
    'definition',pg_catalog.pg_get_constraintdef(k.oid,true),
    'validated',k.convalidated,'deferrable',k.condeferrable,'deferred',k.condeferred)
    ORDER BY c.relname COLLATE "C",k.conname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname LIKE 'gate3_fitted_candidate_%'),
 'indexes',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
    'name',i.relname,'table',t.relname,'definition',pg_catalog.pg_get_indexdef(i.oid),
    'valid',x.indisvalid,'ready',x.indisready,'unique',x.indisunique)
    ORDER BY i.relname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid
    JOIN pg_catalog.pg_class t ON t.oid=x.indrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace
    WHERE n.nspname='public' AND t.relname LIKE 'gate3_fitted_candidate_%'),
 'policies',(SELECT COALESCE(pg_catalog.jsonb_agg(p.polname ORDER BY p.polname),'[]'::jsonb)
    FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname LIKE 'gate3_fitted_candidate_%'),
 'rules',(SELECT COALESCE(pg_catalog.jsonb_agg(r.rulename ORDER BY r.rulename),'[]'::jsonb)
    FROM pg_catalog.pg_rewrite r JOIN pg_catalog.pg_class c ON c.oid=r.ev_class
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname LIKE 'gate3_fitted_candidate_%'),
 'inheritance',(SELECT COALESCE(pg_catalog.jsonb_agg(c.relname||'>'||p.relname
    ORDER BY c.relname COLLATE "C",p.relname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_inherits x JOIN pg_catalog.pg_class c ON c.oid=x.inhrelid
    JOIN pg_catalog.pg_class p ON p.oid=x.inhparent
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname LIKE 'gate3_fitted_candidate_%'),
 'event_triggers',(SELECT COALESCE(pg_catalog.jsonb_agg(e.evtname
    ORDER BY e.evtname COLLATE "C"),'[]'::jsonb) FROM pg_catalog.pg_event_trigger e),
 'global_default_acl',(SELECT COALESCE(pg_catalog.jsonb_agg(
    pg_catalog.jsonb_build_array(pg_catalog.pg_get_userbyid(d.defaclrole),
       d.defaclobjtype::text,d.defaclacl::text[])
    ORDER BY pg_catalog.pg_get_userbyid(d.defaclrole) COLLATE "C",
       d.defaclobjtype),'[]'::jsonb)
    FROM pg_catalog.pg_default_acl d WHERE d.defaclnamespace=0),
 'public_default_acl',(SELECT COALESCE(pg_catalog.jsonb_agg(
    pg_catalog.jsonb_build_array(pg_catalog.pg_get_userbyid(d.defaclrole),
       d.defaclobjtype::text,d.defaclacl::text[])
    ORDER BY pg_catalog.pg_get_userbyid(d.defaclrole) COLLATE "C",
       d.defaclobjtype),'[]'::jsonb)
    FROM pg_catalog.pg_default_acl d JOIN pg_catalog.pg_namespace n
      ON n.oid=d.defaclnamespace WHERE n.nspname='public'),
 'roles',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
    r.rolname,r.rolcanlogin,r.rolsuper,r.rolcreatedb,r.rolcreaterole,
    r.rolreplication,r.rolbypassrls)
    ORDER BY r.rolname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_roles r WHERE r.rolname='ctcc'
      OR pg_catalog.left(r.rolname,5)='ctcc_'),
 'role_create',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
    r.rolname,
    pg_catalog.has_database_privilege(r.oid,pg_catalog.current_database(),'CREATE'),
    pg_catalog.has_schema_privilege(r.oid,'public','CREATE'))
    ORDER BY r.rolname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_roles r WHERE r.rolname='ctcc'
      OR pg_catalog.left(r.rolname,5)='ctcc_'),
 'role_edges',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
    member.rolname,role.rolname) ORDER BY member.rolname COLLATE "C",
    role.rolname COLLATE "C"),'[]'::jsonb)
    FROM pg_catalog.pg_auth_members m
    JOIN pg_catalog.pg_roles member ON member.oid=m.member
    JOIN pg_catalog.pg_roles role ON role.oid=m.roleid),
 'original_write_access',(SELECT COALESCE(pg_catalog.jsonb_agg(
    pg_catalog.jsonb_build_array(r.rolname,p.oid::pg_catalog.regprocedure::text)
    ORDER BY r.rolname COLLATE "C",p.oid::pg_catalog.regprocedure::text),'[]'::jsonb)
    FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_proc p
    WHERE p.oid IN (
      'public.gate3_prereg_seal_append(jsonb)'::pg_catalog.regprocedure,
      'public.gate3_prereg_seal_ack_append(text)'::pg_catalog.regprocedure,
      'public.gate3_capture_schedule_claim_append(jsonb)'::pg_catalog.regprocedure,
      'public.gate3_capture_schedule_claim_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
      'public.gate3_capture_schedule_append(jsonb)'::pg_catalog.regprocedure,
      'public.gate3_capture_schedule_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure)
      AND pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE'))
) AS catalog
"""

OWNER_READWRITE_CATALOG_SQL = CATALOG_SQL.replace(
    "'repeatable',pg_catalog.current_setting('transaction_isolation')='repeatable read'",
    "'read_committed',pg_catalog.current_setting('transaction_isolation')='read committed'",
)


def _inspect_catalog_document(
    document: dict,
    *,
    owner: str,
    demo_roles: tuple[str, ...],
    fitted_roles: dict[str, str] | None,
    owner_readwrite: bool,
) -> str:
    """Pure shape/role check; digest is diagnostic and never native authority."""
    _need(
        type(document) is dict and len(_canonical(document)) <= 1_000_000,
        "fitted_catalog_document_invalid",
    )
    _need(
        type(owner_readwrite) is bool
        and (not owner_readwrite or fitted_roles is None)
        and document.get("schema") == SCHEMA
        and document.get("head") == "0056"
        and document.get("server_version") == "170011"
        and document.get("actor") == document.get("login") == owner
        and document.get("tls") is True
        and document.get("read_only") is (not owner_readwrite)
        and document.get("read_committed" if owner_readwrite else "repeatable") is True
        and ("repeatable" if owner_readwrite else "read_committed") not in document
        and document.get("durable") is True
        and document.get("search_path") == "pg_catalog, pg_temp"
        and type(document.get("server_build")) is str
        and document["server_build"].startswith("PostgreSQL 17.11 "),
        "fitted_catalog_identity_changed",
    )
    _need(
        type(owner) is str
        and owner
        and type(demo_roles) is tuple
        and len(demo_roles) == 3
        and all(type(x) is str and x for x in demo_roles)
        and (
            fitted_roles is None
            or (
                type(fitted_roles) is dict
                and set(fitted_roles)
                == {"publisher", "observer", "reader", "evaluator"}
                and all(type(x) is str and x for x in fitted_roles.values())
            )
        )
        and len({owner, *demo_roles, *(fitted_roles or {}).values()})
        == 4 + (4 if fitted_roles else 0),
        "fitted_role_overlap",
    )
    for key in (
        "tables",
        "functions",
        "function_grants",
        "table_grants",
        "triggers",
        "constraints",
        "indexes",
        "policies",
        "rules",
        "inheritance",
        "event_triggers",
        "global_default_acl",
        "public_default_acl",
        "roles",
        "role_create",
        "role_edges",
        "original_write_access",
    ):
        _need(type(document.get(key)) is list, "fitted_catalog_document_invalid")
    tables = document["tables"]
    _need(
        len(tables) == 3
        and {r.get("name") for r in tables} == TABLES
        and all(
            type(r) is dict
            and r.get("kind") == "r"
            and r.get("owner") == owner
            and r.get("persistence") == "p"
            and r.get("rls") is False
            and r.get("force_rls") is False
            and type(r.get("columns")) is list
            and all(
                type(column) is list and len(column) == 7 and column[-1] is None
                for column in r["columns"]
            )
            for r in tables
        ),
        "fitted_catalog_tables_changed",
    )
    functions = document["functions"]
    _need(
        len(functions) == 20 and all(type(r) is dict for r in functions),
        "fitted_catalog_functions_changed",
    )
    by_signature = {
        r["signature"].removeprefix("public."): r
        for r in functions
        if type(r.get("signature")) is str and r["signature"].startswith("public.")
    }
    _need(
        len(by_signature) == 20 and set(by_signature) == INTERNAL | APIS.keys(),
        "fitted_catalog_functions_changed",
    )
    for signature, row in by_signature.items():
        _need(
            row.get("owner") == owner
            and row.get("language")
            == (
                "sql"
                if signature
                in {"gate3_fitted_hash(text)", "gate3_fitted_json_hash(jsonb)"}
                else "plpgsql"
            )
            and row.get("kind") == "f"
            and row.get("config") == ["search_path=pg_catalog, pg_temp"]
            and type(row.get("definition_sha256")) is str
            and _HEX.fullmatch(row["definition_sha256"]) is not None
            and type(row.get("source_sha256")) is str
            and _HEX.fullmatch(row["source_sha256"]) is not None,
            "fitted_catalog_function_shape_changed",
        )
        if signature in APIS or signature in {
            "gate3_fitted_role(integer)",
            "gate3_fitted_commit_guard()",
            "gate3_fitted_candidate_claim_guard()",
            "gate3_fitted_binding_insert_guard()",
            "gate3_fitted_ack_insert_guard()",
            "gate3_fitted_evaluation_insert_guard()",
        }:
            _need(row.get("definer") is True, "fitted_catalog_definer_changed")
        _need(type(row.get("acl")) is list, "fitted_catalog_function_acl_changed")
    expected_grants = (
        {
            ("public." + signature, fitted_roles[role], "EXECUTE", False, owner)
            for signature, roles in APIS.items()
            for role in roles
        }
        if fitted_roles is not None
        else set()
    )
    grants = document["function_grants"]
    _need(
        len(grants) == len(expected_grants)
        and all(type(row) is list and len(row) == 5 for row in grants)
        and {tuple(row) for row in grants} == expected_grants,
        "fitted_catalog_function_acl_changed",
    )
    _need(document["table_grants"] == [], "fitted_catalog_table_acl_changed")
    triggers = document["triggers"]
    _need(
        len(triggers) == 13
        and all(type(r) is dict for r in triggers)
        and {r.get("name") for r in triggers} == TRIGGERS
        and all(
            r.get("internal") is False
            and r.get("enabled") == "A"
            and r.get("table") in TABLES | {"gate3_capture_schedule_key_claims"}
            for r in triggers
        ),
        "fitted_catalog_trigger_changed",
    )
    for row in triggers:
        name = row["name"]
        if name == "gate3_fitted_candidate_claim_guard":
            expected_table = "gate3_capture_schedule_key_claims"
            expected_function = name
            deferred = False
        else:
            kind = name.removeprefix("gate3_fitted_").split("_", 1)[0]
            expected_table = _TABLE_BY_KIND[kind]
            suffix = name.removeprefix("gate3_fitted_" + kind + "_")
            expected_function = (
                "gate3_fitted_" + kind + "_insert_guard"
                if suffix == "insert_guard"
                else "gate3_fitted_commit_guard"
                if suffix == "commit_guard"
                else "gate3_fitted_immutable"
            )
            deferred = suffix == "commit_guard"
        _need(
            row.get("table") == expected_table
            and row.get("function") == expected_function
            and row.get("deferrable") is deferred
            and row.get("deferred") is deferred,
            "fitted_catalog_trigger_changed",
        )
    _need(
        len(document["constraints"]) == len(CONSTRAINTS)
        and {row.get("name") for row in document["constraints"]} == CONSTRAINTS.keys()
        and all(
            (row.get("table"), row.get("type")) == CONSTRAINTS[row["name"]]
            and row.get("validated") is True
            and row.get("deferrable") is (row["type"] == "t")
            and row.get("deferred") is (row["type"] == "t")
            for row in document["constraints"]
        ),
        "fitted_catalog_constraint_changed",
    )
    _need(
        len(document["indexes"]) == len(INDEXES)
        and {r.get("name") for r in document["indexes"]} == INDEXES
        and all(
            r.get("valid") is True and r.get("ready") is True
            for r in document["indexes"]
        ),
        "fitted_catalog_index_changed",
    )
    _need(
        all(
            document[key] == []
            for key in (
                "policies",
                "rules",
                "inheritance",
                "event_triggers",
                "global_default_acl",
                "public_default_acl",
            )
        ),
        "fitted_catalog_extra_policy",
    )
    watched = {owner, *demo_roles, *(fitted_roles or {}).values()}
    role_rows = document["roles"]
    by_role = {
        row[0]: row
        for row in role_rows
        if type(row) is list and len(row) == 7 and type(row[0]) is str
    }
    _need(
        len(by_role) == len(role_rows)
        and watched <= by_role.keys()
        and all(
            by_role[name][1] is True and by_role[name][2:] == [False] * 5
            for name in watched
            if name != owner
        ),
        "fitted_catalog_role_changed",
    )
    create_rows = document["role_create"]
    by_create = {
        row[0]: row
        for row in create_rows
        if type(row) is list and len(row) == 3 and type(row[0]) is str
    }
    _need(
        len(by_create) == len(create_rows)
        and watched <= by_create.keys()
        and all(
            by_create[name][1:] == [False, False] for name in watched if name != owner
        ),
        "fitted_catalog_role_create",
    )
    _need(
        all(
            type(edge) is list and len(edge) == 2 and not (set(edge) & watched)
            for edge in document["role_edges"]
        ),
        "fitted_catalog_role_membership",
    )
    _need(
        all(
            type(row) is list
            and len(row) == 2
            and row[0] not in {*demo_roles, *(fitted_roles or {}).values()}
            for row in document["original_write_access"]
        ),
        "fitted_catalog_original_write_overlap",
    )
    # Every object definition, ACL, trigger target and constraint is bound by
    # the diagnostic fingerprint. A separately observed native reference is
    # still mandatory; this function never returns a positive startup seal.
    return hashlib.sha256(_canonical(document)).hexdigest()


def inspect_catalog_document(
    document: dict,
    *,
    owner: str,
    demo_roles: tuple[str, ...],
    fitted_roles: dict[str, str] | None,
) -> str:
    return _inspect_catalog_document(
        document,
        owner=owner,
        demo_roles=demo_roles,
        fitted_roles=fitted_roles,
        owner_readwrite=False,
    )


def inspect_owner_readwrite_catalog(
    document: dict, *, owner: str, demo_roles: tuple[str, str, str]
) -> str:
    """Validate the separate owner write context for isolated grant tests."""
    return _inspect_catalog_document(
        document,
        owner=owner,
        demo_roles=demo_roles,
        fitted_roles=None,
        owner_readwrite=True,
    )


def require_native_reference(document: dict, **roles: object) -> str:
    digest = inspect_catalog_document(document, **roles)
    _need(
        _HEX.fullmatch(EXPECTED_NATIVE_REFERENCE_SHA256) is not None,
        "fitted_catalog_native_reference_unsealed",
    )
    _need(digest == EXPECTED_NATIVE_REFERENCE_SHA256, "fitted_catalog_physical_drift")
    return digest


async def observe_catalog(connection, *, source: Path, **roles: object) -> str:
    """Hard DENY before opening a DB while no independent reference exists."""
    _need(
        _HEX.fullmatch(EXPECTED_NATIVE_REFERENCE_SHA256) is not None,
        "fitted_catalog_native_reference_unsealed",
    )
    pinned_migration(source)
    document = await connection.fetchval(CATALOG_SQL)
    return require_native_reference(document, **roles)
