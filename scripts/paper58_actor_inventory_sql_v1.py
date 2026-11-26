"""Actual restricted-actor catalog-only inventory; no owner privilege borrowing.

This read-only DATA collector never calls an application/native authority
routine. A future owned startup must separately bind its actual connection,
owner-held full-schema/ACL proof and recovery resources. Calling it directly
does not create that private context or admit an application.
"""

from __future__ import annotations

from scripts.paper58_actor_acl_v1 import INVENTORY_SCHEMA, Paper58ActorAclRefused


def _need(value):
    if value is not True:
        raise Paper58ActorAclRefused("paper58_actor_inventory_refused") from None


ACTOR_INVENTORY_SQL = r"""
WITH a AS (
 SELECT r.* FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
), db AS (
 SELECT d.* FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()
), pub AS (
 SELECT n.* FROM pg_catalog.pg_namespace n WHERE n.nspname='public'
), rels AS (
 SELECT c.* FROM pg_catalog.pg_class c,pub n WHERE c.relnamespace=n.oid AND c.relkind='r'
), seqs AS (
 SELECT c.* FROM pg_catalog.pg_class c,pub n WHERE c.relnamespace=n.oid AND c.relkind='S'
), funcs AS (
 SELECT p.* FROM pg_catalog.pg_proc p,pub n WHERE p.pronamespace=n.oid
), attrs AS (
 SELECT x.* FROM pg_catalog.pg_attribute x,rels c
 WHERE x.attrelid=c.oid AND x.attnum>0 AND NOT x.attisdropped
), tp AS (
 SELECT p FROM pg_catalog.unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',
  'TRUNCATE','REFERENCES','TRIGGER','MAINTAIN']::pg_catalog.text[]) p
), cp AS (
 SELECT p FROM pg_catalog.unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']::pg_catalog.text[]) p
), sp AS (
 SELECT p FROM pg_catalog.unnest(ARRAY['SELECT','UPDATE','USAGE']::pg_catalog.text[]) p
)
SELECT pg_catalog.jsonb_build_object(
 'schema','ctcc.paper58.restricted_actor_privilege_inventory.v1',
 'profile',pg_catalog.jsonb_build_object(
  'database',db.datname::pg_catalog.text,'database_oid',db.oid::pg_catalog.int8,
  'current_user',current_user::pg_catalog.text,'session_user',session_user::pg_catalog.text,
  'role',pg_catalog.current_setting('role'),
  'read_only',pg_catalog.current_setting('transaction_read_only'),
  'search_path',pg_catalog.current_setting('search_path'),
  'timezone',pg_catalog.current_setting('TimeZone'),
  'server_version',pg_catalog.current_setting('server_version_num'),
  'tls',EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl s
    WHERE s.pid=pg_catalog.pg_backend_pid() AND s.ssl)),
 'actor',pg_catalog.jsonb_build_object(
  'oid',a.oid::pg_catalog.int8,'name',a.rolname::pg_catalog.text,
  'flags',pg_catalog.jsonb_build_array(a.rolsuper,a.rolinherit,a.rolcreaterole,
   a.rolcreatedb,a.rolcanlogin,a.rolreplication,a.rolbypassrls)),
 'database_privileges',(SELECT pg_catalog.jsonb_object_agg(p,
   pg_catalog.has_database_privilege(a.oid,db.oid,p))
  FROM pg_catalog.unnest(ARRAY['CONNECT','CREATE','TEMPORARY']::pg_catalog.text[]) p),
 'database_grant_options',(SELECT pg_catalog.jsonb_object_agg(p,
   pg_catalog.has_database_privilege(a.oid,db.oid,p||' WITH GRANT OPTION'))
  FROM pg_catalog.unnest(ARRAY['CONNECT','CREATE','TEMPORARY']::pg_catalog.text[]) p),
 'public_schema_privileges',(SELECT pg_catalog.jsonb_object_agg(p,
   pg_catalog.has_schema_privilege(a.oid,pub.oid,p))
  FROM pg_catalog.unnest(ARRAY['USAGE','CREATE']::pg_catalog.text[]) p),
 'public_schema_grant_options',(SELECT pg_catalog.jsonb_object_agg(p,
   pg_catalog.has_schema_privilege(a.oid,pub.oid,p||' WITH GRANT OPTION'))
  FROM pg_catalog.unnest(ARRAY['USAGE','CREATE']::pg_catalog.text[]) p),
 'table_privileges',COALESCE((SELECT pg_catalog.jsonb_object_agg(c.relname,
   (SELECT pg_catalog.jsonb_object_agg(p,pg_catalog.has_table_privilege(a.oid,c.oid,p)) FROM tp))
  FROM rels c),'{}'::pg_catalog.jsonb),
 'table_grant_options',COALESCE((SELECT pg_catalog.jsonb_object_agg(c.relname,
   (SELECT pg_catalog.jsonb_object_agg(p,pg_catalog.has_table_privilege(a.oid,c.oid,p||' WITH GRANT OPTION')) FROM tp))
  FROM rels c),'{}'::pg_catalog.jsonb),
 'column_privileges',COALESCE((SELECT pg_catalog.jsonb_object_agg(c.relname,
   (SELECT pg_catalog.jsonb_object_agg(x.attname,
    (SELECT pg_catalog.jsonb_object_agg(p,
     pg_catalog.has_column_privilege(a.oid,c.oid,x.attnum::pg_catalog.int4,p)) FROM cp))
    FROM attrs x WHERE x.attrelid=c.oid)) FROM rels c),'{}'::pg_catalog.jsonb),
 'column_grant_options',COALESCE((SELECT pg_catalog.jsonb_object_agg(c.relname,
   (SELECT pg_catalog.jsonb_object_agg(x.attname,
    (SELECT pg_catalog.jsonb_object_agg(p,
     pg_catalog.has_column_privilege(a.oid,c.oid,x.attnum::pg_catalog.int4,p||' WITH GRANT OPTION')) FROM cp))
    FROM attrs x WHERE x.attrelid=c.oid)) FROM rels c),'{}'::pg_catalog.jsonb),
 'sequence_privileges',COALESCE((SELECT pg_catalog.jsonb_object_agg(c.relname,
   (SELECT pg_catalog.jsonb_object_agg(p,pg_catalog.has_sequence_privilege(a.oid,c.oid,p)) FROM sp))
  FROM seqs c),'{}'::pg_catalog.jsonb),
 'sequence_grant_options',COALESCE((SELECT pg_catalog.jsonb_object_agg(c.relname,
   (SELECT pg_catalog.jsonb_object_agg(p,pg_catalog.has_sequence_privilege(a.oid,c.oid,p||' WITH GRANT OPTION')) FROM sp))
  FROM seqs c),'{}'::pg_catalog.jsonb),
 'application_function_execute',COALESCE((SELECT pg_catalog.jsonb_object_agg(p.proname,
   pg_catalog.has_function_privilege(a.oid,p.oid,'EXECUTE')) FROM funcs p),'{}'::pg_catalog.jsonb),
 'application_function_grant_options',COALESCE((SELECT pg_catalog.jsonb_object_agg(p.proname,
   pg_catalog.has_function_privilege(a.oid,p.oid,'EXECUTE WITH GRANT OPTION')) FROM funcs p),'{}'::pg_catalog.jsonb),
 'application_function_count',(SELECT pg_catalog.count(*) FROM funcs),
 'explicit_table_acl_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(c.relname::pg_catalog.text,e.privilege_type,
    e.grantor::pg_catalog.int8,e.grantee::pg_catalog.int8,e.is_grantable)
   ORDER BY c.relname COLLATE pg_catalog."C",e.privilege_type COLLATE pg_catalog."C")
  FROM rels c CROSS JOIN LATERAL pg_catalog.aclexplode(
   COALESCE(c.relacl,pg_catalog.acldefault('r'::pg_catalog."char",c.relowner))) e
  WHERE e.grantee=a.oid),'[]'::pg_catalog.jsonb),
 'explicit_database_acl_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(db.datname::pg_catalog.text,e.privilege_type,
    e.grantor::pg_catalog.int8,e.grantee::pg_catalog.int8,e.is_grantable)
   ORDER BY e.privilege_type COLLATE pg_catalog."C")
  FROM pg_catalog.aclexplode(COALESCE(db.datacl,
   pg_catalog.acldefault('d'::pg_catalog."char",db.datdba))) e
  WHERE e.grantee=a.oid),'[]'::pg_catalog.jsonb),
 'explicit_schema_acl_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array('public',e.privilege_type,
    e.grantor::pg_catalog.int8,e.grantee::pg_catalog.int8,e.is_grantable)
   ORDER BY e.privilege_type COLLATE pg_catalog."C")
  FROM pg_catalog.aclexplode(COALESCE(pub.nspacl,
   pg_catalog.acldefault('n'::pg_catalog."char",pub.nspowner))) e
  WHERE e.grantee=a.oid),'[]'::pg_catalog.jsonb),
 'explicit_column_acl_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(c.relname::pg_catalog.text,x.attname::pg_catalog.text,
    e.grantor::pg_catalog.int8,e.grantee::pg_catalog.int8,e.privilege_type,e.is_grantable))
  FROM attrs x JOIN rels c ON c.oid=x.attrelid
  CROSS JOIN LATERAL pg_catalog.aclexplode(x.attacl) e
  WHERE e.grantee IN (0,a.oid)),'[]'::pg_catalog.jsonb),
 'role_membership_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(m.roleid::pg_catalog.int8,m.member::pg_catalog.int8,
    m.grantor::pg_catalog.int8,m.admin_option,m.inherit_option,m.set_option))
  FROM pg_catalog.pg_auth_members m WHERE a.oid IN (m.roleid,m.member,m.grantor)),'[]'::pg_catalog.jsonb),
 'ownership_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(d.dbid::pg_catalog.int8,d.classid::pg_catalog.int8,
    d.objid::pg_catalog.int8,d.objsubid)) FROM pg_catalog.pg_shdepend d
  WHERE d.refclassid='pg_catalog.pg_authid'::pg_catalog.regclass
   AND d.refobjid=a.oid AND d.deptype='o'),'[]'::pg_catalog.jsonb),
 'actor_shared_acl_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(d.dbid::pg_catalog.int8,
    CASE d.classid WHEN 'pg_catalog.pg_class'::pg_catalog.regclass THEN 'pg_class'
     WHEN 'pg_catalog.pg_database'::pg_catalog.regclass THEN 'pg_database'
     WHEN 'pg_catalog.pg_namespace'::pg_catalog.regclass THEN 'pg_namespace'
     ELSE d.classid::pg_catalog.text END,
    d.objid::pg_catalog.int8,d.objsubid,'pg_authid',d.refobjid::pg_catalog.int8,
    d.deptype::pg_catalog.text)) FROM pg_catalog.pg_shdepend d
  WHERE d.refclassid='pg_catalog.pg_authid'::pg_catalog.regclass
   AND d.refobjid=a.oid AND d.deptype<>'o'),'[]'::pg_catalog.jsonb),
 'default_acl_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(d.oid::pg_catalog.int8,d.defaclrole::pg_catalog.int8,
    d.defaclnamespace::pg_catalog.int8,e.grantee::pg_catalog.int8,e.privilege_type,e.is_grantable))
  FROM pg_catalog.pg_default_acl d
  CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) e
  WHERE d.defaclrole=a.oid OR e.grantee IN (0,a.oid)),'[]'::pg_catalog.jsonb),
 'parameter_acl_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(p.parname,e.grantee::pg_catalog.int8,e.privilege_type,e.is_grantable))
  FROM pg_catalog.pg_parameter_acl p
  CROSS JOIN LATERAL pg_catalog.aclexplode(p.paracl) e
  WHERE e.grantee IN (0,a.oid)),'[]'::pg_catalog.jsonb),
 'role_settings',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.split_part(v,'=',1))
  FROM pg_catalog.pg_db_role_setting s CROSS JOIN LATERAL pg_catalog.unnest(s.setconfig) v
  WHERE s.setrole=a.oid AND s.setdatabase=0),'[]'::pg_catalog.jsonb),
 'database_settings',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.split_part(v,'=',1))
  FROM pg_catalog.pg_db_role_setting s CROSS JOIN LATERAL pg_catalog.unnest(s.setconfig) v
  WHERE s.setrole=0 AND s.setdatabase=db.oid),'[]'::pg_catalog.jsonb),
 'database_role_settings',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.split_part(v,'=',1))
  FROM pg_catalog.pg_db_role_setting s CROSS JOIN LATERAL pg_catalog.unnest(s.setconfig) v
  WHERE s.setrole=a.oid AND s.setdatabase=db.oid),'[]'::pg_catalog.jsonb),
 'global_settings',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.split_part(v,'=',1))
  FROM pg_catalog.pg_db_role_setting s CROSS JOIN LATERAL pg_catalog.unnest(s.setconfig) v
  WHERE s.setrole=0 AND s.setdatabase=0),'[]'::pg_catalog.jsonb),
 'stock_function_acl_override_edges',COALESCE((SELECT pg_catalog.jsonb_agg(
   pg_catalog.jsonb_build_array(p.oid::pg_catalog.int8,p.proname::pg_catalog.text))
  FROM pg_catalog.pg_proc p
  LEFT JOIN pg_catalog.pg_init_privs i ON i.classoid='pg_catalog.pg_proc'::pg_catalog.regclass
   AND i.objoid=p.oid AND i.objsubid=0
  WHERE p.pronamespace='pg_catalog'::pg_catalog.regnamespace
   AND (i.privtype NOT IN ('i','e') OR
    COALESCE(p.proacl,pg_catalog.acldefault('f'::pg_catalog."char",p.proowner))
     IS DISTINCT FROM COALESCE(i.initprivs,pg_catalog.acldefault('f'::pg_catalog."char",p.proowner)))),
  '[]'::pg_catalog.jsonb),
 'pg_control_system_execute',(SELECT pg_catalog.bool_or(
   pg_catalog.has_function_privilege(a.oid,p.oid,'EXECUTE'))
  FROM pg_catalog.pg_proc p WHERE p.pronamespace='pg_catalog'::pg_catalog.regnamespace
   AND p.proname='pg_control_system' AND p.pronargs=0)
) FROM a CROSS JOIN db CROSS JOIN pub
"""


async def capture_restricted_actor_inventory_data_v1(connection):
    """Read genuine restricted-connection metadata without any authority DTO."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncConnection

    _need(
        type(connection) is AsyncConnection
        and connection.in_transaction()
        and connection.dialect.name == "postgresql"
        and connection.dialect.driver == "asyncpg"
    )
    value = await connection.scalar(text(ACTOR_INVENTORY_SQL))
    _need(type(value) is dict and value.get("schema") == INVENTORY_SCHEMA)
    return value
