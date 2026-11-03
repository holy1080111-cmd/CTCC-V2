"""Additive current55 flat-configuration storage; never installed by this module.

The sole55 migration/catalog must bind these exact bodies and the shared
Control/ledger/positive-submit interlocks. A table, phase or DATA proof alone
is not native source authority. Historical account claims are never created.
"""

from hashlib import sha256

INTENT_TABLE = "qualification_leverage_configuration_intents_v1"
ATTEMPT_TABLE = "qualification_leverage_configuration_attempts_v1"
RESULT_TABLE = "qualification_leverage_configuration_results_v1"
ACTIVE_PHASES = ("prepared", "attempted", "verified", "unknown")
TERMINAL_PHASES = ("settled", "no_change", "not_sent")

TABLE_SQL = (
    """CREATE TABLE public.qualification_leverage_configuration_intents_v1 (
 intent_id varchar(64) PRIMARY KEY CHECK(intent_id ~ '^[a-f0-9]{64}$'),
 environment varchar(8) NOT NULL CHECK(environment='demo'),
 account_id varchar(32) NOT NULL CHECK(account_id ~ '^[0-9]{1,32}$'),
 settlement_currency varchar(16) NOT NULL CHECK(settlement_currency='USDT'),
 account_revision bigint NOT NULL CHECK(account_revision>=0),
 previous_ledger_revision bigint NOT NULL CHECK(previous_ledger_revision>=account_revision),
 ledger_revision bigint NOT NULL CHECK(ledger_revision=previous_ledger_revision+1),
 claims_sha256 varchar(64),
 control_revision bigint NOT NULL CHECK(control_revision>0),
 control_event_sha256 varchar(64) NOT NULL CHECK(control_event_sha256 ~ '^[a-f0-9]{64}$'),
 core_json text NOT NULL CHECK(octet_length(core_json) BETWEEN 1 AND 16384),
 source_json text NOT NULL CHECK(octet_length(source_json) BETWEEN 1 AND 1048576),
 source_sha256 varchar(64) NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
 plan_json text NOT NULL CHECK(octet_length(plan_json) BETWEEN 1 AND 32768),
 plan_sha256 varchar(64) NOT NULL CHECK(plan_sha256 ~ '^[a-f0-9]{64}$'),
 request_json text NOT NULL CHECK(octet_length(request_json) BETWEEN 1 AND 2048),
 request_sha256 varchar(64) NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 original_expires_at timestamptz NOT NULL CHECK(isfinite(original_expires_at)),
 phase varchar(16) NOT NULL CHECK(phase IN ('prepared','attempted','verified','unknown','settled','no_change','not_sent')),
 latest_result_sequence bigint NOT NULL DEFAULT 0 CHECK(latest_result_sequence>=0),
 db_role_oid oid NOT NULL, db_backend_pid integer NOT NULL CHECK(db_backend_pid>0),
 db_transaction_id varchar(20) NOT NULL CHECK(db_transaction_id ~ '^[0-9]{1,20}$'),
 db_recorded_at timestamptz NOT NULL CHECK(isfinite(db_recorded_at)),
 CONSTRAINT leverage_configuration_scope_fk FOREIGN KEY(environment,account_id,settlement_currency)
 REFERENCES public.qualification_account_scopes(environment,account_id,settlement_currency) ON DELETE RESTRICT,
 CONSTRAINT leverage_configuration_control_fk FOREIGN KEY(environment,account_id)
 REFERENCES public.demo_account_controls(environment,account_id) ON DELETE RESTRICT,
 CONSTRAINT leverage_configuration_journal_fk FOREIGN KEY(environment,account_id,control_revision)
 REFERENCES public.demo_control_journal(environment,account_id,control_revision) ON DELETE RESTRICT
)""",
    """CREATE UNIQUE INDEX qualification_leverage_configuration_active_uid_v55
 ON public.qualification_leverage_configuration_intents_v1(environment,account_id)
 WHERE phase IN ('prepared','attempted','verified','unknown')""",
    """CREATE TABLE public.qualification_leverage_configuration_attempts_v1 (
 intent_id varchar(64) PRIMARY KEY REFERENCES public.qualification_leverage_configuration_intents_v1(intent_id) ON DELETE RESTRICT,
 attempt_id varchar(64) UNIQUE NOT NULL CHECK(attempt_id ~ '^[a-f0-9]{64}$'),
 environment varchar(8) NOT NULL CHECK(environment='demo'), account_id varchar(32) NOT NULL,
 settlement_currency varchar(16) NOT NULL CHECK(settlement_currency='USDT'),
 account_revision bigint NOT NULL CHECK(account_revision>=0),
 previous_ledger_revision bigint NOT NULL CHECK(previous_ledger_revision>=account_revision),
 ledger_revision bigint NOT NULL CHECK(ledger_revision=previous_ledger_revision+1),
 attempt_json text NOT NULL CHECK(octet_length(attempt_json) BETWEEN 1 AND 16384),
 db_role_oid oid NOT NULL, db_backend_pid integer NOT NULL CHECK(db_backend_pid>0),
 db_transaction_id varchar(20) NOT NULL CHECK(db_transaction_id ~ '^[0-9]{1,20}$'),
 db_recorded_at timestamptz NOT NULL CHECK(isfinite(db_recorded_at))
)""",
    """CREATE TABLE public.qualification_leverage_configuration_results_v1 (
 result_id varchar(64) PRIMARY KEY CHECK(result_id ~ '^[a-f0-9]{64}$'),
 intent_id varchar(64) NOT NULL REFERENCES public.qualification_leverage_configuration_intents_v1(intent_id) ON DELETE RESTRICT,
 attempt_id varchar(64) REFERENCES public.qualification_leverage_configuration_attempts_v1(attempt_id) ON DELETE RESTRICT,
 environment varchar(8) NOT NULL CHECK(environment='demo'), account_id varchar(32) NOT NULL,
 settlement_currency varchar(16) NOT NULL CHECK(settlement_currency='USDT'),
 account_revision bigint NOT NULL CHECK(account_revision>=0),
 previous_ledger_revision bigint NOT NULL CHECK(previous_ledger_revision>=account_revision),
 ledger_revision bigint NOT NULL CHECK(ledger_revision=previous_ledger_revision+1),
 result_sequence bigint NOT NULL CHECK(result_sequence>0),
 previous_phase varchar(16) NOT NULL, phase varchar(16) NOT NULL,
 result_json text NOT NULL CHECK(octet_length(result_json) BETWEEN 1 AND 262144),
 db_role_oid oid NOT NULL, db_backend_pid integer NOT NULL CHECK(db_backend_pid>0),
 db_transaction_id varchar(20) NOT NULL CHECK(db_transaction_id ~ '^[0-9]{1,20}$'),
 db_recorded_at timestamptz NOT NULL CHECK(isfinite(db_recorded_at)),
 UNIQUE(intent_id,result_sequence),
 CHECK(phase IN ('unknown','verified','settled','no_change','not_sent'))
)""",
)

BLOCKED_SQL = """CREATE FUNCTION public.qualification_leverage_configuration_blocked_v55(e text,a text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $f$
 SELECT EXISTS(SELECT 1 FROM public.qualification_leverage_configuration_intents_v1
 WHERE environment=e AND account_id=a AND phase IN ('prepared','attempted','verified','unknown'))
$f$"""

INITIAL_LEDGER_SQL = """CREATE FUNCTION public.qualification_leverage_configuration_initial_ledger_v55(e text,a text,c text,ar bigint,lr bigint,cs text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $f$
 WITH fences AS (
  SELECT account_revision,previous_ledger_revision,ledger_revision,db_recorded_at FROM public.qualification_leverage_configuration_intents_v1 WHERE environment=e AND account_id=a AND settlement_currency=c
  UNION ALL SELECT account_revision,previous_ledger_revision,ledger_revision,db_recorded_at FROM public.qualification_leverage_configuration_attempts_v1 WHERE environment=e AND account_id=a AND settlement_currency=c
  UNION ALL SELECT account_revision,previous_ledger_revision,ledger_revision,db_recorded_at FROM public.qualification_leverage_configuration_results_v1 WHERE environment=e AND account_id=a AND settlement_currency=c
 ), ordered AS (
  SELECT *,row_number() OVER(ORDER BY ledger_revision) n,lag(db_recorded_at) OVER(ORDER BY ledger_revision) prior_time FROM fences
 ) SELECT e='demo' AND c='USDT' AND ar=0 AND cs IS NULL AND lr>0
 AND (SELECT count(*) FROM ordered)=lr
 AND NOT EXISTS(SELECT 1 FROM ordered WHERE account_revision<>0 OR previous_ledger_revision<>n-1 OR ledger_revision<>n OR NOT isfinite(db_recorded_at) OR db_recorded_at<prior_time)
 AND NOT EXISTS(SELECT 1 FROM public.qualification_reservations WHERE environment=e AND account_id=a AND settlement_currency=c)
$f$"""

# Full original flat local tables are observed under these same UID/SHARE
# locks by the existing B5 repository. The trigger independently refuses known
# holds/exposure; missing legacy initialization is never accepted as empty.
LOCAL_FLAT_SQL = """CREATE FUNCTION public.qualification_leverage_configuration_local_flat_v55(e text,a text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $f$
 SELECT e='demo'
 AND (SELECT count(*) FROM public.demo_automation_state)=1
 AND EXISTS(SELECT 1 FROM public.demo_automation_state WHERE id=1 AND NOT armed AND NOT locked
  AND lock_reasons='[]'::jsonb AND active_instrument_id IS NULL AND active_client_order_id IS NULL
  AND active_start_equity IS NULL AND active_started_at IS NULL AND active_trades IN ('{}'::jsonb,'[]'::jsonb)
  AND (last_error IS NULL OR last_error='') AND (last_started_at IS NULL OR last_completed_at>=last_started_at))
 AND (SELECT count(*) FROM public.okx_demo_sync_checkpoints)=1
 AND EXISTS(SELECT 1 FROM public.okx_demo_sync_checkpoints WHERE id=1 AND status='reconciled'
  AND reconciled_at IS NOT NULL AND order_count=0 AND position_count=0 AND algo_order_count=0
  AND (last_error IS NULL OR last_error='')
  AND details->>'account_uid_sha256'=encode(sha256(convert_to(a,'UTF8')),'hex')
  AND jsonb_typeof(details->'credential_session_binding_id')='string' AND (details->>'credential_session_binding_id') ~ '^[a-f0-9]{64}$')
 AND NOT EXISTS(SELECT 1 FROM public.okx_demo_position_state)
 AND NOT EXISTS(SELECT 1 FROM public.okx_demo_order_state WHERE state NOT IN ('filled','canceled','mmp_canceled'))
 AND NOT EXISTS(SELECT 1 FROM public.okx_demo_algo_order_state WHERE state NOT IN ('canceled','order_failed','effective'))
 AND NOT EXISTS(SELECT 1 FROM public.qualification_reservations WHERE environment=e AND account_id=a AND state<>'reconciled_flat')
$f$"""

GUARD_SQL = r"""CREATE FUNCTION public.qualification_leverage_configuration_guard_v55()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $f$
DECLARE i public.qualification_leverage_configuration_intents_v1%ROWTYPE;
 s public.qualification_account_scopes%ROWTYPE; c public.demo_account_controls%ROWTYPE;
 j public.demo_control_journal%ROWTYPE; l public.demo_collector_current_revision_lineages%ROWTYPE;
 ro record; p jsonb; q jsonb; v jsonb; src jsonb; ld jsonb;
 n timestamptz; lk bigint; expected_phase text; expected_sequence bigint;
BEGIN
 IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'native_configuration_immutable'; END IF;
 IF TG_OP='UPDATE' THEN
  IF TG_TABLE_NAME<>'qualification_leverage_configuration_intents_v1' OR pg_trigger_depth()<>2
   OR (to_jsonb(NEW)-'phase'-'latest_result_sequence') IS DISTINCT FROM (to_jsonb(OLD)-'phase'-'latest_result_sequence')
  THEN RAISE EXCEPTION 'native_configuration_immutable'; END IF;
  SELECT phase,result_sequence INTO expected_phase,expected_sequence
   FROM public.qualification_leverage_configuration_results_v1 WHERE intent_id=OLD.intent_id ORDER BY result_sequence DESC LIMIT 1;
  IF expected_phase IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.qualification_leverage_configuration_results_v1 WHERE intent_id=OLD.intent_id AND result_sequence=expected_sequence AND previous_phase=OLD.phase AND db_transaction_id=txid_current()::text) THEN RAISE EXCEPTION 'native_configuration_parent_event'; END IF;
  IF expected_phase IS NULL THEN
   IF NOT EXISTS(SELECT 1 FROM public.qualification_leverage_configuration_attempts_v1 WHERE intent_id=OLD.intent_id AND db_transaction_id=txid_current()::text)
    OR OLD.phase<>'prepared' THEN RAISE EXCEPTION 'native_configuration_phase'; END IF;
   expected_phase:='attempted'; expected_sequence:=0;
  END IF;
  IF NEW.phase IS DISTINCT FROM expected_phase OR NEW.latest_result_sequence IS DISTINCT FROM expected_sequence
  THEN RAISE EXCEPTION 'native_configuration_phase'; END IF;
  RETURN NEW;
 END IF;
 lk:=('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
 PERFORM pg_advisory_xact_lock(lk);
 SELECT * INTO c FROM public.demo_account_controls WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
 SELECT * INTO s FROM public.qualification_account_scopes WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency FOR UPDATE;
 n:=clock_timestamp();
 SELECT oid,rolsuper,rolcreaterole,rolbypassrls INTO ro FROM pg_roles WHERE rolname=current_user;
 IF c.environment IS NULL OR s.environment IS NULL OR ro.oid IS NULL OR ro.rolsuper OR ro.rolcreaterole OR ro.rolbypassrls
  OR current_user IS DISTINCT FROM session_user OR current_setting('role')<>'none'
  OR NEW.account_revision IS DISTINCT FROM s.account_revision
  OR NEW.previous_ledger_revision IS DISTINCT FROM s.ledger_revision OR NEW.ledger_revision IS DISTINCT FROM s.ledger_revision+1
 THEN RAISE EXCEPTION 'native_configuration_context'; END IF;
 NEW.db_role_oid:=ro.oid; NEW.db_backend_pid:=pg_backend_pid(); NEW.db_transaction_id:=txid_current()::text; NEW.db_recorded_at:=n;
 IF TG_TABLE_NAME='qualification_leverage_configuration_intents_v1' THEN
  IF NEW.phase<>'prepared' OR NEW.latest_result_sequence<>0 OR c.emergency_stop OR c.arm_request_id IS NOT NULL OR c.arm_expires_at IS NOT NULL
   OR c.owner_sha256 IS NULL OR c.lease_until<=n OR NEW.original_expires_at>c.lease_until OR NEW.original_expires_at<=n
   OR NEW.claims_sha256 IS DISTINCT FROM s.claims_sha256 OR NEW.control_revision IS DISTINCT FROM c.control_revision
   OR public.qualification_leverage_configuration_blocked_v55(NEW.environment,NEW.account_id)
  THEN RAISE EXCEPTION 'native_configuration_disarmed_context'; END IF;
  LOCK TABLE public.demo_automation_state,public.okx_demo_sync_checkpoints,public.okx_demo_order_state,public.okx_demo_position_state,public.okx_demo_algo_order_state IN SHARE MODE;
  IF NOT public.qualification_leverage_configuration_local_flat_v55(NEW.environment,NEW.account_id) THEN RAISE EXCEPTION 'native_configuration_local_not_flat'; END IF;
  SELECT * INTO j FROM public.demo_control_journal WHERE environment=c.environment AND account_id=c.account_id AND control_revision=c.control_revision;
  p:=NEW.core_json::jsonb; q:=NEW.request_json::jsonb; v:=NEW.plan_json::jsonb; src:=NEW.source_json::jsonb;
  IF jsonb_typeof(src->'current_lineage_sha256') IS DISTINCT FROM 'string'
   OR (src->>'current_lineage_sha256') !~ '^[a-f0-9]{64}$'
   OR jsonb_typeof(src->'current_lineage_json') IS DISTINCT FROM 'string'
   OR jsonb_typeof(src->'current_reference') IS DISTINCT FROM 'object'
  THEN RAISE EXCEPTION 'native_configuration_current_lineage'; END IF;
  SELECT * INTO l FROM public.demo_collector_current_revision_lineages
   WHERE lineage_sha256=src->>'current_lineage_sha256';
  ld:=l.lineage_json::jsonb;
  IF l.lineage_sha256 IS NULL
   OR encode(sha256(convert_to(l.lineage_json,'UTF8')),'hex') IS DISTINCT FROM l.lineage_sha256
   OR l.lineage_json IS DISTINCT FROM src->>'current_lineage_json'
   OR (l.environment,l.account_id,l.settlement_currency) IS DISTINCT FROM (NEW.environment,NEW.account_id,NEW.settlement_currency)
   OR l.account_revision IS DISTINCT FROM NEW.account_revision OR l.ledger_revision IS DISTINCT FROM NEW.previous_ledger_revision
   OR l.claims_json IS DISTINCT FROM s.claims_json OR l.claims_sha256 IS DISTINCT FROM NEW.claims_sha256
   OR l.control_revision IS DISTINCT FROM NEW.control_revision OR l.control_event_sha256 IS DISTINCT FROM NEW.control_event_sha256
   OR l.control_state_sha256 IS DISTINCT FROM j.state_sha256
   OR l.current_capture_id IS DISTINCT FROM src->'current_reference'->>'capture_id'
   OR l.current_head_sha256 IS DISTINCT FROM src->'current_reference'->>'head_sha256'
   OR ld->'source_reference' IS DISTINCT FROM src->'current_reference'
   OR ld->>'source_bundle_sha256' IS DISTINCT FROM src->>'current_bundle_sha256'
   OR ld->>'schema_version' IS DISTINCT FROM 'ctcc.collector_owned_current_revision_lineage.v1'
   OR ld->>'claims_binding_kind' IS DISTINCT FROM 'UNAUTHENTICATED_LEDGER_SNAPSHOT'
   OR ld->'db_role_oid' IS DISTINCT FROM to_jsonb(ro.oid::bigint)
   OR ld->>'db_transaction_id' IS NULL OR ld->>'db_transaction_id'=NEW.db_transaction_id
   OR l.db_recorded_at>n OR (ld->>'original_expires_at')::timestamptz IS NULL
   OR NEW.original_expires_at>(ld->>'original_expires_at')::timestamptz
   OR ld->'account_revision_issued' IS DISTINCT FROM 'false'::jsonb
   OR ld->'account_complete' IS DISTINCT FROM 'false'::jsonb OR ld->'execution_authority' IS DISTINCT FROM 'false'::jsonb
   OR ld->>'admission' IS DISTINCT FROM 'DENY'
  THEN RAISE EXCEPTION 'native_configuration_current_lineage'; END IF;
  IF encode(sha256(convert_to(NEW.core_json,'UTF8')),'hex') IS DISTINCT FROM NEW.intent_id
   OR encode(sha256(convert_to(NEW.source_json,'UTF8')),'hex') IS DISTINCT FROM NEW.source_sha256
   OR encode(sha256(convert_to(NEW.plan_json,'UTF8')),'hex') IS DISTINCT FROM NEW.plan_sha256
   OR encode(sha256(convert_to(NEW.request_json,'UTF8')),'hex') IS DISTINCT FROM NEW.request_sha256
   OR j.event_sha256 IS DISTINCT FROM NEW.control_event_sha256 OR j.state_sha256 IS DISTINCT FROM c.state_sha256
   OR p->>'schema_version' IS DISTINCT FROM 'ctcc.native_leverage_configuration.intent.v1'
   OR p->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
   OR p->'account_revision' IS DISTINCT FROM to_jsonb(NEW.account_revision)
   OR p->'previous_ledger_revision' IS DISTINCT FROM to_jsonb(NEW.previous_ledger_revision)
   OR p->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
   OR p->>'source_sha256' IS DISTINCT FROM NEW.source_sha256 OR p->>'plan_sha256' IS DISTINCT FROM NEW.plan_sha256
   OR p->>'request_sha256' IS DISTINCT FROM NEW.request_sha256 OR p->>'owner_sha256' IS DISTINCT FROM c.owner_sha256
   OR p->'owner_epoch' IS DISTINCT FROM to_jsonb(c.owner_epoch)
   OR jsonb_typeof(q) IS DISTINCT FROM 'object' OR q->>'mgnMode' IS DISTINCT FROM 'isolated'
   OR q->>'lever' IS NULL OR q->>'lever' NOT IN ('3','5','8','10','20')
   OR jsonb_typeof(q->'instId') IS DISTINCT FROM 'string' OR (q->>'instId')!~'^[A-Z0-9]+-USDT-SWAP$'
   OR v->>'schema_version' IS DISTINCT FROM 'ctcc.native_flat_leverage_configuration.plan.v1'
   OR v->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
   OR v->'target_leverage' IS DISTINCT FROM to_jsonb((q->>'lever')::int)
   OR (v->>'position_mode'='net_mode' AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(q) k) IS DISTINCT FROM ARRAY['instId','lever','mgnMode'])
   OR (v->>'position_mode'='long_short_mode' AND ((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(q) k) IS DISTINCT FROM ARRAY['instId','lever','mgnMode','posSide'] OR q->>'posSide' IS NULL OR q->>'posSide' NOT IN ('long','short')))
   OR v->>'position_mode' IS NULL OR v->>'position_mode' NOT IN ('net_mode','long_short_mode')
   OR v->'account_complete' IS DISTINCT FROM 'false'::jsonb OR v->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR v->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
   OR (NEW.source_json::jsonb)->>'schema_version' IS DISTINCT FROM 'ctcc.native_flat_leverage_source.v1'
   OR (NEW.source_json::jsonb)->>'configuration_plan_sha256' IS DISTINCT FROM NEW.plan_sha256
   OR src->'scope' IS DISTINCT FROM p->'scope'
   OR src->'account_revision_issued' IS DISTINCT FROM 'false'::jsonb
   OR src->'account_complete' IS DISTINCT FROM 'false'::jsonb
   OR src->'execution_authority' IS DISTINCT FROM 'false'::jsonb
   OR src->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
   OR jsonb_typeof((NEW.source_json::jsonb)->'current_receipt_json') IS DISTINCT FROM 'string'
   OR (((NEW.source_json::jsonb)->>'current_receipt_json')::jsonb)->'observed_flat' IS DISTINCT FROM 'true'::jsonb
   OR jsonb_typeof((NEW.source_json::jsonb)->'local_checkpoint_json') IS DISTINCT FROM 'string'
   OR (((NEW.source_json::jsonb)->>'local_checkpoint_json')::jsonb)->'blocking_reasons' IS DISTINCT FROM '[]'::jsonb
  THEN RAISE EXCEPTION 'native_configuration_source_binding'; END IF;
 ELSE
  SELECT * INTO i FROM public.qualification_leverage_configuration_intents_v1 WHERE intent_id=NEW.intent_id FOR UPDATE;
  IF i.intent_id IS NULL OR (NEW.environment,NEW.account_id,NEW.settlement_currency,NEW.account_revision)
   IS DISTINCT FROM (i.environment,i.account_id,i.settlement_currency,i.account_revision)
   OR i.phase IN ('settled','no_change','not_sent') THEN RAISE EXCEPTION 'native_configuration_parent'; END IF;
  IF TG_TABLE_NAME='qualification_leverage_configuration_attempts_v1' THEN
   p:=NEW.attempt_json::jsonb;
   IF i.phase<>'prepared' OR i.original_expires_at<=n OR c.control_revision<>i.control_revision OR c.emergency_stop
    OR c.arm_request_id IS NOT NULL OR c.arm_expires_at IS NOT NULL OR c.lease_until<=n
    OR encode(sha256(convert_to(NEW.attempt_json,'UTF8')),'hex') IS DISTINCT FROM NEW.attempt_id
    OR p->>'schema_version' IS DISTINCT FROM 'ctcc.native_leverage_configuration.attempt.v1'
    OR p->>'intent_id' IS DISTINCT FROM i.intent_id OR p->>'request_sha256' IS DISTINCT FROM i.request_sha256
    OR p->'previous_ledger_revision' IS DISTINCT FROM to_jsonb(NEW.previous_ledger_revision)
    OR p->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
   THEN RAISE EXCEPTION 'native_configuration_attempt'; END IF;
  ELSE
   p:=NEW.result_json::jsonb;
   IF NEW.previous_phase IS DISTINCT FROM i.phase OR NEW.result_sequence<>i.latest_result_sequence+1
    OR encode(sha256(convert_to(NEW.result_json,'UTF8')),'hex') IS DISTINCT FROM NEW.result_id
    OR p->>'schema_version' IS DISTINCT FROM 'ctcc.native_leverage_configuration.result.v1'
    OR p->>'intent_id' IS DISTINCT FROM i.intent_id OR p->>'classification' IS DISTINCT FROM NEW.phase
    OR p->>'request_sha256' IS DISTINCT FROM i.request_sha256
    OR p->>'previous_phase' IS DISTINCT FROM NEW.previous_phase OR p->'result_sequence' IS DISTINCT FROM to_jsonb(NEW.result_sequence)
    OR p->'attempt_id' IS DISTINCT FROM COALESCE(to_jsonb(NEW.attempt_id),'null'::jsonb)
    OR p->'previous_ledger_revision' IS DISTINCT FROM to_jsonb(NEW.previous_ledger_revision)
    OR p->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
    OR p->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR p->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
   THEN RAISE EXCEPTION 'native_configuration_result'; END IF;
   IF NEW.phase='no_change' THEN
    IF i.phase<>'prepared' OR NEW.attempt_id IS NOT NULL
     OR EXISTS(SELECT 1 FROM public.qualification_leverage_configuration_attempts_v1 WHERE intent_id=i.intent_id)
     OR (i.plan_json::jsonb)->>'action' IS DISTINCT FROM 'NO_CHANGE'
     OR (i.plan_json::jsonb)->'configured_leverage' IS DISTINCT FROM (i.plan_json::jsonb)->'target_leverage'
    THEN RAISE EXCEPTION 'native_configuration_no_change'; END IF;
   ELSE
    IF NEW.attempt_id IS NULL OR NOT EXISTS(SELECT 1 FROM public.qualification_leverage_configuration_attempts_v1 WHERE intent_id=i.intent_id AND attempt_id=NEW.attempt_id)
    THEN RAISE EXCEPTION 'native_configuration_exact_attempt'; END IF;
   END IF;
   IF NEW.phase='unknown' AND i.phase NOT IN ('attempted','unknown','verified') THEN RAISE EXCEPTION 'native_configuration_unknown_phase'; END IF;
   IF NEW.phase='verified' AND (i.phase NOT IN ('attempted','unknown') OR jsonb_typeof(p->'signed_readback_json') IS DISTINCT FROM 'string' OR jsonb_typeof(p->'root_response_receipt_json') IS DISTINCT FROM 'string')
   THEN RAISE EXCEPTION 'native_configuration_verified_proof'; END IF;
   IF NEW.phase='settled' AND i.phase<>'verified' THEN RAISE EXCEPTION 'native_configuration_verified_required'; END IF;
   IF NEW.phase IN ('settled','no_change','not_sent') AND (jsonb_typeof(p->'source_cleanup_receipt_json') IS DISTINCT FROM 'string' OR jsonb_typeof(p->'root_cleanup_receipt_json') IS DISTINCT FROM 'string')
   THEN RAISE EXCEPTION 'native_configuration_terminal_proof'; END IF;
   IF NEW.phase='not_sent' AND (i.phase<>'attempted' OR p->>'physical_no_send_schema' IS DISTINCT FROM 'ctcc.native_leverage_configuration.root_never_sent.v1')
   THEN RAISE EXCEPTION 'native_configuration_no_send_proof'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END; $f$"""

FENCE_SQL = """CREATE FUNCTION public.qualification_leverage_configuration_fence_v55()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $f$
BEGIN
 UPDATE public.qualification_account_scopes SET ledger_revision=NEW.ledger_revision,updated_at=NEW.db_recorded_at
 WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency
 AND account_revision=NEW.account_revision AND ledger_revision=NEW.previous_ledger_revision;
 IF NOT FOUND THEN RAISE EXCEPTION 'native_configuration_scope_cas'; END IF;
 IF TG_TABLE_NAME='qualification_leverage_configuration_attempts_v1' THEN
  UPDATE public.qualification_leverage_configuration_intents_v1 SET phase='attempted' WHERE intent_id=NEW.intent_id;
 ELSIF TG_TABLE_NAME='qualification_leverage_configuration_results_v1' THEN
  UPDATE public.qualification_leverage_configuration_intents_v1 SET phase=NEW.phase,latest_result_sequence=NEW.result_sequence WHERE intent_id=NEW.intent_id;
 END IF;
 RETURN NEW;
END; $f$"""

FUNCTION_SQL = (BLOCKED_SQL, INITIAL_LEDGER_SQL, LOCAL_FLAT_SQL, GUARD_SQL, FENCE_SQL)
TRIGGER_SQL = tuple(
    statement
    for name in (INTENT_TABLE, ATTEMPT_TABLE, RESULT_TABLE)
    for statement in (
        f"CREATE TRIGGER {name}_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{name} FOR EACH ROW EXECUTE FUNCTION public.qualification_leverage_configuration_guard_v55()",
        f"CREATE TRIGGER {name}_fence AFTER INSERT ON public.{name} FOR EACH ROW EXECUTE FUNCTION public.qualification_leverage_configuration_fence_v55()",
        f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON public.{name} FOR EACH STATEMENT EXECUTE FUNCTION public.qualification_leverage_configuration_guard_v55()",
    )
)


def statements():
    return TABLE_SQL + FUNCTION_SQL + TRIGGER_SQL


def source_descriptor():
    """Exact inert source specification consumed by the sole55 catalog author."""
    return tuple(
        (name, sha256(sql.encode("utf-8")).hexdigest(), sql)
        for name, sql in zip(
            ("blocked", "initial_ledger", "local_flat", "guard", "fence"),
            FUNCTION_SQL,
            strict=True,
        )
    )


TABLES = (INTENT_TABLE, ATTEMPT_TABLE, RESULT_TABLE)
FUNCTION_IDENTITIES = (
    (
        "qualification_leverage_configuration_blocked_v55",
        "text,text",
        "boolean",
        "sql",
        "s",
        BLOCKED_SQL,
    ),
    (
        "qualification_leverage_configuration_initial_ledger_v55",
        "text,text,text,bigint,bigint,text",
        "boolean",
        "sql",
        "s",
        INITIAL_LEDGER_SQL,
    ),
    (
        "qualification_leverage_configuration_local_flat_v55",
        "text,text",
        "boolean",
        "sql",
        "s",
        LOCAL_FLAT_SQL,
    ),
    (
        "qualification_leverage_configuration_guard_v55",
        "",
        "trigger",
        "plpgsql",
        "v",
        GUARD_SQL,
    ),
    (
        "qualification_leverage_configuration_fence_v55",
        "",
        "trigger",
        "plpgsql",
        "v",
        FENCE_SQL,
    ),
)
FOREIGN_KEYS = (
    (
        INTENT_TABLE,
        ("environment", "account_id", "settlement_currency"),
        "qualification_account_scopes",
        ("environment", "account_id", "settlement_currency"),
    ),
    (
        INTENT_TABLE,
        ("environment", "account_id"),
        "demo_account_controls",
        ("environment", "account_id"),
    ),
    (
        INTENT_TABLE,
        ("environment", "account_id", "control_revision"),
        "demo_control_journal",
        ("environment", "account_id", "control_revision"),
    ),
    (ATTEMPT_TABLE, ("intent_id",), INTENT_TABLE, ("intent_id",)),
    (RESULT_TABLE, ("intent_id",), INTENT_TABLE, ("intent_id",)),
    (RESULT_TABLE, ("attempt_id",), ATTEMPT_TABLE, ("attempt_id",)),
)
# No runtime grants are made here. The actual restricted55 role and exact
# owner/ACL/no-SET-ROLE closure are supplied by the sole deployment/catalog.
INDEX_SPECS = (
    (
        "qualification_leverage_configuration_active_uid_v55",
        INTENT_TABLE,
        ("environment", "account_id"),
        True,
        ("prepared", "attempted", "verified", "unknown"),
    ),
)
