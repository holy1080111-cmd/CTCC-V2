"""Complete source17 catalog comparison DATA; pending genuine reference seal.

No DTO, equal pair, expected digest or saved report enables a schema upgrade.
Only explicitly enumerated structural references/allocation fields normalize.
Original retained row OIDs, sequence values and ACLs remain a separate proof.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import Path

SCHEMA = "ctcc.retained0017.normalized_catalog.v1"
REFERENCE_STATUS = "PENDING_TWO_INDEPENDENT_ACTUAL_PG17_REFERENCES"
SEALED_CATALOG_SHA256 = ""
SEALED_SOURCE17_PREFIX_SHA256 = ""
ROOT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FUNCTIONS = frozenset(
    {
        "qualification_ledger_immutable",
        "qualification_reservation_update",
        "qualification_scope_update",
    }
)
_REF_TABLES = (
    "pg_class",
    "pg_type",
    "pg_proc",
    "pg_namespace",
    "pg_operator",
    "pg_opclass",
    "pg_collation",
    "pg_am",
    "pg_tablespace",
    "pg_language",
    "pg_constraint",
    "pg_trigger",
    "pg_attrdef",
    "pg_default_acl",
    "pg_database",
)


def _source_trigger_bodies():
    """Parse exact migration literals without importing/executing a migration."""
    source = (
        ROOT / "migrations/versions/0017_qualification_reservations.py"
    ).read_bytes()
    tree = ast.parse(source.decode("utf-8"))
    upgrade = [
        node
        for node in tree.body
        if type(node) is ast.FunctionDef and node.name == "upgrade"
    ]
    _need(len(upgrade) == 1)
    result = {}
    for node in ast.walk(upgrade[0]):
        if (
            type(node) is ast.Call
            and type(node.func) is ast.Attribute
            and node.func.attr == "execute"
            and len(node.args) == 1
            and type(node.args[0]) is ast.Constant
            and type(node.args[0].value) is str
        ):
            match = re.fullmatch(
                r"\s*CREATE FUNCTION ([a-z_]+)\(\) RETURNS trigger AS \$\$(.*?)\$\$ LANGUAGE plpgsql;\s*",
                node.args[0].value,
                re.DOTALL,
            )
            if match:
                _need(match[1] in _FUNCTIONS and match[1] not in result)
                result[match[1]] = match[2]
    _need(set(result) == _FUNCTIONS)
    return result


def _quote_source(text):
    return "'" + text.replace("'", "''") + "'"


def _source_functions_predicate():
    bodies = _source_trigger_bodies()
    values = ",".join(
        "(" + _quote_source(name) + "," + _quote_source(body) + ")"
        for name, body in sorted(bodies.items())
    )
    return (
        """NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 LEFT JOIN (VALUES """
        + values
        + """) expected(name,body) ON p.proname=expected.name
 WHERE n.nspname='public' AND (expected.name IS NULL OR p.prosrc IS DISTINCT FROM expected.body
 OR p.proowner<>(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
 OR p.prokind<>'f' OR p.prorettype<>2279 OR p.pronargs<>0 OR p.proargtypes::pg_catalog.text<>''
 OR p.prolang<>(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql')
 OR p.proargdefaults IS NOT NULL OR p.prosqlbody IS NOT NULL
 OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL OR p.proargnames IS NOT NULL
 OR p.prosupport<>0 OR p.provariadic<>0 OR p.pronargdefaults<>0))
 AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public')=3"""
    )


_LANGUAGE_ADMISSION_SQL = """
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_language WHERE lanname NOT IN ('internal','c','sql','plpgsql'))
 AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_language WHERE lanname='plpgsql')=1
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_language l WHERE l.lanname='plpgsql'
 AND l.lanowner=10 AND l.lanispl AND l.lanpltrusted AND l.lanacl IS NULL
 AND (SELECT pg_catalog.count(*) FROM (VALUES
  ('plpgsql_call_handler','',2280,false,l.lanplcallfoid),
  ('plpgsql_inline_handler','2281',2278,true,l.laninline),
  ('plpgsql_validator','26',2278,true,l.lanvalidator)) expected(name,args,returns,strict,oid)
 JOIN pg_catalog.pg_proc p ON p.oid=expected.oid
 WHERE p.proname=expected.name AND p.pronamespace=11 AND p.proowner=10 AND p.prolang=13
 AND p.proargtypes::pg_catalog.text=expected.args AND p.prorettype=expected.returns
 AND p.prosrc=expected.name AND p.probin='$libdir/plpgsql' AND p.proisstrict=expected.strict
 AND p.prokind='f' AND p.provolatile='v' AND p.proparallel='u' AND NOT p.prosecdef
 AND NOT p.proretset AND NOT p.proleakproof AND p.provariadic=0 AND p.prosupport=0
 AND p.pronargdefaults=0 AND p.procost=1 AND p.prorows=0
 AND p.proconfig IS NULL AND p.proallargtypes IS NULL AND p.proargmodes IS NULL
 AND p.proargnames IS NULL AND p.proargdefaults IS NULL AND p.protrftypes IS NULL
 AND p.prosqlbody IS NULL AND p.proacl IS NULL)=3)
 AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_extension)=1
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_extension e WHERE e.extname='plpgsql' AND e.extowner=10
 AND e.extnamespace=11 AND e.extversion='1.0' AND NOT e.extrelocatable
 AND e.extconfig IS NULL AND e.extcondition IS NULL
 AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_depend d WHERE d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass
 AND d.refobjid=e.oid AND d.deptype='e')=4
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d WHERE d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass
 AND d.refobjid=e.oid AND d.deptype='e' AND NOT (
  (d.classid='pg_catalog.pg_language'::pg_catalog.regclass AND d.objid=(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql') AND d.objsubid=0)
  OR (d.classid='pg_catalog.pg_proc'::pg_catalog.regclass AND d.objsubid=0 AND d.objid IN
   (SELECT lanplcallfoid FROM pg_catalog.pg_language WHERE lanname='plpgsql'
    UNION ALL SELECT laninline FROM pg_catalog.pg_language WHERE lanname='plpgsql'
    UNION ALL SELECT lanvalidator FROM pg_catalog.pg_language WHERE lanname='plpgsql')))))
"""
_REFERENCE_SELECTS = tuple(
    "SELECT '" + name + "'::pg_catalog.text AS catalog,p.oid::pg_catalog.int8 AS oid,"
    "pg_catalog.to_jsonb(pg_catalog.pg_identify_object('pg_catalog."
    + name
    + "'::pg_catalog.regclass,p.oid,0)) AS address "
    "FROM pg_catalog." + name + " p"
    for name in _REF_TABLES
)

_CATALOG_CASE = (
    "CASE {field} "
    + " ".join(
        "WHEN 'pg_catalog." + name + "'::pg_catalog.regclass THEN '" + name + "'"
        for name in (*_REF_TABLES, "pg_authid")
    )
    + " ELSE 'UNSUPPORTED_OBJECT_CLASS' END"
)

SAFE_CATALOG_SQL = """
SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname NOT IN ('public','information_schema','pg_catalog','pg_toast') AND nspname !~ '^pg_(toast_)?temp_[0-9]+$')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_largeobject_metadata)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_server)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_publication)
 AND NOT EXISTS(SELECT oid FROM pg_catalog.pg_subscription)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_transform)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_statistic_ext s JOIN pg_catalog.pg_namespace n ON n.oid=s.stxnamespace WHERE n.nspname='public')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND (i.indexprs IS NOT NULL OR i.indpred IS NOT NULL))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_foreign_table)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND (c.relkind NOT IN ('r','i','S') OR c.relhasrules OR c.relrowsecurity OR c.relforcerowsecurity OR c.relispartition))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i JOIN pg_catalog.pg_class c ON c.oid=i.inhrelid OR c.oid=i.inhparent
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND a.attnum>0
  AND (a.attisdropped OR a.atttypid NOT IN (16,17,20,21,23,25,26,114,700,701,869,1042,1043,1082,1114,1184,1700,2950,3802)))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.prokind<>'f')
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
 WHERE n.nspname='public' AND NOT (
  (t.typtype='c' AND EXISTS(SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid=t.typrelid AND c.relnamespace=t.typnamespace AND c.relkind='r' AND c.reltype=t.oid))
  OR (t.typtype='b' AND EXISTS(SELECT 1 FROM pg_catalog.pg_type elem JOIN pg_catalog.pg_class c ON c.oid=elem.typrelid
   WHERE elem.oid=t.typelem AND elem.typarray=t.oid AND elem.typtype='c' AND c.relkind='r' AND c.relnamespace=t.typnamespace))))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND a.attnum>0 AND a.attcollation NOT IN (0,100))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_namespace n ON n.oid=d.objid
  WHERE d.classid='pg_catalog.pg_namespace'::pg_catalog.regclass AND n.nspname='public' AND d.deptype='e')
"""

CATALOG_SQL = (
    """
WITH RECURSIVE public_relations AS (
 SELECT c.* FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
), toast_roots AS (
 SELECT p.oid AS parent_oid,p.relname AS parent_name,t.oid AS toast_oid
 FROM public_relations p JOIN pg_catalog.pg_class t ON t.oid=p.reltoastrelid
), relations AS (
 SELECT c.* FROM public_relations c UNION
 SELECT t.* FROM pg_catalog.pg_class t JOIN toast_roots r ON r.toast_oid=t.oid UNION
 SELECT c.* FROM pg_catalog.pg_class c JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid
 JOIN toast_roots r ON r.toast_oid=i.indrelid
), expression_nodes AS (
 SELECT d.adbin::pg_catalog.text raw FROM pg_catalog.pg_attrdef d JOIN relations r ON r.oid=d.adrelid
 UNION ALL SELECT c.conbin::pg_catalog.text FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND c.conbin IS NOT NULL
 UNION ALL SELECT t.tgqual::pg_catalog.text FROM pg_catalog.pg_trigger t JOIN relations r ON r.oid=t.tgrelid WHERE t.tgqual IS NOT NULL
), expression_operator_ids AS (
 SELECT DISTINCT m.parts[1]::pg_catalog.oid id FROM expression_nodes e
 CROSS JOIN LATERAL pg_catalog.regexp_matches(e.raw,'[:]opno ([0-9]+)','g') m(parts)
 UNION SELECT p.value FROM expression_operator_ids used JOIN pg_catalog.pg_operator o ON o.oid=used.id
 CROSS JOIN LATERAL (VALUES(o.oprcom),(o.oprnegate)) p(value) WHERE p.value<>0
), initial_callback_ids AS (
 SELECT DISTINCT m.parts[2]::pg_catalog.oid id FROM expression_nodes e
 CROSS JOIN LATERAL pg_catalog.regexp_matches(e.raw,':(funcid|opfuncid|hashfuncid|negfuncid) ([0-9]+)','g') m(parts)
 UNION SELECT p.value::pg_catalog.oid FROM expression_operator_ids used JOIN pg_catalog.pg_operator o ON o.oid=used.id
 CROSS JOIN LATERAL (VALUES(o.oprcode),(o.oprrest),(o.oprjoin)) p(value) WHERE p.value::pg_catalog.oid<>0
), expression_callback_ids AS (
 SELECT id FROM initial_callback_ids WHERE id<>0
 UNION SELECT p.prosupport::pg_catalog.oid FROM expression_callback_ids used JOIN pg_catalog.pg_proc p ON p.oid=used.id WHERE p.prosupport::pg_catalog.oid<>0
), object_references AS ("""
    + " UNION ALL ".join(_REFERENCE_SELECTS)
    + """
 UNION ALL SELECT 'pg_authid',r.oid::pg_catalog.int8,
 pg_catalog.to_jsonb(pg_catalog.pg_identify_object('pg_catalog.pg_authid'::pg_catalog.regclass,r.oid,0)) FROM pg_catalog.pg_roles r
), tracked_objects AS (
 SELECT 'pg_catalog.pg_class'::pg_catalog.regclass AS catalog,c.oid FROM relations c
 UNION SELECT 'pg_catalog.pg_type'::pg_catalog.regclass,t.oid FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
 UNION SELECT 'pg_catalog.pg_proc'::pg_catalog.regclass,p.oid FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
 UNION SELECT 'pg_catalog.pg_constraint'::pg_catalog.regclass,k.oid FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_namespace n ON n.oid=k.connamespace WHERE n.nspname='public'
 UNION SELECT 'pg_catalog.pg_attrdef'::pg_catalog.regclass,d.oid FROM pg_catalog.pg_attrdef d JOIN relations c ON c.oid=d.adrelid
 UNION SELECT 'pg_catalog.pg_trigger'::pg_catalog.regclass,t.oid FROM pg_catalog.pg_trigger t JOIN relations c ON c.oid=t.tgrelid
 UNION SELECT 'pg_catalog.pg_default_acl'::pg_catalog.regclass,d.oid FROM pg_catalog.pg_default_acl d
 UNION SELECT 'pg_catalog.pg_namespace'::pg_catalog.regclass,n.oid FROM pg_catalog.pg_namespace n WHERE n.nspname='public'
 UNION SELECT 'pg_catalog.pg_database'::pg_catalog.regclass,d.oid FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()
), retained_dependencies AS (
 SELECT d.* FROM pg_catalog.pg_depend d WHERE EXISTS(SELECT 1 FROM tracked_objects x WHERE (x.catalog=d.classid AND x.oid=d.objid) OR (x.catalog=d.refclassid AND x.oid=d.refobjid))
), retained_shared_dependencies AS (
 SELECT d.* FROM pg_catalog.pg_shdepend d WHERE
 (d.dbid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()) OR d.dbid=0)
 AND EXISTS(SELECT 1 FROM tracked_objects x WHERE x.catalog=d.classid AND x.oid=d.objid)
), entries AS (
 SELECT 'relation' AS kind,pg_catalog.to_jsonb(c) AS value,'{}'::pg_catalog.jsonb AS extra FROM relations c
 UNION ALL SELECT 'attribute',pg_catalog.to_jsonb(a),'{}'::pg_catalog.jsonb
 FROM pg_catalog.pg_attribute a JOIN relations c ON c.oid=a.attrelid WHERE a.attnum>0
 UNION ALL SELECT 'default',pg_catalog.to_jsonb(d),'{}'::pg_catalog.jsonb
 FROM pg_catalog.pg_attrdef d JOIN relations c ON c.oid=d.adrelid
 UNION ALL SELECT 'constraint',pg_catalog.to_jsonb(k),'{}'::pg_catalog.jsonb
 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_namespace n ON n.oid=k.connamespace WHERE n.nspname='public'
 UNION ALL SELECT 'trigger',pg_catalog.to_jsonb(t),pg_catalog.jsonb_build_object('constraint_type',k.contype)
 FROM pg_catalog.pg_trigger t JOIN relations c ON c.oid=t.tgrelid LEFT JOIN pg_catalog.pg_constraint k ON k.oid=t.tgconstraint
 UNION ALL SELECT 'index',pg_catalog.to_jsonb(i)||pg_catalog.jsonb_build_object('indclass',i.indclass::pg_catalog.oid[],'indcollation',i.indcollation::pg_catalog.oid[]),
 '{}'::pg_catalog.jsonb
 FROM pg_catalog.pg_index i JOIN relations c ON c.oid=i.indrelid
 UNION ALL SELECT 'function',pg_catalog.to_jsonb(p)||pg_catalog.jsonb_build_object('prosupport',p.prosupport::pg_catalog.int8,'proargtypes',p.proargtypes::pg_catalog.oid[]),
 '{}'::pg_catalog.jsonb
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
 UNION ALL SELECT 'type',pg_catalog.to_jsonb(t)||pg_catalog.jsonb_build_object('typsubscript',t.typsubscript::pg_catalog.int8,'typinput',t.typinput::pg_catalog.int8,'typoutput',t.typoutput::pg_catalog.int8,'typreceive',t.typreceive::pg_catalog.int8,'typsend',t.typsend::pg_catalog.int8,'typmodin',t.typmodin::pg_catalog.int8,'typmodout',t.typmodout::pg_catalog.int8,'typanalyze',t.typanalyze::pg_catalog.int8),'{}'::pg_catalog.jsonb
 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
 UNION ALL SELECT 'sequence',pg_catalog.to_jsonb(s),'{}'::pg_catalog.jsonb FROM pg_catalog.pg_sequence s JOIN public_relations c ON c.oid=s.seqrelid
 UNION ALL SELECT 'default_acl',pg_catalog.to_jsonb(d),'{}'::pg_catalog.jsonb FROM pg_catalog.pg_default_acl d
 UNION ALL SELECT 'namespace',pg_catalog.to_jsonb(n),'{}'::pg_catalog.jsonb FROM pg_catalog.pg_namespace n WHERE n.nspname='public'
 UNION ALL SELECT 'database',pg_catalog.to_jsonb(d),'{}'::pg_catalog.jsonb FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()
 UNION ALL SELECT 'initial_acl',pg_catalog.to_jsonb(p),pg_catalog.jsonb_build_object('catalog',__INITCLASS__)
 FROM pg_catalog.pg_init_privs p WHERE EXISTS(SELECT 1 FROM tracked_objects t WHERE t.catalog=p.classoid AND t.oid=p.objoid)
 OR (p.classoid='pg_catalog.pg_language'::pg_catalog.regclass AND p.objoid=(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql'))
 OR (p.classoid='pg_catalog.pg_proc'::pg_catalog.regclass AND p.objoid IN
  (SELECT lanplcallfoid FROM pg_catalog.pg_language WHERE lanname='plpgsql'
   UNION ALL SELECT laninline FROM pg_catalog.pg_language WHERE lanname='plpgsql'
   UNION ALL SELECT lanvalidator FROM pg_catalog.pg_language WHERE lanname='plpgsql'))
)
SELECT pg_catalog.jsonb_build_object('schema','ctcc.retained0017.raw_catalog.v1',
 'profile',pg_catalog.jsonb_build_object('owner',current_user,'session_owner',session_user,'role',pg_catalog.current_setting('role'),
 'database',pg_catalog.current_database(),'read_only',pg_catalog.current_setting('transaction_read_only'),'search_path',pg_catalog.current_setting('search_path'),
 'timezone',pg_catalog.current_setting('TimeZone'),'server_version',pg_catalog.current_setting('server_version_num'),
 'datum_abi',CASE WHEN pg_catalog.version() ~ ' on x86_64[^,]*,' THEN 'pg17.amd64.little.datum8.signedchar' ELSE NULL END,
 'tls',EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)),
 'head',(SELECT pg_catalog.jsonb_agg(version_num ORDER BY version_num) FROM public.alembic_version),
 'references',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(catalog,oid,address) ORDER BY catalog,oid) FROM object_references),
 'toast',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(parent_oid,toast_oid,parent_name) ORDER BY parent_name),'[]'::pg_catalog.jsonb) FROM toast_roots),
 'dependencies',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(__CLASS__,d.objid::pg_catalog.int8,d.objsubid,__REFCLASS__,d.refobjid::pg_catalog.int8,d.refobjsubid,d.deptype::pg_catalog.text) ORDER BY d.classid,d.objid,d.objsubid,d.refclassid,d.refobjid,d.refobjsubid,d.deptype),'[]'::pg_catalog.jsonb) FROM retained_dependencies d),
 'shared_dependencies',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(d.dbid::pg_catalog.int8,__CLASS__,d.objid::pg_catalog.int8,d.objsubid,__REFCLASS__,d.refobjid::pg_catalog.int8,d.deptype::pg_catalog.text) ORDER BY d.dbid,d.classid,d.objid,d.objsubid,d.refclassid,d.refobjid,d.deptype),'[]'::pg_catalog.jsonb) FROM retained_shared_dependencies d),
 'bootstrap',pg_catalog.jsonb_build_object(
  'languages',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(l) ORDER BY l.lanname) FROM pg_catalog.pg_language l),
  'language_callbacks',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(p)||pg_catalog.jsonb_build_object('proargtypes',p.proargtypes::pg_catalog.oid[],'prosupport',p.prosupport::pg_catalog.int8) ORDER BY p.proname) FROM pg_catalog.pg_proc p WHERE EXISTS(SELECT 1 FROM pg_catalog.pg_language l WHERE p.oid IN (l.lanplcallfoid,l.laninline,l.lanvalidator))),
  'expression_operators',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(o)||pg_catalog.jsonb_build_object('oprcode',o.oprcode::pg_catalog.int8,'oprrest',o.oprrest::pg_catalog.int8,'oprjoin',o.oprjoin::pg_catalog.int8) ORDER BY o.oid),'[]'::pg_catalog.jsonb) FROM pg_catalog.pg_operator o JOIN expression_operator_ids used ON used.id=o.oid),
  'expression_callbacks',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(p)||pg_catalog.jsonb_build_object('proargtypes',p.proargtypes::pg_catalog.oid[],'prosupport',p.prosupport::pg_catalog.int8) ORDER BY p.oid),'[]'::pg_catalog.jsonb) FROM pg_catalog.pg_proc p JOIN expression_callback_ids used ON used.id=p.oid),
  'extension',(SELECT pg_catalog.to_jsonb(e) FROM pg_catalog.pg_extension e WHERE e.extname='plpgsql'),
  'collations',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) ORDER BY c.oid) FROM pg_catalog.pg_collation c WHERE c.oid IN (100,950))),
 'actor_physical',pg_catalog.jsonb_build_object(
  'database_oid',(SELECT oid::pg_catalog.int8 FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()),
  'owner',(SELECT pg_catalog.jsonb_build_array(oid::pg_catalog.int8,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls) FROM pg_catalog.pg_roles WHERE rolname=current_user),
  'membership_count',(SELECT pg_catalog.count(*) FROM pg_catalog.pg_auth_members m WHERE m.member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user) OR m.roleid=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)),
  'membership_sha256',(SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(COALESCE(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(m) ORDER BY m.roleid,m.member,m.grantor),'[]'::pg_catalog.jsonb)::pg_catalog.text,'UTF8')),'hex') FROM pg_catalog.pg_auth_members m WHERE m.member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user) OR m.roleid=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)),
  'maintenance_settings_sha256',(SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.jsonb_build_object('role_config',r.rolconfig,'settings',(SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(s) ORDER BY s.setdatabase,s.setrole) FROM pg_catalog.pg_db_role_setting s WHERE s.setrole=r.oid OR s.setdatabase=(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database())))::pg_catalog.text,'UTF8')),'hex') FROM pg_catalog.pg_roles r WHERE r.rolname=current_user)),
 'entries',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(kind,value,extra) ORDER BY kind,value::pg_catalog.text) FROM entries))
""".replace("__REFCLASS__", _CATALOG_CASE.format(field="d.refclassid"))
    .replace("__CLASS__", _CATALOG_CASE.format(field="d.classid"))
    .replace("__INITCLASS__", _CATALOG_CASE.format(field="p.classoid"))
)


class Retained0017CatalogRefused(ValueError):
    pass


def _need(value):
    if value is not True:
        raise Retained0017CatalogRefused("retained0017_catalog_refused") from None


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


_SOURCE_TOAST_ALLOCATION_PREDICATE = """n.nspname='pg_toast' AND (
 (o.relkind='t' AND EXISTS(SELECT 1 FROM pg_catalog.pg_class p
 JOIN pg_catalog.pg_namespace pn ON pn.oid=p.relnamespace
 WHERE pn.nspname='public' AND p.relkind='r' AND p.reltoastrelid=o.oid))
 OR (o.relkind='i' AND EXISTS(SELECT 1 FROM pg_catalog.pg_index i
 JOIN pg_catalog.pg_class t ON t.oid=i.indrelid
 JOIN pg_catalog.pg_namespace tn ON tn.oid=t.relnamespace
 JOIN pg_catalog.pg_class p ON p.reltoastrelid=t.oid
 JOIN pg_catalog.pg_namespace pn ON pn.oid=p.relnamespace
 WHERE pn.nspname='public' AND p.relkind='r' AND tn.nspname='pg_toast'
 AND t.relkind='t' AND i.indexrelid=o.oid)))"""
_RELATION_NAMES_SQL = """SELECT c.relname::pg_catalog.text
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','S')
 ORDER BY c.relname COLLATE pg_catalog."C"
"""


def _unknown_nonpublic_objects_predicate():
    # FirstNormalObjectId is the PG17 bootstrap boundary. The only source17
    # extension is the four-object plpgsql extension checked independently.
    parts = []
    for catalog, namespace in (
        ("pg_class", "relnamespace"),
        ("pg_proc", "pronamespace"),
        ("pg_type", "typnamespace"),
        ("pg_operator", "oprnamespace"),
        ("pg_opclass", "opcnamespace"),
        ("pg_opfamily", "opfnamespace"),
        ("pg_collation", "collnamespace"),
        ("pg_conversion", "connamespace"),
        ("pg_ts_dict", "dictnamespace"),
        ("pg_ts_config", "cfgnamespace"),
        ("pg_ts_parser", "prsnamespace"),
        ("pg_ts_template", "tmplnamespace"),
    ):
        exception = ""
        if catalog == "pg_class":
            # TOAST names and allocation OIDs are not source identities. Admit
            # only the actual public parent pointer and that TOAST heap's
            # actual index pointer, after the native storage/index proof.
            exception = " AND NOT (" + _SOURCE_TOAST_ALLOCATION_PREDICATE + ")"
        elif catalog == "pg_proc":
            exception = " AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_language l WHERE l.lanname='plpgsql' AND o.oid IN (l.lanplcallfoid,l.laninline,l.lanvalidator))"
        parts.append(
            "NOT EXISTS(SELECT 1 FROM pg_catalog."
            + catalog
            + " o JOIN pg_catalog.pg_namespace n ON n.oid=o."
            + namespace
            + " WHERE n.nspname<>'public' AND o.oid>=16384"
            + exception
            + ")"
        )
    parts.append("NOT EXISTS(SELECT 1 FROM pg_catalog.pg_am WHERE oid>=16384)")
    parts.append("NOT EXISTS(SELECT 1 FROM pg_catalog.pg_cast WHERE oid>=16384)")
    return " AND ".join(parts)


async def _require_raw_catalog_admission_v1(connection, combined, components):
    """A failed combined proof remains denied; diagnostics never read a head."""
    from sqlalchemy import text

    if await connection.scalar(text(combined)) is True:
        return
    # These are the same four fixed catalog-only clauses, evaluated only after
    # native storage/marker admission. No observed identity/value is returned.
    for name, predicate in components:
        try:
            passed = await connection.scalar(text("SELECT " + predicate))
        except Exception:  # noqa: BLE001 -- failed diagnostics cannot expose driver details.
            raise Retained0017CatalogRefused(
                "retained0017_raw_admission_diagnostic_unavailable"
            ) from None
        if passed is not True:
            raise Retained0017CatalogRefused(
                "retained0017_raw_admission_" + name + "_refused"
            ) from None
    # A changed result or SQL NULL cannot turn a failed first proof into a pass.
    raise Retained0017CatalogRefused("retained0017_raw_admission_changed") from None


async def capture_catalog17_v1(connection):
    """Fresh read-only TLS catalog DATA; does not enable the pending reference.

    Caller must establish its reviewed owner route and READ ONLY transaction.
    This function changes no isolation/settings, role, row, sequence or DDL.
    Native metadata is checked before any application head/deparser access.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncConnection

    from scripts import native_owner_heap_am_v1 as storage

    _need(
        type(connection) is AsyncConnection
        and connection.in_transaction()
        and connection.dialect.name == "postgresql"
        and connection.dialect.driver == "asyncpg"
    )
    admission = """SELECT current_user=session_user
 AND pg_catalog.current_setting('role')='none'
 AND pg_catalog.current_setting('transaction_read_only')='on'
 AND pg_catalog.current_setting('search_path')='pg_catalog'
 AND pg_catalog.current_setting('TimeZone')='UTC'
 AND pg_catalog.current_setting('server_version_num')::pg_catalog.int8>=170000
 AND pg_catalog.current_setting('server_version_num')::pg_catalog.int8<180000
 AND pg_catalog.version() ~ ' on x86_64[^,]*,'
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.oid=d.datdba
 WHERE d.datname=pg_catalog.current_database() AND r.rolname=current_user AND r.rolcanlogin)"""
    _need(await connection.scalar(text(admission)) is True)
    await storage.lock_native_owner_marker_v1(connection)
    components = (
        ("scope", SAFE_CATALOG_SQL.removeprefix("\nSELECT ")),
        ("functions", _source_functions_predicate()),
        ("language", _LANGUAGE_ADMISSION_SQL),
        ("nonpublic", _unknown_nonpublic_objects_predicate()),
    )
    raw_admission = "SELECT " + " AND ".join(part for _, part in components)
    await _require_raw_catalog_admission_v1(connection, raw_admission, components)
    # The marker and all row/index/type-output paths have already been proved.
    # Keep every source relation stable against DDL through all catalog reads.
    relations = tuple((await connection.execute(text(_RELATION_NAMES_SQL))).scalars())
    _need(
        0 < len(relations) <= 512
        and all(type(name) is str and _NAME.fullmatch(name) for name in relations)
    )
    contract = source17_contract_v1()
    tables = [name for name in relations if name in contract["columns"]]
    _need(set(tables) == set(contract["columns"]))
    await connection.execute(
        text(
            "LOCK TABLE "
            + ",".join('public."' + name + '"' for name in tables)
            + " IN ACCESS SHARE MODE NOWAIT"
        )
    )
    await storage.require_native_owner_storage_v1(connection)
    await _require_raw_catalog_admission_v1(connection, raw_admission, components)
    document = await connection.scalar(text(CATALOG_SQL))
    _need(type(document) is dict)
    normalize_catalog17_v1(document)
    _need(source17_contract_v1() == contract)
    return document


def source17_contract_v1():
    from scripts.retained0017_source_contract_v1 import source17_contract_from_root_v1

    return source17_contract_from_root_v1(ROOT)


def _acl(value, owner):
    if value is None:
        return {"explicit": False, "entries": []}
    _need(type(value) is list)
    result = []
    for item in value:
        _need(type(item) is str and item.count("=") == item.count("/") == 1)
        role, rest = item.split("=")
        privileges, grantor = rest.split("/")

        def actor(name):
            if name == owner:
                return "$OWNER"
            if name == "":
                return "$PUBLIC"
            if name == "pg_database_owner":
                return "pg_catalog.pg_database_owner"
            _need(False)

        rights = []
        for character in privileges:
            if character == "*":
                _need(bool(rights) and rights[-1][1] is False)
                rights[-1][1] = True
            else:
                _need(
                    character in "arwdDxtXmUCTcsA"
                    and not any(r[0] == character for r in rights)
                )
                rights.append([character, False])
        result.append([actor(role), actor(grantor), sorted(rights)])
    _need(len({canonical(row) for row in result}) == len(result))
    return {"explicit": True, "entries": sorted(result, key=canonical)}


_NODE_FIELDS = {
    "VAR": "varno varattno vartype vartypmod varcollid varnullingrels varlevelsup varnosyn varattnosyn location",
    "CONST": "consttype consttypmod constcollid constlen constbyval constisnull location constvalue",
    "FUNCEXPR": "funcid funcresulttype funcretset funcvariadic funcformat funccollid inputcollid args location",
    "OPEXPR": "opno opfuncid opresulttype opretset opcollid inputcollid args location",
    "BOOLEXPR": "boolop args location",
    "NULLTEST": "arg nulltesttype argisrow location",
    "BOOLEANTEST": "arg booltesttype location",
    "SCALARARRAYOPEXPR": "opno opfuncid hashfuncid negfuncid useOr inputcollid args location",
    "ARRAYEXPR": "array_typeid array_collid element_typeid elements multidims location",
    "RELABELTYPE": "arg resulttype resulttypmod resultcollid relabelformat location",
    "COERCEVIAIO": "arg resulttype resultcollid coerceformat location",
    "COALESCEEXPR": "coalescetype coalescecollid args location",
    "ROWEXPR": "args row_typeid row_format colnames location",
    "ROWCOMPAREEXPR": "rctype opnos opfamilies inputcollids largs rargs",
    "MINMAXEXPR": "minmaxtype minmaxcollid inputcollid op args location",
    "SQLVALUEFUNCTION": "op type typmod location",
    "CASEEXPR": "casetype casecollid arg args defresult location",
    "CASEWHEN": "expr result location",
    "CASETESTEXPR": "typeId typeMod collation",
}
_NODE_TYPES = frozenset(
    {
        16,
        17,
        20,
        21,
        23,
        25,
        26,
        114,
        700,
        701,
        869,
        1009,
        1015,
        1042,
        1043,
        1082,
        1114,
        1184,
        1700,
        2205,
        2249,
        2950,
        3802,
    }
)
_CONST_ABI = {
    16: (1, True),
    17: (-1, False),
    20: (8, True),
    21: (2, True),
    23: (4, True),
    25: (-1, False),
    26: (4, True),
    114: (-1, False),
    700: (4, True),
    701: (8, True),
    869: (-1, False),
    1009: (-1, False),
    1015: (-1, False),
    1042: (-1, False),
    1043: (-1, False),
    1082: (4, True),
    1114: (8, True),
    1184: (8, True),
    1700: (-1, False),
    2205: (4, True),
    2950: (16, False),
    3802: (-1, False),
}
_TYPE_FIELDS = frozenset(
    [
        "vartype",
        "consttype",
        "funcresulttype",
        "opresulttype",
        "array_typeid",
        "element_typeid",
        "resulttype",
        "coalescetype",
        "row_typeid",
        "minmaxtype",
        "type",
        "casetype",
        "typeId",
    ]
)
_PROC_FIELDS = frozenset({"funcid", "opfuncid", "hashfuncid", "negfuncid"})
_NODE_TOKEN = re.compile(r'\s*(\{|\}|\(|\)|\[|\]|"(?:\\.|[^"\\])*"|[^\s{}()\[\]]+)')


def normalize_node_tree17_v1(raw, *, sequence_oid=None, sequence_identity=None):
    """Pure, bounded PG17 node reader. Never evaluate text or call type I/O.

    Every semantic field and constant byte is retained. Only the typed parser
    location field and an exact dependency-proven sequence regclass Datum are
    allocation-independent. No arbitrary integer/string replacement exists.
    """
    if raw is None:
        return None
    _need(type(raw) is str and 0 < len(raw.encode("utf-8")) <= 2 * 1024 * 1024)
    tokens = []
    end = 0
    for match in _NODE_TOKEN.finditer(raw):
        _need(not raw[end : match.start()].strip())
        tokens.append(match[1])
        end = match.end()
    _need(not raw[end:].strip() and 0 < len(tokens) <= 250000)
    position = 0
    regclasses = 0

    def integer(value):
        _need(type(value) is int)
        return value

    def parse(depth):
        nonlocal position, regclasses
        _need(depth <= 128 and position < len(tokens))
        token = tokens[position]
        position += 1
        if token == "{":
            _need(position < len(tokens))
            tag = tokens[position]
            position += 1
            _need(tag in _NODE_FIELDS)
            allowed = set(_NODE_FIELDS[tag].split())
            fields = []
            seen = set()
            while position < len(tokens) and tokens[position] != "}":
                field = tokens[position]
                position += 1
                _need(
                    field.startswith(":") and field[1:] in allowed and field not in seen
                )
                seen.add(field)
                values = []
                while (
                    position < len(tokens)
                    and tokens[position] != "}"
                    and not tokens[position].startswith(":")
                ):
                    values.append(parse(depth + 1))
                _need(bool(values))
                name = field[1:]
                if name == "location":
                    _need(len(values) == 1 and integer(values[0]) >= -1)
                    values = ["$PARSER_LOCATION"]
                elif name in _TYPE_FIELDS:
                    _need(len(values) == 1 and integer(values[0]) in _NODE_TYPES)
                elif name in _PROC_FIELDS or name == "opno":
                    _need(len(values) == 1 and 0 <= integer(values[0]) < 16384)
                    if name in ("hashfuncid", "negfuncid"):
                        _need(values[0] == 0)
                elif name in {
                    "varcollid",
                    "constcollid",
                    "funccollid",
                    "opcollid",
                    "inputcollid",
                    "array_collid",
                    "resultcollid",
                    "coalescecollid",
                    "minmaxcollid",
                    "casecollid",
                    "collation",
                }:
                    _need(len(values) == 1 and integer(values[0]) in (0, 100))
                fields.append([name, values])
            _need(position < len(tokens) and tokens[position] == "}")
            position += 1
            mapping = {name: values for name, values in fields}
            if tag == "CONST":
                _need(set(mapping) == set(_NODE_FIELDS["CONST"].split()))
                value = mapping["constvalue"]
                _need(
                    value == ["<>"]
                    or (
                        len(value) == 2
                        and type(value[0]) is int
                        and type(value[1]) is list
                        and len(value[1]) == 2
                        and value[1][0] == "["
                    )
                )
                _need(
                    mapping["consttype"] != [2249]
                    and mapping["constisnull"] in (["true"], ["false"])
                    and mapping["constbyval"] in (["true"], ["false"])
                    and len(mapping["constlen"]) == 1
                    and integer(mapping["constlen"][0]) in (-2, -1, 1, 2, 4, 8, 16)
                )
                _need(mapping["consttype"][0] in _CONST_ABI)
                expected_length, expected_byval = _CONST_ABI[mapping["consttype"][0]]
                _need(
                    mapping["constlen"] == [expected_length]
                    and mapping["constbyval"] == ["true" if expected_byval else "false"]
                )
                if mapping["constisnull"] == ["true"]:
                    _need(value == ["<>"])
                else:
                    _need(
                        len(value) == 2
                        and 0 <= value[0] <= 2 * 1024 * 1024
                        and len(value[1]) == 2
                        and type(value[1][1]) is list
                        and all(
                            type(v) is int and -128 <= v <= 127 for v in value[1][1]
                        )
                    )
                    if mapping["constbyval"] == ["true"]:
                        _need(
                            value[0] == mapping["constlen"][0]
                            and value[0] in (1, 2, 4, 8)
                            and len(value[1][1]) == 8
                        )
                    else:
                        _need(
                            value[0] > 0
                            and len(value[1][1]) == value[0]
                            and (expected_length < 0 or value[0] == expected_length)
                        )
                if mapping["consttype"] == [2205]:
                    _need(
                        type(sequence_oid) is int
                        and 0 < sequence_oid < 2**32
                        and type(sequence_identity) is dict
                    )
                    _need(
                        mapping["constlen"] == [4]
                        and mapping["constbyval"] == ["true"]
                        and mapping["constisnull"] == ["false"]
                    )
                    _need(
                        len(value) == 2
                        and value[0] == 4
                        and len(value[1]) == 2
                        and type(value[1][1]) is list
                    )
                    signed = value[1][1]
                    _need(
                        len(signed) == 8
                        and all(type(v) is int and -128 <= v <= 127 for v in signed)
                    )
                    payload = bytes(v % 256 for v in signed)
                    _need(payload == sequence_oid.to_bytes(8, "little"))
                    mapping["constvalue"][:] = [
                        "$SOURCE_SEQUENCE_REGCLASS",
                        copy.deepcopy(sequence_identity),
                    ]
                    regclasses += 1
            if tag == "VAR":
                _need(
                    mapping.get("varno") == [1]
                    and mapping.get("varlevelsup") == [0]
                    and len(mapping.get("varattno", [])) == 1
                    and integer(mapping["varattno"][0]) > 0
                )
            return ["NODE", tag, fields]
        if token in ("(", "["):
            closing = ")" if token == "(" else "]"
            values = []
            while position < len(tokens) and tokens[position] != closing:
                values.append(parse(depth + 1))
            _need(position < len(tokens) and tokens[position] == closing)
            position += 1
            return [token, values]
        _need(token not in ("}", ")", "]") and not token.startswith(":"))
        if re.fullmatch(r"-?[0-9]+", token):
            return int(token)
        return token

    try:
        result = parse(0)
        _need(position == len(tokens))
        _need(
            (sequence_oid is None and regclasses == 0)
            or (sequence_oid is not None and regclasses == 1)
        )
        if sequence_oid is not None:
            _need(result[0:2] == ["NODE", "FUNCEXPR"])
            original_fields = {name: value for name, value in result[2]}
            _need(
                original_fields.get("funcid") == [1574]
                and original_fields.get("funcresulttype") == [20]
            )
        return result
    finally:
        # Break only this invocation's recursive closure; no global cache or
        # garbage-collection policy changes, including on refusal paths.
        parse = None


def normalize_catalog17_v1(document):
    """Pure DATA transform. Caller JSON and equal outputs have no authority."""
    try:
        return _normalize(document)
    except Retained0017CatalogRefused:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        _need(False)


def _bootstrap_acl(value, bootstrap_owner):
    result = _acl(value, bootstrap_owner)
    for entry in result["entries"]:
        for index in (0, 1):
            if entry[index] == "$OWNER":
                entry[index] = "$PG_BOOTSTRAP_OWNER_10"
    return result


def _normalize_bootstrap(value, ref, bootstrap_owner):
    _need(
        type(value) is dict
        and set(value)
        == {
            "languages",
            "language_callbacks",
            "expression_operators",
            "expression_callbacks",
            "extension",
            "collations",
        }
    )
    normalized = {}
    languages = []
    callbacks = []
    collations = []
    _need(
        type(value["languages"]) is list
        and {row["lanname"] for row in value["languages"]}
        == {"internal", "c", "sql", "plpgsql"}
    )
    for original in value["languages"]:
        row = copy.deepcopy(original)
        key = ref("pg_language", row.pop("oid"))
        _need(row["lanowner"] == 10)
        row["lanowner"] = "$PG_BOOTSTRAP_OWNER_10"
        row["lanacl"] = _bootstrap_acl(row["lanacl"], bootstrap_owner)
        for field in ("lanplcallfoid", "laninline", "lanvalidator"):
            row[field] = ref("pg_proc", row[field])
        languages.append([key, row])
    _need(
        type(value["language_callbacks"]) is list
        and {row["proname"] for row in value["language_callbacks"]}
        == {
            "plpgsql_call_handler",
            "plpgsql_inline_handler",
            "plpgsql_validator",
            "fmgr_internal_validator",
            "fmgr_c_validator",
            "fmgr_sql_validator",
        }
    )
    for original in value["language_callbacks"]:
        row = copy.deepcopy(original)
        key = ref("pg_proc", row.pop("oid"))
        _need(row["proowner"] == 10)
        row["proowner"] = "$PG_BOOTSTRAP_OWNER_10"
        for field, catalog in {
            "pronamespace": "pg_namespace",
            "prolang": "pg_language",
            "provariadic": "pg_type",
            "prosupport": "pg_proc",
            "prorettype": "pg_type",
        }.items():
            row[field] = ref(catalog, row[field])
        for field in ("proargtypes", "proallargtypes", "protrftypes"):
            row[field] = (
                None
                if row[field] is None
                else [ref("pg_type", oid) for oid in row[field]]
            )
        row["proacl"] = _bootstrap_acl(row["proacl"], bootstrap_owner)
        callbacks.append([key, row])
    row = copy.deepcopy(value["extension"])
    _need(type(row) is dict and row["extname"] == "plpgsql" and row["extowner"] == 10)
    _need(type(row.pop("oid")) is int)
    row["extowner"] = "$PG_BOOTSTRAP_OWNER_10"
    row["extnamespace"] = ref("pg_namespace", row["extnamespace"])
    normalized["extension"] = row
    _need(
        type(value["collations"]) is list
        and {row["oid"] for row in value["collations"]} == {100, 950}
    )
    for original in value["collations"]:
        row = copy.deepcopy(original)
        key = ref("pg_collation", row.pop("oid"))
        _need(row["collowner"] == 10)
        row["collowner"] = "$PG_BOOTSTRAP_OWNER_10"
        row["collnamespace"] = ref("pg_namespace", row["collnamespace"])
        collations.append([key, row])
    normalized.update(
        languages=sorted(languages, key=canonical),
        language_callbacks=sorted(callbacks, key=canonical),
        collations=sorted(collations, key=canonical),
    )
    callbacks = []
    operators = []
    _need(
        type(value["expression_callbacks"]) is list
        and len(value["expression_callbacks"]) <= 1000
        and type(value["expression_operators"]) is list
        and len(value["expression_operators"]) <= 1000
    )
    for original in value["expression_callbacks"]:
        row = copy.deepcopy(original)
        oid = row.pop("oid")
        key = ref("pg_proc", oid)
        _need(
            type(oid) is int
            and 0 < oid < 16384
            and row["proowner"] == 10
            and row["pronamespace"] == 11
            and row["prolang"] == 12
            and row["prokind"] == "f"
            and row["proargdefaults"] is None
            and row["prosqlbody"] is None
        )
        row["source_builtin_oid"] = oid
        row["proowner"] = "$PG_BOOTSTRAP_OWNER_10"
        for field, catalog in {
            "pronamespace": "pg_namespace",
            "prolang": "pg_language",
            "provariadic": "pg_type",
            "prosupport": "pg_proc",
            "prorettype": "pg_type",
        }.items():
            row[field] = ref(catalog, row[field])
        for field in ("proargtypes", "proallargtypes", "protrftypes"):
            row[field] = (
                None if row[field] is None else [ref("pg_type", v) for v in row[field]]
            )
        row["proacl"] = _bootstrap_acl(row["proacl"], bootstrap_owner)
        callbacks.append([key, row])
    for original in value["expression_operators"]:
        row = copy.deepcopy(original)
        oid = row.pop("oid")
        key = ref("pg_operator", oid)
        _need(
            type(oid) is int
            and 0 < oid < 16384
            and row["oprowner"] == 10
            and row["oprnamespace"] == 11
        )
        row["source_builtin_oid"] = oid
        row["oprowner"] = "$PG_BOOTSTRAP_OWNER_10"
        for field, catalog in {
            "oprnamespace": "pg_namespace",
            "oprleft": "pg_type",
            "oprright": "pg_type",
            "oprresult": "pg_type",
            "oprcom": "pg_operator",
            "oprnegate": "pg_operator",
            "oprcode": "pg_proc",
            "oprrest": "pg_proc",
            "oprjoin": "pg_proc",
        }.items():
            row[field] = ref(catalog, row[field])
        operators.append([key, row])
    _need(
        len({canonical(row[0]) for row in callbacks}) == len(callbacks)
        and len({canonical(row[0]) for row in operators}) == len(operators)
    )
    normalized.update(
        expression_callbacks=sorted(callbacks, key=canonical),
        expression_operators=sorted(operators, key=canonical),
    )
    return normalized


def _normalize(document):
    _need(
        type(document) is dict
        and set(document)
        == {
            "schema",
            "profile",
            "head",
            "references",
            "toast",
            "entries",
            "dependencies",
            "shared_dependencies",
            "bootstrap",
            "actor_physical",
        }
    )
    _need(
        document["schema"] == "ctcc.retained0017.raw_catalog.v1"
        and document["head"] == ["0017"]
    )
    profile = document["profile"]
    _need(
        type(profile) is dict
        and set(profile)
        == {
            "owner",
            "session_owner",
            "role",
            "database",
            "read_only",
            "search_path",
            "timezone",
            "server_version",
            "tls",
            "datum_abi",
        }
    )
    owner = profile["owner"]
    _need(
        type(owner) is str
        and _NAME.fullmatch(owner) is not None
        and not owner.startswith("pg_")
    )
    _need(
        profile["session_owner"] == owner
        and profile["role"] == "none"
        and profile["read_only"] == "on"
        and profile["search_path"] == "pg_catalog"
        and profile["timezone"] == "UTC"
        and profile["tls"] is True
    )
    _need(profile["datum_abi"] == "pg17.amd64.little.datum8.signedchar")
    _need(
        type(profile["database"]) is str
        and _NAME.fullmatch(profile["database"]) is not None
    )
    _need(
        type(profile["server_version"]) is str
        and re.fullmatch(r"17[0-9]{4}", profile["server_version"]) is not None
    )
    contract = source17_contract_v1()
    references = {}
    _need(
        type(document["references"]) is list
        and 0 < len(document["references"]) <= 40000
    )
    for catalog, oid, address in document["references"]:
        _need(
            catalog in {*_REF_TABLES, "pg_authid"}
            and type(oid) is int
            and 0 < oid < 2**32
        )
        _need(
            type(address) is dict
            and set(address) == {"type", "schema", "name", "identity"}
        )
        _need(
            type(address["type"]) is str
            and type(address["identity"]) is str
            and len(address["identity"]) > 0
        )
        _need((catalog, oid) not in references)
        references[catalog, oid] = copy.deepcopy(address)

    def ref(catalog, oid):
        _need(type(oid) is int and 0 <= oid < 2**32)
        if oid == 0:
            return None
        value = references[catalog, oid]
        if catalog == "pg_authid":
            identity = value["identity"]
            if identity == owner:
                return "$OWNER"
            if identity == "pg_database_owner":
                return "pg_catalog.pg_database_owner"
            _need(False)
        if catalog == "pg_database":
            _need(value["identity"] == profile["database"])
            return "$DATABASE"
        return copy.deepcopy(value)

    entries = document["entries"]
    _need(type(entries) is list and 0 < len(entries) <= 20000)
    physical = document["actor_physical"]
    _need(
        type(physical) is dict
        and set(physical)
        == {
            "database_oid",
            "owner",
            "membership_count",
            "membership_sha256",
            "maintenance_settings_sha256",
        }
    )
    _need(
        type(physical["database_oid"]) is int
        and ref("pg_database", physical["database_oid"]) == "$DATABASE"
    )
    role = physical["owner"]
    _need(
        type(role) is list
        and len(role) == 9
        and type(role[0]) is int
        and role[1] == owner
        and ref("pg_authid", role[0]) == "$OWNER"
        and all(type(flag) is bool for flag in role[2:])
    )
    _need(
        type(physical["membership_count"]) is int
        and physical["membership_count"] >= 0
        and type(physical["membership_sha256"]) is str
        and _HEX.fullmatch(physical["membership_sha256"]) is not None
        and type(physical["maintenance_settings_sha256"]) is str
        and _HEX.fullmatch(physical["maintenance_settings_sha256"]) is not None
    )
    dependencies = document["dependencies"]
    _need(type(dependencies) is list and len(dependencies) <= 100000)
    for edge in dependencies:
        _need(
            type(edge) is list
            and len(edge) == 7
            and edge[0] in _REF_TABLES
            and edge[3] in _REF_TABLES
            and type(edge[1]) is int
            and type(edge[2]) is int
            and type(edge[4]) is int
            and type(edge[5]) is int
            and edge[2] >= 0
            and edge[5] >= 0
            and edge[6] in ("n", "a", "i")
        )
    sequence_oids = {
        row["oid"]
        for kind, row, _ in entries
        if kind == "relation" and row["relkind"] == "S"
    }
    # TOAST allocation identities derive only from actual parent pointers.
    for parent, toast, parent_name in document["toast"]:
        _need(
            parent_name in contract["columns"]
            and ref("pg_class", parent)["identity"] == "public." + parent_name
        )
        _need(ref("pg_class", toast)["identity"] == "pg_toast.pg_toast_" + str(parent))
        references["pg_class", toast] = {
            "type": "toast table",
            "schema": "pg_toast",
            "name": None,
            "identity": "$TOAST_OF:public." + parent_name,
        }
        for kind, row, _extra in entries:
            if kind == "index" and row["indrelid"] == toast:
                index_oid = row["indexrelid"]
                references["pg_class", index_oid] = {
                    "type": "toast index",
                    "schema": "pg_toast",
                    "name": None,
                    "identity": "$TOAST_INDEX_OF:public." + parent_name,
                }
    # FK trigger allocation names are replaced only with their exact named
    # constraint, table, function and complete trigger flags retained below.
    for kind, row, extra in entries:
        if kind == "trigger" and row["tgisinternal"] is True:
            _need(
                extra["constraint_type"] == "f"
                and re.fullmatch(r"RI_ConstraintTrigger_[ac]_[0-9]+", row["tgname"])
                is not None
            )
            references["pg_trigger", row["oid"]] = {
                "type": "constraint trigger",
                "schema": "public",
                "name": None,
                "identity": canonical(
                    [
                        ref("pg_constraint", row["tgconstraint"]),
                        ref("pg_class", row["tgrelid"]),
                        ref("pg_proc", row["tgfoid"]),
                        row["tgtype"],
                    ]
                ).decode("ascii"),
            }
    # Default ACL objects have allocated OIDs and owner names inside their
    # identity text; derive their structural key from explicit typed fields.
    for kind, row, _ in entries:
        if kind == "default_acl":
            references["pg_default_acl", row["oid"]] = {
                "type": "default ACL",
                "schema": None,
                "name": None,
                "identity": canonical(
                    [
                        ref("pg_authid", row["defaclrole"]),
                        ref("pg_namespace", row["defaclnamespace"]),
                        row["defaclobjtype"],
                    ]
                ).decode("ascii"),
            }
    output = []
    tables = {}
    columns = {}
    functions = set()
    keys = set()
    mappings = {
        "relation": {
            "relnamespace": "pg_namespace",
            "reltype": "pg_type",
            "relowner": "pg_authid",
            "relam": "pg_am",
            "reltablespace": "pg_tablespace",
            "reltoastrelid": "pg_class",
        },
        "attribute": {
            "attrelid": "pg_class",
            "atttypid": "pg_type",
            "attcollation": "pg_collation",
        },
        "default": {"adrelid": "pg_class"},
        "constraint": {
            "connamespace": "pg_namespace",
            "conrelid": "pg_class",
            "contypid": "pg_type",
            "conindid": "pg_class",
            "confrelid": "pg_class",
            "conparentid": "pg_constraint",
        },
        "trigger": {
            "tgrelid": "pg_class",
            "tgparentid": "pg_trigger",
            "tgfoid": "pg_proc",
            "tgconstraint": "pg_constraint",
            "tgconstrrelid": "pg_class",
            "tgconstrindid": "pg_class",
        },
        "index": {"indexrelid": "pg_class", "indrelid": "pg_class"},
        "function": {
            "pronamespace": "pg_namespace",
            "proowner": "pg_authid",
            "prolang": "pg_language",
            "provariadic": "pg_type",
            "prosupport": "pg_proc",
            "prorettype": "pg_type",
        },
        "type": {
            "typnamespace": "pg_namespace",
            "typowner": "pg_authid",
            "typrelid": "pg_class",
            "typelem": "pg_type",
            "typarray": "pg_type",
            "typbasetype": "pg_type",
            "typcollation": "pg_collation",
            **{
                name: "pg_proc"
                for name in (
                    "typsubscript",
                    "typinput",
                    "typoutput",
                    "typreceive",
                    "typsend",
                    "typmodin",
                    "typmodout",
                    "typanalyze",
                )
            },
        },
        "sequence": {"seqrelid": "pg_class", "seqtypid": "pg_type"},
        "default_acl": {"defaclrole": "pg_authid", "defaclnamespace": "pg_namespace"},
        "namespace": {"nspowner": "pg_authid"},
        "database": {"datdba": "pg_authid", "dattablespace": "pg_tablespace"},
        "initial_acl": {},
    }
    acl_fields = {
        "relation": "relacl",
        "attribute": "attacl",
        "function": "proacl",
        "type": "typacl",
        "default_acl": "defaclacl",
        "namespace": "nspacl",
        "database": "datacl",
    }
    catalog_for = {
        "relation": "pg_class",
        "default": "pg_attrdef",
        "constraint": "pg_constraint",
        "trigger": "pg_trigger",
        "function": "pg_proc",
        "type": "pg_type",
        "default_acl": "pg_default_acl",
        "namespace": "pg_namespace",
        "database": "pg_database",
    }
    for kind, original, extra in entries:
        _need(kind in mappings and type(original) is dict and type(extra) is dict)
        row = copy.deepcopy(original)
        if kind == "initial_acl":
            _need(set(extra) == {"catalog"} and extra["catalog"] in _REF_TABLES)
            catalog = extra["catalog"]
            key = [
                catalog,
                ref(catalog, row["objoid"]),
                row["objsubid"],
                row["privtype"],
            ]
            row["classoid"] = catalog
            row["objoid"] = ref(catalog, row["objoid"])
            if catalog in ("pg_proc", "pg_language"):
                row["initprivs"] = _bootstrap_acl(
                    row["initprivs"], references["pg_authid", 10]["identity"]
                )
            else:
                row["initprivs"] = _acl(row["initprivs"], owner)
        elif kind == "attribute":
            key = [ref("pg_class", row["attrelid"]), row["attnum"]]
        elif kind == "index":
            key = ref("pg_class", row["indexrelid"])
        elif kind == "sequence":
            key = ref("pg_class", row["seqrelid"])
        else:
            key = ref(catalog_for[kind], row.pop("oid"))
        identity = canonical([kind, key])
        _need(identity not in keys)
        keys.add(identity)
        for name, catalog in mappings[kind].items():
            row[name] = ref(catalog, row[name])
        if kind in acl_fields:
            field = acl_fields[kind]
            row[field] = _acl(row[field], owner)
        if kind == "relation":
            _need(
                original["relkind"] in {"r", "i", "S", "t"}
                and original["relrewrite"] == 0
                and not original["relispartition"]
            )
            for name in (
                "relfilenode",
                "relpages",
                "reltuples",
                "relallvisible",
                "relfrozenxid",
                "relminmxid",
            ):
                row.pop(name, None)
            if key["schema"] == "public" and original["relkind"] == "r":
                tables[original["relname"]] = original["oid"]
            if key["schema"] == "pg_toast":
                row["relname"] = key["identity"]
        if kind == "attribute":
            _need(
                type(row["attnum"]) is int
                and row["attnum"] > 0
                and not row["attisdropped"]
            )
            columns.setdefault(original["attrelid"], []).append(
                (row["attnum"], row["attname"])
            )
        if kind == "default":
            seqs = [
                edge[4]
                for edge in dependencies
                if edge[0] == "pg_attrdef"
                and edge[1] == original["oid"]
                and edge[2] == 0
                and edge[3] == "pg_class"
                and edge[4] in sequence_oids
                and edge[5] == 0
                and edge[6] == "n"
            ]
            _need(len(seqs) <= 1)
            row["adbin"] = normalize_node_tree17_v1(
                original["adbin"],
                sequence_oid=seqs[0] if seqs else None,
                sequence_identity=ref("pg_class", seqs[0]) if seqs else None,
            )
        if kind == "constraint":
            row["conbin"] = normalize_node_tree17_v1(original["conbin"])
            for field in ("conpfeqop", "conppeqop", "conffeqop", "conexclop"):
                row[field] = (
                    None
                    if row[field] is None
                    else [ref("pg_operator", oid) for oid in row[field]]
                )
        if kind == "trigger":
            row["tgqual"] = normalize_node_tree17_v1(original["tgqual"])
            row["tgname"] = key["identity"] if row["tgisinternal"] else row["tgname"]
        if kind == "index":
            _need(row["indexprs"] is None and row["indpred"] is None)
            row["indclass"] = [ref("pg_opclass", oid) for oid in row["indclass"]]
            row["indcollation"] = [
                ref("pg_collation", oid) for oid in row["indcollation"]
            ]
        if kind == "function":
            _need(
                row["proname"] in _FUNCTIONS
                and row["prokind"] == "f"
                and row["proargdefaults"] is None
            )
            functions.add(row["proname"])
            for field in ("proargtypes", "proallargtypes", "protrftypes"):
                row[field] = (
                    None
                    if row[field] is None
                    else [ref("pg_type", oid) for oid in row[field]]
                )
        if kind == "type":
            _need(row["typtype"] in {"c", "b"} and row["typdefaultbin"] is None)
        if kind == "database":
            _need(row["datname"] == profile["database"])
            row["datname"] = "$DATABASE"
            row.pop("datfrozenxid", None)
            row.pop("datminmxid", None)
        output.append([kind, key, row])
    _need(set(tables) == set(contract["columns"]) and functions == _FUNCTIONS)
    for name, oid in tables.items():
        _need([name for _, name in sorted(columns[oid])] == contract["columns"][name])
    normalized_dependencies = []
    for source, oid, subid, target, refid, refsubid, deptype in dependencies:
        normalized_dependencies.append(
            [
                source,
                ref(source, oid),
                subid,
                target,
                ref(target, refid),
                refsubid,
                deptype,
            ]
        )
    _need(
        len({canonical(edge) for edge in normalized_dependencies})
        == len(normalized_dependencies)
    )
    shared = document["shared_dependencies"]
    normalized_shared = []
    _need(type(shared) is list and len(shared) <= 100000)
    for edge in shared:
        _need(type(edge) is list and len(edge) == 7)
        dbid, source, oid, subid, target, refid, deptype = edge
        _need(
            type(dbid) is int
            and dbid in (0, physical["database_oid"])
            and source in _REF_TABLES
            and target in ("pg_authid", "pg_tablespace")
            and type(subid) is int
            and subid >= 0
            and deptype in ("o", "a", "i", "r", "t")
        )
        normalized_shared.append(
            [
                "$DATABASE" if dbid else "$SHARED_OBJECT",
                source,
                ref(source, oid),
                subid,
                target,
                ref(target, refid),
                deptype,
            ]
        )
    _need(
        len({canonical(edge) for edge in normalized_shared}) == len(normalized_shared)
    )
    return {
        "schema": SCHEMA,
        "source17_prefix_sha256": contract["source17_prefix_sha256"],
        "server_version": profile["server_version"],
        "datum_abi": profile["datum_abi"],
        "entries": sorted(output, key=canonical),
        "dependencies": sorted(normalized_dependencies, key=canonical),
        "shared_dependencies": sorted(normalized_shared, key=canonical),
        "bootstrap": _normalize_bootstrap(
            document["bootstrap"], ref, references["pg_authid", 10]["identity"]
        ),
    }


def require_sealed_catalog17_v1(document):
    """Literal source seal only. Caller reports never install a reference."""
    _need(
        REFERENCE_STATUS == "SEALED_FROM_TWO_REVIEWED_ACTUAL_PG17_REFERENCES"
        and _HEX.fullmatch(SEALED_CATALOG_SHA256) is not None
        and _HEX.fullmatch(SEALED_SOURCE17_PREFIX_SHA256) is not None
    )
    value = normalize_catalog17_v1(document)
    _need(
        value["source17_prefix_sha256"] == SEALED_SOURCE17_PREFIX_SHA256
        and sha(canonical(value)) == SEALED_CATALOG_SHA256
    )
    return {
        "schema": "ctcc.retained0017.catalog_reference_match.v1",
        "catalog_sha256": SEALED_CATALOG_SHA256,
        "schema_upgrade_authorized": False,
        "execution_authority": False,
    }
