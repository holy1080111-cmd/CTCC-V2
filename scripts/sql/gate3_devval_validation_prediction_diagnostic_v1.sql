-- PostgreSQL 17 label-free Validation custody diagnostic, isolated evidence database only.
-- Install after the existing DevVal campaign custody and dispatch SQL and the exact DB0046
-- raw-custody leaf. Do not install in an application database or grant table access.
-- Provision two separate restricted TLS LOGINs with schema USAGE and only these
-- direct function grants: publisher (anchor_schedule, publish_prediction),
-- observer (acknowledge_prediction, read_prediction). This script creates no roles.
-- This records local custody. It establishes no independent source timing, first
-- outcome access, acceptance, or execution authority.

BEGIN;

DO $preflight$
BEGIN
  IF pg_catalog.current_setting('server_version_num')::integer NOT BETWEEN 170000 AND 179999
    OR pg_catalog.current_setting('server_encoding') <> 'UTF8'
    OR pg_catalog.to_regclass('public.alembic_version') IS NOT NULL
    OR pg_catalog.to_regclass('public.public_runtime_raw_bundles_v1') IS NULL
    OR pg_catalog.to_regclass('ctcc_devval_custody.campaigns') IS NULL
    OR pg_catalog.to_regclass('ctcc_devval_custody.acknowledgements') IS NULL
    OR pg_catalog.to_regprocedure('ctcc_devval_custody.claim_dispatch(text,text)') IS NULL
  THEN
    RAISE EXCEPTION 'ctcc_prediction_isolated_evidence_database_required';
  END IF;
END
$preflight$;

CREATE SCHEMA ctcc_devval_prediction_diagnostic;
REVOKE ALL ON SCHEMA ctcc_devval_prediction_diagnostic FROM PUBLIC;

CREATE TABLE ctcc_devval_prediction_diagnostic.schedule_anchors (
  campaign_sha256 text COLLATE "C" PRIMARY KEY,
  schedule_sha256 text COLLATE "C" NOT NULL UNIQUE,
  schedule_json text COLLATE "C" NOT NULL,
  anchored_at timestamptz NOT NULL,
  publisher_oid oid NOT NULL,
  anchor_xid xid8 NOT NULL
);

CREATE TABLE ctcc_devval_prediction_diagnostic.publications (
  campaign_sha256 text COLLATE "C" NOT NULL
    REFERENCES ctcc_devval_prediction_diagnostic.schedule_anchors(campaign_sha256),
  slot_ordinal integer NOT NULL CHECK (slot_ordinal BETWEEN 0 AND 4095),
  prediction_sha256 text COLLATE "C" NOT NULL,
  db0046_bundle_id text COLLATE "C" NOT NULL,
  db0046_proof_sha256 text COLLATE "C" NOT NULL,
  db0046_recorded_at timestamptz NOT NULL,
  published_at timestamptz NOT NULL CHECK (published_at > db0046_recorded_at),
  publisher_oid oid NOT NULL,
  publication_xid xid8 NOT NULL,
  PRIMARY KEY (campaign_sha256,slot_ordinal)
);

CREATE TABLE ctcc_devval_prediction_diagnostic.acknowledgements (
  campaign_sha256 text COLLATE "C" NOT NULL,
  slot_ordinal integer NOT NULL,
  acknowledged_at timestamptz NOT NULL,
  observer_oid oid NOT NULL,
  acknowledgement_xid xid8 NOT NULL,
  PRIMARY KEY (campaign_sha256,slot_ordinal),
  FOREIGN KEY (campaign_sha256,slot_ordinal)
    REFERENCES ctcc_devval_prediction_diagnostic.publications(campaign_sha256,slot_ordinal)
);

CREATE FUNCTION ctcc_devval_prediction_diagnostic._immutable()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $immutable$
BEGIN
  RAISE EXCEPTION 'ctcc_prediction_immutable';
END
$immutable$;

DO $triggers$
DECLARE
  relation regclass;
BEGIN
  FOREACH relation IN ARRAY ARRAY[
    'ctcc_devval_prediction_diagnostic.schedule_anchors'::regclass,
    'ctcc_devval_prediction_diagnostic.publications'::regclass,
    'ctcc_devval_prediction_diagnostic.acknowledgements'::regclass
  ] LOOP
    EXECUTE pg_catalog.format('REVOKE ALL ON %s FROM PUBLIC', relation);
    EXECUTE pg_catalog.format(
      'CREATE TRIGGER ctcc_prediction_no_mutation BEFORE UPDATE OR DELETE ON %s '
      'FOR EACH ROW EXECUTE FUNCTION ctcc_devval_prediction_diagnostic._immutable()', relation
    );
    EXECUTE pg_catalog.format(
      'CREATE TRIGGER ctcc_prediction_no_truncate BEFORE TRUNCATE ON %s '
      'FOR EACH STATEMENT EXECUTE FUNCTION ctcc_devval_prediction_diagnostic._immutable()', relation
    );
    EXECUTE pg_catalog.format('ALTER TABLE %s ENABLE ALWAYS TRIGGER ctcc_prediction_no_mutation', relation);
    EXECUTE pg_catalog.format('ALTER TABLE %s ENABLE ALWAYS TRIGGER ctcc_prediction_no_truncate', relation);
  END LOOP;
END
$triggers$;

CREATE FUNCTION ctcc_devval_prediction_diagnostic._actor(p_observer boolean, p_write boolean)
RETURNS oid LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $actor$
DECLARE
  actor oid := pg_catalog.to_regrole(session_user)::oid;
  function_name regprocedure;
  functions constant regprocedure[] := ARRAY[
    'ctcc_devval_prediction_diagnostic.anchor_schedule(text,text,text,text)'::regprocedure,
    'ctcc_devval_prediction_diagnostic.publish_prediction(text,text,integer,text,text)'::regprocedure,
    'ctcc_devval_prediction_diagnostic.acknowledge_prediction(text,text,integer,text)'::regprocedure,
    'ctcc_devval_prediction_diagnostic.read_prediction(text,text,integer,text)'::regprocedure
  ];
  has_direct boolean;
  allowed boolean;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles r
    WHERE r.oid=actor AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
      AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles r
    WHERE r.oid<>actor AND pg_catalog.pg_has_role(actor,r.oid,'MEMBER')
  ) OR pg_catalog.has_database_privilege(actor,pg_catalog.current_database(),'CREATE')
  OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace n
    WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
      AND pg_catalog.has_schema_privilege(actor,n.oid,'CREATE')
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE (n.nspname IN ('ctcc_devval_prediction_diagnostic','ctcc_devval_custody')
        AND c.relkind='r'
      OR c.oid='public.public_runtime_raw_bundles_v1'::regclass)
    AND (pg_catalog.has_table_privilege(actor,c.oid,
        'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
      OR pg_catalog.has_any_column_privilege(actor,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))
  ) OR pg_catalog.has_function_privilege(actor,
      'public.public_runtime_bundle_v1_append(text,text,text,bytea)','EXECUTE')
    OR pg_catalog.has_function_privilege(actor,'public.public_runtime_bundle_v1_read(text)','EXECUTE')
    OR pg_catalog.has_function_privilege(actor,'ctcc_devval_custody.publish_campaign(text,text)','EXECUTE')
    OR pg_catalog.has_function_privilege(actor,'ctcc_devval_custody.acknowledge_campaign(text)','EXECUTE')
    OR pg_catalog.has_function_privilege(actor,'ctcc_devval_custody.claim_dispatch(text,text)','EXECUTE')
    OR pg_catalog.current_setting('role') <> 'none'
    OR pg_catalog.current_setting('session_replication_role') <> 'origin'
    OR NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_stat_activity
      WHERE pid=pg_catalog.pg_backend_pid() AND usesysid=actor
    )
    OR pg_catalog.pg_is_in_recovery()
    OR pg_catalog.inet_client_addr() IS NULL
    OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_stat_ssl
      WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
  THEN
    RAISE EXCEPTION 'ctcc_prediction_role_denied';
  END IF;
  FOREACH function_name IN ARRAY functions LOOP
    allowed := (function_name=functions[3] OR function_name=functions[4]) = p_observer;
    SELECT COALESCE(pg_catalog.bool_or(a.grantee=actor AND NOT a.is_grantable),false)
      INTO has_direct
    FROM pg_catalog.pg_proc p, LATERAL pg_catalog.aclexplode(
      COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
    WHERE p.oid=function_name::oid AND a.privilege_type='EXECUTE';
    IF allowed IS DISTINCT FROM has_direct
      OR allowed IS DISTINCT FROM pg_catalog.has_function_privilege(actor,function_name::oid,'EXECUTE')
      OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p, LATERAL pg_catalog.aclexplode(
          COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
        WHERE p.oid=function_name::oid AND a.grantee=0 AND a.privilege_type='EXECUTE'
      )
    THEN
      RAISE EXCEPTION 'ctcc_prediction_role_denied';
    END IF;
  END LOOP;
  IF pg_catalog.current_setting('fsync') <> 'on'
    OR pg_catalog.current_setting('full_page_writes') <> 'on'
    OR (p_write AND (pg_catalog.current_setting('synchronous_commit') <> 'on'
      OR pg_catalog.current_setting('transaction_read_only') <> 'off'))
  THEN
    RAISE EXCEPTION 'ctcc_prediction_durability_denied';
  END IF;
  RETURN actor;
END
$actor$;

-- The bounded contracts use canonical sorted UTF-8 JSON. Verify the original
-- byte spelling (including nested keys), rather than hashing a JSONB rewrite.
CREATE FUNCTION ctcc_devval_prediction_diagnostic._canonical(p_doc jsonb, p_depth integer)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp
AS $canonical$
BEGIN
  IF p_depth > 16 THEN
    RAISE EXCEPTION 'ctcc_prediction_json_denied';
  END IF;
  CASE pg_catalog.jsonb_typeof(p_doc)
    WHEN 'object' THEN RETURN (
      SELECT '{' || COALESCE(pg_catalog.string_agg(
        pg_catalog.to_jsonb(k)::text || ':' ||
          ctcc_devval_prediction_diagnostic._canonical(v,p_depth+1),
        ',' ORDER BY k COLLATE "C"),'') || '}'
      FROM pg_catalog.jsonb_each(p_doc) AS item(k,v)
    );
    WHEN 'array' THEN RETURN (
      SELECT '[' || COALESCE(pg_catalog.string_agg(
        ctcc_devval_prediction_diagnostic._canonical(v,p_depth+1),',' ORDER BY position),'') || ']'
      FROM pg_catalog.jsonb_array_elements(p_doc) WITH ORDINALITY AS item(v,position)
    );
    ELSE RETURN p_doc::text;
  END CASE;
END
$canonical$;

CREATE FUNCTION ctcc_devval_prediction_diagnostic._sha(p_bytes bytea)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp
RETURN pg_catalog.encode(pg_catalog.sha256(p_bytes),'hex');

CREATE FUNCTION ctcc_devval_prediction_diagnostic._document(p_json text, p_sha text, p_max integer)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
AS $document$
DECLARE
  doc jsonb;
BEGIN
  IF (p_sha ~ '^[0-9a-f]{64}$') IS NOT TRUE OR p_json IS NULL
    OR pg_catalog.octet_length(p_json) NOT BETWEEN 1 AND p_max
    OR ctcc_devval_prediction_diagnostic._sha(pg_catalog.convert_to(p_json,'UTF8')) <> p_sha
    OR NOT (p_json IS JSON OBJECT WITH UNIQUE KEYS)
  THEN
    RAISE EXCEPTION 'ctcc_prediction_json_denied';
  END IF;
  doc := p_json::jsonb;
  IF ctcc_devval_prediction_diagnostic._canonical(doc,0) COLLATE "C"
    IS DISTINCT FROM p_json COLLATE "C" THEN
    RAISE EXCEPTION 'ctcc_prediction_json_denied';
  END IF;
  RETURN doc;
END
$document$;

CREATE FUNCTION ctcc_devval_prediction_diagnostic._instant(p_at text)
RETURNS timestamptz LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp
AS $instant$
DECLARE
  at_utc timestamptz;
BEGIN
  IF (p_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$') IS NOT TRUE THEN
    RAISE EXCEPTION 'ctcc_prediction_time_denied';
  END IF;
  at_utc := p_at::timestamptz;
  IF pg_catalog.to_char(at_utc AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') <> p_at THEN
    RAISE EXCEPTION 'ctcc_prediction_time_denied';
  END IF;
  RETURN at_utc;
END
$instant$;

CREATE FUNCTION ctcc_devval_prediction_diagnostic.anchor_schedule(
  p_campaign_sha text, p_schedule_sha text, p_schedule_json text, p_blueprint_json text
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $anchor$
DECLARE
  actor oid := ctcc_devval_prediction_diagnostic._actor(false,true);
  schedule jsonb;
  blueprint jsonb;
  campaign ctcc_devval_custody.campaigns%ROWTYPE;
  ack ctcc_devval_custody.acknowledgements%ROWTYPE;
  manifest jsonb;
  plan jsonb;
  feature jsonb;
  expected_slots jsonb;
  width integer;
  history integer;
  outcome integer;
  delay integer;
  anchored_at timestamptz;
BEGIN
  SELECT * INTO STRICT campaign FROM ctcc_devval_custody.campaigns
    WHERE campaign_sha256=p_campaign_sha;
  SELECT * INTO STRICT ack FROM ctcc_devval_custody.acknowledgements
    WHERE campaign_sha256=p_campaign_sha;
  manifest := ctcc_devval_prediction_diagnostic._document(campaign.campaign_json,p_campaign_sha,131072);
  plan := manifest->'plan';
  schedule := ctcc_devval_prediction_diagnostic._document(p_schedule_json,p_schedule_sha,1048576);
  blueprint := ctcc_devval_prediction_diagnostic._document(
    p_blueprint_json,schedule->>'research_blueprint_sha256',131072);
  feature := blueprint#>'{study,feature_plan}';
  IF schedule->>'schema_version' IS DISTINCT FROM 'ctcc.mie.devval_global_companion_schedule.v1'
    OR schedule->>'environment' IS DISTINCT FROM 'anonymous_global_public'
    OR schedule->>'rest_origin' IS DISTINCT FROM 'https://openapi.okx.com'
    OR schedule->>'ws_origin' IS DISTINCT FROM 'wss://ws.okx.com:443/ws/v5/public'
    OR schedule->'instrument_ids' IS DISTINCT FROM '["BTC-USDT-SWAP","ETH-USDT-SWAP"]'::jsonb
    OR schedule->>'timing_basis' IS DISTINCT FROM 'caller_declared_unverified'
    OR schedule->'pre_window_commit_proven' IS DISTINCT FROM 'false'::jsonb
    OR schedule->'independent_availability_verified' IS DISTINCT FROM 'false'::jsonb
    OR schedule->'predictive_oos_eligible' IS DISTINCT FROM 'false'::jsonb
    OR schedule->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(schedule)) <> 21
    OR schedule->>'campaign_id' IS DISTINCT FROM campaign.campaign_id
    OR campaign.campaign_id IS DISTINCT FROM 'devval:research:' || (schedule->>'research_blueprint_sha256')
    OR plan->>'origin' IS DISTINCT FROM 'https://openapi.okx.com'
    OR schedule->>'campaign_plan_sha256' IS DISTINCT FROM manifest->>'plan_sha256'
    OR manifest->>'plan_sha256' IS DISTINCT FROM ctcc_devval_prediction_diagnostic._sha(
      pg_catalog.convert_to(ctcc_devval_prediction_diagnostic._canonical(plan,0),'UTF8'))
    OR blueprint->>'schema_version' IS DISTINCT FROM 'ctcc.mie.devval_research_blueprint.v1'
    OR blueprint->'capture' IS DISTINCT FROM plan - ARRAY['schema_version','campaign_id']
    OR schedule->>'planned_at' IS DISTINCT FROM plan->>'planned_at'
    OR schedule->'source_checkpoint' IS DISTINCT FROM pg_catalog.jsonb_build_object(
      'commit',plan->'declared_source_commit','tree',plan->'declared_source_tree',
      'manifest_sha256',plan->'manifest_sha256')
    OR schedule->>'legacy_scorer_config_sha256' IS DISTINCT FROM blueprint->>'legacy_score_source_sha256'
    OR (schedule->>'legacy_scorer_config_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
    OR (schedule->>'source_archive_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
    OR (schedule->>'market_policy_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
    OR pg_catalog.jsonb_typeof(schedule->'slots') IS DISTINCT FROM 'array'
    OR (schedule->>'horizon_seconds' IN ('900','3600','14400')) IS NOT TRUE
    OR schedule->'horizon_seconds' IS DISTINCT FROM feature#>'{bar_horizon,seconds}'
    OR (schedule->>'decision_delay_seconds' ~ '^[0-9]{1,3}$') IS NOT TRUE
    OR pg_catalog.jsonb_typeof(schedule->'decision_delay_seconds') IS DISTINCT FROM 'number'
    OR (feature->>'history_bars' ~ '^[0-9]{1,5}$') IS NOT TRUE
    OR (feature->>'outcome_horizon_seconds' ~ '^[0-9]{1,8}$') IS NOT TRUE
    OR (blueprint#>>'{study,feature_dependency_seconds}' ~ '^[0-9]{1,9}$') IS NOT TRUE
  THEN
    RAISE EXCEPTION 'ctcc_prediction_schedule_denied';
  END IF;
  width := (schedule->>'horizon_seconds')::integer;
  history := (feature->>'history_bars')::integer;
  outcome := (feature->>'outcome_horizon_seconds')::integer;
  delay := (schedule->>'decision_delay_seconds')::integer;
  IF delay NOT BETWEEN 1 AND 299 OR history NOT BETWEEN 21 AND 10000
    OR outcome < width OR pg_catalog.mod(outcome,width) <> 0
    OR history*width+delay > (blueprint#>>'{study,feature_dependency_seconds}')::integer
    OR pg_catalog.jsonb_array_length(schedule->'slots') NOT BETWEEN 2 AND 4096
    OR (EXTRACT(EPOCH FROM campaign.development_end_at-campaign.development_start_at) / width)
      < history + outcome / width
    OR (EXTRACT(EPOCH FROM campaign.validation_end_at-campaign.validation_start_at) / width)
      < history + outcome / width
  THEN
    RAISE EXCEPTION 'ctcc_prediction_schedule_denied';
  END IF;
  SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
    'partition',windows.partition,
    'event_closed_at',pg_catalog.to_char(close_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'decision_cutoff_at',pg_catalog.to_char((close_at + pg_catalog.make_interval(secs=>delay))
      AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
  ) ORDER BY close_at) INTO expected_slots
  FROM (VALUES
    ('development',campaign.development_start_at,campaign.development_end_at),
    ('validation',campaign.validation_start_at,campaign.validation_end_at)
  ) AS windows(partition,opens,closes)
  CROSS JOIN LATERAL pg_catalog.generate_series(
    windows.opens + pg_catalog.make_interval(secs=>history*width),
    windows.closes - pg_catalog.make_interval(secs=>outcome),
    pg_catalog.make_interval(secs=>width)
  ) AS slots(close_at);
  anchored_at := pg_catalog.clock_timestamp();
  IF expected_slots IS DISTINCT FROM schedule->'slots'
    OR ctcc_devval_prediction_diagnostic._instant(plan#>>'{development,start_at}')
      IS DISTINCT FROM campaign.development_start_at
    OR ctcc_devval_prediction_diagnostic._instant(plan#>>'{development,end_at}')
      IS DISTINCT FROM campaign.development_end_at
    OR ctcc_devval_prediction_diagnostic._instant(plan#>>'{validation,start_at}')
      IS DISTINCT FROM campaign.validation_start_at
    OR ctcc_devval_prediction_diagnostic._instant(plan#>>'{validation,end_at}')
      IS DISTINCT FROM campaign.validation_end_at
    OR ack.observer_role_oid=campaign.publisher_role_oid
    OR ack.acknowledgement_xid=campaign.publication_xid
    OR ack.acknowledged_at < campaign.recorded_at
    OR anchored_at < ctcc_devval_prediction_diagnostic._instant(schedule->>'planned_at')
    OR ack.acknowledged_at >= anchored_at OR anchored_at >= campaign.development_start_at
    OR ack.acknowledgement_xid=pg_catalog.pg_current_xact_id()::text
    OR campaign.publication_xid=pg_catalog.pg_current_xact_id()::text
  THEN
    RAISE EXCEPTION 'ctcc_prediction_schedule_denied';
  END IF;
  INSERT INTO ctcc_devval_prediction_diagnostic.schedule_anchors VALUES (
    p_campaign_sha,p_schedule_sha,p_schedule_json,anchored_at,actor,pg_catalog.pg_current_xact_id());
END
$anchor$;

CREATE FUNCTION ctcc_devval_prediction_diagnostic.publish_prediction(
  p_campaign_sha text, p_schedule_sha text, p_slot integer,
  p_prediction_sha text, p_bundle_id text
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $publish$
DECLARE
  actor oid := ctcc_devval_prediction_diagnostic._actor(false,true);
  anchor ctcc_devval_prediction_diagnostic.schedule_anchors%ROWTYPE;
  raw public.public_runtime_raw_bundles_v1%ROWTYPE;
  prediction jsonb;
  identity jsonb;
  schedule jsonb;
  slot jsonb;
  side jsonb;
  probability jsonb;
  idx integer;
  key text;
  proof_sha text;
  expected_bundle_id text;
  event_at timestamptz;
  cutoff_at timestamptz;
  decision_at timestamptz;
  published_at timestamptz;
BEGIN
  IF p_slot IS NULL OR p_slot NOT BETWEEN 0 AND 4095
    OR (p_bundle_id ~ '^[0-9a-f]{32}$') IS NOT TRUE THEN
    RAISE EXCEPTION 'ctcc_prediction_slot_denied';
  END IF;
  SELECT * INTO STRICT anchor FROM ctcc_devval_prediction_diagnostic.schedule_anchors
    WHERE campaign_sha256=p_campaign_sha AND schedule_sha256=p_schedule_sha;
  schedule := anchor.schedule_json::jsonb;
  slot := schedule->'slots'->p_slot;
  IF slot->>'partition' IS DISTINCT FROM 'validation'
    OR anchor.publisher_oid<>actor OR anchor.anchor_xid=pg_catalog.pg_current_xact_id()
  THEN
    RAISE EXCEPTION 'ctcc_prediction_slot_denied';
  END IF;
  SELECT * INTO STRICT raw FROM public.public_runtime_raw_bundles_v1
    WHERE bundle_id=p_bundle_id AND bundle_sha256=p_prediction_sha AND contract_version=1
      AND pg_catalog.octet_length(bundle_raw) BETWEEN 1 AND 16384;
  prediction := ctcc_devval_prediction_diagnostic._document(
    pg_catalog.convert_from(raw.bundle_raw,'UTF8'),p_prediction_sha,16384);
  identity := prediction->'identity';
  IF prediction->>'schema_version' IS DISTINCT FROM 'ctcc.mie.devval_validation_prediction.v1'
    OR (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(prediction)) <> 18
    OR prediction->'independent_availability_verified' IS DISTINCT FROM 'false'::jsonb
    OR prediction->'evaluator_first_access_verified' IS DISTINCT FROM 'false'::jsonb
    OR prediction->'predictive_oos_eligible' IS DISTINCT FROM 'false'::jsonb
    OR prediction->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR (prediction->>'status' IN ('incomplete','complete_computational')) IS NOT TRUE
    OR pg_catalog.jsonb_typeof(identity) IS DISTINCT FROM 'object'
    OR (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(identity)) <> 8
    OR identity->>'schedule_sha256' IS DISTINCT FROM p_schedule_sha
    OR identity->'slot_ordinal' IS DISTINCT FROM pg_catalog.to_jsonb(p_slot)
    OR identity->>'partition' IS DISTINCT FROM 'validation'
    OR identity->>'event_closed_at' IS DISTINCT FROM slot->>'event_closed_at'
    OR identity->>'decision_cutoff_at' IS DISTINCT FROM slot->>'decision_cutoff_at'
    OR (identity->>'coverage_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
    OR (identity->>'batch_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
    OR NOT identity ? 'decision_at'
    OR (identity->'decision_at'='null'::jsonb) <> (prediction->>'status'='incomplete')
    OR prediction->>'scorer_config_sha256' IS DISTINCT FROM schedule->>'legacy_scorer_config_sha256'
    OR pg_catalog.jsonb_typeof(prediction->'sides') IS DISTINCT FROM 'array'
    OR pg_catalog.jsonb_array_length(prediction->'sides') <> 2
  THEN
    RAISE EXCEPTION 'ctcc_prediction_shape_denied';
  END IF;
  FOREACH key IN ARRAY ARRAY[
    'feature_projection_sha256','raw_event_sha256','policy_sha256','development_fit_sha256',
    'development_fit_input_sha256','final_development_rows_sha256','feature_plan_sha256',
    'scorer_config_sha256'
  ] LOOP
    IF (prediction->>key ~ '^[0-9a-f]{64}$') IS NOT TRUE
      OR pg_catalog.jsonb_typeof(prediction->key) IS DISTINCT FROM 'string' THEN
      RAISE EXCEPTION 'ctcc_prediction_shape_denied';
    END IF;
  END LOOP;
  FOR idx IN 0..1 LOOP
    side := prediction->'sides'->idx;
    probability := side->'probability';
    IF pg_catalog.jsonb_typeof(side) IS DISTINCT FROM 'object'
      OR (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(side)) <> 7
      OR NOT side ?& ARRAY['instrument_id','status','feature_replay_sha256','raw_result_sha256',
        'raw_direction','bin_index','probability']
      OR side->'instrument_id' IS DISTINCT FROM schedule->'instrument_ids'->idx
      OR (side->>'status' IN ('missing','neutral','unsupported','computed')) IS NOT TRUE
      OR (side->>'status'='missing') <> (prediction->>'status'='incomplete')
      OR (probability='null'::jsonb) = (side->>'status'='computed')
      OR (side->>'status'='missing' AND (
        side->'feature_replay_sha256'<>'null'::jsonb OR side->'raw_result_sha256'<>'null'::jsonb
        OR side->'raw_direction'<>'null'::jsonb OR side->'bin_index'<>'null'::jsonb))
      OR (side->>'status'<>'missing' AND (
        (side->>'feature_replay_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE
        OR (side->>'raw_result_sha256' ~ '^[0-9a-f]{64}$') IS NOT TRUE))
      OR (side->>'status'='neutral' AND (
        side->>'raw_direction' IS DISTINCT FROM 'neutral' OR side->'bin_index'<>'null'::jsonb))
      OR (side->>'status' IN ('computed','unsupported') AND (
        (side->>'raw_direction' IN ('long','short')) IS NOT TRUE
        OR pg_catalog.jsonb_typeof(side->'bin_index') IS DISTINCT FROM 'number'
        OR (side->>'bin_index' ~ '^[0-9]{1,3}$') IS NOT TRUE
        OR (side->>'bin_index')::integer > 100))
      OR (side->>'status'='computed' AND (
        pg_catalog.jsonb_typeof(probability) IS DISTINCT FROM 'object'
        OR (SELECT pg_catalog.count(*) FROM pg_catalog.jsonb_object_keys(probability)) <> 2
        OR pg_catalog.jsonb_typeof(probability->'numerator') IS DISTINCT FROM 'number'
        OR pg_catalog.jsonb_typeof(probability->'denominator') IS DISTINCT FROM 'number'
        OR (probability->>'numerator' ~ '^[1-9][0-9]{0,11}$') IS NOT TRUE
        OR (probability->>'denominator' ~ '^[1-9][0-9]{0,11}$') IS NOT TRUE
        OR (probability->>'denominator')::bigint > 2008192
        OR (probability->>'numerator')::bigint >= (probability->>'denominator')::bigint
        OR pg_catalog.gcd((probability->>'numerator')::bigint,
          (probability->>'denominator')::bigint) <> 1))
    THEN
      RAISE EXCEPTION 'ctcc_prediction_shape_denied';
    END IF;
  END LOOP;
  event_at := ctcc_devval_prediction_diagnostic._instant(slot->>'event_closed_at');
  cutoff_at := ctcc_devval_prediction_diagnostic._instant(slot->>'decision_cutoff_at');
  decision_at := CASE WHEN prediction->>'status'='incomplete' THEN cutoff_at
    ELSE ctcc_devval_prediction_diagnostic._instant(identity->>'decision_at') END;
  IF decision_at NOT BETWEEN event_at AND cutoff_at
    OR ctcc_devval_prediction_diagnostic._instant(prediction->>'latest_development_label_read_at')
      >= ctcc_devval_prediction_diagnostic._instant(prediction->>'validation_feature_history_start_at')
    OR ctcc_devval_prediction_diagnostic._instant(prediction->>'validation_feature_history_start_at') >= event_at
    OR raw.recorded_at < decision_at
    OR anchor.anchored_at >= raw.recorded_at
  THEN
    RAISE EXCEPTION 'ctcc_prediction_time_denied';
  END IF;
  proof_sha := ctcc_devval_prediction_diagnostic._sha(pg_catalog.convert_to(
    ctcc_devval_prediction_diagnostic._canonical(pg_catalog.jsonb_build_object(
      'schema','ctcc.mie.devval_prediction_db0046_precursor.v1',
      'pins',pg_catalog.jsonb_build_object(
        'prediction_sha256',p_prediction_sha,'schedule_sha256',p_schedule_sha,
        'slot_ordinal',p_slot,'coverage_sha256',identity->'coverage_sha256',
        'batch_sha256',identity->'batch_sha256',
        'feature_projection_sha256',prediction->'feature_projection_sha256',
        'raw_event_sha256',prediction->'raw_event_sha256','policy_sha256',prediction->'policy_sha256',
        'development_fit_sha256',prediction->'development_fit_sha256',
        'development_fit_input_sha256',prediction->'development_fit_input_sha256',
        'feature_plan_sha256',prediction->'feature_plan_sha256',
        'scorer_config_sha256',prediction->'scorer_config_sha256'
      )),0),'UTF8'));
  expected_bundle_id := pg_catalog.left(ctcc_devval_prediction_diagnostic._sha(
    pg_catalog.convert_to('ctcc.public_runtime_bundle_custody.bundle_id.v1','UTF8')
      || pg_catalog.decode('00','hex') || pg_catalog.decode(p_prediction_sha,'hex')
      || pg_catalog.decode(proof_sha,'hex')),32);
  IF raw.proof_envelope_sha256 IS DISTINCT FROM proof_sha
    OR p_bundle_id IS DISTINCT FROM expected_bundle_id THEN
    RAISE EXCEPTION 'ctcc_prediction_proof_denied';
  END IF;
  published_at := pg_catalog.clock_timestamp();
  INSERT INTO ctcc_devval_prediction_diagnostic.publications VALUES (
    p_campaign_sha,p_slot,p_prediction_sha,p_bundle_id,proof_sha,raw.recorded_at,
    published_at,actor,pg_catalog.pg_current_xact_id());
END
$publish$;

CREATE FUNCTION ctcc_devval_prediction_diagnostic.acknowledge_prediction(
  p_campaign_sha text, p_schedule_sha text, p_slot integer, p_prediction_sha text
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $ack$
DECLARE
  actor oid := ctcc_devval_prediction_diagnostic._actor(true,true);
  publication ctcc_devval_prediction_diagnostic.publications%ROWTYPE;
  xid xid8 := pg_catalog.pg_current_xact_id();
  acknowledged_at timestamptz;
BEGIN
  SELECT p.* INTO STRICT publication
  FROM ctcc_devval_prediction_diagnostic.publications p
  JOIN ctcc_devval_prediction_diagnostic.schedule_anchors a USING (campaign_sha256)
  WHERE p.campaign_sha256=p_campaign_sha AND a.schedule_sha256=p_schedule_sha
    AND p.slot_ordinal=p_slot AND p.prediction_sha256=p_prediction_sha;
  acknowledged_at := pg_catalog.clock_timestamp();
  IF actor=publication.publisher_oid OR xid=publication.publication_xid
    OR acknowledged_at<=publication.published_at THEN
    RAISE EXCEPTION 'ctcc_prediction_ack_denied';
  END IF;
  INSERT INTO ctcc_devval_prediction_diagnostic.acknowledgements VALUES (
    p_campaign_sha,p_slot,acknowledged_at,actor,xid);
END
$ack$;

CREATE FUNCTION ctcc_devval_prediction_diagnostic.read_prediction(
  p_campaign_sha text, p_schedule_sha text, p_slot integer, p_prediction_sha text
)
RETURNS TABLE (
  campaign_sha256 text, schedule_sha256 text, slot_ordinal integer, prediction_sha256 text,
  db0046_bundle_id text, db0046_proof_sha256 text, db0046_recorded_at timestamptz,
  anchored_at timestamptz, anchor_xid text, published_at timestamptz,
  publisher_oid oid, publication_xid text, acknowledged_at timestamptz,
  observer_oid oid, acknowledgement_xid text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $read$
BEGIN
  PERFORM ctcc_devval_prediction_diagnostic._actor(true,false);
  IF pg_catalog.current_setting('transaction_read_only') <> 'on' THEN
    RAISE EXCEPTION 'ctcc_prediction_read_only_required';
  END IF;
  RETURN QUERY
  SELECT p.campaign_sha256,a.schedule_sha256,p.slot_ordinal,p.prediction_sha256,
    p.db0046_bundle_id,p.db0046_proof_sha256,p.db0046_recorded_at,a.anchored_at,
    a.anchor_xid::text,p.published_at,p.publisher_oid,p.publication_xid::text,
    ack.acknowledged_at,ack.observer_oid,ack.acknowledgement_xid::text
  FROM ctcc_devval_prediction_diagnostic.publications p
  JOIN ctcc_devval_prediction_diagnostic.schedule_anchors a
    ON a.campaign_sha256=p.campaign_sha256
  LEFT JOIN ctcc_devval_prediction_diagnostic.acknowledgements ack
    ON ack.campaign_sha256=p.campaign_sha256 AND ack.slot_ordinal=p.slot_ordinal
  WHERE p.campaign_sha256=p_campaign_sha AND a.schedule_sha256=p_schedule_sha
    AND p.slot_ordinal=p_slot AND p.prediction_sha256=p_prediction_sha;
END
$read$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ctcc_devval_prediction_diagnostic FROM PUBLIC;
COMMIT;
