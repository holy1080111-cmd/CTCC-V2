
WITH tables AS MATERIALIZED (
  SELECT c.* FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(CAST(:tables AS pg_catalog.text[]))
), toast AS MATERIALIZED (
  SELECT p.relname AS parent_name,p.oid AS parent_oid,c.oid,c.relowner,c.relacl,c.relname
  FROM tables p JOIN pg_catalog.pg_class c ON c.oid=p.reltoastrelid
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='pg_toast' AND c.relkind='t' AND c.relname=('pg_toast_'||p.oid::pg_catalog.text)
), toast_indexes AS MATERIALIZED (
  SELECT t.parent_name,c.oid,c.relowner,c.relacl FROM toast t
  JOIN pg_catalog.pg_index i ON i.indrelid=t.oid JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
  WHERE c.relkind='i' AND c.relname=('pg_toast_'||t.parent_oid::pg_catalog.text||'_index')
), constraints AS MATERIALIZED (
  SELECT k.* FROM pg_catalog.pg_constraint k JOIN tables t ON t.oid=k.conrelid
), triggers AS MATERIALIZED (
  SELECT g.*,k.conname,k.contype FROM pg_catalog.pg_trigger g
  LEFT JOIN constraints k ON k.oid=g.tgconstraint
  WHERE g.tgrelid IN (SELECT oid FROM tables) OR k.oid IS NOT NULL
    OR (g.tgname='gate3_fitted_candidate_claim_guard' AND g.tgrelid='public.gate3_capture_schedule_key_claims'::pg_catalog.regclass)
), types AS MATERIALIZED (
  SELECT y.* FROM pg_catalog.pg_type y JOIN tables t ON y.oid=t.reltype
  UNION ALL
  SELECT a.* FROM pg_catalog.pg_type a JOIN pg_catalog.pg_type y ON a.oid=y.typarray
  JOIN tables t ON t.reltype=y.oid
), routines AS MATERIALIZED (
  SELECT p.* FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.oid::pg_catalog.regprocedure::pg_catalog.text=ANY(CAST(:routines AS pg_catalog.text[]))
), targets(classid,objid,alias,owner_oid,raw_acl) AS MATERIALIZED (
  SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,t.oid,NULL::pg_catalog.jsonb,t.relowner,t.relacl::pg_catalog.text[] FROM tables t
  UNION ALL SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,c.oid,NULL,c.relowner,c.relacl::pg_catalog.text[]
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_index i ON c.oid=i.indexrelid JOIN tables t ON t.oid=i.indrelid
  UNION ALL SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,oid,
    pg_catalog.jsonb_build_array('toast_of',parent_name),relowner,relacl::pg_catalog.text[] FROM toast
  UNION ALL SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid,oid,
    pg_catalog.jsonb_build_array('toast_index_of',parent_name),relowner,relacl::pg_catalog.text[] FROM toast_indexes
  UNION ALL SELECT 'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid,oid,NULL,typowner,typacl::pg_catalog.text[] FROM types
  UNION ALL SELECT 'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid,oid,NULL,proowner,proacl::pg_catalog.text[] FROM routines
  UNION ALL SELECT 'pg_catalog.pg_constraint'::pg_catalog.regclass::pg_catalog.oid,oid,NULL,NULL,NULL FROM constraints
  UNION ALL SELECT 'pg_catalog.pg_attrdef'::pg_catalog.regclass::pg_catalog.oid,d.oid,NULL,NULL,NULL
    FROM pg_catalog.pg_attrdef d JOIN tables t ON t.oid=d.adrelid
  UNION ALL SELECT 'pg_catalog.pg_trigger'::pg_catalog.regclass::pg_catalog.oid,g.oid,
    CASE WHEN g.tgisinternal THEN pg_catalog.jsonb_build_array('foreign_key_trigger',g.conname,
      g.tgrelid::pg_catalog.regclass::pg_catalog.text,g.tgfoid::pg_catalog.regprocedure::pg_catalog.text,
      g.tgtype,g.tgenabled::pg_catalog.text,g.tgdeferrable,g.tginitdeferred) END,NULL,NULL
    FROM triggers g
), dependencies AS MATERIALIZED (
  SELECT d.* FROM pg_catalog.pg_depend d WHERE EXISTS(SELECT 1 FROM targets t
    WHERE (d.classid,d.objid)=(t.classid,t.objid) OR (d.refclassid,d.refobjid)=(t.classid,t.objid))
), references_set(classid,objid) AS MATERIALIZED (
  SELECT classid,objid FROM targets UNION SELECT classid,objid FROM dependencies
  UNION SELECT refclassid,refobjid FROM dependencies
), names AS MATERIALIZED (
  SELECT r.classid,r.objid,COALESCE(t.alias,pg_catalog.to_jsonb(pg_catalog.pg_identify_object(r.classid,r.objid,0))) AS semantic
  FROM references_set r LEFT JOIN targets t ON (t.classid,t.objid)=(r.classid,r.objid)
), graph AS MATERIALIZED (
  SELECT pg_catalog.jsonb_build_array(d.classid::pg_catalog.int8,src.semantic,d.objsubid,
    d.refclassid::pg_catalog.int8,dst.semantic,d.refobjsubid,d.deptype::pg_catalog.text) AS edge
  FROM dependencies d JOIN names src ON (src.classid,src.objid)=(d.classid,d.objid)
  JOIN names dst ON (dst.classid,dst.objid)=(d.refclassid,d.refobjid)
), bound AS MATERIALIZED (
  SELECT pg_catalog.jsonb_build_array(t.classid::pg_catalog.int8,n.semantic,t.owner_oid::pg_catalog.int8,t.raw_acl) AS entry
  FROM targets t JOIN names n ON (t.classid,t.objid)=(n.classid,n.objid)
)
SELECT pg_catalog.jsonb_build_object(
 'layout_ok',(SELECT count(*)=3 FROM tables) AND (SELECT count(*)=20 FROM routines)
  AND (SELECT count(*)=6 FROM types) AND (SELECT count(*)=3 FROM toast) AND (SELECT count(*)=3 FROM toast_indexes)
  AND (SELECT count(*)=12 FROM triggers WHERE tgisinternal)
  AND NOT EXISTS(SELECT 1 FROM triggers WHERE tgisinternal AND (contype IS DISTINCT FROM 'f'
    OR tgname !~ '^RI_ConstraintTrigger_[ac]_[0-9]+$' OR tgenabled<>'O' OR tgdeferrable OR tginitdeferred))
  AND NOT EXISTS(SELECT 1 FROM targets WHERE owner_oid IS NOT NULL AND owner_oid<>10)
  AND NOT EXISTS(SELECT 1 FROM names WHERE semantic IS NULL OR semantic->>'identity' IS NULL
    AND pg_catalog.jsonb_typeof(semantic)<>'array')
  AND NOT EXISTS(SELECT 1 FROM dependencies WHERE deptype NOT IN ('n','a','i')),
 'shared',(SELECT count(*) FROM pg_catalog.pg_shdepend d JOIN targets t ON (d.classid,d.objid)=(t.classid,t.objid)
   WHERE d.dbid IN (0,(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()))),
 'initial',(SELECT count(*) FROM pg_catalog.pg_init_privs p JOIN targets t ON (p.classoid,p.objoid)=(t.classid,t.objid)),
 'dependency_count',(SELECT count(*) FROM dependencies),
 'edges',(SELECT pg_catalog.jsonb_agg(edge ORDER BY edge::pg_catalog.text COLLATE "C") FROM graph),
 'objects',(SELECT pg_catalog.jsonb_agg(entry ORDER BY entry::pg_catalog.text COLLATE "C") FROM bound),
 'physical',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(classid::pg_catalog.int8,objid::pg_catalog.int8)
   ORDER BY classid,objid) FROM targets)
)
