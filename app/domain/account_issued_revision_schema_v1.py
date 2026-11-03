"""Frozen additive DB0054 DDL, no role grant or source-authentication API."""

import hashlib

TABLE = "demo_collector_issued_account_revisions"
GUARD = "collector_issued_account_revision_guard_v1"
IMMUTABLE = "collector_issued_account_revision_immutable_v1"
FROZEN_DDL_SHA256 = "5f82b31ffe168f803d65e485c8296d1df49d3fb2cda6701b0216fea0d6be24ee"
DDL = """-- SOURCE-ONLY, UNINSTALLED additive 0054 interface; predecessor is exact 0053.
-- This is not a source-authentication API or permission grant. The fixed joint
-- native issuer must verify original sources before inserting, then perform an
-- independent-session committed readback before returning its held owner.
-- The table/trigger only enforce durable cross-binding and atomic revision CAS.
-- No SQL caller-provided boolean substitutes for the native verification.

CREATE TABLE public.demo_collector_issued_account_revisions (
 issuer_sha256 varchar(64) PRIMARY KEY,
 environment varchar(8) NOT NULL,
 account_id varchar(32) NOT NULL,
 settlement_currency varchar(16) NOT NULL,
 previous_account_revision bigint NOT NULL,
 previous_ledger_revision bigint NOT NULL,
 previous_claims_sha256 varchar(64),
 account_revision bigint NOT NULL,
 ledger_revision bigint NOT NULL,
 claims_sha256 varchar(64) NOT NULL,
 claims_json text NOT NULL,
 control_revision bigint NOT NULL,
 control_event_sha256 varchar(64) NOT NULL,
 control_state_sha256 varchar(64) NOT NULL,
 source_lineage_sha256 varchar(64) NOT NULL,
 hwm_custody_id varchar(32) NOT NULL,
 hwm_bundle_sha256 varchar(64) NOT NULL,
 proof_json text NOT NULL,
 original_expires_at timestamptz NOT NULL,
 db_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT fk_collector_issued_scope FOREIGN KEY(environment,account_id,settlement_currency)
   REFERENCES public.qualification_account_scopes(environment,account_id,settlement_currency)
   ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT fk_collector_issued_control FOREIGN KEY(environment,account_id,control_revision)
   REFERENCES public.demo_control_journal(environment,account_id,control_revision)
   ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT fk_collector_issued_source_lineage FOREIGN KEY(source_lineage_sha256)
   REFERENCES public.demo_collector_current_revision_lineages(lineage_sha256)
   ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT fk_collector_issued_hwm_custody FOREIGN KEY(hwm_custody_id)
   REFERENCES public.public_runtime_raw_bundles_v1(bundle_id)
   ON DELETE RESTRICT ON UPDATE NO ACTION NOT DEFERRABLE,
 CONSTRAINT uq_collector_issued_scope_revision UNIQUE
   (environment,account_id,settlement_currency,account_revision),
 CONSTRAINT uq_collector_issued_once_per_current UNIQUE(source_lineage_sha256),
 CONSTRAINT collector_issued_scope CHECK
   (environment='demo' AND account_id ~ '^[0-9]{1,32}$'
    AND settlement_currency='USDT'),
 CONSTRAINT collector_issued_revision_step CHECK
   (previous_account_revision>=0 AND previous_ledger_revision>=previous_account_revision
    AND account_revision=previous_account_revision+1
    AND ledger_revision=previous_ledger_revision+1),
 CONSTRAINT collector_issued_digests CHECK
   (issuer_sha256 ~ '^[a-f0-9]{64}$' AND claims_sha256 ~ '^[a-f0-9]{64}$'
    AND source_lineage_sha256 ~ '^[a-f0-9]{64}$'
    AND hwm_bundle_sha256 ~ '^[a-f0-9]{64}$'
    AND hwm_custody_id ~ '^[a-f0-9]{32}$'
    AND control_event_sha256 ~ '^[a-f0-9]{64}$'
    AND control_state_sha256 ~ '^[a-f0-9]{64}$'
    AND (previous_claims_sha256 IS NULL OR previous_claims_sha256 ~ '^[a-f0-9]{64}$')),
 CONSTRAINT collector_issued_bounds CHECK
   (control_revision>0 AND octet_length(claims_json) BETWEEN 1 AND 8388608
    AND octet_length(proof_json) BETWEEN 1 AND 1048576)
);

CREATE FUNCTION public.collector_issued_account_revision_guard_v1()
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

CREATE FUNCTION public.collector_issued_account_revision_immutable_v1()
RETURNS trigger AS $ctcc$
BEGIN
  RAISE EXCEPTION 'collector_issuer_immutable';
END;
$ctcc$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;

CREATE TRIGGER collector_issued_revision_insert BEFORE INSERT
 ON public.demo_collector_issued_account_revisions FOR EACH ROW
 EXECUTE FUNCTION public.collector_issued_account_revision_guard_v1();
CREATE TRIGGER collector_issued_revision_immutable BEFORE UPDATE OR DELETE
 ON public.demo_collector_issued_account_revisions FOR EACH ROW
 EXECUTE FUNCTION public.collector_issued_account_revision_immutable_v1();
CREATE TRIGGER collector_issued_revision_no_truncate BEFORE TRUNCATE
 ON public.demo_collector_issued_account_revisions FOR EACH STATEMENT
 EXECUTE FUNCTION public.collector_issued_account_revision_immutable_v1();
REVOKE ALL ON public.demo_collector_issued_account_revisions FROM PUBLIC;
REVOKE ALL ON FUNCTION public.collector_issued_account_revision_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.collector_issued_account_revision_immutable_v1() FROM PUBLIC;
"""
DDL_SHA256 = hashlib.sha256(DDL.encode("utf-8")).hexdigest()


def statements():
    """Split this exact frozen body at its fixed top-level source boundaries.

    It is deliberately not a generic SQL parser. Each PL/pgSQL function body
    remains one statement; the fixed trigger/revoke tail has no quoted delimiter.
    """
    if (
        DDL_SHA256 != FROZEN_DDL_SHA256
        or hashlib.sha256(DDL.encode("utf-8")).hexdigest() != FROZEN_DDL_SHA256
    ):
        raise ValueError("collector_issuer_frozen_0054_ddl_changed") from None
    markers = (
        "CREATE FUNCTION public.collector_issued_account_revision_guard_v1()",
        "CREATE FUNCTION public.collector_issued_account_revision_immutable_v1()",
        "CREATE TRIGGER collector_issued_revision_insert BEFORE INSERT",
    )
    if any(DDL.count(marker) != 1 for marker in markers):
        raise ValueError("collector_issuer_frozen_statement_boundary_changed") from None
    first, second, third = (DDL.index(marker) for marker in markers)
    table, guard, immutable, tail = (
        DDL[:first],
        DDL[first:second],
        DDL[second:third],
        DDL[third:],
    )
    flat = tuple(part for part in tail.split(";") if part.strip())
    if len(flat) != 6 or any("$ctcc$" in part for part in flat):
        raise ValueError("collector_issuer_frozen_tail_changed") from None
    return table, guard, immutable, *flat
