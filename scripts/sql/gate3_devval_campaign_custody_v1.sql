-- CTCC D/V campaign custody v1. Install only in a separately administered
-- PostgreSQL 17 evidence database. This script creates no login or grant.
-- The installer must retain its own exact source/SQL pin and provision three
-- different restricted LOGINs outside this transaction: publisher may execute
-- publish_campaign, observer may execute acknowledge_campaign and read_campaign,
-- and an optional independent reader may execute read_campaign only. Grant
-- schema USAGE and those exact function EXECUTEs directly; never grant tables.
-- Database timestamps establish ordering on this server, not independent UTC,
-- independently administered custody, Gate 3, OOS, or execution authority.

BEGIN;

DO $ctcc_preflight$
BEGIN
  IF pg_catalog.current_setting('server_version_num')::integer NOT BETWEEN 170000 AND 179999
     OR pg_catalog.current_setting('server_encoding') <> 'UTF8'
     OR pg_catalog.to_regclass('public.alembic_version') IS NOT NULL THEN
    RAISE EXCEPTION 'ctcc_devval_evidence_database_required';
  END IF;
END
$ctcc_preflight$;

CREATE SCHEMA ctcc_devval_custody;
REVOKE ALL ON SCHEMA ctcc_devval_custody FROM PUBLIC;

CREATE TABLE ctcc_devval_custody.campaigns (
  campaign_sha256 varchar(64) COLLATE "C" PRIMARY KEY,
  campaign_json text COLLATE "C" NOT NULL,
  campaign_id varchar(160) COLLATE "C" NOT NULL UNIQUE,
  window_key varchar(64) COLLATE "C" NOT NULL UNIQUE,
  development_start_at timestamptz NOT NULL,
  development_end_at timestamptz NOT NULL,
  validation_start_at timestamptz NOT NULL,
  validation_end_at timestamptz NOT NULL,
  recorded_at timestamptz NOT NULL,
  publisher_role_oid oid NOT NULL,
  publication_xid text COLLATE "C" NOT NULL,
  CONSTRAINT ck_devval_campaign_sha CHECK (campaign_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_devval_window_key CHECK (window_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_devval_campaign_id CHECK (
    pg_catalog.length(campaign_id) BETWEEN 3 AND 160
    AND campaign_id ~ '^[a-z0-9]+([._:-][a-z0-9]+)*$'
  ),
  CONSTRAINT ck_devval_campaign_bytes CHECK (
    pg_catalog.octet_length(campaign_json) BETWEEN 1 AND 131072
  ),
  CONSTRAINT ck_devval_campaign_order CHECK (
    recorded_at < development_start_at
    AND development_start_at < development_end_at
    AND development_end_at < validation_start_at
    AND validation_start_at < validation_end_at
  ),
  CONSTRAINT ck_devval_publication_xid CHECK (
    publication_xid ~ '^[1-9][0-9]*$'
  )
);

CREATE TABLE ctcc_devval_custody.acknowledgements (
  campaign_sha256 varchar(64) COLLATE "C" PRIMARY KEY
    REFERENCES ctcc_devval_custody.campaigns(campaign_sha256),
  acknowledged_at timestamptz NOT NULL,
  observer_role_oid oid NOT NULL,
  acknowledgement_xid text COLLATE "C" NOT NULL,
  CONSTRAINT ck_devval_ack_xid CHECK (
    acknowledgement_xid ~ '^[1-9][0-9]*$'
  )
);

REVOKE ALL ON TABLE ctcc_devval_custody.campaigns FROM PUBLIC;
REVOKE ALL ON TABLE ctcc_devval_custody.acknowledgements FROM PUBLIC;

CREATE FUNCTION ctcc_devval_custody._immutable()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_immutable$
BEGIN
  RAISE EXCEPTION 'ctcc_devval_custody_immutable';
END
$ctcc_immutable$;

CREATE TRIGGER ctcc_devval_campaign_immutable
  BEFORE UPDATE OR DELETE ON ctcc_devval_custody.campaigns
  FOR EACH ROW EXECUTE FUNCTION ctcc_devval_custody._immutable();
CREATE TRIGGER ctcc_devval_campaign_no_truncate
  BEFORE TRUNCATE ON ctcc_devval_custody.campaigns
  FOR EACH STATEMENT EXECUTE FUNCTION ctcc_devval_custody._immutable();
CREATE TRIGGER ctcc_devval_ack_immutable
  BEFORE UPDATE OR DELETE ON ctcc_devval_custody.acknowledgements
  FOR EACH ROW EXECUTE FUNCTION ctcc_devval_custody._immutable();
CREATE TRIGGER ctcc_devval_ack_no_truncate
  BEFORE TRUNCATE ON ctcc_devval_custody.acknowledgements
  FOR EACH STATEMENT EXECUTE FUNCTION ctcc_devval_custody._immutable();

CREATE FUNCTION ctcc_devval_custody._require_role(p_mode text)
RETURNS oid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_role$
DECLARE
  actor oid;
  target oid;
  publisher oid := 'ctcc_devval_custody.publish_campaign(text,text)'::pg_catalog.regprocedure;
  acknowledger oid := 'ctcc_devval_custody.acknowledge_campaign(text)'::pg_catalog.regprocedure;
  reader oid := 'ctcc_devval_custody.read_campaign(text)'::pg_catalog.regprocedure;
  has_direct boolean;
BEGIN
  IF p_mode NOT IN ('publish','acknowledge','read') THEN
    RAISE EXCEPTION 'ctcc_devval_role_denied';
  END IF;
  actor := pg_catalog.to_regrole(session_user)::oid;
  target := CASE p_mode
    WHEN 'publish' THEN publisher
    WHEN 'acknowledge' THEN acknowledger
    ELSE reader END;
  IF actor IS NULL OR target IS NULL OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles r
    WHERE r.oid=actor AND r.rolcanlogin AND NOT r.rolsuper
      AND NOT r.rolcreatedb AND NOT r.rolcreaterole
      AND NOT r.rolreplication AND NOT r.rolbypassrls
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles r
    WHERE r.oid<>actor AND pg_catalog.pg_has_role(actor,r.oid,'MEMBER')
  ) OR pg_catalog.has_database_privilege(
    actor,pg_catalog.current_database(),'CREATE'
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace n
    WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
      AND pg_catalog.has_schema_privilege(actor,n.oid,'CREATE')
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    WHERE c.oid IN (
      'ctcc_devval_custody.campaigns'::pg_catalog.regclass,
      'ctcc_devval_custody.acknowledgements'::pg_catalog.regclass
    ) AND (
      pg_catalog.has_table_privilege(actor,c.oid,
        'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
      OR pg_catalog.has_any_column_privilege(actor,c.oid,
        'SELECT,INSERT,UPDATE,REFERENCES')
    )
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p,
      LATERAL pg_catalog.aclexplode(
        COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
      ) a
    WHERE p.oid IN (publisher,acknowledger,reader)
      AND a.grantee=0 AND a.privilege_type='EXECUTE'
  ) THEN
    RAISE EXCEPTION 'ctcc_devval_role_denied';
  END IF;
  SELECT COALESCE(pg_catalog.bool_or(
    a.grantee=actor AND a.privilege_type='EXECUTE' AND NOT a.is_grantable
  ),false) INTO has_direct
  FROM pg_catalog.pg_proc p,
    LATERAL pg_catalog.aclexplode(
      COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
    ) a
  WHERE p.oid=target;
  IF has_direct IS DISTINCT FROM true
    OR (p_mode='publish' AND (
      pg_catalog.has_function_privilege(actor,acknowledger,'EXECUTE')
      OR pg_catalog.has_function_privilege(actor,reader,'EXECUTE')
    ))
    OR (p_mode IN ('acknowledge','read') AND
      pg_catalog.has_function_privilege(actor,publisher,'EXECUTE'))
    OR (p_mode='acknowledge' AND
      pg_catalog.has_function_privilege(actor,reader,'EXECUTE') IS NOT TRUE)
  THEN
    RAISE EXCEPTION 'ctcc_devval_role_denied';
  END IF;
  RETURN actor;
END
$ctcc_role$;

CREATE FUNCTION ctcc_devval_custody._durable(p_write boolean)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_durable$
BEGIN
  IF pg_catalog.current_setting('server_encoding') <> 'UTF8'
    OR pg_catalog.current_setting('fsync') <> 'on'
    OR pg_catalog.current_setting('full_page_writes') <> 'on'
    OR (p_write AND (
      pg_catalog.current_setting('synchronous_commit') <> 'on'
      OR pg_catalog.current_setting('transaction_read_only') <> 'off'
    ))
  THEN
    RAISE EXCEPTION 'ctcc_devval_durability_denied';
  END IF;
END
$ctcc_durable$;

CREATE FUNCTION ctcc_devval_custody.publish_campaign(
  p_campaign_sha256 text, p_campaign_json text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_publish$
DECLARE
  actor oid;
  doc jsonb;
  plan jsonb;
  dev jsonb;
  val jsonb;
  campaign_id text;
  origin text;
  planned_at timestamptz;
  dev_start timestamptz;
  dev_end timestamptz;
  val_start timestamptz;
  val_end timestamptz;
  blocks integer;
  expected_rows bigint;
  listed_count bigint;
  distinct_count bigint;
  hashes_valid boolean;
  window_key text;
  recorded_at timestamptz;
BEGIN
  actor := ctcc_devval_custody._require_role('publish');
  PERFORM ctcc_devval_custody._durable(true);
  IF p_campaign_sha256 IS NULL
    OR p_campaign_sha256 !~ '^[0-9a-f]{64}$'
    OR p_campaign_json IS NULL
    OR pg_catalog.octet_length(p_campaign_json) NOT BETWEEN 1 AND 131072
    OR pg_catalog.encode(pg_catalog.sha256(
      pg_catalog.convert_to(p_campaign_json,'UTF8')),'hex'
    ) IS DISTINCT FROM p_campaign_sha256
  THEN
    RAISE EXCEPTION 'ctcc_devval_manifest_hash_denied';
  END IF;
  BEGIN
    doc := p_campaign_json::jsonb;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'ctcc_devval_manifest_json_denied';
  END;
  IF pg_catalog.jsonb_typeof(doc) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'ctcc_devval_manifest_shape_denied';
  END IF;
  IF (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(doc)) <> 7
    OR NOT doc ?& ARRAY[
      'schema_version','plan','plan_sha256','source_plan_sha256s',
      'pre_window_commit_proven','predictive_oos_eligible',
      'execution_authority'
    ]
    OR doc->>'schema_version' IS DISTINCT FROM
      'ctcc.mie.gate3.prospective_devval_campaign_manifest.v1'
    OR doc->'pre_window_commit_proven' IS DISTINCT FROM 'false'::jsonb
    OR doc->'predictive_oos_eligible' IS DISTINCT FROM 'false'::jsonb
    OR doc->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR (doc->>'plan_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
    OR pg_catalog.jsonb_typeof(doc->'source_plan_sha256s') IS DISTINCT FROM 'array'
  THEN
    RAISE EXCEPTION 'ctcc_devval_manifest_shape_denied';
  END IF;
  plan := doc->'plan';
  IF pg_catalog.jsonb_typeof(plan) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'ctcc_devval_plan_shape_denied';
  END IF;
  dev := plan->'development';
  val := plan->'validation';
  IF pg_catalog.jsonb_typeof(dev) IS DISTINCT FROM 'object'
    OR pg_catalog.jsonb_typeof(val) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'ctcc_devval_plan_shape_denied';
  END IF;
  IF (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(plan)) <> 18
    OR NOT plan ?& ARRAY[
      'schema_version','campaign_id','planned_at','declared_source_commit',
      'declared_source_tree','manifest_sha256','origin','development',
      'validation','instrument_ids','hours_per_block','expected_rows',
      'planning_time_basis','maximum_observation_lag_seconds',
      'current_claim','pre_window_commit_proven',
      'predictive_oos_eligible','execution_authority'
    ]
    OR plan->>'schema_version' IS DISTINCT FROM
      'ctcc.mie.gate3.prospective_devval_campaign_plan.v1'
    OR plan->>'planning_time_basis' IS DISTINCT FROM 'caller_declared_unverified'
    OR plan->>'current_claim' IS DISTINCT FROM 'computational'
    OR plan->'pre_window_commit_proven' IS DISTINCT FROM 'false'::jsonb
    OR plan->'predictive_oos_eligible' IS DISTINCT FROM 'false'::jsonb
    OR plan->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR plan->'hours_per_block' IS DISTINCT FROM '1'::jsonb
    OR plan->'maximum_observation_lag_seconds' IS DISTINCT FROM '60'::jsonb
    OR plan->'instrument_ids' IS DISTINCT FROM
      '["BTC-USDT-SWAP","ETH-USDT-SWAP"]'::jsonb
    OR (plan->>'declared_source_commit' ~ '^[0-9a-f]{40}$') IS NOT TRUE
    OR (plan->>'declared_source_tree' ~ '^[0-9a-f]{40}$') IS NOT TRUE
    OR (plan->>'manifest_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
    OR (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(dev)) <> 3
    OR (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(val)) <> 3
    OR dev->>'partition' IS DISTINCT FROM 'development'
    OR val->>'partition' IS DISTINCT FROM 'validation'
  THEN
    RAISE EXCEPTION 'ctcc_devval_plan_shape_denied';
  END IF;
  campaign_id := plan->>'campaign_id';
  origin := plan->>'origin';
  IF campaign_id IS NULL
    OR pg_catalog.length(campaign_id) NOT BETWEEN 3 AND 160
    OR campaign_id !~ '^[a-z0-9]+([._:-][a-z0-9]+)*$'
    OR origin IS NULL OR origin NOT IN (
      'https://openapi.okx.com','https://us.okx.com','https://eea.okx.com'
    )
    OR (plan->>'expected_rows' ~ '^[0-9]+$') IS NOT TRUE
    OR (plan->>'planned_at' ~
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
      ) IS NOT TRUE
    OR (dev->>'start_at' ~
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
      ) IS NOT TRUE
    OR (dev->>'end_at' ~
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
      ) IS NOT TRUE
    OR (val->>'start_at' ~
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
      ) IS NOT TRUE
    OR (val->>'end_at' ~
      '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
      ) IS NOT TRUE
  THEN
    RAISE EXCEPTION 'ctcc_devval_plan_fields_denied';
  END IF;
  BEGIN
    planned_at := (plan->>'planned_at')::timestamptz;
    dev_start := (dev->>'start_at')::timestamptz;
    dev_end := (dev->>'end_at')::timestamptz;
    val_start := (val->>'start_at')::timestamptz;
    val_end := (val->>'end_at')::timestamptz;
    expected_rows := (plan->>'expected_rows')::bigint;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'ctcc_devval_plan_time_denied';
  END;
  IF NOT (planned_at < dev_start AND dev_start < dev_end
    AND dev_end + interval '60 seconds' <= val_start
    AND val_start < val_end
    AND dev_end-dev_start >= interval '84 hours'
    AND val_end-val_start >= interval '84 hours'
    AND pg_catalog.mod((EXTRACT(EPOCH FROM dev_start)*1000000)::bigint,
      14400000000)=0
    AND pg_catalog.mod((EXTRACT(EPOCH FROM dev_end)*1000000)::bigint,
      14400000000)=0
    AND pg_catalog.mod((EXTRACT(EPOCH FROM val_start)*1000000)::bigint,
      14400000000)=0
    AND pg_catalog.mod((EXTRACT(EPOCH FROM val_end)*1000000)::bigint,
      14400000000)=0)
  THEN
    RAISE EXCEPTION 'ctcc_devval_plan_window_denied';
  END IF;
  blocks := ((EXTRACT(EPOCH FROM dev_end-dev_start)
    + EXTRACT(EPOCH FROM val_end-val_start))/3600)::integer;
  IF blocks NOT BETWEEN 168 AND 1008
    OR expected_rows <> blocks::bigint*120
    OR pg_catalog.jsonb_array_length(doc->'source_plan_sha256s') <> blocks
  THEN
    RAISE EXCEPTION 'ctcc_devval_plan_coverage_denied';
  END IF;
  SELECT pg_catalog.count(*),
         pg_catalog.count(DISTINCT item #>> '{}'),
         pg_catalog.bool_and(pg_catalog.jsonb_typeof(item)='string'
           AND (item #>> '{}') ~ '^[0-9a-f]{64}$')
  INTO listed_count,distinct_count,hashes_valid
  FROM pg_catalog.jsonb_array_elements(doc->'source_plan_sha256s') AS t(item);
  IF listed_count <> blocks OR distinct_count <> blocks
    OR hashes_valid IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ctcc_devval_child_inventory_denied';
  END IF;
  -- Lock the tiny publication lane across concurrent semantic-key claims.
  IF pg_catalog.pg_try_advisory_xact_lock(421962, 142001)
    IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ctcc_devval_publication_busy';
  END IF;
  recorded_at := pg_catalog.clock_timestamp();
  IF recorded_at < planned_at OR recorded_at >= dev_start THEN
    RAISE EXCEPTION 'ctcc_devval_publication_late';
  END IF;
  window_key := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
    pg_catalog.concat_ws(pg_catalog.chr(31),
      'ctcc.devval.window.v1','okx.public_market_source',
      'BTC-USDT-SWAP','ETH-USDT-SWAP',
      dev->>'start_at',dev->>'end_at',val->>'start_at',val->>'end_at'
    ),'UTF8')),'hex');
  INSERT INTO ctcc_devval_custody.campaigns (
    campaign_sha256,campaign_json,campaign_id,window_key,
    development_start_at,development_end_at,
    validation_start_at,validation_end_at,recorded_at,
    publisher_role_oid,publication_xid
  ) VALUES (
    p_campaign_sha256,p_campaign_json,campaign_id,window_key,
    dev_start,dev_end,val_start,val_end,recorded_at,
    actor,pg_catalog.pg_current_xact_id()::text
  );
END
$ctcc_publish$;

CREATE FUNCTION ctcc_devval_custody.acknowledge_campaign(p_campaign_sha256 text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_ack$
DECLARE
  actor oid;
  pinned ctcc_devval_custody.campaigns%ROWTYPE;
  acknowledged_at timestamptz;
  xid text;
BEGIN
  actor := ctcc_devval_custody._require_role('acknowledge');
  PERFORM ctcc_devval_custody._durable(true);
  IF p_campaign_sha256 IS NULL OR p_campaign_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'ctcc_devval_ack_pin_denied';
  END IF;
  IF pg_catalog.pg_try_advisory_xact_lock(421962, 142001)
    IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ctcc_devval_ack_busy';
  END IF;
  SELECT * INTO pinned FROM ctcc_devval_custody.campaigns
    WHERE campaign_sha256=p_campaign_sha256 FOR KEY SHARE NOWAIT;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ctcc_devval_campaign_missing';
  END IF;
  xid := pg_catalog.pg_current_xact_id()::text;
  acknowledged_at := pg_catalog.clock_timestamp();
  IF actor=pinned.publisher_role_oid
    OR xid=pinned.publication_xid
    OR acknowledged_at < pinned.recorded_at
    OR acknowledged_at >= pinned.development_start_at
  THEN
    RAISE EXCEPTION 'ctcc_devval_ack_ownership_or_time_denied';
  END IF;
  INSERT INTO ctcc_devval_custody.acknowledgements (
    campaign_sha256,acknowledged_at,observer_role_oid,acknowledgement_xid
  ) VALUES (p_campaign_sha256,acknowledged_at,actor,xid);
END
$ctcc_ack$;

CREATE FUNCTION ctcc_devval_custody.read_campaign(p_campaign_sha256 text)
RETURNS TABLE (
  campaign_sha256 text,
  campaign_json text,
  campaign_id text,
  recorded_at timestamptz,
  publisher_role_oid oid,
  publication_xid text,
  acknowledged_at timestamptz,
  observer_role_oid oid,
  acknowledgement_xid text,
  database_read_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_read$
BEGIN
  PERFORM ctcc_devval_custody._require_role('read');
  PERFORM ctcc_devval_custody._durable(false);
  IF p_campaign_sha256 IS NULL OR p_campaign_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'ctcc_devval_read_pin_denied';
  END IF;
  RETURN QUERY
    SELECT c.campaign_sha256::text,c.campaign_json::text,c.campaign_id::text,
      c.recorded_at,c.publisher_role_oid,c.publication_xid::text,
      a.acknowledged_at,a.observer_role_oid,a.acknowledgement_xid::text,
      pg_catalog.clock_timestamp()
    FROM ctcc_devval_custody.campaigns c
    LEFT JOIN ctcc_devval_custody.acknowledgements a
      ON a.campaign_sha256=c.campaign_sha256
    WHERE c.campaign_sha256=p_campaign_sha256;
END
$ctcc_read$;

REVOKE ALL ON FUNCTION ctcc_devval_custody._immutable() FROM PUBLIC;
REVOKE ALL ON FUNCTION ctcc_devval_custody._require_role(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ctcc_devval_custody._durable(boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION ctcc_devval_custody.publish_campaign(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ctcc_devval_custody.acknowledge_campaign(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ctcc_devval_custody.read_campaign(text) FROM PUBLIC;

-- Refuse unexpected inherited default grants. A role granted direct table
-- INSERT by the installer account's default ACL could bypass the three APIs.
DO $ctcc_install_acl$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace n,
      LATERAL pg_catalog.aclexplode(
        COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))
      ) a
    WHERE n.nspname='ctcc_devval_custody' AND a.grantee<>n.nspowner
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c,
      LATERAL pg_catalog.aclexplode(
        COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))
      ) a
    WHERE c.oid IN (
      'ctcc_devval_custody.campaigns'::pg_catalog.regclass,
      'ctcc_devval_custody.acknowledgements'::pg_catalog.regclass
    ) AND a.grantee<>c.relowner
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p,
      LATERAL pg_catalog.aclexplode(
        COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
      ) a
    WHERE p.pronamespace='ctcc_devval_custody'::pg_catalog.regnamespace
      AND a.grantee<>p.proowner
  ) THEN
    RAISE EXCEPTION 'ctcc_devval_install_default_acl_denied';
  END IF;
END
$ctcc_install_acl$;

COMMIT;
