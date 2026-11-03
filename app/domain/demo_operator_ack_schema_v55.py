"""Frozen SOURCE-ONLY DB0055 operator recovery/counter DDL; no grants or authority."""

import hashlib

MIGRATION_HEAD = "0055"
PREVIOUS_HEAD = "0054"
CLEAR_TABLE = "demo_control_clear_acknowledgements_v2"
COUNTER_TABLE = "demo_policy_counter_initializations_v1"

CLEAR_TABLE_SQL = """CREATE TABLE public.demo_control_clear_acknowledgements_v2 (
 ack_sha256 varchar(64) PRIMARY KEY,
 environment varchar(8) NOT NULL,
 account_id varchar(32) NOT NULL,
 settlement_currency varchar(16) NOT NULL,
 command_id varchar(64) NOT NULL,
 stopped_control_revision bigint NOT NULL,
 stopped_event_sha256 varchar(64) NOT NULL,
 stopped_state_sha256 varchar(64) NOT NULL,
 clear_control_revision bigint NOT NULL,
 clear_state_sha256 varchar(64) NOT NULL,
 clear_event_sha256 varchar(64) NOT NULL,
 source_lineage_sha256 varchar(64) NOT NULL,
 account_revision bigint NOT NULL,
 ledger_revision bigint NOT NULL,
 claims_sha256 varchar(64),
 owner_sha256 varchar(64) NOT NULL,
 owner_epoch bigint NOT NULL,
 original_lease_until timestamptz NOT NULL,
 owner_sampled_at timestamptz NOT NULL,
 original_expires_at timestamptz NOT NULL,
 ack_json text NOT NULL,
 state_json text NOT NULL,
 event_json text NOT NULL,
 db_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT fk_control_clear_scope FOREIGN KEY(environment,account_id,settlement_currency)
  REFERENCES public.qualification_account_scopes(environment,account_id,settlement_currency)
  ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT fk_control_clear_stopped FOREIGN KEY(environment,account_id,stopped_control_revision)
  REFERENCES public.demo_control_journal(environment,account_id,control_revision)
  ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT fk_control_clear_result FOREIGN KEY(environment,account_id,clear_control_revision)
  REFERENCES public.demo_control_journal(environment,account_id,control_revision)
  ON DELETE NO ACTION ON UPDATE NO ACTION DEFERRABLE INITIALLY DEFERRED,
 CONSTRAINT fk_control_clear_current FOREIGN KEY(source_lineage_sha256)
  REFERENCES public.demo_collector_current_revision_lineages(lineage_sha256)
  ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT uq_control_clear_command UNIQUE(environment,account_id,command_id),
 CONSTRAINT uq_control_clear_once UNIQUE(environment,account_id,stopped_control_revision),
 CONSTRAINT control_clear_scope CHECK
  (environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency='USDT'),
 CONSTRAINT control_clear_revisions CHECK
  (stopped_control_revision>0 AND stopped_control_revision<9223372036854775807
   AND clear_control_revision=stopped_control_revision+1 AND owner_epoch>0
   AND account_revision>=0 AND ledger_revision>=account_revision),
 CONSTRAINT control_clear_claims_pair CHECK
  ((account_revision=0 AND claims_sha256 IS NULL)
   OR (account_revision>0 AND claims_sha256 IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')),
 CONSTRAINT control_clear_digests CHECK
  (ack_sha256 ~ '^[a-f0-9]{64}$' AND command_id ~ '^[a-f0-9]{64}$'
   AND stopped_event_sha256 ~ '^[a-f0-9]{64}$' AND stopped_state_sha256 ~ '^[a-f0-9]{64}$'
   AND clear_state_sha256 ~ '^[a-f0-9]{64}$' AND clear_event_sha256 ~ '^[a-f0-9]{64}$'
   AND source_lineage_sha256 ~ '^[a-f0-9]{64}$' AND owner_sha256 ~ '^[a-f0-9]{64}$'),
 CONSTRAINT control_clear_bounds CHECK
  (octet_length(ack_json) BETWEEN 1 AND 32768 AND octet_length(state_json) BETWEEN 1 AND 8192
   AND octet_length(event_json) BETWEEN 1 AND 12288),
 CONSTRAINT control_clear_clocks CHECK
  (isfinite(owner_sampled_at) AND isfinite(original_expires_at) AND isfinite(original_lease_until)
   AND owner_sampled_at<original_expires_at AND original_expires_at<=original_lease_until)
);
"""

COUNTER_TABLE_SQL = """CREATE TABLE public.demo_policy_counter_initializations_v1 (
 initialization_sha256 varchar(64) PRIMARY KEY,
 environment varchar(8) NOT NULL,
 account_id varchar(32) NOT NULL,
 settlement_currency varchar(16) NOT NULL,
 command_id varchar(64) NOT NULL,
 control_revision bigint NOT NULL,
 control_event_sha256 varchar(64) NOT NULL,
 control_state_sha256 varchar(64) NOT NULL,
 source_lineage_sha256 varchar(64) NOT NULL,
 account_revision bigint NOT NULL,
 ledger_revision bigint NOT NULL,
 claims_sha256 varchar(64),
 configured_policy_sha256 varchar(64) NOT NULL,
 configured_policy_json text NOT NULL,
 measured_genesis_sha256 varchar(64) NOT NULL,
 measured_genesis_json text NOT NULL,
 owner_sampled_at timestamptz NOT NULL,
 original_expires_at timestamptz NOT NULL,
 ack_json text NOT NULL,
 initialized_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 db_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT fk_policy_counter_scope FOREIGN KEY(environment,account_id,settlement_currency)
  REFERENCES public.qualification_account_scopes(environment,account_id,settlement_currency)
  ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT fk_policy_counter_control FOREIGN KEY(environment,account_id,control_revision)
  REFERENCES public.demo_control_journal(environment,account_id,control_revision)
  ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT fk_policy_counter_current FOREIGN KEY(source_lineage_sha256)
  REFERENCES public.demo_collector_current_revision_lineages(lineage_sha256)
  ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT uq_policy_counter_once UNIQUE(environment,account_id,settlement_currency),
 CONSTRAINT uq_policy_counter_command UNIQUE(environment,account_id,command_id),
 CONSTRAINT policy_counter_scope CHECK
  (environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency='USDT'),
 CONSTRAINT policy_counter_revisions CHECK
  (control_revision>0 AND account_revision>=0 AND ledger_revision>=account_revision),
 CONSTRAINT policy_counter_claims_pair CHECK
  ((account_revision=0 AND claims_sha256 IS NULL)
   OR (account_revision>0 AND claims_sha256 IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')),
 CONSTRAINT policy_counter_digests CHECK
  (initialization_sha256 ~ '^[a-f0-9]{64}$' AND command_id ~ '^[a-f0-9]{64}$'
   AND control_event_sha256 ~ '^[a-f0-9]{64}$' AND control_state_sha256 ~ '^[a-f0-9]{64}$'
   AND source_lineage_sha256 ~ '^[a-f0-9]{64}$' AND configured_policy_sha256 ~ '^[a-f0-9]{64}$'
   AND measured_genesis_sha256 ~ '^[a-f0-9]{64}$'),
 CONSTRAINT policy_counter_bounds CHECK
  (octet_length(ack_json) BETWEEN 1 AND 32768
   AND octet_length(configured_policy_json) BETWEEN 1 AND 32768
   AND octet_length(measured_genesis_json) BETWEEN 1 AND 1048576),
 CONSTRAINT policy_counter_clocks CHECK
  (isfinite(owner_sampled_at) AND isfinite(original_expires_at)
   AND owner_sampled_at<original_expires_at AND initialized_at=db_recorded_at
   AND isfinite(initialized_at) AND owner_sampled_at<=initialized_at
   AND initialized_at<original_expires_at)
);
"""

ACK_GUARD_SQL = """CREATE FUNCTION public.control_clear_ack_guard_v2() RETURNS trigger AS $ctcc$
DECLARE c RECORD; cj RECORD; a RECORD; l RECORD; r RECORD; p jsonb; s jsonb; e jsonb;
        old_state jsonb; sampled timestamptz; expires timestamptz; lock_key bigint;
BEGIN
 IF current_setting('role')<>'none' OR current_setting('transaction_read_only')<>'off'
   OR current_setting('synchronous_commit')<>'on'
   OR current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'control_clear_session_denied';
 END IF;
 SELECT oid,rolsuper,rolcreaterole,rolbypassrls INTO r
  FROM pg_catalog.pg_roles WHERE rolname=session_user;
 IF r.oid IS NULL OR r.rolsuper OR r.rolcreaterole OR r.rolbypassrls THEN
  RAISE EXCEPTION 'control_clear_role_denied';
 END IF;
 lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')
  ||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')
  ||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
 PERFORM pg_advisory_xact_lock(lock_key);
 SELECT * INTO c FROM public.demo_account_controls
  WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
 SELECT * INTO a FROM public.qualification_account_scopes
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND settlement_currency=NEW.settlement_currency FOR UPDATE;
 SELECT * INTO cj FROM public.demo_control_journal
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND control_revision=NEW.stopped_control_revision FOR KEY SHARE;
 SELECT * INTO l FROM public.demo_collector_current_revision_lineages
  WHERE lineage_sha256=NEW.source_lineage_sha256 FOR KEY SHARE;
 p := NEW.ack_json::jsonb; s := NEW.state_json::jsonb; e := NEW.event_json::jsonb;
 old_state := c.state_json::jsonb;
 sampled := NEW.owner_sampled_at; expires := NEW.original_expires_at;
 IF c.control_revision IS NULL OR a.account_revision IS NULL OR cj.control_revision IS NULL
  OR l.lineage_sha256 IS NULL OR NOT c.emergency_stop OR c.owner_epoch<=0
  OR c.arm_request_id IS NOT NULL OR c.arm_expires_at IS NOT NULL
  OR ROW(c.control_revision,c.state_sha256,c.owner_sha256,c.owner_epoch,c.lease_until)
   IS DISTINCT FROM ROW(NEW.stopped_control_revision,NEW.stopped_state_sha256,
    NEW.owner_sha256,NEW.owner_epoch,NEW.original_lease_until)
  OR ROW(cj.event_sha256,cj.state_sha256)
   IS DISTINCT FROM ROW(NEW.stopped_event_sha256,NEW.stopped_state_sha256)
  OR ROW(a.account_revision,a.ledger_revision,a.claims_sha256)
   IS DISTINCT FROM ROW(NEW.account_revision,NEW.ledger_revision,NEW.claims_sha256)
  OR ROW(l.environment,l.account_id,l.settlement_currency,l.account_revision,l.ledger_revision,
    l.claims_sha256,l.control_revision,l.control_event_sha256,l.control_state_sha256)
   IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency,NEW.account_revision,
    NEW.ledger_revision,NEW.claims_sha256,NEW.stopped_control_revision,
    NEW.stopped_event_sha256,NEW.stopped_state_sha256)
  OR l.claims_json IS DISTINCT FROM a.claims_json
  OR encode(sha256(convert_to(c.state_json,'UTF8')),'hex') IS DISTINCT FROM c.state_sha256
  OR encode(sha256(convert_to(cj.event_json,'UTF8')),'hex') IS DISTINCT FROM cj.event_sha256
  OR encode(sha256(convert_to(l.lineage_json,'UTF8')),'hex') IS DISTINCT FROM l.lineage_sha256
  OR NEW.clear_control_revision IS DISTINCT FROM NEW.stopped_control_revision+1
  OR sampled IS NULL OR NOT isfinite(sampled) OR expires IS NULL OR NOT isfinite(expires)
  OR NOT isfinite(NEW.original_lease_until) OR sampled<c.updated_at
  OR sampled>=expires OR expires>c.lease_until OR clock_timestamp()>=expires
  OR l.db_recorded_at>sampled
  OR (l.lineage_json::jsonb->>'owner_sampled_at')::timestamptz IS NULL
  OR NOT isfinite((l.lineage_json::jsonb->>'owner_sampled_at')::timestamptz)
  OR NOT isfinite((l.lineage_json::jsonb->>'original_expires_at')::timestamptz)
  OR (l.lineage_json::jsonb->>'owner_sampled_at')::timestamptz>sampled
  OR (l.lineage_json::jsonb->>'original_expires_at')::timestamptz IS NULL
  OR expires>(l.lineage_json::jsonb->>'original_expires_at')::timestamptz
  OR encode(sha256(convert_to(NEW.ack_json,'UTF8')),'hex') IS DISTINCT FROM NEW.ack_sha256
  OR encode(sha256(convert_to(NEW.state_json,'UTF8')),'hex') IS DISTINCT FROM NEW.clear_state_sha256
  OR encode(sha256(convert_to(NEW.event_json,'UTF8')),'hex') IS DISTINCT FROM NEW.clear_event_sha256
  OR jsonb_typeof(p) IS DISTINCT FROM 'object'
  OR (SELECT count(*) FROM jsonb_object_keys(p))<>22
  OR p->>'schema_version' IS DISTINCT FROM 'ctcc.demo_control_clear_ack.v2'
  OR p->>'purpose' IS DISTINCT FROM 'clear_stop'
  OR p->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
  OR p->>'command_id' IS DISTINCT FROM NEW.command_id
  OR p->'stopped_control_revision' IS DISTINCT FROM to_jsonb(NEW.stopped_control_revision)
  OR p->>'stopped_event_sha256' IS DISTINCT FROM NEW.stopped_event_sha256
  OR p->>'stopped_state_sha256' IS DISTINCT FROM NEW.stopped_state_sha256
  OR p->>'clear_state_sha256' IS DISTINCT FROM NEW.clear_state_sha256
  OR p->>'source_lineage_sha256' IS DISTINCT FROM NEW.source_lineage_sha256
  OR p->'account_revision' IS DISTINCT FROM to_jsonb(NEW.account_revision)
  OR p->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
  OR NOT (p ? 'claims_sha256') OR p->>'claims_sha256' IS DISTINCT FROM NEW.claims_sha256
  OR p->>'owner_sha256' IS DISTINCT FROM NEW.owner_sha256
  OR p->'owner_epoch' IS DISTINCT FROM to_jsonb(NEW.owner_epoch)
  OR jsonb_typeof(p->'original_lease_until') IS DISTINCT FROM 'string'
  OR (p->>'original_lease_until')::timestamptz IS DISTINCT FROM NEW.original_lease_until
  OR jsonb_typeof(p->'owner_sampled_at') IS DISTINCT FROM 'string'
  OR (p->>'owner_sampled_at')::timestamptz IS DISTINCT FROM sampled
  OR jsonb_typeof(p->'original_expires_at') IS DISTINCT FROM 'string'
  OR (p->>'original_expires_at')::timestamptz IS DISTINCT FROM expires
  OR p->'db_role_oid' IS DISTINCT FROM to_jsonb(r.oid::bigint)
  OR p->'db_backend_pid' IS DISTINCT FROM to_jsonb(pg_backend_pid())
  OR EXISTS(SELECT 1 FROM jsonb_each(p) x WHERE x.key=ANY(ARRAY[
   'stopped_control_revision','account_revision','ledger_revision','owner_epoch','db_role_oid','db_backend_pid'])
   AND (jsonb_typeof(x.value)<>'number' OR x.value#>>'{}' !~ '^(0|[1-9][0-9]*)$'))
  OR p->>'db_transaction_id' IS DISTINCT FROM pg_current_xact_id()::text
  OR EXISTS(SELECT 1 FROM jsonb_each(p) x WHERE x.key=ANY(ARRAY['schema_version','purpose','command_id',
   'stopped_event_sha256','stopped_state_sha256','clear_state_sha256','source_lineage_sha256','owner_sha256',
   'db_transaction_id']) AND jsonb_typeof(x.value)<>'string')
  OR jsonb_typeof(p->'claims_sha256') IS DISTINCT FROM (CASE WHEN NEW.claims_sha256 IS NULL THEN 'null' ELSE 'string' END)
  OR jsonb_typeof(p->'proofs') IS DISTINCT FROM 'object'
  OR (SELECT count(*) FROM jsonb_object_keys(p->'proofs'))<>6
  OR NOT (p->'proofs' ?& ARRAY['current_identity','flat_positions','ordinary_pending',
    'algo_pending','local_reconciliation','operator_ack'])
  OR EXISTS(SELECT 1 FROM jsonb_each(p->'proofs') x
    WHERE jsonb_typeof(x.value)<>'string' OR x.value#>>'{}' !~ '^[a-f0-9]{64}$')
  OR p->'operator_ack' IS DISTINCT FROM jsonb_build_object(
    'schema_version','ctcc.demo_control_clear_operator.v2','purpose','clear_stop',
    'scope',p->'scope','command_id',NEW.command_id,
    'stopped_control_revision',NEW.stopped_control_revision,
    'stopped_event_sha256',NEW.stopped_event_sha256,
    'source_lineage_sha256',NEW.source_lineage_sha256,
    'original_expires_at',p->'original_expires_at',
    'confirmation','CTCC DEMO CLEAR STOP DISARMED')
  OR jsonb_typeof(s) IS DISTINCT FROM 'object'
  OR (s-ARRAY['revision','emergency_stop','updated_at'])
    IS DISTINCT FROM (old_state-ARRAY['revision','emergency_stop','updated_at'])
  OR s->'revision' IS DISTINCT FROM to_jsonb(NEW.clear_control_revision)
  OR s->'emergency_stop' IS DISTINCT FROM 'false'::jsonb
  OR s->'arm_request_id' IS DISTINCT FROM 'null'::jsonb
  OR s->'arm_expires_at' IS DISTINCT FROM 'null'::jsonb
  OR (s->>'updated_at')::timestamptz IS DISTINCT FROM sampled
  OR e IS DISTINCT FROM jsonb_build_object(
    'version','ctcc.demo_control_event.v2','command_id',NEW.command_id,
    'action','clear_stop','previous_sha256',NEW.stopped_event_sha256,
    'state',s,'clear_ack_sha256',NEW.ack_sha256) THEN
  RAISE EXCEPTION 'control_clear_binding_denied';
 END IF;
 -- Exact source proofs are authenticated by the native boundary, never by
 -- these JSON hashes. SQL additionally rechecks every local reservation under
 -- the SAME UID lock; expired/consumed/unknown records are not dropped.
 IF EXISTS(SELECT 1 FROM public.qualification_reservations
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND state<>'reconciled_flat') THEN
  RAISE EXCEPTION 'control_clear_local_exposure_denied';
 END IF;
 NEW.db_recorded_at := clock_timestamp();
 IF NEW.db_recorded_at IS NULL OR NOT isfinite(NEW.db_recorded_at)
   OR NEW.db_recorded_at<sampled OR NEW.db_recorded_at>=expires THEN
  RAISE EXCEPTION 'control_clear_expired';
 END IF;
 RETURN NEW;
EXCEPTION WHEN others THEN
 RAISE EXCEPTION 'control_clear_write_denied';
END;
$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;
"""

ACK_COMMIT_SQL = """CREATE FUNCTION public.control_clear_ack_commit_guard_v2() RETURNS trigger AS $ctcc$
DECLARE c RECORD; j RECORD; a RECORD;
BEGIN
 SELECT * INTO c FROM public.demo_account_controls
  WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
 SELECT * INTO j FROM public.demo_control_journal
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND control_revision=NEW.clear_control_revision FOR KEY SHARE;
 SELECT * INTO a FROM public.qualification_account_scopes
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND settlement_currency=NEW.settlement_currency FOR UPDATE;
 IF c.control_revision IS DISTINCT FROM NEW.clear_control_revision
  OR c.state_sha256 IS DISTINCT FROM NEW.clear_state_sha256
  OR c.state_json IS DISTINCT FROM NEW.state_json OR c.emergency_stop
  OR c.arm_request_id IS NOT NULL OR c.arm_expires_at IS NOT NULL
  OR c.owner_sha256 IS DISTINCT FROM NEW.owner_sha256 OR c.owner_epoch IS DISTINCT FROM NEW.owner_epoch
  OR c.lease_until IS DISTINCT FROM NEW.original_lease_until OR c.updated_at IS DISTINCT FROM NEW.owner_sampled_at
  OR ROW(j.command_id,j.action,j.previous_sha256,j.event_sha256,j.state_sha256,j.event_json,j.occurred_at)
   IS DISTINCT FROM ROW(NEW.command_id,'clear_stop'::varchar,NEW.stopped_event_sha256,
    NEW.clear_event_sha256,NEW.clear_state_sha256,NEW.event_json,NEW.owner_sampled_at)
  OR ROW(a.account_revision,a.ledger_revision,a.claims_sha256)
   IS DISTINCT FROM ROW(NEW.account_revision,NEW.ledger_revision,NEW.claims_sha256)
  OR NEW.ack_json::jsonb->>'db_transaction_id' IS DISTINCT FROM pg_current_xact_id()::text
  OR NEW.ack_json::jsonb->'db_backend_pid' IS DISTINCT FROM to_jsonb(pg_backend_pid())
  OR current_setting('role')<>'none' OR current_setting('transaction_read_only')<>'off'
  OR current_setting('synchronous_commit')<>'on'
  OR current_setting('transaction_isolation')<>'read committed' OR clock_timestamp()>=NEW.original_expires_at
  OR EXISTS(SELECT 1 FROM public.qualification_reservations
   WHERE environment=NEW.environment AND account_id=NEW.account_id AND state<>'reconciled_flat') THEN
  RAISE EXCEPTION 'control_clear_commit_without_exact_disarmed_journal';
 END IF;
 RETURN NEW;
END;
$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;
"""

COUNTER_GUARD_SQL = """CREATE FUNCTION public.policy_counter_initialization_guard_v1() RETURNS trigger AS $ctcc$
DECLARE c RECORD; cj RECORD; a RECORD; l RECORD; r RECORD; p jsonb; policy jsonb; g jsonb;
        sampled timestamptz; expires timestamptz; lock_key bigint;
BEGIN
 IF current_setting('role')<>'none' OR current_setting('transaction_read_only')<>'off'
   OR current_setting('synchronous_commit')<>'on'
   OR current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'policy_counter_session_denied';
 END IF;
 SELECT oid,rolsuper,rolcreaterole,rolbypassrls INTO r
  FROM pg_catalog.pg_roles WHERE rolname=session_user;
 IF r.oid IS NULL OR r.rolsuper OR r.rolcreaterole OR r.rolbypassrls THEN
  RAISE EXCEPTION 'policy_counter_role_denied';
 END IF;
 lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')
  ||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')
  ||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
 PERFORM pg_advisory_xact_lock(lock_key);
 SELECT * INTO c FROM public.demo_account_controls
  WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
 SELECT * INTO a FROM public.qualification_account_scopes
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND settlement_currency=NEW.settlement_currency FOR UPDATE;
 SELECT * INTO cj FROM public.demo_control_journal
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND control_revision=NEW.control_revision FOR KEY SHARE;
 SELECT * INTO l FROM public.demo_collector_current_revision_lineages
  WHERE lineage_sha256=NEW.source_lineage_sha256 FOR KEY SHARE;
 p := NEW.ack_json::jsonb; policy := NEW.configured_policy_json::jsonb;
 g := NEW.measured_genesis_json::jsonb;
 sampled := NEW.owner_sampled_at; expires := NEW.original_expires_at;
 IF c.control_revision IS NULL OR a.account_revision IS NULL OR cj.control_revision IS NULL
  OR l.lineage_sha256 IS NULL OR NOT c.emergency_stop OR c.owner_epoch<=0
  OR c.arm_request_id IS NOT NULL OR c.arm_expires_at IS NOT NULL
  OR ROW(c.control_revision,c.state_sha256,cj.event_sha256,cj.state_sha256)
   IS DISTINCT FROM ROW(NEW.control_revision,NEW.control_state_sha256,
    NEW.control_event_sha256,NEW.control_state_sha256)
  OR ROW(a.account_revision,a.ledger_revision,a.claims_sha256)
   IS DISTINCT FROM ROW(NEW.account_revision,NEW.ledger_revision,NEW.claims_sha256)
  OR ROW(l.environment,l.account_id,l.settlement_currency,l.account_revision,l.ledger_revision,
    l.claims_sha256,l.control_revision,l.control_event_sha256,l.control_state_sha256)
   IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency,
    NEW.account_revision,NEW.ledger_revision,NEW.claims_sha256,NEW.control_revision,
    NEW.control_event_sha256,NEW.control_state_sha256)
  OR l.claims_json IS DISTINCT FROM a.claims_json
  OR encode(sha256(convert_to(c.state_json,'UTF8')),'hex') IS DISTINCT FROM c.state_sha256
  OR encode(sha256(convert_to(cj.event_json,'UTF8')),'hex') IS DISTINCT FROM cj.event_sha256
  OR encode(sha256(convert_to(l.lineage_json,'UTF8')),'hex') IS DISTINCT FROM l.lineage_sha256
  OR sampled IS NULL OR NOT isfinite(sampled) OR expires IS NULL OR NOT isfinite(expires)
  OR sampled<c.updated_at OR sampled>=expires OR expires>c.lease_until OR clock_timestamp()>=expires
  OR l.db_recorded_at>sampled
  OR (l.lineage_json::jsonb->>'owner_sampled_at')::timestamptz IS NULL
  OR NOT isfinite((l.lineage_json::jsonb->>'owner_sampled_at')::timestamptz)
  OR NOT isfinite((l.lineage_json::jsonb->>'original_expires_at')::timestamptz)
  OR (l.lineage_json::jsonb->>'owner_sampled_at')::timestamptz>sampled
  OR (l.lineage_json::jsonb->>'original_expires_at')::timestamptz IS NULL
  OR expires>(l.lineage_json::jsonb->>'original_expires_at')::timestamptz
  OR encode(sha256(convert_to(NEW.ack_json,'UTF8')),'hex') IS DISTINCT FROM NEW.initialization_sha256
  OR encode(sha256(convert_to(NEW.configured_policy_json,'UTF8')),'hex') IS DISTINCT FROM NEW.configured_policy_sha256
  OR encode(sha256(convert_to(NEW.measured_genesis_json,'UTF8')),'hex') IS DISTINCT FROM NEW.measured_genesis_sha256
  OR jsonb_typeof(p) IS DISTINCT FROM 'object'
  OR (SELECT count(*) FROM jsonb_object_keys(p))<>20
  OR p->>'schema_version' IS DISTINCT FROM 'ctcc.demo_policy_counter_init.v1'
  OR p->>'purpose' IS DISTINCT FROM 'policy_counter_initialize'
  OR p->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
  OR p->>'command_id' IS DISTINCT FROM NEW.command_id
  OR p->'control_revision' IS DISTINCT FROM to_jsonb(NEW.control_revision)
  OR p->>'control_event_sha256' IS DISTINCT FROM NEW.control_event_sha256
  OR p->>'control_state_sha256' IS DISTINCT FROM NEW.control_state_sha256
  OR p->>'source_lineage_sha256' IS DISTINCT FROM NEW.source_lineage_sha256
  OR p->'account_revision' IS DISTINCT FROM to_jsonb(NEW.account_revision)
  OR p->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
  OR NOT (p ? 'claims_sha256') OR p->>'claims_sha256' IS DISTINCT FROM NEW.claims_sha256
  OR p->>'configured_policy_sha256' IS DISTINCT FROM NEW.configured_policy_sha256
  OR p->>'measured_genesis_sha256' IS DISTINCT FROM NEW.measured_genesis_sha256
  OR jsonb_typeof(p->'owner_sampled_at') IS DISTINCT FROM 'string'
  OR (p->>'owner_sampled_at')::timestamptz IS DISTINCT FROM sampled
  OR jsonb_typeof(p->'original_expires_at') IS DISTINCT FROM 'string'
  OR (p->>'original_expires_at')::timestamptz IS DISTINCT FROM expires
  OR p->'db_role_oid' IS DISTINCT FROM to_jsonb(r.oid::bigint)
  OR p->'db_backend_pid' IS DISTINCT FROM to_jsonb(pg_backend_pid())
  OR EXISTS(SELECT 1 FROM jsonb_each(p) x WHERE x.key=ANY(ARRAY[
   'control_revision','account_revision','ledger_revision','db_role_oid','db_backend_pid'])
   AND (jsonb_typeof(x.value)<>'number' OR x.value#>>'{}' !~ '^(0|[1-9][0-9]*)$'))
  OR p->>'db_transaction_id' IS DISTINCT FROM pg_current_xact_id()::text
  OR EXISTS(SELECT 1 FROM jsonb_each(p) x WHERE x.key=ANY(ARRAY['schema_version','purpose','command_id',
   'control_event_sha256','control_state_sha256','source_lineage_sha256','configured_policy_sha256',
   'measured_genesis_sha256','db_transaction_id']) AND jsonb_typeof(x.value)<>'string')
  OR jsonb_typeof(p->'claims_sha256') IS DISTINCT FROM (CASE WHEN NEW.claims_sha256 IS NULL THEN 'null' ELSE 'string' END)
  OR jsonb_typeof(p->'proofs') IS DISTINCT FROM 'object'
  OR (SELECT count(*) FROM jsonb_object_keys(p->'proofs'))<>8
  OR NOT (p->'proofs' ?& ARRAY['current_identity','flat_positions','ordinary_pending',
    'algo_pending','local_reconciliation','operator_ack','configured_policy','measured_genesis'])
  OR EXISTS(SELECT 1 FROM jsonb_each(p->'proofs') x
    WHERE jsonb_typeof(x.value)<>'string' OR x.value#>>'{}' !~ '^[a-f0-9]{64}$')
  OR p->'proofs'->>'configured_policy' IS DISTINCT FROM NEW.configured_policy_sha256
  OR p->'proofs'->>'measured_genesis' IS DISTINCT FROM NEW.measured_genesis_sha256
  OR p->'operator_ack' IS DISTINCT FROM jsonb_build_object(
    'schema_version','ctcc.demo_policy_counter_operator.v1','purpose','policy_counter_initialize',
    'scope',p->'scope','command_id',NEW.command_id,'control_revision',NEW.control_revision,
    'control_event_sha256',NEW.control_event_sha256,'source_lineage_sha256',NEW.source_lineage_sha256,
    'configured_policy_sha256',NEW.configured_policy_sha256,'measured_genesis_sha256',NEW.measured_genesis_sha256,
    'original_expires_at',p->'original_expires_at','confirmation','CTCC DEMO INITIALIZE POLICY COUNTER ONCE')
  OR jsonb_typeof(policy) IS DISTINCT FROM 'object'
  OR policy->>'schema_version' IS DISTINCT FROM 'ctcc.configured_owned_risk_policy.v1'
  OR jsonb_typeof(policy->'policy') IS DISTINCT FROM 'object'
  OR policy->'new_authority' IS DISTINCT FROM 'false'::jsonb
  OR jsonb_typeof(g) IS DISTINCT FROM 'object'
  OR g->>'schema_version' IS DISTINCT FROM 'ctcc.original_v5_v6_v7.balance_measurement.v1'
  OR g->>'environment' IS DISTINCT FROM NEW.environment OR g->>'account_id' IS DISTINCT FROM NEW.account_id
  OR g->>'settlement_currency' IS DISTINCT FROM NEW.settlement_currency
  OR g->'native_clock_issued' IS DISTINCT FROM 'false'::jsonb
  OR g->'current_owner_issued' IS DISTINCT FROM 'false'::jsonb
  OR (g->>'body_completed_at')::timestamptz IS NULL
  OR NOT isfinite((g->>'body_completed_at')::timestamptz)
  OR (g->>'body_completed_at')::timestamptz>sampled
  OR (policy->'policy'->>'drawdown_window_started_at')::timestamptz
    IS DISTINCT FROM (g->>'body_completed_at')::timestamptz THEN
  RAISE EXCEPTION 'policy_counter_binding_denied';
 END IF;
 IF EXISTS(SELECT 1 FROM public.qualification_reservations
  WHERE environment=NEW.environment AND account_id=NEW.account_id AND state<>'reconciled_flat') THEN
  RAISE EXCEPTION 'policy_counter_local_exposure_denied';
 END IF;
 -- SQL assigns the prospective counter origin; caller timestamps are never
 -- adopted. This does not reset any financial history, loss window or HWM.
 NEW.initialized_at := clock_timestamp();
 NEW.db_recorded_at := NEW.initialized_at;
 IF NEW.initialized_at IS NULL OR NOT isfinite(NEW.initialized_at)
  OR NEW.initialized_at<sampled OR NEW.initialized_at>=expires THEN
  RAISE EXCEPTION 'policy_counter_expired';
 END IF;
 RETURN NEW;
EXCEPTION WHEN others THEN
 RAISE EXCEPTION 'policy_counter_write_denied';
END;
$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;
"""

COUNTER_COMMIT_SQL = """CREATE FUNCTION public.policy_counter_initialization_commit_guard_v1() RETURNS trigger AS $ctcc$
DECLARE c RECORD; j RECORD; a RECORD;
BEGIN
 SELECT * INTO c FROM public.demo_account_controls
  WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
 SELECT * INTO j FROM public.demo_control_journal
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND control_revision=NEW.control_revision FOR KEY SHARE;
 SELECT * INTO a FROM public.qualification_account_scopes
  WHERE environment=NEW.environment AND account_id=NEW.account_id
   AND settlement_currency=NEW.settlement_currency FOR UPDATE;
 IF ROW(c.control_revision,c.state_sha256,j.event_sha256,j.state_sha256)
   IS DISTINCT FROM ROW(NEW.control_revision,NEW.control_state_sha256,NEW.control_event_sha256,NEW.control_state_sha256)
  OR NOT c.emergency_stop OR c.owner_epoch<=0 OR c.arm_request_id IS NOT NULL OR c.arm_expires_at IS NOT NULL
  OR ROW(a.account_revision,a.ledger_revision,a.claims_sha256)
   IS DISTINCT FROM ROW(NEW.account_revision,NEW.ledger_revision,NEW.claims_sha256)
  OR NEW.ack_json::jsonb->>'db_transaction_id' IS DISTINCT FROM pg_current_xact_id()::text
  OR NEW.ack_json::jsonb->'db_backend_pid' IS DISTINCT FROM to_jsonb(pg_backend_pid())
  OR current_setting('role')<>'none' OR current_setting('transaction_read_only')<>'off'
  OR current_setting('synchronous_commit')<>'on'
  OR current_setting('transaction_isolation')<>'read committed'
  OR clock_timestamp()>=NEW.original_expires_at
  OR EXISTS(SELECT 1 FROM public.qualification_reservations
   WHERE environment=NEW.environment AND account_id=NEW.account_id AND state<>'reconciled_flat') THEN
  RAISE EXCEPTION 'policy_counter_commit_binding_denied';
 END IF;
 RETURN NEW;
END;
$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;
"""

IMMUTABLE_SQL = """CREATE FUNCTION public.control_operator_ack_immutable_v55() RETURNS trigger AS $ctcc$
BEGIN
 RAISE EXCEPTION 'control_operator_ack_immutable';
END;
$ctcc$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
"""

STATE_GUARD_SQL = """CREATE OR REPLACE FUNCTION public.demo_control_state_guard() RETURNS trigger AS $$
        DECLARE j JSONB;
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'demo_control_delete_denied'; END IF;
          j := NEW.state_json::jsonb;
          IF j->>'version' IS DISTINCT FROM 'ctcc.demo_control.v1'
             OR j->>'environment' IS DISTINCT FROM NEW.environment
             OR j->>'account_id' IS DISTINCT FROM NEW.account_id
             OR (j->>'revision')::bigint IS DISTINCT FROM NEW.control_revision
             OR j->>'owner_sha256' IS DISTINCT FROM NEW.owner_sha256
             OR (j->>'owner_epoch')::bigint IS DISTINCT FROM NEW.owner_epoch
             OR (j->>'lease_until')::timestamptz IS DISTINCT FROM NEW.lease_until
             OR j->'emergency_stop' IS DISTINCT FROM to_jsonb(NEW.emergency_stop)
             OR j->>'arm_request_id' IS DISTINCT FROM NEW.arm_request_id
             OR (j->>'arm_expires_at')::timestamptz IS DISTINCT FROM NEW.arm_expires_at
             OR (j->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
             OR (j->>'updated_at')::timestamptz IS DISTINCT FROM NEW.updated_at
             OR (NEW.owner_epoch=0 AND ROW(j->>'config_sha256',j->>'policy_sha256',j->>'credential_session_sha256') IS DISTINCT FROM ROW(NULL::text,NULL::text,NULL::text))
             OR (NEW.owner_epoch>0 AND (NOT COALESCE(j->>'config_sha256' ~ '^[a-f0-9]{64}$',false)
                OR NOT COALESCE(j->>'policy_sha256' ~ '^[a-f0-9]{64}$',false)
                OR NOT COALESCE(j->>'credential_session_sha256' ~ '^[a-f0-9]{64}$',false)))
             OR encode(sha256(convert_to(NEW.state_json,'UTF8')),'hex') IS DISTINCT FROM NEW.state_sha256
             OR NEW.lease_until > NEW.updated_at + INTERVAL '30 seconds' THEN
            RAISE EXCEPTION 'demo_control_state_binding_denied';
          END IF;
          IF TG_OP = 'INSERT' THEN
            IF NEW.control_revision <> 1 OR NEW.owner_epoch NOT IN (0,1) OR NEW.arm_request_id IS NOT NULL THEN
              RAISE EXCEPTION 'demo_control_initial_state_denied';
            END IF;
          ELSE
            IF ROW(NEW.environment,NEW.account_id,NEW.created_at) IS DISTINCT FROM ROW(OLD.environment,OLD.account_id,OLD.created_at)
               OR NEW.control_revision <> OLD.control_revision+1
               OR NEW.updated_at < OLD.updated_at
               OR (OLD.emergency_stop AND NOT NEW.emergency_stop AND NOT EXISTS (
                 SELECT 1 FROM public.demo_control_clear_acknowledgements_v2 ack
                 WHERE ack.environment=NEW.environment AND ack.account_id=NEW.account_id
                  AND ack.stopped_control_revision=OLD.control_revision
                  AND ack.stopped_state_sha256=OLD.state_sha256
                  AND ack.clear_control_revision=NEW.control_revision
                  AND ack.clear_state_sha256=NEW.state_sha256 AND ack.state_json=NEW.state_json
                  AND ack.owner_sha256=OLD.owner_sha256 AND ack.owner_epoch=OLD.owner_epoch
                  AND ack.original_lease_until=OLD.lease_until AND NEW.lease_until=OLD.lease_until
                  AND NEW.arm_request_id IS NULL AND NEW.arm_expires_at IS NULL
                  AND ack.owner_sampled_at=NEW.updated_at
                  AND ack.ack_json::jsonb->>'db_transaction_id'=pg_current_xact_id()::text
                  AND ack.ack_json::jsonb->'db_backend_pid'=to_jsonb(pg_backend_pid())
                  AND clock_timestamp()<ack.original_expires_at)) THEN
              RAISE EXCEPTION 'demo_control_transition_denied';
            END IF;
            IF NEW.owner_sha256 IS DISTINCT FROM OLD.owner_sha256 THEN
              IF NEW.owner_epoch <> OLD.owner_epoch+1 OR NEW.updated_at < OLD.lease_until OR NEW.arm_request_id IS NOT NULL THEN
                RAISE EXCEPTION 'demo_control_takeover_denied';
              END IF;
            ELSIF NEW.owner_epoch <> OLD.owner_epoch THEN
              RAISE EXCEPTION 'demo_control_epoch_denied';
            END IF;
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
"""

JOURNAL_GUARD_SQL = """CREATE OR REPLACE FUNCTION public.demo_control_journal_guard() RETURNS trigger AS $$
        DECLARE c RECORD; prior RECORD; old_state JSONB; current_state JSONB;
        BEGIN
          SELECT * INTO c FROM demo_account_controls WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
          SELECT * INTO prior FROM demo_control_journal WHERE environment=NEW.environment AND account_id=NEW.account_id ORDER BY control_revision DESC LIMIT 1;
          current_state := c.state_json::jsonb;
          old_state := prior.event_json::jsonb->'state';
          IF c.control_revision IS DISTINCT FROM NEW.control_revision
             OR c.state_sha256 IS DISTINCT FROM NEW.state_sha256
             OR c.updated_at IS DISTINCT FROM NEW.occurred_at
             OR NEW.control_revision <> COALESCE(prior.control_revision,0)+1
             OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256
             OR encode(sha256(convert_to(NEW.event_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
             OR NEW.event_json::jsonb IS DISTINCT FROM (CASE WHEN NEW.action='clear_stop' THEN
                jsonb_build_object('version','ctcc.demo_control_event.v2','command_id',NEW.command_id,
                  'action',NEW.action,'previous_sha256',NEW.previous_sha256,'state',current_state,
                  'clear_ack_sha256',(SELECT ack.ack_sha256 FROM public.demo_control_clear_acknowledgements_v2 ack
                    WHERE ack.environment=NEW.environment AND ack.account_id=NEW.account_id
                     AND ack.clear_control_revision=NEW.control_revision AND ack.command_id=NEW.command_id))
              ELSE jsonb_build_object(
                'version','ctcc.demo_control_event.v1','command_id',NEW.command_id,
                'action',NEW.action,'previous_sha256',NEW.previous_sha256,'state',current_state) END) THEN
            RAISE EXCEPTION 'demo_control_journal_binding_denied';
          END IF;
          IF NEW.action='clear_stop' AND NOT EXISTS (
            SELECT 1 FROM public.demo_control_clear_acknowledgements_v2 ack
            WHERE ack.environment=NEW.environment AND ack.account_id=NEW.account_id
              AND ack.command_id=NEW.command_id AND ack.clear_control_revision=NEW.control_revision
              AND ack.stopped_control_revision=prior.control_revision
              AND ack.stopped_event_sha256=prior.event_sha256
              AND ack.stopped_state_sha256=prior.state_sha256
              AND ack.clear_state_sha256=c.state_sha256 AND ack.state_json=c.state_json
              AND ack.clear_event_sha256=NEW.event_sha256 AND ack.event_json=NEW.event_json
              AND (old_state->>'emergency_stop')::boolean AND NOT c.emergency_stop
              AND c.arm_request_id IS NULL AND c.arm_expires_at IS NULL
              AND c.lease_until=(old_state->>'lease_until')::timestamptz
              AND ack.ack_json::jsonb->>'db_transaction_id'=pg_current_xact_id()::text
              AND ack.ack_json::jsonb->'db_backend_pid'=to_jsonb(pg_backend_pid())
              AND clock_timestamp()<ack.original_expires_at) THEN
            RAISE EXCEPTION 'demo_control_clear_ack_required';
          END IF;
          IF NEW.action='acquire' THEN
            IF c.arm_request_id IS NOT NULL OR (prior.control_revision IS NULL AND c.owner_epoch<>1) OR (prior.control_revision IS NOT NULL AND
               (c.owner_sha256 IS NOT DISTINCT FROM old_state->>'owner_sha256' OR c.owner_epoch <> (old_state->>'owner_epoch')::bigint+1)) THEN
              RAISE EXCEPTION 'demo_control_acquire_denied';
            END IF;
          ELSIF prior.control_revision IS NULL THEN
            IF NEW.action<>'estop' OR c.owner_epoch<>0 OR NOT c.emergency_stop THEN
              RAISE EXCEPTION 'demo_control_stopped_genesis_required';
            END IF;
          ELSIF c.owner_sha256 IS DISTINCT FROM old_state->>'owner_sha256'
             OR c.owner_epoch IS DISTINCT FROM (old_state->>'owner_epoch')::bigint THEN
            RAISE EXCEPTION 'demo_control_owner_binding_denied';
          END IF;
          IF NEW.action IN ('arm_requested','rebind','release','renew') AND NEW.occurred_at >= (old_state->>'lease_until')::timestamptz THEN
            RAISE EXCEPTION 'demo_control_expired_owner_denied';
          END IF;
          IF NEW.action IN ('arm_requested','rebind') AND c.lease_until IS DISTINCT FROM (old_state->>'lease_until')::timestamptz THEN
            RAISE EXCEPTION 'demo_control_lease_change_denied';
          END IF;
          IF NEW.action='renew' AND c.lease_until IS DISTINCT FROM NEW.occurred_at + INTERVAL '30 seconds' THEN
            RAISE EXCEPTION 'demo_control_renew_lease_denied';
          END IF;
          IF NEW.action IN ('disarm','estop') AND c.lease_until IS DISTINCT FROM GREATEST(NEW.occurred_at,(old_state->>'lease_until')::timestamptz) THEN
            RAISE EXCEPTION 'demo_control_tightening_lease_denied';
          END IF;
          IF NEW.action='arm_requested' THEN
            IF c.arm_request_id IS DISTINCT FROM NEW.command_id OR c.arm_expires_at <= NEW.occurred_at
               OR c.arm_expires_at > NEW.occurred_at + INTERVAL '30 seconds'
               OR (old_state->>'arm_expires_at')::timestamptz > NEW.occurred_at THEN
              RAISE EXCEPTION 'demo_control_arm_denied';
            END IF;
          ELSIF NEW.action IN ('acquire','disarm','estop','rebind','release') AND c.arm_request_id IS NOT NULL THEN
            RAISE EXCEPTION 'demo_control_disarm_required';
          ELSIF NEW.action='renew' AND c.arm_request_id IS NOT NULL AND
              (c.arm_request_id IS DISTINCT FROM old_state->>'arm_request_id'
               OR c.arm_expires_at IS DISTINCT FROM (old_state->>'arm_expires_at')::timestamptz) THEN
            RAISE EXCEPTION 'demo_control_renew_cannot_arm';
          END IF;
          IF NEW.action='estop' AND NOT c.emergency_stop THEN
            RAISE EXCEPTION 'demo_control_stop_required';
          END IF;
          IF NEW.action='release' AND c.lease_until IS DISTINCT FROM NEW.occurred_at THEN
            RAISE EXCEPTION 'demo_control_release_required';
          END IF;
          IF NEW.action NOT IN ('acquire','rebind') AND
             ROW(current_state->>'config_sha256',current_state->>'policy_sha256',current_state->>'credential_session_sha256') IS DISTINCT FROM
             ROW(old_state->>'config_sha256',old_state->>'policy_sha256',old_state->>'credential_session_sha256') THEN
            RAISE EXCEPTION 'demo_control_rebind_required';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
"""

ISSUER_GUARD_SQL = """CREATE OR REPLACE FUNCTION public.collector_issued_account_revision_guard_v1()
RETURNS trigger AS $ctcc$
DECLARE a RECORD; c RECORD; cj RECORD; l RECORD; b RECORD; r RECORD;
        p jsonb; claims jsonb; hw jsonb; sampled timestamptz;
        lineage_expires timestamptz; hwm_expires timestamptz;
        lock_key bigint; n bigint;
BEGIN
  IF current_setting('role')<>'none' THEN
    RAISE EXCEPTION 'collector_issuer_role_denied';
  END IF;
  SELECT oid,rolsuper,rolcreaterole,rolbypassrls INTO r
    FROM pg_catalog.pg_roles WHERE rolname=session_user;
  IF r.oid IS NULL OR r.rolsuper OR r.rolcreaterole OR r.rolbypassrls THEN
    RAISE EXCEPTION 'collector_issuer_role_denied';
  END IF;
  lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')
    ||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')
    ||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
  PERFORM pg_advisory_xact_lock(lock_key);
  SELECT * INTO c FROM public.demo_account_controls
    WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
  SELECT * INTO a FROM public.qualification_account_scopes
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND settlement_currency=NEW.settlement_currency FOR UPDATE;
  SELECT * INTO cj FROM public.demo_control_journal
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND control_revision=NEW.control_revision FOR KEY SHARE;
  SELECT * INTO l FROM public.demo_collector_current_revision_lineages
    WHERE lineage_sha256=NEW.source_lineage_sha256 FOR KEY SHARE;
  SELECT * INTO b FROM public.public_runtime_raw_bundles_v1
    WHERE bundle_id=NEW.hwm_custody_id FOR KEY SHARE;
  p := NEW.proof_json::jsonb;
  claims := NEW.claims_json::jsonb;
  hw := convert_from(b.bundle_raw,'UTF8')::jsonb;
  sampled := (p->>'owner_sampled_at')::timestamptz;
  lineage_expires := (l.lineage_json::jsonb->>'original_expires_at')::timestamptz;
  hwm_expires := (hw->'observed_source'->>'original_request_expires_at')::timestamptz;
  IF a.account_revision IS NULL OR c.control_revision IS NULL
    OR cj.control_revision IS NULL OR l.lineage_sha256 IS NULL OR b.bundle_id IS NULL
    OR ROW(a.account_revision,a.ledger_revision,a.claims_sha256)
       IS DISTINCT FROM ROW(NEW.previous_account_revision,NEW.previous_ledger_revision,NEW.previous_claims_sha256)
    OR ROW(l.environment,l.account_id,l.settlement_currency,l.account_revision,l.ledger_revision,l.claims_sha256)
       IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency,
         NEW.previous_account_revision,NEW.previous_ledger_revision,NEW.previous_claims_sha256)
    OR ROW(l.control_revision,l.control_event_sha256,l.control_state_sha256)
       IS DISTINCT FROM ROW(NEW.control_revision,NEW.control_event_sha256,NEW.control_state_sha256)
    OR ROW(c.control_revision,c.state_sha256,cj.state_sha256,cj.event_sha256)
       IS DISTINCT FROM ROW(NEW.control_revision,NEW.control_state_sha256,
         NEW.control_state_sha256,NEW.control_event_sha256)
    OR encode(sha256(convert_to(c.state_json,'UTF8')),'hex') IS DISTINCT FROM c.state_sha256
    OR encode(sha256(convert_to(cj.event_json,'UTF8')),'hex') IS DISTINCT FROM cj.event_sha256
    OR encode(sha256(convert_to(l.lineage_json,'UTF8')),'hex') IS DISTINCT FROM l.lineage_sha256
    OR encode(sha256(b.bundle_raw),'hex') IS DISTINCT FROM b.bundle_sha256
    OR b.bundle_sha256 IS DISTINCT FROM NEW.hwm_bundle_sha256
    OR encode(sha256(convert_to(NEW.proof_json,'UTF8')),'hex') IS DISTINCT FROM NEW.issuer_sha256
    OR encode(sha256(convert_to(NEW.claims_json,'UTF8')),'hex') IS DISTINCT FROM NEW.claims_sha256
    OR p->>'schema_version' IS DISTINCT FROM 'ctcc.collector_issued_account_revision.v1'
    OR p->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
    OR p->>'claims_sha256' IS DISTINCT FROM NEW.claims_sha256
    OR p->>'source_lineage_sha256' IS DISTINCT FROM NEW.source_lineage_sha256
    OR p->>'hwm_custody_id' IS DISTINCT FROM b.bundle_id
    OR p->>'hwm_bundle_sha256' IS DISTINCT FROM b.bundle_sha256
    OR p->'previous_account_revision' IS DISTINCT FROM to_jsonb(NEW.previous_account_revision)
    OR p->'previous_ledger_revision' IS DISTINCT FROM to_jsonb(NEW.previous_ledger_revision)
    OR p->'account_revision' IS DISTINCT FROM to_jsonb(NEW.account_revision)
    OR p->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
    OR p->'control_revision' IS DISTINCT FROM to_jsonb(NEW.control_revision)
    OR p->>'control_event_sha256' IS DISTINCT FROM NEW.control_event_sha256
    OR p->>'control_state_sha256' IS DISTINCT FROM NEW.control_state_sha256
    OR p->'db_role_oid' IS DISTINCT FROM to_jsonb(r.oid::bigint)
    OR p->'db_backend_pid' IS DISTINCT FROM to_jsonb(pg_backend_pid())
    OR p->>'db_transaction_id' IS DISTINCT FROM pg_current_xact_id()::text
    OR (p->>'original_expires_at')::timestamptz IS DISTINCT FROM NEW.original_expires_at
    OR lineage_expires IS NULL OR NOT isfinite(lineage_expires)
    OR hwm_expires IS NULL OR NOT isfinite(hwm_expires)
    OR NOT isfinite(NEW.original_expires_at)
    OR NEW.original_expires_at > lineage_expires
    OR NEW.original_expires_at > hwm_expires
    OR sampled IS NULL OR NOT isfinite(sampled) OR sampled>=NEW.original_expires_at
    OR clock_timestamp()>=NEW.original_expires_at
    OR b.recorded_at>sampled OR l.db_recorded_at>sampled
    OR (hw->>'schema_version' IS DISTINCT FROM 'ctcc.native_owned_sampled_balance_peak_record.v1'
      AND hw->>'schema_version' IS DISTINCT FROM 'ctcc.native_owned_controlled_balance_peak_record.v2')
    OR hw->'observed_source'->'scope' IS DISTINCT FROM p->'scope'
    OR hw->'observed_source'->'current_source_reference'
       IS DISTINCT FROM l.lineage_json::jsonb->'source_reference'
    OR hw->'observed_source'->>'current_proof_envelope_sha256'
       IS DISTINCT FROM l.lineage_json::jsonb->>'proof_envelope_sha256'
    OR hw->'historical_account_peak' IS DISTINCT FROM 'null'::jsonb
    OR hw->'account_revision_issued' IS DISTINCT FROM 'false'::jsonb
    OR hw->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR claims->>'schema_version' IS DISTINCT FROM 'ctcc.account_ledger_claims.v2'
    OR claims->'scope' IS DISTINCT FROM p->'scope'
    OR p->'execution_authority' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'collector_issuer_binding_denied';
  END IF;
  -- Historical v1 checks above remain exact. Only the explicit controlled v2
  -- schema enters this additional finite measured-policy/source binding path.
  IF hw->>'schema_version'='ctcc.native_owned_controlled_balance_peak_record.v2' THEN
    IF jsonb_typeof(hw) IS DISTINCT FROM 'object'
      OR (SELECT count(*) FROM jsonb_object_keys(hw))<>11
      OR hw->>'policy_sha256' IS DISTINCT FROM 'f82775c69bef1e9c70ec4607b1497ea7b3264817970879c825ff76d18b78b047'
      OR hw->'initial_account_peak' IS DISTINCT FROM 'null'::jsonb
      OR hw->'external_cashflow_adjustment' IS DISTINCT FROM 'null'::jsonb
      OR hw->'cashflow_adjusted_drawdown' IS DISTINCT FROM 'null'::jsonb
      OR hw->'account_complete' IS DISTINCT FROM 'false'::jsonb
      OR octet_length(b.bundle_raw) NOT BETWEEN 1 AND 2097152
      OR jsonb_typeof(hw->'owner_sampled_at') IS DISTINCT FROM 'string'
      OR (hw->>'owner_sampled_at')::timestamptz IS NULL
      OR NOT isfinite((hw->>'owner_sampled_at')::timestamptz)
      OR NOT isfinite(b.recorded_at)
      OR (hw->>'owner_sampled_at')::timestamptz>b.recorded_at
      OR jsonb_typeof(hw->'observed_source') IS DISTINCT FROM 'object'
      OR (SELECT count(*) FROM jsonb_object_keys(hw->'observed_source'))<>17
      OR hw->'observed_source'->>'current_balance_basis' IS DISTINCT FROM 'latest_measured_original_index_member'
      OR claims->'claims'->>'payload_schema' IS DISTINCT FROM 'ctcc.account_ledger_claims.finite_api_asof.v1'
      OR claims->'claims'->'financial'->>'financial_schema' IS DISTINCT FROM 'ctcc.account_financial_operands.finite_api_asof.v1'
      OR claims->'claims'->'account'->'original_start_seed' IS DISTINCT FROM 'null'::jsonb
      OR claims->'claims'->'financial'->'original_start_seed' IS DISTINCT FROM 'null'::jsonb
      OR claims->'claims'->'financial'->'actual_continuous_peak' IS DISTINCT FROM 'null'::jsonb
      OR claims->'claims'->'financial'->'cashflow_adjusted_peak' IS DISTINCT FROM 'null'::jsonb
      OR hw->'observed_source'->'current_source_reference'
        IS DISTINCT FROM claims->'claims'->'financial'->'current_source_reference'
      OR hw->'observed_source'->>'original_prefix_sha256'
        IS DISTINCT FROM claims->'claims'->'financial'->>'original_prefix_sha256'
      OR hw->'observed_source'->>'original_raw_bank_sha256'
        IS DISTINCT FROM claims->'claims'->'financial'->>'original_raw_bank_sha256'
      OR hw->'observed_source'->>'api_population_receipt_sha256'
        IS DISTINCT FROM claims->'claims'->'financial'->>'api_population_receipt_sha256'
      OR hw->'observed_source'->>'measured_hwm_policy_sha256'
        IS DISTINCT FROM claims->'claims'->'financial'->>'measured_hwm_policy_sha256'
      OR jsonb_typeof(hw->'observed_source'->'measured_hwm_receipt_json') IS DISTINCT FROM 'string'
      OR octet_length(hw->'observed_source'->>'measured_hwm_receipt_json') NOT BETWEEN 1 AND 2097152
      OR encode(sha256(convert_to(hw->'observed_source'->>'measured_hwm_receipt_json','UTF8')),'hex')
        IS DISTINCT FROM hw->'observed_source'->>'measured_hwm_receipt_sha256'
      OR hw->'observed_source'->>'measured_hwm_receipt_sha256'
        IS DISTINCT FROM claims->'claims'->'financial'->>'measured_hwm_receipt_sha256'
      OR jsonb_typeof(hw->'observed_source'->'financial_source_receipt_json') IS DISTINCT FROM 'string'
      OR octet_length(hw->'observed_source'->>'financial_source_receipt_json') NOT BETWEEN 1 AND 2097152
      OR encode(sha256(convert_to(hw->'observed_source'->>'financial_source_receipt_json','UTF8')),'hex')
        IS DISTINCT FROM hw->'observed_source'->>'financial_source_receipt_sha256'
      OR hw->'observed_source'->>'financial_source_receipt_sha256'
        IS DISTINCT FROM claims->'claims'->'financial'->>'source_receipt_sha256'
      OR jsonb_typeof(hw->'observed_source'->'measured_peak_equity') IS DISTINCT FROM 'object'
      OR jsonb_typeof(hw->'observed_source'->'measured_current_equity') IS DISTINCT FROM 'object'
      OR hw->'observed_source'->'measured_peak_equity'
        IS DISTINCT FROM claims->'claims'->'financial'->'measured_peak_equity'
      OR hw->'observed_source'->'measured_current_equity'
        IS DISTINCT FROM claims->'claims'->'financial'->'measured_current_equity'
      OR jsonb_typeof(hw->'observed_source'->'measured_peak_observed_at') IS DISTINCT FROM 'string'
      OR (hw->'observed_source'->>'measured_peak_observed_at')::timestamptz IS NULL
      OR NOT isfinite((hw->'observed_source'->>'measured_peak_observed_at')::timestamptz)
      OR (hw->'observed_source'->>'measured_peak_observed_at')::timestamptz
        IS DISTINCT FROM (claims->'claims'->'financial'->>'measured_peak_observed_at')::timestamptz
      OR jsonb_typeof(hw->'observed_source'->'drawdown_window_started_at') IS DISTINCT FROM 'string'
      OR (hw->'observed_source'->>'drawdown_window_started_at')::timestamptz IS NULL
      OR NOT isfinite((hw->'observed_source'->>'drawdown_window_started_at')::timestamptz)
      OR (hw->'observed_source'->>'drawdown_window_started_at')::timestamptz
        IS DISTINCT FROM (claims->'claims'->'financial'->>'measured_hwm_window_started_at')::timestamptz
      OR (hw->'observed_source'->>'drawdown_window_started_at')::timestamptz
        IS DISTINCT FROM (claims->'claims'->'risk_policy'->>'drawdown_window_started_at')::timestamptz
      OR (hw->'observed_source'->>'drawdown_window_started_at')::timestamptz
        >(hw->'observed_source'->>'measured_peak_observed_at')::timestamptz
      OR (hw->'observed_source'->>'measured_peak_observed_at')::timestamptz
        >(hw->>'owner_sampled_at')::timestamptz
      OR (claims->'claims'->'financial'->>'evaluated_at')::timestamptz IS NULL
      OR NOT isfinite((claims->'claims'->'financial'->>'evaluated_at')::timestamptz)
      OR (claims->'claims'->'financial'->>'evaluated_at')::timestamptz>(hw->>'owner_sampled_at')::timestamptz
      OR (claims->'claims'->'financial'->>'original_expires_at')::timestamptz IS DISTINCT FROM hwm_expires
      OR claims->'claims'->'financial'->>'drawdown_basis'
        IS DISTINCT FROM 'every_accepted_measured_balance_in_original_window' THEN
      RAISE EXCEPTION 'collector_issuer_controlled_hwm_binding_denied';
    END IF;
    -- A new prospective counter is a durable one-time policy basis, not an
    -- assertion that pre-origin/global history or original seed was zero.
    IF claims->'claims'->'financial'->>'loss_streak_basis'='explicit_operator_policy_counter' THEN
      IF NOT EXISTS(SELECT 1 FROM public.demo_policy_counter_initializations_v1 init
        WHERE init.initialization_sha256=claims->'claims'->'financial'->>'init_ack_sha256'
          AND ROW(init.environment,init.account_id,init.settlement_currency)
            =ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          AND init.initialized_at=(claims->'claims'->'financial'->>'init_at')::timestamptz
          AND init.initialized_at=init.db_recorded_at
          AND init.initialized_at<=(claims->'claims'->'financial'->>'source_as_of_at')::timestamptz
          AND init.configured_policy_sha256=
            (hw->'observed_source'->>'measured_hwm_receipt_json')::jsonb->>'configured_policy_sha256'
          AND init.measured_genesis_sha256=
            (hw->'observed_source'->>'measured_hwm_receipt_json')::jsonb->>'measured_genesis_sha256'
          AND init.measured_genesis_json=
            (hw->'observed_source'->>'measured_hwm_receipt_json')::jsonb->>'measured_genesis_json'
          AND encode(sha256(convert_to(init.configured_policy_json,'UTF8')),'hex')=init.configured_policy_sha256
          AND encode(sha256(convert_to(init.measured_genesis_json,'UTF8')),'hex')=init.measured_genesis_sha256
          AND (init.measured_genesis_json::jsonb->>'body_completed_at')::timestamptz
            =(claims->'claims'->'financial'->>'measured_hwm_window_started_at')::timestamptz
          AND init.configured_policy_json::jsonb->>'portfolio_risk_policy_sha256'
            =claims->'claims'->'financial'->>'original_risk_policy_sha256') THEN
        RAISE EXCEPTION 'collector_issuer_policy_counter_binding_denied';
      END IF;
    ELSIF claims->'claims'->'financial'->>'loss_streak_basis' IS DISTINCT FROM 'observed_positive_reset'
      OR claims->'claims'->'financial'->'init_ack_sha256' IS DISTINCT FROM 'null'::jsonb
      OR claims->'claims'->'financial'->'init_at' IS DISTINCT FROM 'null'::jsonb THEN
      RAISE EXCEPTION 'collector_issuer_streak_basis_unknown';
    END IF;
  END IF;
  -- The native issuer verifies the exact proof bytes and all coverage operands;
  -- these SQL shape checks confer no authenticity on a manually supplied row.
  IF jsonb_typeof(p->'proofs') IS DISTINCT FROM 'object'
    OR (SELECT count(*) FROM jsonb_object_keys(p->'proofs'))<>12
    OR NOT (p->'proofs' ?& ARRAY['registration','current_identity','history_prefix',
      'product_scope','history_coverage','cashflow_and_funding_finality','loss_streak_seed',
      'unchanged_window_drawdown','inventory_metadata','protection','local_ledger','current_portfolio'])
    OR EXISTS(SELECT 1 FROM jsonb_each(p->'proofs') e WHERE jsonb_typeof(e.value)<>'string'
      OR e.value#>>'{}' !~ '^[a-f0-9]{64}$') THEN
    RAISE EXCEPTION 'collector_issuer_proof_membership_denied';
  END IF;
  UPDATE public.qualification_account_scopes
    SET account_revision=NEW.account_revision,ledger_revision=NEW.ledger_revision,
        claims_json=NEW.claims_json,claims_sha256=NEW.claims_sha256,
        updated_at=clock_timestamp()
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND settlement_currency=NEW.settlement_currency
      AND account_revision=NEW.previous_account_revision
      AND ledger_revision=NEW.previous_ledger_revision
      AND claims_sha256 IS NOT DISTINCT FROM NEW.previous_claims_sha256;
  GET DIAGNOSTICS n=ROW_COUNT;
  IF n<>1 THEN
    RAISE EXCEPTION 'collector_issuer_revision_conflict';
  END IF;
  NEW.db_recorded_at := clock_timestamp();
  IF NEW.db_recorded_at IS NULL OR NOT isfinite(NEW.db_recorded_at) OR NEW.db_recorded_at<sampled OR NEW.db_recorded_at>=NEW.original_expires_at THEN
    RAISE EXCEPTION 'collector_issuer_final_expiry';
  END IF;
  RETURN NEW;
EXCEPTION WHEN others THEN
  RAISE EXCEPTION 'collector_issuer_write_denied';
END;
$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;
"""

TAIL_STATEMENTS = (
    """ALTER TABLE public.demo_control_journal DROP CONSTRAINT ck_demo_control_journal_action;
""",
    """ALTER TABLE public.demo_control_journal ADD CONSTRAINT ck_demo_control_journal_action
 CHECK(action IN ('acquire','renew','arm_requested','disarm','estop','rebind','release','clear_stop'));
""",
    """CREATE TRIGGER control_clear_ack_insert BEFORE INSERT ON public.demo_control_clear_acknowledgements_v2
 FOR EACH ROW EXECUTE FUNCTION public.control_clear_ack_guard_v2();
""",
    """CREATE CONSTRAINT TRIGGER control_clear_ack_commit AFTER INSERT ON public.demo_control_clear_acknowledgements_v2
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.control_clear_ack_commit_guard_v2();
""",
    """CREATE TRIGGER control_clear_ack_immutable BEFORE UPDATE OR DELETE ON public.demo_control_clear_acknowledgements_v2
 FOR EACH ROW EXECUTE FUNCTION public.control_operator_ack_immutable_v55();
""",
    """CREATE TRIGGER control_clear_ack_no_truncate BEFORE TRUNCATE ON public.demo_control_clear_acknowledgements_v2
 FOR EACH STATEMENT EXECUTE FUNCTION public.control_operator_ack_immutable_v55();
""",
    """REVOKE ALL ON public.demo_control_clear_acknowledgements_v2 FROM PUBLIC;
""",
    """REVOKE ALL ON FUNCTION public.control_clear_ack_guard_v2() FROM PUBLIC;
""",
    """REVOKE ALL ON FUNCTION public.control_clear_ack_commit_guard_v2() FROM PUBLIC;
""",
    """REVOKE ALL ON FUNCTION public.control_operator_ack_immutable_v55() FROM PUBLIC;
""",
    """CREATE TRIGGER policy_counter_initialization_insert BEFORE INSERT ON public.demo_policy_counter_initializations_v1
 FOR EACH ROW EXECUTE FUNCTION public.policy_counter_initialization_guard_v1();
""",
    """CREATE CONSTRAINT TRIGGER policy_counter_initialization_commit AFTER INSERT ON public.demo_policy_counter_initializations_v1
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.policy_counter_initialization_commit_guard_v1();
""",
    """CREATE TRIGGER policy_counter_initialization_immutable BEFORE UPDATE OR DELETE ON public.demo_policy_counter_initializations_v1
 FOR EACH ROW EXECUTE FUNCTION public.control_operator_ack_immutable_v55();
""",
    """CREATE TRIGGER policy_counter_initialization_no_truncate BEFORE TRUNCATE ON public.demo_policy_counter_initializations_v1
 FOR EACH STATEMENT EXECUTE FUNCTION public.control_operator_ack_immutable_v55();
""",
    """REVOKE ALL ON public.demo_policy_counter_initializations_v1 FROM PUBLIC;
""",
    """REVOKE ALL ON FUNCTION public.policy_counter_initialization_guard_v1() FROM PUBLIC;
""",
    """REVOKE ALL ON FUNCTION public.policy_counter_initialization_commit_guard_v1() FROM PUBLIC;
""",
)

RESTORE_0054_ISSUER_SQL = """CREATE OR REPLACE FUNCTION public.collector_issued_account_revision_guard_v1()
RETURNS trigger AS $ctcc$
DECLARE a RECORD; c RECORD; cj RECORD; l RECORD; b RECORD; r RECORD;
        p jsonb; claims jsonb; hw jsonb; sampled timestamptz;
        lineage_expires timestamptz; hwm_expires timestamptz;
        lock_key bigint; n bigint;
BEGIN
  IF current_setting('role')<>'none' THEN
    RAISE EXCEPTION 'collector_issuer_role_denied';
  END IF;
  SELECT oid,rolsuper,rolcreaterole,rolbypassrls INTO r
    FROM pg_catalog.pg_roles WHERE rolname=session_user;
  IF r.oid IS NULL OR r.rolsuper OR r.rolcreaterole OR r.rolbypassrls THEN
    RAISE EXCEPTION 'collector_issuer_role_denied';
  END IF;
  lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')
    ||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')
    ||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
  PERFORM pg_advisory_xact_lock(lock_key);
  SELECT * INTO c FROM public.demo_account_controls
    WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
  SELECT * INTO a FROM public.qualification_account_scopes
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND settlement_currency=NEW.settlement_currency FOR UPDATE;
  SELECT * INTO cj FROM public.demo_control_journal
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND control_revision=NEW.control_revision FOR KEY SHARE;
  SELECT * INTO l FROM public.demo_collector_current_revision_lineages
    WHERE lineage_sha256=NEW.source_lineage_sha256 FOR KEY SHARE;
  SELECT * INTO b FROM public.public_runtime_raw_bundles_v1
    WHERE bundle_id=NEW.hwm_custody_id FOR KEY SHARE;
  p := NEW.proof_json::jsonb;
  claims := NEW.claims_json::jsonb;
  hw := convert_from(b.bundle_raw,'UTF8')::jsonb;
  sampled := (p->>'owner_sampled_at')::timestamptz;
  lineage_expires := (l.lineage_json::jsonb->>'original_expires_at')::timestamptz;
  hwm_expires := (hw->'observed_source'->>'original_request_expires_at')::timestamptz;
  IF a.account_revision IS NULL OR c.control_revision IS NULL
    OR cj.control_revision IS NULL OR l.lineage_sha256 IS NULL OR b.bundle_id IS NULL
    OR ROW(a.account_revision,a.ledger_revision,a.claims_sha256)
       IS DISTINCT FROM ROW(NEW.previous_account_revision,NEW.previous_ledger_revision,NEW.previous_claims_sha256)
    OR ROW(l.environment,l.account_id,l.settlement_currency,l.account_revision,l.ledger_revision,l.claims_sha256)
       IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency,
         NEW.previous_account_revision,NEW.previous_ledger_revision,NEW.previous_claims_sha256)
    OR ROW(l.control_revision,l.control_event_sha256,l.control_state_sha256)
       IS DISTINCT FROM ROW(NEW.control_revision,NEW.control_event_sha256,NEW.control_state_sha256)
    OR ROW(c.control_revision,c.state_sha256,cj.state_sha256,cj.event_sha256)
       IS DISTINCT FROM ROW(NEW.control_revision,NEW.control_state_sha256,
         NEW.control_state_sha256,NEW.control_event_sha256)
    OR encode(sha256(convert_to(c.state_json,'UTF8')),'hex') IS DISTINCT FROM c.state_sha256
    OR encode(sha256(convert_to(cj.event_json,'UTF8')),'hex') IS DISTINCT FROM cj.event_sha256
    OR encode(sha256(convert_to(l.lineage_json,'UTF8')),'hex') IS DISTINCT FROM l.lineage_sha256
    OR encode(sha256(b.bundle_raw),'hex') IS DISTINCT FROM b.bundle_sha256
    OR b.bundle_sha256 IS DISTINCT FROM NEW.hwm_bundle_sha256
    OR encode(sha256(convert_to(NEW.proof_json,'UTF8')),'hex') IS DISTINCT FROM NEW.issuer_sha256
    OR encode(sha256(convert_to(NEW.claims_json,'UTF8')),'hex') IS DISTINCT FROM NEW.claims_sha256
    OR p->>'schema_version' IS DISTINCT FROM 'ctcc.collector_issued_account_revision.v1'
    OR p->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
    OR p->>'claims_sha256' IS DISTINCT FROM NEW.claims_sha256
    OR p->>'source_lineage_sha256' IS DISTINCT FROM NEW.source_lineage_sha256
    OR p->>'hwm_custody_id' IS DISTINCT FROM b.bundle_id
    OR p->>'hwm_bundle_sha256' IS DISTINCT FROM b.bundle_sha256
    OR p->'previous_account_revision' IS DISTINCT FROM to_jsonb(NEW.previous_account_revision)
    OR p->'previous_ledger_revision' IS DISTINCT FROM to_jsonb(NEW.previous_ledger_revision)
    OR p->'account_revision' IS DISTINCT FROM to_jsonb(NEW.account_revision)
    OR p->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
    OR p->'control_revision' IS DISTINCT FROM to_jsonb(NEW.control_revision)
    OR p->>'control_event_sha256' IS DISTINCT FROM NEW.control_event_sha256
    OR p->>'control_state_sha256' IS DISTINCT FROM NEW.control_state_sha256
    OR p->'db_role_oid' IS DISTINCT FROM to_jsonb(r.oid::bigint)
    OR p->'db_backend_pid' IS DISTINCT FROM to_jsonb(pg_backend_pid())
    OR p->>'db_transaction_id' IS DISTINCT FROM pg_current_xact_id()::text
    OR (p->>'original_expires_at')::timestamptz IS DISTINCT FROM NEW.original_expires_at
    OR lineage_expires IS NULL OR NOT isfinite(lineage_expires)
    OR hwm_expires IS NULL OR NOT isfinite(hwm_expires)
    OR NOT isfinite(NEW.original_expires_at)
    OR NEW.original_expires_at > lineage_expires
    OR NEW.original_expires_at > hwm_expires
    OR sampled IS NULL OR NOT isfinite(sampled) OR sampled>=NEW.original_expires_at
    OR clock_timestamp()>=NEW.original_expires_at
    OR b.recorded_at>sampled OR l.db_recorded_at>sampled
    OR hw->>'schema_version' IS DISTINCT FROM 'ctcc.native_owned_sampled_balance_peak_record.v1'
    OR hw->'observed_source'->'scope' IS DISTINCT FROM p->'scope'
    OR hw->'observed_source'->'current_source_reference'
       IS DISTINCT FROM l.lineage_json::jsonb->'source_reference'
    OR hw->'observed_source'->>'current_proof_envelope_sha256'
       IS DISTINCT FROM l.lineage_json::jsonb->>'proof_envelope_sha256'
    OR hw->'historical_account_peak' IS DISTINCT FROM 'null'::jsonb
    OR hw->'account_revision_issued' IS DISTINCT FROM 'false'::jsonb
    OR hw->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR claims->>'schema_version' IS DISTINCT FROM 'ctcc.account_ledger_claims.v2'
    OR claims->'scope' IS DISTINCT FROM p->'scope'
    OR p->'execution_authority' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'collector_issuer_binding_denied';
  END IF;
  -- The native issuer verifies the exact proof bytes and all coverage operands;
  -- these SQL shape checks confer no authenticity on a manually supplied row.
  IF jsonb_typeof(p->'proofs') IS DISTINCT FROM 'object'
    OR (SELECT count(*) FROM jsonb_object_keys(p->'proofs'))<>12
    OR NOT (p->'proofs' ?& ARRAY['registration','current_identity','history_prefix',
      'product_scope','history_coverage','cashflow_and_funding_finality','loss_streak_seed',
      'unchanged_window_drawdown','inventory_metadata','protection','local_ledger','current_portfolio'])
    OR EXISTS(SELECT 1 FROM jsonb_each(p->'proofs') e WHERE jsonb_typeof(e.value)<>'string'
      OR e.value#>>'{}' !~ '^[a-f0-9]{64}$') THEN
    RAISE EXCEPTION 'collector_issuer_proof_membership_denied';
  END IF;
  UPDATE public.qualification_account_scopes
    SET account_revision=NEW.account_revision,ledger_revision=NEW.ledger_revision,
        claims_json=NEW.claims_json,claims_sha256=NEW.claims_sha256,
        updated_at=clock_timestamp()
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND settlement_currency=NEW.settlement_currency
      AND account_revision=NEW.previous_account_revision
      AND ledger_revision=NEW.previous_ledger_revision
      AND claims_sha256 IS NOT DISTINCT FROM NEW.previous_claims_sha256;
  GET DIAGNOSTICS n=ROW_COUNT;
  IF n<>1 THEN
    RAISE EXCEPTION 'collector_issuer_revision_conflict';
  END IF;
  NEW.db_recorded_at := clock_timestamp();
  IF NEW.db_recorded_at IS NULL OR NOT isfinite(NEW.db_recorded_at) OR NEW.db_recorded_at<sampled OR NEW.db_recorded_at>=NEW.original_expires_at THEN
    RAISE EXCEPTION 'collector_issuer_final_expiry';
  END IF;
  RETURN NEW;
EXCEPTION WHEN others THEN
  RAISE EXCEPTION 'collector_issuer_write_denied';
END;
$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;
"""

RESTORE_0019_STATE_SQL = """CREATE OR REPLACE FUNCTION public.demo_control_state_guard() RETURNS trigger AS $$
        DECLARE j JSONB;
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'demo_control_delete_denied'; END IF;
          j := NEW.state_json::jsonb;
          IF j->>'version' IS DISTINCT FROM 'ctcc.demo_control.v1'
             OR j->>'environment' IS DISTINCT FROM NEW.environment
             OR j->>'account_id' IS DISTINCT FROM NEW.account_id
             OR (j->>'revision')::bigint IS DISTINCT FROM NEW.control_revision
             OR j->>'owner_sha256' IS DISTINCT FROM NEW.owner_sha256
             OR (j->>'owner_epoch')::bigint IS DISTINCT FROM NEW.owner_epoch
             OR (j->>'lease_until')::timestamptz IS DISTINCT FROM NEW.lease_until
             OR j->'emergency_stop' IS DISTINCT FROM to_jsonb(NEW.emergency_stop)
             OR j->>'arm_request_id' IS DISTINCT FROM NEW.arm_request_id
             OR (j->>'arm_expires_at')::timestamptz IS DISTINCT FROM NEW.arm_expires_at
             OR (j->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
             OR (j->>'updated_at')::timestamptz IS DISTINCT FROM NEW.updated_at
             OR (NEW.owner_epoch=0 AND ROW(j->>'config_sha256',j->>'policy_sha256',j->>'credential_session_sha256') IS DISTINCT FROM ROW(NULL::text,NULL::text,NULL::text))
             OR (NEW.owner_epoch>0 AND (NOT COALESCE(j->>'config_sha256' ~ '^[a-f0-9]{64}$',false)
                OR NOT COALESCE(j->>'policy_sha256' ~ '^[a-f0-9]{64}$',false)
                OR NOT COALESCE(j->>'credential_session_sha256' ~ '^[a-f0-9]{64}$',false)))
             OR encode(sha256(convert_to(NEW.state_json,'UTF8')),'hex') IS DISTINCT FROM NEW.state_sha256
             OR NEW.lease_until > NEW.updated_at + INTERVAL '30 seconds' THEN
            RAISE EXCEPTION 'demo_control_state_binding_denied';
          END IF;
          IF TG_OP = 'INSERT' THEN
            IF NEW.control_revision <> 1 OR NEW.owner_epoch NOT IN (0,1) OR NEW.arm_request_id IS NOT NULL THEN
              RAISE EXCEPTION 'demo_control_initial_state_denied';
            END IF;
          ELSE
            IF ROW(NEW.environment,NEW.account_id,NEW.created_at) IS DISTINCT FROM ROW(OLD.environment,OLD.account_id,OLD.created_at)
               OR NEW.control_revision <> OLD.control_revision+1
               OR NEW.updated_at < OLD.updated_at
               OR (OLD.emergency_stop AND NOT NEW.emergency_stop) THEN
              RAISE EXCEPTION 'demo_control_transition_denied';
            END IF;
            IF NEW.owner_sha256 IS DISTINCT FROM OLD.owner_sha256 THEN
              IF NEW.owner_epoch <> OLD.owner_epoch+1 OR NEW.updated_at < OLD.lease_until OR NEW.arm_request_id IS NOT NULL THEN
                RAISE EXCEPTION 'demo_control_takeover_denied';
              END IF;
            ELSIF NEW.owner_epoch <> OLD.owner_epoch THEN
              RAISE EXCEPTION 'demo_control_epoch_denied';
            END IF;
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
"""

RESTORE_0019_JOURNAL_SQL = """CREATE OR REPLACE FUNCTION public.demo_control_journal_guard() RETURNS trigger AS $$
        DECLARE c RECORD; prior RECORD; old_state JSONB; current_state JSONB;
        BEGIN
          SELECT * INTO c FROM demo_account_controls WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
          SELECT * INTO prior FROM demo_control_journal WHERE environment=NEW.environment AND account_id=NEW.account_id ORDER BY control_revision DESC LIMIT 1;
          current_state := c.state_json::jsonb;
          old_state := prior.event_json::jsonb->'state';
          IF c.control_revision IS DISTINCT FROM NEW.control_revision
             OR c.state_sha256 IS DISTINCT FROM NEW.state_sha256
             OR c.updated_at IS DISTINCT FROM NEW.occurred_at
             OR NEW.control_revision <> COALESCE(prior.control_revision,0)+1
             OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256
             OR encode(sha256(convert_to(NEW.event_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
             OR NEW.event_json::jsonb IS DISTINCT FROM jsonb_build_object(
                'version','ctcc.demo_control_event.v1','command_id',NEW.command_id,
                'action',NEW.action,'previous_sha256',NEW.previous_sha256,'state',current_state) THEN
            RAISE EXCEPTION 'demo_control_journal_binding_denied';
          END IF;
          IF NEW.action='acquire' THEN
            IF c.arm_request_id IS NOT NULL OR (prior.control_revision IS NULL AND c.owner_epoch<>1) OR (prior.control_revision IS NOT NULL AND
               (c.owner_sha256 IS NOT DISTINCT FROM old_state->>'owner_sha256' OR c.owner_epoch <> (old_state->>'owner_epoch')::bigint+1)) THEN
              RAISE EXCEPTION 'demo_control_acquire_denied';
            END IF;
          ELSIF prior.control_revision IS NULL THEN
            IF NEW.action<>'estop' OR c.owner_epoch<>0 OR NOT c.emergency_stop THEN
              RAISE EXCEPTION 'demo_control_stopped_genesis_required';
            END IF;
          ELSIF c.owner_sha256 IS DISTINCT FROM old_state->>'owner_sha256'
             OR c.owner_epoch IS DISTINCT FROM (old_state->>'owner_epoch')::bigint THEN
            RAISE EXCEPTION 'demo_control_owner_binding_denied';
          END IF;
          IF NEW.action IN ('arm_requested','rebind','release','renew') AND NEW.occurred_at >= (old_state->>'lease_until')::timestamptz THEN
            RAISE EXCEPTION 'demo_control_expired_owner_denied';
          END IF;
          IF NEW.action IN ('arm_requested','rebind') AND c.lease_until IS DISTINCT FROM (old_state->>'lease_until')::timestamptz THEN
            RAISE EXCEPTION 'demo_control_lease_change_denied';
          END IF;
          IF NEW.action='renew' AND c.lease_until IS DISTINCT FROM NEW.occurred_at + INTERVAL '30 seconds' THEN
            RAISE EXCEPTION 'demo_control_renew_lease_denied';
          END IF;
          IF NEW.action IN ('disarm','estop') AND c.lease_until IS DISTINCT FROM GREATEST(NEW.occurred_at,(old_state->>'lease_until')::timestamptz) THEN
            RAISE EXCEPTION 'demo_control_tightening_lease_denied';
          END IF;
          IF NEW.action='arm_requested' THEN
            IF c.arm_request_id IS DISTINCT FROM NEW.command_id OR c.arm_expires_at <= NEW.occurred_at
               OR c.arm_expires_at > NEW.occurred_at + INTERVAL '30 seconds'
               OR (old_state->>'arm_expires_at')::timestamptz > NEW.occurred_at THEN
              RAISE EXCEPTION 'demo_control_arm_denied';
            END IF;
          ELSIF NEW.action IN ('acquire','disarm','estop','rebind','release') AND c.arm_request_id IS NOT NULL THEN
            RAISE EXCEPTION 'demo_control_disarm_required';
          ELSIF NEW.action='renew' AND c.arm_request_id IS NOT NULL AND
              (c.arm_request_id IS DISTINCT FROM old_state->>'arm_request_id'
               OR c.arm_expires_at IS DISTINCT FROM (old_state->>'arm_expires_at')::timestamptz) THEN
            RAISE EXCEPTION 'demo_control_renew_cannot_arm';
          END IF;
          IF NEW.action='estop' AND NOT c.emergency_stop THEN
            RAISE EXCEPTION 'demo_control_stop_required';
          END IF;
          IF NEW.action='release' AND c.lease_until IS DISTINCT FROM NEW.occurred_at THEN
            RAISE EXCEPTION 'demo_control_release_required';
          END IF;
          IF NEW.action NOT IN ('acquire','rebind') AND
             ROW(current_state->>'config_sha256',current_state->>'policy_sha256',current_state->>'credential_session_sha256') IS DISTINCT FROM
             ROW(old_state->>'config_sha256',old_state->>'policy_sha256',old_state->>'credential_session_sha256') THEN
            RAISE EXCEPTION 'demo_control_rebind_required';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
"""

FROZEN_DDL_SHA256 = "9535c233c7398c9f70d407a6c172576f251cc4d6d2cd9ce2a0e716164b69a629"
FROZEN_STATEMENT_COUNT = 27


def statements():
    """Return only this fixed complete SQL tuple, after fresh byte-hash check."""
    result = (
        CLEAR_TABLE_SQL,
        COUNTER_TABLE_SQL,
        ACK_GUARD_SQL,
        ACK_COMMIT_SQL,
        COUNTER_GUARD_SQL,
        COUNTER_COMMIT_SQL,
        IMMUTABLE_SQL,
        STATE_GUARD_SQL,
        JOURNAL_GUARD_SQL,
        ISSUER_GUARD_SQL,
        *TAIL_STATEMENTS,
    )
    if (
        len(result) != FROZEN_STATEMENT_COUNT
        or hashlib.sha256(
            "\n--ctcc-v55-boundary--\n".join(result).encode("utf-8")
        ).hexdigest()
        != FROZEN_DDL_SHA256
    ):
        raise ValueError("operator_0055_frozen_ddl_changed") from None
    return result


ORIGINAL_STATE_BODY_SHA256 = (
    "4048ca74eabe8d274c01da038478409c6c989b5f7379dcb0303a677953abd790"
)
ORIGINAL_JOURNAL_BODY_SHA256 = (
    "a4f4360488fa4bc1b183649d066a7f99ac03dbd20462174fa8d63e7f5585c203"
)
FROZEN_RESTORE_SHA256 = (
    "df3b4f0946a1d4e2a235952558f97740c3186e5078a6449c80f121cab4ce29f2"
)


def restore_statements():
    result = (RESTORE_0019_STATE_SQL, RESTORE_0019_JOURNAL_SQL, RESTORE_0054_ISSUER_SQL)
    if (
        hashlib.sha256(
            "\n--ctcc-v55-boundary--\n".join(result).encode("utf-8")
        ).hexdigest()
        != FROZEN_RESTORE_SHA256
    ):
        raise ValueError("operator_0055_frozen_restore_changed") from None
    return result


ORIGINAL_ISSUER_BODY_SHA256 = (
    "f0ce7cdfa8026c0c7461ae308f36e60cd6b59f3a0b4c49cf5d8bae55410566e4"
)
