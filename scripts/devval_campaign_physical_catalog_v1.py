"""Inert, exact PostgreSQL 17.11 campaign physical-catalog readback.

No installer or credential is here. The native reference digest is intentionally
unsealed: observe_catalog refuses before opening a connection until a separate
reviewed PG17.11 installation supplies an independent expected digest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from scripts.evidence_json import read_evidence_json

SOURCE = Path(__file__).resolve().parents[1]
SQL_PINS = {
    "scripts/sql/gate3_devval_campaign_custody_v1.sql": "ea49891ec5a01c6a07845074051ffb82391451fa68a1e7286aa20ec1ed8196e5",
    "scripts/sql/gate3_devval_campaign_dispatch_claim_v1.sql": "41e1916b78ea6c970d14ce01f3e9dde1664e57330c76c85418d6fbe2326fc8b4",
}
EXPECTED_CATALOG_SHA256 = ""  # No native reference has been admitted.
SCHEMA = "ctcc.devval.campaign.physical_catalog.v1"
HERMETIC_CATALOG_ENV = "CTCC_DEVVAL_RETAIN_HERMETIC_CATALOGS"
HERMETIC_CATALOG_FILE = "devval-campaign-native-catalogs.json"
MAX_HERMETIC_CATALOG_BYTES = 256 * 1024
HERMETIC_CATALOG_CASE = (
    "tests.integration.test_devval_campaign_custody_pg_v1",
    "test_two_fresh_tls_campaign_catalogs_match_and_drift_is_refused",
)
SERVER_BUILD = (
    "PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35229, 64-bit"
)
TABLES = frozenset({"campaigns", "acknowledgements", "dispatch_claims"})
INDEXES = frozenset(
    {
        "campaigns_pkey",
        "campaigns_campaign_id_key",
        "campaigns_window_key_key",
        "acknowledgements_pkey",
        "dispatch_claims_pkey",
        "dispatch_claims_root_binding_sha256_key",
    }
)
FUNCTIONS = frozenset(
    {
        "_immutable",
        "_require_role",
        "_durable",
        "publish_campaign",
        "acknowledge_campaign",
        "read_campaign",
        "_require_collector",
        "claim_dispatch",
        "read_dispatch_claim",
    }
)
USER_TRIGGERS = frozenset(
    {
        "ctcc_devval_campaign_immutable",
        "ctcc_devval_campaign_no_truncate",
        "ctcc_devval_ack_immutable",
        "ctcc_devval_ack_no_truncate",
        "ctcc_devval_dispatch_immutable",
        "ctcc_devval_dispatch_no_truncate",
    }
)
NONOWNER_ACL = frozenset(
    {
        ("schema", "ctcc_devval_custody", "ctcc_dv_publisher", "USAGE", False, "ctcc"),
        ("schema", "ctcc_devval_custody", "ctcc_dv_observer", "USAGE", False, "ctcc"),
        ("schema", "ctcc_devval_custody", "ctcc_dv_collector", "USAGE", False, "ctcc"),
        ("function", "publish_campaign", "ctcc_dv_publisher", "EXECUTE", False, "ctcc"),
        (
            "function",
            "acknowledge_campaign",
            "ctcc_dv_observer",
            "EXECUTE",
            False,
            "ctcc",
        ),
        ("function", "read_campaign", "ctcc_dv_observer", "EXECUTE", False, "ctcc"),
        ("function", "read_campaign", "ctcc_dv_collector", "EXECUTE", False, "ctcc"),
        ("function", "claim_dispatch", "ctcc_dv_collector", "EXECUTE", False, "ctcc"),
        (
            "function",
            "read_dispatch_claim",
            "ctcc_dv_collector",
            "EXECUTE",
            False,
            "ctcc",
        ),
    }
)
CONSTRAINTS = frozenset(
    {
        "campaigns_pkey",
        "campaigns_campaign_id_key",
        "campaigns_window_key_key",
        "ck_devval_campaign_sha",
        "ck_devval_window_key",
        "ck_devval_campaign_id",
        "ck_devval_campaign_bytes",
        "ck_devval_campaign_order",
        "ck_devval_publication_xid",
        "acknowledgements_pkey",
        "acknowledgements_campaign_sha256_fkey",
        "ck_devval_ack_xid",
        "dispatch_claims_pkey",
        "dispatch_claims_root_binding_sha256_key",
        "dispatch_claims_campaign_sha256_fkey",
        "ck_devval_dispatch_campaign_sha",
        "ck_devval_dispatch_root_sha",
        "ck_devval_dispatch_xid",
    }
)
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_CREATE = re.compile(rb"CREATE FUNCTION ctcc_devval_custody\.([a-z_]+)\(")
_AS = re.compile(rb"\bAS (\$[a-z_]+\$)")


class CatalogRefusal(ValueError):
    """Fixed reason only; raw catalog and connection details never escape."""


def _need(condition: bool, code: str) -> None:
    if condition is not True:
        raise CatalogRefusal(code)


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pinned_sql(path: Path, digest: str) -> bytes:
    try:
        info = path.lstat()
        _need(
            stat.S_ISREG(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400
            and path.resolve(strict=True) == path
            and 0 < info.st_size <= 256 * 1024,
            "campaign_catalog_source_changed",
        )
        with path.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
            opened = os.fstat(stream.fileno())
        _need(
            len(raw) == info.st_size
            and os.path.samestat(info, opened)
            and os.path.samestat(info, path.lstat())
            and hashlib.sha256(raw).hexdigest() == digest,
            "campaign_catalog_source_changed",
        )
        return raw
    except CatalogRefusal:
        raise
    except Exception:  # noqa: BLE001 -- redact source filesystem details.
        raise CatalogRefusal("campaign_catalog_source_changed") from None


def expected_function_bodies() -> dict[str, str]:
    """Bind prosrc hashes to all nine exact versioned SQL definitions."""
    bodies = {}
    for relative, digest in SQL_PINS.items():
        raw = _pinned_sql(SOURCE / relative, digest)
        matches = list(_CREATE.finditer(raw))
        _need(bool(matches), "campaign_catalog_sql_function_shape")
        for index, found in enumerate(matches):
            name = found.group(1).decode("ascii")
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            section = raw[found.start() : end]
            opening = _AS.search(section)
            _need(
                opening is not None and name not in bodies,
                "campaign_catalog_sql_function_shape",
            )
            tag = opening.group(1)
            close = section.find(tag, opening.end())
            _need(
                close > opening.end() and section[close + len(tag) :].startswith(b";"),
                "campaign_catalog_sql_function_shape",
            )
            body = section[opening.end() : close]
            bodies[name] = hashlib.sha256(body).hexdigest()
    _need(set(bodies) == FUNCTIONS, "campaign_catalog_sql_function_shape")
    return bodies


# Every PostgreSQL query uses a fixed qualified name and runs in one native
# REPEATABLE READ, READ ONLY transaction with pg_catalog/pg_temp search_path.
# OIDs and generated RI-trigger names are normalized; the actual owner names,
# all ACL strings, SQL definitions, function source digests and object counts
# remain bound. The approved native digest is a separate, currently missing pin.
CATALOG_SQL = """
SELECT pg_catalog.jsonb_build_object(
 'schema','ctcc.devval.campaign.physical_catalog.v1',
 'database',pg_catalog.current_database(),
 'actor',CURRENT_USER::text,
 'login',SESSION_USER::text,
 'version',pg_catalog.current_setting('server_version_num'),
 'build',pg_catalog.version(),
 'tls',EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl
              WHERE pid=pg_catalog.pg_backend_pid() AND ssl),
 'durable',pg_catalog.current_setting('transaction_read_only')='on'
   AND pg_catalog.current_setting('fsync')='on'
   AND pg_catalog.current_setting('full_page_writes')='on'
   AND pg_catalog.current_setting('synchronous_commit')='on'
   AND pg_catalog.current_setting('session_replication_role')='origin'
   AND NOT pg_catalog.pg_is_in_recovery(),
 'has_alembic',pg_catalog.to_regclass('public.alembic_version') IS NOT NULL,
 'namespace',(SELECT pg_catalog.jsonb_build_object(
   'owner',pg_catalog.pg_get_userbyid(n.nspowner),'acl',n.nspacl::text[])
   FROM pg_catalog.pg_namespace n WHERE n.nspname='ctcc_devval_custody'),
 'nonowner_acl',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
   q.kind,q.name,CASE WHEN q.grantee=0 THEN 'PUBLIC'
     ELSE pg_catalog.pg_get_userbyid(q.grantee) END,
   q.privilege_type,q.is_grantable,pg_catalog.pg_get_userbyid(q.grantor))
   ORDER BY q.kind COLLATE "C",q.name COLLATE "C",
     CASE WHEN q.grantee=0 THEN 'PUBLIC'
       ELSE pg_catalog.pg_get_userbyid(q.grantee) END COLLATE "C",
     q.privilege_type COLLATE "C"),'[]'::jsonb)
   FROM (
     SELECT 'schema'::text kind,n.nspname::text name,a.grantee,
       a.privilege_type,a.is_grantable,a.grantor,n.nspowner owner_oid
     FROM pg_catalog.pg_namespace n CROSS JOIN LATERAL pg_catalog.aclexplode(
       COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a
     WHERE n.nspname='ctcc_devval_custody'
     UNION ALL
     SELECT 'table'::text,c.relname::text,a.grantee,
       a.privilege_type,a.is_grantable,a.grantor,c.relowner
     FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
     CROSS JOIN LATERAL pg_catalog.aclexplode(
       COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
     WHERE n.nspname='ctcc_devval_custody' AND c.relkind='r'
     UNION ALL
     SELECT 'function'::text,p.proname::text,a.grantee,
       a.privilege_type,a.is_grantable,a.grantor,p.proowner
     FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
     CROSS JOIN LATERAL pg_catalog.aclexplode(
       COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
     WHERE n.nspname='ctcc_devval_custody'
   ) q WHERE q.grantee<>q.owner_oid),
 'classes',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'name',c.relname,'kind',c.relkind::text,'owner',pg_catalog.pg_get_userbyid(c.relowner),
   'persistence',c.relpersistence::text,'rls',c.relrowsecurity,
   'force_rls',c.relforcerowsecurity,'partition',c.relispartition,
   'replica_identity',c.relreplident::text,'tablespace',c.reltablespace,
   'options',c.reloptions,'acl',c.relacl::text[],
   'columns',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
      a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
      pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity::text,
      a.attgenerated::text,co.collname,a.attacl::text[]) ORDER BY a.attnum),'[]'::jsonb)
      FROM pg_catalog.pg_attribute a
      LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
      LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
      WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped)
   ) ORDER BY c.relname COLLATE "C"),'[]'::jsonb)
   FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='ctcc_devval_custody'),
 'functions',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'name',p.proname,'signature',p.oid::pg_catalog.regprocedure::text,
   'result',pg_catalog.pg_get_function_result(p.oid),
   'owner',pg_catalog.pg_get_userbyid(p.proowner),'language',l.lanname,
   'arity',p.pronargs,'defaults',p.pronargdefaults,'set',p.proretset,
   'definer',p.prosecdef,'strict',p.proisstrict,'leakproof',p.proleakproof,
   'volatility',p.provolatile::text,'parallel',p.proparallel::text,
   'kind',p.prokind::text,'support_zero',p.prosupport=0,
   'config',p.proconfig,'acl',p.proacl::text[],
   'source_sha256',pg_catalog.encode(pg_catalog.sha256(
     pg_catalog.convert_to(p.prosrc,'UTF8')),'hex'))
   ORDER BY p.proname COLLATE "C",p.oid::pg_catalog.regprocedure::text),'[]'::jsonb)
   FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
   JOIN pg_catalog.pg_language l ON l.oid=p.prolang
   WHERE n.nspname='ctcc_devval_custody'),
 'constraints',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'name',k.conname,'table',c.relname,'type',k.contype::text,
   'definition',pg_catalog.pg_get_constraintdef(k.oid,true),
   'validated',k.convalidated,'deferrable',k.condeferrable,
   'deferred',k.condeferred,'key',k.conkey,'foreign_key',k.confkey,
   'references',r.relname,'index',i.relname,'update_action',k.confupdtype::text,
   'delete_action',k.confdeltype::text,'match_type',k.confmatchtype::text,
   'local',k.conislocal,'no_inherit',k.connoinherit,'inherit_count',k.coninhcount)
   ORDER BY c.relname COLLATE "C",k.conname COLLATE "C"),'[]'::jsonb)
   FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
   JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   LEFT JOIN pg_catalog.pg_class r ON r.oid=k.confrelid
   LEFT JOIN pg_catalog.pg_class i ON i.oid=k.conindid
   WHERE n.nspname='ctcc_devval_custody'),
 'indexes',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'name',i.relname,'table',t.relname,'owner',pg_catalog.pg_get_userbyid(i.relowner),
   'definition',pg_catalog.pg_get_indexdef(i.oid),
   'valid',x.indisvalid,'ready',x.indisready,'unique',x.indisunique,
   'primary',x.indisprimary,'immediate',x.indimmediate,
   'nulls_not_distinct',x.indnullsnotdistinct,
   'predicate',x.indpred IS NOT NULL,'expressions',x.indexprs IS NOT NULL)
   ORDER BY i.relname COLLATE "C"),'[]'::jsonb)
   FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid
   JOIN pg_catalog.pg_class t ON t.oid=x.indrelid
   JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace
   WHERE n.nspname='ctcc_devval_custody'),
 'triggers',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'name',CASE WHEN g.tgisinternal THEN 'internal' ELSE g.tgname END,
   'table',c.relname,'internal',g.tgisinternal,'enabled',g.tgenabled::text,
   'type',g.tgtype,'deferrable',g.tgdeferrable,'initially_deferred',g.tginitdeferred,
   'arguments',pg_catalog.encode(g.tgargs,'hex'),'argument_count',g.tgnargs,
   'predicate',pg_catalog.pg_get_expr(g.tgqual,g.tgrelid),
   'function',g.tgfoid::pg_catalog.regprocedure::text,
   'constraint',k.conname,'related',r.relname,
   'definition',CASE WHEN g.tgisinternal THEN NULL
       ELSE pg_catalog.pg_get_triggerdef(g.oid,true) END)
   ORDER BY c.relname COLLATE "C",g.tgisinternal,g.tgtype,
     CASE WHEN g.tgisinternal THEN 'internal' ELSE g.tgname END,
     k.conname COLLATE "C",g.tgfoid::pg_catalog.regprocedure::text),'[]'::jsonb)
   FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_class c ON c.oid=g.tgrelid
   JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   LEFT JOIN pg_catalog.pg_constraint k ON k.oid=g.tgconstraint
   LEFT JOIN pg_catalog.pg_class r ON r.oid=g.tgconstrrelid
   WHERE n.nspname='ctcc_devval_custody'),
 'types',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'name',t.typname,'kind',t.typtype::text,'category',t.typcategory::text,
   'owner',pg_catalog.pg_get_userbyid(t.typowner),
   'relation',c.relname,'acl',t.typacl::text[])
   ORDER BY t.typname COLLATE "C"),'[]'::jsonb)
   FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
   LEFT JOIN pg_catalog.pg_class c ON c.oid=t.typrelid
   WHERE n.nspname='ctcc_devval_custody'),
 'policies',(SELECT COALESCE(pg_catalog.jsonb_agg(p.polname ORDER BY p.polname),'[]'::jsonb)
   FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
   JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='ctcc_devval_custody'),
 'rules',(SELECT COALESCE(pg_catalog.jsonb_agg(r.rulename ORDER BY r.rulename),'[]'::jsonb)
   FROM pg_catalog.pg_rewrite r JOIN pg_catalog.pg_class c ON c.oid=r.ev_class
   JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='ctcc_devval_custody'),
 'inheritance',(SELECT COALESCE(pg_catalog.jsonb_agg(c.relname||'>'||p.relname
   ORDER BY c.relname,p.relname),'[]'::jsonb)
   FROM pg_catalog.pg_inherits x JOIN pg_catalog.pg_class c ON c.oid=x.inhrelid
   JOIN pg_catalog.pg_class p ON p.oid=x.inhparent
   JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='ctcc_devval_custody'),
 'extensions',(SELECT COALESCE(pg_catalog.jsonb_agg(e.extname ORDER BY e.extname),'[]'::jsonb)
   FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
   WHERE n.nspname='ctcc_devval_custody'),
 'event_triggers',(SELECT COALESCE(pg_catalog.jsonb_agg(e.evtname
   ORDER BY e.evtname COLLATE "C"),'[]'::jsonb)
   FROM pg_catalog.pg_event_trigger e),
 'global_default_acl',(SELECT COALESCE(pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(pg_catalog.pg_get_userbyid(d.defaclrole),
     d.defaclobjtype::text,d.defaclacl::text[])
   ORDER BY pg_catalog.pg_get_userbyid(d.defaclrole) COLLATE "C",
     d.defaclobjtype),'[]'::jsonb)
   FROM pg_catalog.pg_default_acl d WHERE d.defaclnamespace=0),
 'default_acl',(SELECT COALESCE(pg_catalog.jsonb_agg(d.defaclobjtype::text||':'||
    COALESCE(d.defaclacl::text,'NULL') ORDER BY d.defaclobjtype),'[]'::jsonb)
   FROM pg_catalog.pg_default_acl d JOIN pg_catalog.pg_namespace n
     ON n.oid=d.defaclnamespace WHERE n.nspname='ctcc_devval_custody')
) AS catalog
"""


def catalog_digest(document: dict) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def inspect_catalog_document(document: dict) -> str:
    """Pure physical shape/body check for the fixed production profile."""
    return _inspect_catalog_document(document, server_build=SERVER_BUILD)


def _inspect_catalog_document(document: dict, *, server_build: str) -> str:
    _need(type(document) is dict, "campaign_catalog_document_invalid")
    _need(
        len(_canonical(document)) <= 1_000_000,
        "campaign_catalog_document_invalid",
    )
    _need(
        document.get("schema") == SCHEMA
        and document.get("database") == "ctcc_devval_campaign"
        and document.get("actor") == document.get("login") == "ctcc"
        and document.get("version") == "170011"
        and document.get("build") == server_build
        and document.get("tls") is True
        and document.get("durable") is True
        and document.get("has_alembic") is False
        and type(document.get("namespace")) is dict
        and document["namespace"].get("owner") == "ctcc",
        "campaign_catalog_identity_changed",
    )
    for name in (
        "classes",
        "functions",
        "constraints",
        "indexes",
        "triggers",
        "types",
        "policies",
        "rules",
        "inheritance",
        "extensions",
        "event_triggers",
        "global_default_acl",
        "default_acl",
        "nonowner_acl",
    ):
        _need(type(document.get(name)) is list, "campaign_catalog_document_invalid")
    for name in ("classes", "functions", "constraints", "indexes", "triggers"):
        _need(
            all(type(row) is dict for row in document[name]),
            "campaign_catalog_document_invalid",
        )
    classes = document["classes"]
    _need(
        len(classes) == len(TABLES | INDEXES)
        and {row.get("name") for row in classes} == TABLES | INDEXES
        and all(
            row.get("kind") == ("r" if row["name"] in TABLES else "i")
            and row.get("owner") == "ctcc"
            for row in classes
        ),
        "campaign_catalog_relation_changed",
    )
    _need(
        all(
            type(row.get("columns")) is list
            and all(
                type(column) is list
                and len(column) == 8
                and column[3] is None
                and column[4] == ""
                and column[5] == ""
                and column[7] is None
                for column in row["columns"]
            )
            for row in classes
        ),
        "campaign_catalog_column_changed",
    )
    functions = document["functions"]
    bodies = expected_function_bodies()
    _need(
        len(functions) == len(FUNCTIONS)
        and {row.get("name") for row in functions} == FUNCTIONS
        and all(
            row.get("owner") == "ctcc"
            and row.get("language") == "plpgsql"
            and row.get("definer") is True
            and row.get("strict") is False
            and row.get("leakproof") is False
            and row.get("volatility") == "v"
            and row.get("parallel") == "u"
            and row.get("kind") == "f"
            and row.get("support_zero") is True
            and row.get("config") == ["search_path=pg_catalog, pg_temp"]
            and row.get("source_sha256") == bodies[row["name"]]
            for row in functions
        ),
        "campaign_catalog_function_changed",
    )
    _need(
        len(document["constraints"]) == len(CONSTRAINTS)
        and {row.get("name") for row in document["constraints"]} == CONSTRAINTS
        and all(row.get("validated") is True for row in document["constraints"]),
        "campaign_catalog_constraint_changed",
    )
    _need(
        len(document["indexes"]) == len(INDEXES)
        and {row.get("name") for row in document["indexes"]} == INDEXES
        and all(
            row.get("valid") is True and row.get("ready") is True
            for row in document["indexes"]
        ),
        "campaign_catalog_index_changed",
    )
    user = [row for row in document["triggers"] if row.get("internal") is False]
    internal = [row for row in document["triggers"] if row.get("internal") is True]
    _need(
        len(user) == len(USER_TRIGGERS)
        and {row.get("name") for row in user} == USER_TRIGGERS
        and len(internal) == 8
        and all(row.get("enabled") == "O" for row in (*user, *internal)),
        "campaign_catalog_trigger_changed",
    )
    _need(
        all(
            document[key] == []
            for key in (
                "policies",
                "rules",
                "inheritance",
                "extensions",
                "event_triggers",
                "global_default_acl",
                "default_acl",
            )
        ),
        "campaign_catalog_extra_object",
    )
    acl = document["nonowner_acl"]
    _need(
        len(acl) == len(NONOWNER_ACL)
        and all(type(row) is list and len(row) == 6 for row in acl)
        and {tuple(row) for row in acl} == NONOWNER_ACL
        and all(row.get("acl") is None for row in document["types"]),
        "campaign_catalog_acl_changed",
    )
    digest = catalog_digest(document)
    return digest


def verify_hermetic_linux_catalogs(raw: bytes) -> None:
    """Recompute both diagnostic fingerprints without admitting a profile."""
    _need(
        type(raw) is bytes and 0 < len(raw) <= MAX_HERMETIC_CATALOG_BYTES,
        "campaign_catalog_evidence_size",
    )
    evidence = read_evidence_json(raw)
    _need(
        type(evidence) is dict
        and _canonical(evidence) == raw
        and set(evidence)
        == {
            "schema",
            "case",
            "scope",
            "sql_sha256",
            "server_build",
            "catalogs",
            "windows_reference_admitted",
            "execution_authority",
        }
        and evidence["schema"] == "ctcc.devval.hermetic_linux_catalog_pair.v1"
        and evidence["case"] == list(HERMETIC_CATALOG_CASE)
        and evidence["scope"] == "two_disposable_linux_tls_installations"
        and evidence["sql_sha256"] == SQL_PINS
        and evidence["windows_reference_admitted"] is False
        and evidence["execution_authority"] is False,
        "campaign_catalog_evidence_identity",
    )
    build = evidence["server_build"]
    _need(
        type(build) is str
        and re.fullmatch(
            r"PostgreSQL 17\.11(?: [^\r\n]{1,160})? on [\w.+-]*linux[\w.+-]*, compiled by [^\r\n]{1,160}, 64-bit",
            build,
            flags=re.ASCII,
        )
        is not None,
        "campaign_catalog_evidence_not_linux_pg17_11",
    )
    rows = evidence["catalogs"]
    _need(type(rows) is list and len(rows) == 2, "campaign_catalog_evidence_pair")
    for row in rows:
        _need(
            type(row) is dict
            and set(row)
            == {
                "document",
                "catalog_sha256",
                "native_document_sha256",
                "fixture_database_sha256",
            }
            and all(
                type(row[key]) is str and _HEX.fullmatch(row[key]) is not None
                for key in (
                    "catalog_sha256",
                    "native_document_sha256",
                    "fixture_database_sha256",
                )
            ),
            "campaign_catalog_evidence_row",
        )
        _need(
            _inspect_catalog_document(row["document"], server_build=build)
            == row["catalog_sha256"],
            "campaign_catalog_evidence_hash_changed",
        )
    first, second = rows
    _need(
        first["document"] == second["document"]
        and first["catalog_sha256"] == second["catalog_sha256"]
        and first["native_document_sha256"] != second["native_document_sha256"]
        and first["fixture_database_sha256"] != second["fixture_database_sha256"],
        "campaign_catalog_evidence_pair",
    )


def retain_hermetic_linux_catalogs(first_native, first_doc, second_native, second_doc):
    """Opt-in test-only output in the existing fixed hermetic results mount."""
    from app.trade_evidence.storage import _posix_root, _root_path

    _need(sys.platform == "linux", "campaign_catalog_evidence_not_linux_pg17_11")
    rows = [
        {
            "document": normalized,
            "catalog_sha256": catalog_digest(normalized),
            "native_document_sha256": catalog_digest(native),
            "fixture_database_sha256": catalog_digest(
                {"fixture_database": native["database"]}
            ),
        }
        for native, normalized in (
            (first_native, first_doc),
            (second_native, second_doc),
        )
    ]
    raw = _canonical(
        {
            "schema": "ctcc.devval.hermetic_linux_catalog_pair.v1",
            "case": HERMETIC_CATALOG_CASE,
            "scope": "two_disposable_linux_tls_installations",
            "sql_sha256": SQL_PINS,
            "server_build": first_native["build"],
            "catalogs": rows,
            "windows_reference_admitted": False,
            "execution_authority": False,
        }
    )
    verify_hermetic_linux_catalogs(raw)
    with _posix_root(_root_path(Path("/validation-results"))) as results:
        results.publish(HERMETIC_CATALOG_FILE, raw)
        _need(
            results.read(HERMETIC_CATALOG_FILE, MAX_HERMETIC_CATALOG_BYTES) == raw,
            "campaign_catalog_evidence_readback",
        )


def validate_catalog_document(document: dict) -> str:
    """Require a separately sealed native reference, never a caller digest."""
    digest = inspect_catalog_document(document)
    _need(
        _HEX.fullmatch(EXPECTED_CATALOG_SHA256) is not None,
        "campaign_catalog_reference_unsealed",
    )
    _need(digest == EXPECTED_CATALOG_SHA256, "campaign_catalog_physical_drift")
    return digest


async def observe_catalog(engine) -> str:
    """Future read-only verifier; currently refuses before opening a DB."""
    _need(
        _HEX.fullmatch(EXPECTED_CATALOG_SHA256) is not None,
        "campaign_catalog_reference_unsealed",
    )
    try:
        async with asyncio.timeout(15), engine.connect() as connection:
            await connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            await connection.exec_driver_sql("SET LOCAL statement_timeout = '8s'")
            await connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            await connection.exec_driver_sql(
                "SET LOCAL search_path = pg_catalog, pg_temp"
            )
            document = (await connection.exec_driver_sql(CATALOG_SQL)).scalar_one()
        return validate_catalog_document(document)
    except CatalogRefusal:
        raise
    except Exception:  # noqa: BLE001 -- never reveal native catalog or DSN details.
        raise CatalogRefusal("campaign_catalog_readback_incomplete") from None
