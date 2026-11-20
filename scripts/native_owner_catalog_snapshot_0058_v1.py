"""Owner maintenance DATA: stable logical catalog fingerprints across DDL rollback.

Fresh PostgreSQL object OIDs, relfilenodes and statistics are allocation data.
Object definitions, ownership, privileges, constraints and trigger identities
remain part of the fingerprint. Internal FK trigger names contain fresh OIDs;
their owning named constraint, function and complete flags replace that name.
This observer neither grants privileges nor confers any execution authority.
"""

from sqlalchemy import text

LOGICAL_CATALOG = text("""
WITH relations AS (
 SELECT c.*,n.nspname FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
), entries AS (
 SELECT 'relation' AS kind,
 (to_jsonb(c)-ARRAY['oid','relfilenode','relpages','reltuples','relallvisible',
 'relfrozenxid','relminmxid','reltype','reltoastrelid','relrewrite']) ||
 jsonb_build_object('type_name',NULLIF(c.reltype,0)::regtype::text,
 'has_toast',c.reltoastrelid<>0) AS value FROM relations c
 UNION ALL
 SELECT 'attribute',(to_jsonb(a)-ARRAY['attrelid','atttypid','attcollation']) ||
 jsonb_build_object('relation',c.relname,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
 'collation',NULLIF(a.attcollation,0)::regcollation::text,
 'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid))
 FROM pg_catalog.pg_attribute a JOIN relations c ON c.oid=a.attrelid
 LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attnum>0
 UNION ALL
 SELECT 'constraint',(to_jsonb(k)-ARRAY['oid','conrelid','contypid','conindid','confrelid','conparentid']) ||
 jsonb_build_object('relation',NULLIF(k.conrelid,0)::regclass::text,
 'type',NULLIF(k.contypid,0)::regtype::text,
 'index',NULLIF(k.conindid,0)::regclass::text,
 'foreign_relation',NULLIF(k.confrelid,0)::regclass::text,
 'parent_constraint',(SELECT conname FROM pg_catalog.pg_constraint WHERE oid=k.conparentid),
 'definition',pg_catalog.pg_get_constraintdef(k.oid))
 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_namespace n ON n.oid=k.connamespace
 WHERE n.nspname='public'
 UNION ALL
 SELECT 'trigger',(to_jsonb(t)-ARRAY['oid','tgrelid','tgparentid','tgfoid',
 'tgconstraint','tgconstrrelid','tgconstrindid','tgname']) ||
 jsonb_build_object('relation',c.relname,'name',CASE WHEN t.tgisinternal THEN NULL ELSE t.tgname END,
 'function',t.tgfoid::regprocedure::text,'constraint',k.conname,
 'constraint_relation',NULLIF(t.tgconstrrelid,0)::regclass::text,
 'constraint_index',NULLIF(t.tgconstrindid,0)::regclass::text,
 'parent_trigger',(SELECT CASE WHEN q.tgisinternal THEN NULL ELSE q.tgname END
 FROM pg_catalog.pg_trigger q WHERE q.oid=t.tgparentid))
 FROM pg_catalog.pg_trigger t JOIN relations c ON c.oid=t.tgrelid
 LEFT JOIN pg_catalog.pg_constraint k ON k.oid=t.tgconstraint
 UNION ALL
 SELECT 'index',(to_jsonb(i)-ARRAY['indexrelid','indrelid']) ||
 jsonb_build_object('table',c.relname,'index',i.indexrelid::regclass::text,
 'definition',pg_catalog.pg_get_indexdef(i.indexrelid))
 FROM pg_catalog.pg_index i JOIN relations c ON c.oid=i.indrelid
 UNION ALL
 SELECT 'function',jsonb_build_object('name',p.proname,
 'arguments',pg_catalog.pg_get_function_identity_arguments(p.oid),
 'definition',CASE WHEN p.prokind='a' THEN p.prosrc ELSE pg_catalog.pg_get_functiondef(p.oid) END,
 'aggregate',(SELECT to_jsonb(a)-'aggfnoid' FROM pg_catalog.pg_aggregate a WHERE a.aggfnoid=p.oid),
 'owner',p.proowner,'acl',p.proacl,'kind',p.prokind)
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public'
 UNION ALL
 SELECT 'type',(to_jsonb(t)-ARRAY['oid','typarray','typrelid','typelem','typbasetype',
 'typsubscript','typinput','typoutput','typreceive','typsend','typmodin','typmodout','typanalyze']) ||
 jsonb_build_object('array',NULLIF(t.typarray,0)::regtype::text,
 'relation',NULLIF(t.typrelid,0)::regclass::text,'element',NULLIF(t.typelem,0)::regtype::text,
 'base',NULLIF(t.typbasetype,0)::regtype::text,
 'subscript',NULLIF(t.typsubscript,0)::regprocedure::text,
 'input',NULLIF(t.typinput,0)::regprocedure::text,'output',NULLIF(t.typoutput,0)::regprocedure::text,
 'receive',NULLIF(t.typreceive,0)::regprocedure::text,'send',NULLIF(t.typsend,0)::regprocedure::text,
 'typmodin',NULLIF(t.typmodin,0)::regprocedure::text,'typmodout',NULLIF(t.typmodout,0)::regprocedure::text,
 'analyze',NULLIF(t.typanalyze,0)::regprocedure::text)
 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
 UNION ALL
 SELECT 'enum',jsonb_build_array(e.enumtypid::regtype::text,e.enumsortorder,e.enumlabel)
 FROM pg_catalog.pg_enum e JOIN pg_catalog.pg_type t ON t.oid=e.enumtypid
 JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
 UNION ALL
 SELECT 'sequence',to_jsonb(s) FROM pg_catalog.pg_sequences s WHERE schemaname='public'
 UNION ALL
 SELECT 'default_acl',to_jsonb(a)-'oid' FROM pg_catalog.pg_default_acl a
), document AS (
 SELECT jsonb_agg(jsonb_build_array(kind,value) ORDER BY kind,value::text)::text AS raw
 FROM entries
)
SELECT pg_catalog.octet_length(raw),
 pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(raw,'UTF8')),'hex')
FROM document
""")

LOGICAL_ACL = text("""
SELECT kind,object_name,grantee,grantor,privilege,is_grantable FROM (
 SELECT 'relation'::text AS kind,c.relname::text AS object_name,a.grantee::bigint,
 a.grantor::bigint,a.privilege_type::text AS privilege,a.is_grantable
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault(
 CASE WHEN c.relkind='S' THEN 's'::"char" ELSE 'r'::"char" END,c.relowner))) a
 WHERE n.nspname='public' AND c.relkind IN ('r','p','m','S','v','f')
 UNION ALL
 SELECT 'function',p.proname||'('||pg_catalog.pg_get_function_identity_arguments(p.oid)||')',
 a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE n.nspname='public'
 UNION ALL
 SELECT 'schema',n.nspname,a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_namespace n CROSS JOIN LATERAL
 pg_catalog.aclexplode(COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a
 WHERE n.nspname='public'
 UNION ALL
 SELECT 'database',d.datname,a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_database d CROSS JOIN LATERAL
 pg_catalog.aclexplode(COALESCE(d.datacl,pg_catalog.acldefault('d',d.datdba))) a
 WHERE d.datname=current_database()
) q ORDER BY kind,object_name,grantee,grantor,privilege,is_grantable
""")

NEW_LIVE_ACCESS = text("""
SELECT NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:tables AS text[])) AND a.grantee<>c.relowner
) AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
 WHERE n.nspname='public' AND c.relname=ANY(CAST(:tables AS text[])) AND x.grantee<>c.relowner
) AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
 WHERE n.nspname='public' AND p.proname=ANY(CAST(:functions AS text[])) AND a.grantee<>p.proowner
)
""")
