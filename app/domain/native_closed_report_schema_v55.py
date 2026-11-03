"""Narrow immutable terminal DB0018 eligibility; legacy0042 remains denied.

No caller boolean upgrades an ACK or initial report. A distinct terminal row
must bind the actual closed reservation, after-retirement cleanup, durable full
forensics and exact report/policy bytes. Missing financial fields stay unknown;
eligibility of a source-authenticated report never asserts metric completeness.
"""

import hashlib

REPORT_POLICY_VERSION = "ctcc.native_closed_trade_observed_unknowns.v1"
REPORT_POLICY_JSON = '{"causality_claim":false,"eligibility":"owned_closed_flat_and_retired_resources_and_exact_forensic_replay","financial_completeness_required":false,"funding_accrual_from_bill_generation_time":false,"missing_funding_path_and_reference":"null_with_reason","price_friction_subtracted_twice":false,"retained_post_response_custody":"complete_safe_raw_required_for_this_finite_reporting_branch","strategy_edge_claim":false,"version":"ctcc.native_closed_trade_observed_unknowns.v1"}'
REPORT_POLICY_SHA256 = hashlib.sha256(REPORT_POLICY_JSON.encode("ascii")).hexdigest()

LEGACY_SPOOL_SQL = """
      CREATE OR REPLACE FUNCTION public.qualification_spool_binding()
      RETURNS trigger AS $$
      DECLARE o RECORD; r RECORD; j JSONB;
      BEGIN
        SELECT * INTO o FROM public.qualification_submission_outcomes
          WHERE outcome_id=NEW.outcome_id;
        SELECT * INTO r FROM public.qualification_reservations
          WHERE reservation_id=NEW.reservation_id;
        IF o.outcome_id IS NULL OR r.reservation_id IS NULL
          OR o.reservation_id IS DISTINCT FROM NEW.reservation_id
          OR r.report_id IS DISTINCT FROM NEW.report_id
          OR NEW.created_at IS DISTINCT FROM o.recorded_at THEN
          RAISE EXCEPTION 'submission_spool_binding_denied';
        END IF;
        BEGIN
          j := o.outcome_json::jsonb;
        EXCEPTION WHEN others THEN
          RAISE EXCEPTION 'submission_spool_transport_evidence_invalid';
        END;
        IF jsonb_typeof(j) IS DISTINCT FROM 'object'
          OR j->>'status' IS DISTINCT FROM o.status
          OR j->'transport_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR NEW.eligible IS DISTINCT FROM false THEN
          RAISE EXCEPTION 'submission_spool_transport_unverified';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """
LEGACY_PROJECTION_SQL = """
      CREATE OR REPLACE FUNCTION public.qualification_projection_binding()
      RETURNS trigger AS $$
      BEGIN
        RAISE EXCEPTION 'submission_projection_transport_unverified';
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """

ELIGIBLE_SQL = r"""CREATE FUNCTION public.qualification_native_report_eligible_v55(
  oid text, rid text, report_sha text, policy_sha text) RETURNS boolean AS $ctcc$
DECLARE r record; o record; closed record; clean record; forensic record; q record; close_intent record; original_filled record;
  entry_response record; close_response record; entry_capture jsonb; close_capture jsonb; response_capture jsonb;
  w jsonb; c jsonb; f jsonb; data jsonb; analysis jsonb; request jsonb; metadata jsonb; receipt jsonb;
  receipt_name text; seen_head text; sequence_no bigint; revision_no bigint; lock_hex text; metric_name text;
  final_group jsonb; final_pages jsonb; fill_data jsonb := '{}'::jsonb; raw_row jsonb; evidence jsonb; fee_row jsonb;
  fill_source jsonb; candidate jsonb; terminal jsonb; metric jsonb; expected_metric jsonb; expected_metrics jsonb;
  entry_average numeric; close_average numeric; quantity numeric; contract_value numeric; sign_value numeric; fee_total numeric := 0;
  planned_entry numeric; stop_price numeric; known_fills integer := 0;
  receipt_keys integer; root_resources jsonb; finalized_receipt jsonb; ledger_receipt jsonb; close_receipt jsonb;
  retired_receipt jsonb; expected_retirement jsonb; expected_prepared jsonb; socket_observation jsonb;
  source_receipt jsonb; source_group jsonb; source_manifest jsonb; source_request jsonb;
  source_resolution jsonb; control_read jsonb; before_control jsonb; after_control jsonb;
  control_meta jsonb; control_scope jsonb; before_meta jsonb; after_meta jsonb;
  disarm_control jsonb; released_control jsonb; original_control jsonb; control_event record;
  expected_manifest_ids jsonb; expected_requests jsonb; digest_group jsonb; digest_value jsonb;
  source_index integer; source_position integer; control_index integer;
BEGIN
  SELECT * INTO r FROM public.qualification_reservations WHERE reservation_id=rid;
  SELECT * INTO o FROM public.qualification_submission_outcomes WHERE outcome_id=oid AND reservation_id=rid;
  w := public.qualification_native_plain_v55(o.capture_json); request := r.request_json::jsonb;
  IF r.reservation_id IS NULL OR r.environment<>'demo' OR r.state<>'reconciled_flat' OR r.state_revision<>4
     OR request->>'contract_version' IS DISTINCT FROM 'ctcc-reservation-request-native-v4'
     OR o.outcome_id IS NULL OR o.observation_kind<>'reconciliation' OR o.status<>'acknowledged'
     OR w->>'version' IS DISTINCT FROM 'ctcc.native_closed_trade_report_ready.v1'
     OR w->>'reservation_id' IS DISTINCT FROM rid OR w->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR w->>'intent_sha256' IS DISTINCT FROM o.intent_sha256
     OR w->>'report_sha256' IS DISTINCT FROM report_sha OR w->>'policy_sha256' IS DISTINCT FROM policy_sha
     OR w->>'report_policy_version' IS DISTINCT FROM 'ctcc.native_closed_trade_observed_unknowns.v1'
     OR w->>'report_policy_sha256' IS DISTINCT FROM '__REPORT_POLICY_SHA256__'
     OR w->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR w->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb THEN RETURN false; END IF;
  lock_hex := encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||
    convert_to(r.environment,'UTF8')||decode('00','hex')||convert_to(r.account_id,'UTF8')),'hex');
  PERFORM pg_advisory_xact_lock((('x'||substr(lock_hex,1,16))::bit(64))::bigint);
  PERFORM 1 FROM public.demo_account_controls WHERE environment=r.environment AND account_id=r.account_id FOR UPDATE;
  PERFORM 1 FROM public.qualification_account_scopes WHERE environment=r.environment AND account_id=r.account_id AND settlement_currency=r.settlement_currency FOR UPDATE;
  IF NOT public.qualification_native_filled_flat_data_v55(rid,r.updated_at) THEN RETURN false; END IF;
  SELECT * INTO closed FROM public.qualification_submission_outcomes WHERE outcome_id=w->>'closure_outcome_id' AND reservation_id=rid;
  SELECT * INTO clean FROM public.qualification_submission_outcomes WHERE outcome_id=w->>'cleanup_outcome_id' AND reservation_id=rid;
  SELECT * INTO forensic FROM public.qualification_submission_outcomes WHERE outcome_id=w->>'forensics_outcome_id' AND reservation_id=rid;
  SELECT * INTO close_intent FROM public.qualification_submission_outcomes WHERE outcome_id=closed.capture_json::jsonb->>'close_intent_outcome_id' AND reservation_id=rid;
  SELECT * INTO original_filled FROM public.qualification_submission_outcomes WHERE outcome_id=close_intent.capture_json::jsonb->>'original_filled_outcome_id' AND reservation_id=rid;
  c := public.qualification_native_plain_v55(clean.capture_json); f := public.qualification_native_plain_v55(forensic.capture_json);
  SELECT * INTO entry_response FROM public.qualification_submission_outcomes WHERE reservation_id=rid AND sequence=1;
  SELECT * INTO close_response FROM public.qualification_submission_outcomes WHERE reservation_id=rid AND outcome_id=c->>'close_response_outcome_id';
  entry_capture := public.qualification_native_plain_v55(entry_response.capture_json);
  close_capture := public.qualification_native_plain_v55(close_response.capture_json);
  data := public.qualification_native_plain_v55(f->>'forensics_json'); analysis := data->'analysis';
  IF closed.outcome_id IS NULL OR closed.recorded_at IS DISTINCT FROM r.updated_at
     OR closed.capture_json::jsonb->>'version' IS DISTINCT FROM 'ctcc.native_owned_filled_flat_closure.v1'
     OR clean.outcome_id IS NULL OR clean.sequence<=closed.sequence OR clean.sequence>=o.sequence
     OR clean.observation_kind<>'reconciliation' OR clean.status<>'acknowledged'
     OR c->>'version' IS DISTINCT FROM 'ctcc.native_closed_trade_cleanup.v1'
     OR c->>'reservation_id' IS DISTINCT FROM rid OR c->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR c->>'intent_sha256' IS DISTINCT FROM o.intent_sha256 OR c->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
     OR c->>'terminal_data_outcome_id' IS DISTINCT FROM closed.capture_json::jsonb->>'terminal_data_outcome_id'
     OR c->>'close_intent_outcome_id' IS DISTINCT FROM closed.capture_json::jsonb->>'close_intent_outcome_id'
     OR c->>'closure_receipt_sha256' IS DISTINCT FROM closed.capture_json::jsonb->>'assessment_sha256'
     OR NOT coalesce(c->>'entry_response_body_sha256' ~ '^[a-f0-9]{64}$',false)
     OR NOT coalesce(c->>'close_response_body_sha256' ~ '^[a-f0-9]{64}$',false)
     OR entry_capture->>'version' IS DISTINCT FROM 'ctcc.native_initial_transport_data.v1'
     OR entry_capture->>'raw_body_sha256' IS DISTINCT FROM c->>'entry_response_body_sha256'
     OR entry_capture->>'response_observation_sha256' IS DISTINCT FROM c->>'entry_response_body_sha256'
     OR close_response.outcome_id IS NULL OR close_response.sequence<=close_intent.sequence OR close_response.sequence>=closed.sequence
     OR close_response.observation_kind IS DISTINCT FROM 'reconciliation'
     OR close_capture->>'version' IS DISTINCT FROM 'ctcc.native_close_transport_response.v1'
     OR close_capture->>'close_intent_outcome_id' IS DISTINCT FROM close_intent.outcome_id
     OR close_capture->>'close_request_sha256' IS DISTINCT FROM close_intent.capture_json::jsonb->>'close_request_sha256'
     OR close_capture->>'raw_body_sha256' IS DISTINCT FROM c->>'close_response_body_sha256'
     OR close_capture->'received_body_complete' IS DISTINCT FROM 'true'::jsonb
     OR c->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR c->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR forensic.outcome_id IS NULL OR forensic.sequence<=closed.sequence OR forensic.sequence>=o.sequence
     OR forensic.observation_kind<>'reconciliation' OR forensic.status<>'acknowledged'
     OR f->>'version' IS DISTINCT FROM 'ctcc.native_closed_trade_forensics_readback.v1'
     OR f->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
     OR f->>'terminal_data_outcome_id' IS DISTINCT FROM c->>'terminal_data_outcome_id'
     OR f->>'close_intent_outcome_id' IS DISTINCT FROM c->>'close_intent_outcome_id'
     OR f->>'original_manifest_outcome_id' IS DISTINCT FROM original_filled.capture_json::jsonb->>'manifest_outcome_id'
     OR f->>'preflight_manifest_outcome_id' IS DISTINCT FROM close_intent.capture_json::jsonb->>'preflight_manifest_outcome_id'
     OR f->>'final_manifest_outcome_id' IS DISTINCT FROM closed.capture_json::jsonb->>'manifest_outcome_id'
     OR encode(sha256(convert_to(f->>'forensics_json','UTF8')),'hex') IS DISTINCT FROM report_sha
     OR f->>'forensics_sha256' IS DISTINCT FROM report_sha OR octet_length(f->>'forensics_json') NOT BETWEEN 1 AND 32768
     OR data->>'version' IS DISTINCT FROM 'ctcc.native_closed_trade_forensics.v1'
     OR data->>'reservation_id' IS DISTINCT FROM rid OR data->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR data->>'intent_sha256' IS DISTINCT FROM o.intent_sha256 OR data->>'original_event_key' IS DISTINCT FROM r.original_event_key
     OR data->>'native_original_candidate_sha256' IS DISTINCT FROM request->'origin'->>'original_candidate_sha256'
     OR data->>'original_policy_sha256' IS DISTINCT FROM request->'origin'->>'original_policy_sha256'
     OR data->>'g12_evidence_sha256' IS DISTINCT FROM request->'origin'->>'evidence_sha256'
     OR data->>'publication_sha256' IS DISTINCT FROM request->'origin'->>'publication_sha256'
     OR data->>'terminal_assessment_sha256' IS DISTINCT FROM c->>'closure_receipt_sha256'
     OR data->'actual_fill_pnl_already_contains_price_friction' IS DISTINCT FROM 'true'::jsonb
     OR analysis->>'schema_version' IS DISTINCT FROM 'ctcc_trade_forensics_v1'
     OR analysis->>'position_status' IS DISTINCT FROM 'flat' OR analysis->'metrics_complete' IS DISTINCT FROM 'false'::jsonb
     OR analysis->'inputs'->>'purpose' IS DISTINCT FROM 'observed'
     OR analysis->'inputs'->'candidate'->>'report_id' IS DISTINCT FROM r.report_id
     OR analysis->'inputs'->'candidate'->>'account_id' IS DISTINCT FROM r.account_id
     OR analysis->'inputs'->'candidate'->>'environment' IS DISTINCT FROM 'demo'
     OR analysis->'inputs'->'candidate'->>'settlement_currency' IS DISTINCT FROM r.settlement_currency
     OR jsonb_typeof(analysis->'inputs'->'coverage') IS DISTINCT FROM 'array' OR jsonb_array_length(analysis->'inputs'->'coverage')<>4
     OR analysis->'inputs'->'path' IS DISTINCT FROM '[]'::jsonb
     OR analysis->'inputs'->'facts' IS DISTINCT FROM '[]'::jsonb
     OR data->'unknown_funding_effective_accrual' IS DISTINCT FROM 'null'::jsonb
     OR data->'unknown_holding_price_path' IS DISTINCT FROM 'null'::jsonb
     OR data->'unknown_prefill_and_preclose_quote_references' IS DISTINCT FROM 'null'::jsonb
     OR analysis->>'primary_cause' IS DISTINCT FROM 'unknown'
     OR analysis->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
     OR analysis->'account_evidence_authenticated' IS DISTINCT FROM 'false'::jsonb
     OR analysis->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR analysis->'strategy_edge_established' IS DISTINCT FROM 'false'::jsonb
     OR analysis->'calibration_sample_sufficient' IS DISTINCT FROM 'false'::jsonb
     OR data->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
     OR data->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR data->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR f->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR f->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR analysis->>'exit_reason' IS DISTINCT FROM 'other'
     OR encode(sha256(convert_to(public.qualification_native_canonical_v55(analysis-'result_sha256'),'UTF8')),'hex') IS DISTINCT FROM analysis->>'result_sha256'
     OR encode(sha256(convert_to(public.qualification_native_canonical_v55(analysis->'inputs'),'UTF8')),'hex') IS DISTINCT FROM analysis->>'input_sha256'
     OR encode(sha256(convert_to(public.qualification_native_canonical_v55(analysis->'inputs'->'candidate'),'UTF8')),'hex') IS DISTINCT FROM analysis->>'candidate_sha256'
     OR analysis->>'candidate_sha256' IS DISTINCT FROM data->>'forensic_projection_candidate_sha256' THEN RETURN false; END IF;
  FOREACH response_capture IN ARRAY ARRAY[entry_capture,close_capture] LOOP
    IF response_capture->'received_eof' IS DISTINCT FROM 'true'::jsonb
       OR response_capture->'received_overflow' IS DISTINCT FROM 'false'::jsonb
       OR response_capture->'received_faults' IS DISTINCT FROM '[]'::jsonb
       OR jsonb_typeof(response_capture->'raw_body_hex') IS DISTINCT FROM 'string'
       OR NOT coalesce((response_capture->>'raw_body_hex') ~ '^[a-f0-9]*$',false)
       OR octet_length(response_capture->>'raw_body_hex')>131072 OR octet_length(response_capture->>'raw_body_hex')%2<>0
       OR encode(sha256(decode(response_capture->>'raw_body_hex','hex')),'hex') IS DISTINCT FROM response_capture->>'raw_body_sha256'
       OR jsonb_typeof(response_capture->'received_http_status') IS DISTINCT FROM 'number'
       OR (response_capture->>'received_http_status')::integer NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
  END LOOP;
  IF jsonb_typeof(close_capture->'raw_body_bytes') IS DISTINCT FROM 'number'
     OR (close_capture->>'raw_body_bytes')::integer<>octet_length(decode(close_capture->>'raw_body_hex','hex')) THEN RETURN false; END IF;
  -- These DATA receipts must all be the actual final after-retirement receipts,
  -- not a prepared-cleanup marker or an early Root transport-finalized handle.
  FOREACH receipt_name IN ARRAY ARRAY['root_resource_receipt','source_cleanup_receipt','ledger_retirement',
      'close_retirement','root_finalized_receipt','root_retirement_receipt'] LOOP
    IF octet_length(c->>(receipt_name||'_json')) NOT BETWEEN 1 AND 32768
       OR NOT coalesce(c->>(receipt_name||'_sha256') ~ '^[a-f0-9]{64}$',false)
       OR encode(sha256(convert_to(c->>(receipt_name||'_json'),'UTF8')),'hex') IS DISTINCT FROM c->>(receipt_name||'_sha256') THEN RETURN false; END IF;
    receipt := public.qualification_native_plain_v55(c->>(receipt_name||'_json'));
    IF jsonb_typeof(receipt) IS DISTINCT FROM 'object' OR receipt='{}'::jsonb
       OR public.qualification_native_canonical_v55(receipt) IS DISTINCT FROM c->>(receipt_name||'_json') THEN RETURN false; END IF;
    SELECT count(*) INTO receipt_keys FROM jsonb_object_keys(receipt);
    CASE receipt_name
    WHEN 'root_resource_receipt' THEN
      IF receipt_keys<>9 OR receipt->>'version' IS DISTINCT FROM 'ctcc.native_filled_pair_resources_closed.v1'
         OR receipt->>'reservation_id' IS DISTINCT FROM rid OR receipt->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
         OR receipt->>'closure_receipt_sha256' IS DISTINCT FROM c->>'closure_receipt_sha256'
         OR receipt->>'entry_request_sha256' IS DISTINCT FROM o.exchange_request_sha256
         OR receipt->>'close_request_sha256' IS DISTINCT FROM close_intent.capture_json::jsonb->>'close_request_sha256'
         OR receipt->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR receipt->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
         OR jsonb_typeof(receipt->'socket_observations') IS DISTINCT FROM 'array'
         OR jsonb_array_length(receipt->'socket_observations')<>2 THEN RETURN false; END IF;
      FOR socket_observation IN SELECT value FROM jsonb_array_elements(receipt->'socket_observations') LOOP
        IF jsonb_typeof(socket_observation) IS DISTINCT FROM 'array' OR jsonb_array_length(socket_observation)<>5
           OR socket_observation->0 IS DISTINCT FROM '"ctcc.native_socket_closed_observation.v1"'::jsonb
           OR jsonb_typeof(socket_observation->1) IS DISTINCT FROM 'number' OR jsonb_typeof(socket_observation->2) IS DISTINCT FROM 'number'
           OR NOT coalesce(socket_observation->>1 ~ '^[1-9][0-9]{0,19}$',false)
           OR NOT coalesce(socket_observation->>2 ~ '^[1-9][0-9]{0,19}$',false)
           OR socket_observation->3 IS DISTINCT FROM '-1'::jsonb
           OR socket_observation->4 IS DISTINCT FROM '"native_callback_tail_and_sdk_pool_closed"'::jsonb
           OR (socket_observation->>1)::numeric<extract(epoch FROM closed.recorded_at)*1000000000
           OR (socket_observation->>1)::numeric>extract(epoch FROM clean.recorded_at)*1000000000 THEN RETURN false; END IF;
      END LOOP;
      root_resources := receipt;
    WHEN 'source_cleanup_receipt' THEN
      IF receipt_keys<>11 OR receipt->>'schema_version' IS DISTINCT FROM 'ctcc.native_filled_flat_source_cleanup_retirement.v2'
         OR receipt->'account_complete' IS DISTINCT FROM 'false'::jsonb
         OR receipt->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR receipt->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
         OR octet_length(receipt->>'control_resolution_receipt_json') NOT BETWEEN 1 AND 16384
         OR encode(sha256(convert_to(receipt->>'control_resolution_receipt_json','UTF8')),'hex') IS DISTINCT FROM receipt->>'control_resolution_receipt_sha256'
         OR jsonb_typeof(receipt->'read_groups') IS DISTINCT FROM 'array' OR jsonb_array_length(receipt->'read_groups')<>3
         OR jsonb_typeof(receipt->'original_sdk_closed_contracts') IS DISTINCT FROM 'array'
         OR jsonb_array_length(receipt->'original_sdk_closed_contracts')<>2 THEN RETURN false; END IF;
      expected_manifest_ids := jsonb_build_array(original_filled.capture_json::jsonb->>'manifest_outcome_id',
        close_intent.capture_json::jsonb->>'preflight_manifest_outcome_id',closed.capture_json::jsonb->>'manifest_outcome_id');
      IF receipt->'manifest_outcome_ids' IS DISTINCT FROM expected_manifest_ids THEN RETURN false; END IF;
      FOR source_index IN 0..2 LOOP
        source_group := receipt->'read_groups'->source_index;
        IF jsonb_typeof(source_group) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(source_group))<>5
           OR source_group->>'manifest_outcome_id' IS DISTINCT FROM expected_manifest_ids->>source_index THEN RETURN false; END IF;
        source_manifest := public.qualification_native_manifest_v55(rid,expected_manifest_ids->>source_index,
          closed.capture_json::jsonb->>'original_origin',request->>'credential_session_sha256',clean.recorded_at);
        IF source_group->>'credential_session_sha256' IS DISTINCT FROM source_manifest->>'credential'
           OR source_group->>'read_lease_binding_sha256' IS DISTINCT FROM encode(sha256(convert_to(source_manifest->>'read_session','UTF8')),'hex')
           OR jsonb_typeof(source_group->'requests') IS DISTINCT FROM 'array'
           OR jsonb_typeof(source_group->'closed_observations') IS DISTINCT FROM 'array'
           OR jsonb_array_length(source_group->'closed_observations')<>jsonb_array_length(source_manifest->'pages') THEN RETURN false; END IF;
        SELECT jsonb_agg(jsonb_build_object('role',value->>'role','body_sha256',value->'meta'->>'body_sha256',
          'request_signature_sha256',value->'meta'->>'request_signature_sha256') ORDER BY ordinality)
          INTO expected_requests FROM jsonb_array_elements(source_manifest->'pages') WITH ORDINALITY;
        IF source_group->'requests' IS DISTINCT FROM expected_requests THEN RETURN false; END IF;
        FOR socket_observation IN SELECT value FROM jsonb_array_elements(source_group->'closed_observations') LOOP
          IF jsonb_typeof(socket_observation) IS DISTINCT FROM 'array' OR jsonb_array_length(socket_observation)<>5
             OR socket_observation->0 IS DISTINCT FROM '"ctcc.native_socket_closed_observation.v1"'::jsonb
             OR jsonb_typeof(socket_observation->1) IS DISTINCT FROM 'number' OR jsonb_typeof(socket_observation->2) IS DISTINCT FROM 'number'
             OR NOT coalesce(socket_observation->>1 ~ '^[1-9][0-9]{0,19}$',false)
             OR NOT coalesce(socket_observation->>2 ~ '^[1-9][0-9]{0,19}$',false)
             OR socket_observation->3 IS DISTINCT FROM '-1'::jsonb
             OR socket_observation->4 IS DISTINCT FROM '"native_callback_tail_and_sdk_pool_closed"'::jsonb
             OR (socket_observation->>1)::numeric<extract(epoch FROM (source_manifest->>'last_at')::timestamptz)*1000000000
             OR (socket_observation->>1)::numeric>extract(epoch FROM clean.recorded_at)*1000000000
             OR (socket_observation->>2)::numeric<(source_manifest->>'last_mono')::numeric THEN RETURN false; END IF;
        END LOOP;
      END LOOP;
      FOR digest_group IN SELECT value FROM jsonb_array_elements(receipt->'original_sdk_closed_contracts') LOOP
        IF jsonb_typeof(digest_group) IS DISTINCT FROM 'array' OR jsonb_array_length(digest_group)<>4 THEN RETURN false; END IF;
        FOR digest_value IN SELECT value FROM jsonb_array_elements(digest_group) LOOP
          IF jsonb_typeof(digest_value) IS DISTINCT FROM 'string' OR NOT coalesce(digest_value#>>'{}' ~ '^[a-f0-9]{64}$',false) THEN RETURN false; END IF;
        END LOOP;
      END LOOP;
      source_resolution := public.qualification_native_plain_v55(receipt->>'control_resolution_receipt_json');
      IF jsonb_typeof(source_resolution) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(source_resolution))<>11
         OR source_resolution->>'schema_version' IS DISTINCT FROM 'ctcc.native_filled_flat_source_cleanup_control_resolution.v2'
         OR public.qualification_native_canonical_v55(source_resolution) IS DISTINCT FROM receipt->>'control_resolution_receipt_json'
         OR NOT coalesce(source_resolution->>'classification' IN ('own_released','own_expired','owner_superseded'),false)
         OR NOT coalesce(source_resolution->>'original_owner_sha256' ~ '^[a-f0-9]{64}$',false)
         OR jsonb_typeof(source_resolution->'original_owner_epoch') IS DISTINCT FROM 'number'
         OR NOT coalesce(source_resolution->>'original_owner_epoch' ~ '^[1-9][0-9]{0,18}$',false)
         OR source_resolution->'account_complete' IS DISTINCT FROM 'false'::jsonb
         OR source_resolution->'execution_authority' IS DISTINCT FROM 'false'::jsonb
         OR source_resolution->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb THEN RETURN false; END IF;
      SELECT event_json::jsonb->'state' INTO original_control FROM public.demo_control_journal
        WHERE environment=r.environment AND account_id=r.account_id AND occurred_at<=r.created_at
        ORDER BY control_revision DESC LIMIT 1;
      IF original_control->>'owner_sha256' IS DISTINCT FROM source_resolution->>'original_owner_sha256'
         OR original_control->'owner_epoch' IS DISTINCT FROM source_resolution->'original_owner_epoch'
         OR original_control->>'credential_session_sha256' IS DISTINCT FROM request->>'credential_session_sha256'
         OR original_control->>'policy_sha256' IS DISTINCT FROM request->'origin'->>'original_policy_sha256' THEN RETURN false; END IF;
      before_control := source_resolution->'before'; after_control := source_resolution->'after';
      FOR control_index IN 0..1 LOOP
        control_read := jsonb_build_array(before_control,after_control)->control_index;
        control_meta := control_read->'database_metadata';
        IF jsonb_typeof(control_read) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(control_read))<>6
           OR jsonb_typeof(control_meta) IS DISTINCT FROM 'array' OR jsonb_array_length(control_meta)<>10
           OR jsonb_typeof(control_meta->0) IS DISTINCT FROM 'string' OR coalesce(control_meta->>0,'')=''
           OR control_meta->0 IS DISTINCT FROM control_meta->1 OR control_meta->2 IS DISTINCT FROM '"none"'::jsonb
           OR jsonb_typeof(control_meta->3) IS DISTINCT FROM 'number' OR NOT coalesce(control_meta->>3 ~ '^[1-9][0-9]*$',false)
           OR control_meta->4 IS DISTINCT FROM 'false'::jsonb OR control_meta->5 IS DISTINCT FROM 'false'::jsonb OR control_meta->6 IS DISTINCT FROM 'false'::jsonb
           OR jsonb_typeof(control_meta->7) IS DISTINCT FROM 'number' OR NOT coalesce(control_meta->>7 ~ '^[1-9][0-9]*$',false)
           OR (control_meta->>7)::numeric>=2147483648 OR jsonb_typeof(control_meta->8) IS DISTINCT FROM 'string'
           OR NOT coalesce(control_meta->>8 ~ '^[1-9][0-9]{0,19}$',false)
           OR jsonb_typeof(control_read->'observed_at') IS DISTINCT FROM 'string'
           OR NOT coalesce(control_read->>'observed_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?\+00:00$',false)
           OR control_read->'observed_at' IS DISTINCT FROM control_meta->9
           OR (control_read->>'observed_at')::timestamptz<closed.recorded_at
           OR (control_read->>'observed_at')::timestamptz>clean.recorded_at
           OR (control_read->'state'->>'updated_at')::timestamptz>(control_read->>'observed_at')::timestamptz
           OR octet_length(control_read->>'ledger_scope_json') NOT BETWEEN 1 AND 4096
           OR encode(sha256(convert_to(control_read->>'ledger_scope_json','UTF8')),'hex') IS DISTINCT FROM control_read->>'ledger_scope_sha256' THEN RETURN false; END IF;
        control_scope := public.qualification_native_plain_v55(control_read->>'ledger_scope_json');
        IF jsonb_typeof(control_scope) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(control_scope))<>4
           OR public.qualification_native_canonical_v55(control_scope) IS DISTINCT FROM control_read->>'ledger_scope_json'
           OR control_scope->'scope' IS DISTINCT FROM jsonb_build_object('environment',r.environment,'account_id',r.account_id,'settlement_currency',r.settlement_currency)
           OR jsonb_typeof(control_scope->'account_revision') IS DISTINCT FROM 'number'
           OR jsonb_typeof(control_scope->'ledger_revision') IS DISTINCT FROM 'number'
           OR NOT coalesce(control_scope->>'account_revision' ~ '^(0|[1-9][0-9]{0,18})$',false)
           OR NOT coalesce(control_scope->>'ledger_revision' ~ '^(0|[1-9][0-9]{0,18})$',false)
           OR NOT coalesce((control_scope->'claims_sha256'='null'::jsonb OR coalesce(control_scope->>'claims_sha256' ~ '^[a-f0-9]{64}$',false)),false) THEN RETURN false; END IF;
        SELECT * INTO control_event FROM public.demo_control_journal WHERE environment=r.environment AND account_id=r.account_id
          AND event_sha256=control_read->>'event_sha256' AND event_json::jsonb->'state'=control_read->'state';
        IF control_event.event_sha256 IS NULL OR encode(sha256(convert_to(control_event.event_json,'UTF8')),'hex') IS DISTINCT FROM control_event.event_sha256
           OR control_event.control_revision IS DISTINCT FROM (control_read->'state'->>'revision')::bigint
           OR control_event.occurred_at IS DISTINCT FROM (control_read->'state'->>'updated_at')::timestamptz THEN RETURN false; END IF;
      END LOOP;
      before_meta := before_control->'database_metadata'; after_meta := after_control->'database_metadata';
      IF before_meta->8 IS NOT DISTINCT FROM after_meta->8 OR (before_meta->>9)::timestamptz>(after_meta->>9)::timestamptz
         OR before_control->>'ledger_scope_json' IS DISTINCT FROM after_control->>'ledger_scope_json'
         OR before_control->>'ledger_scope_sha256' IS DISTINCT FROM after_control->>'ledger_scope_sha256'
         OR EXISTS(SELECT 1 FROM generate_series(0,6) AS offsets(ordinal_index) WHERE before_meta->ordinal_index IS DISTINCT FROM after_meta->ordinal_index) THEN RETURN false; END IF;
      IF source_resolution->>'classification'='own_released' THEN
        disarm_control := source_resolution->'disarm'; released_control := source_resolution->'release';
        IF jsonb_typeof(disarm_control) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(disarm_control))<>3
           OR jsonb_typeof(released_control) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(released_control))<>3
           OR before_control->'state'->>'owner_sha256' IS DISTINCT FROM source_resolution->>'original_owner_sha256'
           OR before_control->'state'->'owner_epoch' IS DISTINCT FROM source_resolution->'original_owner_epoch'
           OR (before_control->'state'->>'lease_until')::timestamptz<=(before_control->>'observed_at')::timestamptz
           OR (disarm_control->'state'->>'revision')::bigint<>(before_control->'state'->>'revision')::bigint+1
           OR (released_control->'state'->>'revision')::bigint<>(disarm_control->'state'->>'revision')::bigint+1
           OR (disarm_control->'state'-ARRAY['revision','arm_request_id','arm_expires_at','updated_at'])
             IS DISTINCT FROM (before_control->'state'-ARRAY['revision','arm_request_id','arm_expires_at','updated_at'])
           OR (released_control->'state'-ARRAY['revision','lease_until','updated_at'])
             IS DISTINCT FROM (disarm_control->'state'-ARRAY['revision','lease_until','updated_at'])
           OR disarm_control->'state'->'arm_request_id' IS DISTINCT FROM 'null'::jsonb
           OR disarm_control->'state'->'arm_expires_at' IS DISTINCT FROM 'null'::jsonb
           OR released_control->'state'->>'lease_until' IS DISTINCT FROM released_control->'state'->>'updated_at'
           OR after_control->'state' IS DISTINCT FROM released_control->'state'
           OR after_control->>'event_sha256' IS DISTINCT FROM released_control->>'event_sha256'
           OR (after_control->'state'->>'lease_until')::timestamptz>(after_control->>'observed_at')::timestamptz THEN RETURN false; END IF;
        FOR control_index IN 0..1 LOOP
          control_read := jsonb_build_array(disarm_control,released_control)->control_index;
          SELECT * INTO control_event FROM public.demo_control_journal WHERE environment=r.environment AND account_id=r.account_id
            AND event_sha256=control_read->>'event_sha256' AND event_json::jsonb->'state'=control_read->'state';
          IF control_event.event_sha256 IS NULL OR encode(sha256(convert_to(control_event.event_json,'UTF8')),'hex') IS DISTINCT FROM control_event.event_sha256
             OR control_event.action IS DISTINCT FROM (jsonb_build_array('disarm','release')->>control_index)
             OR control_event.previous_sha256 IS DISTINCT FROM (jsonb_build_array(before_control->>'event_sha256',disarm_control->>'event_sha256')->>control_index)
             OR (control_read->>'observed_at')::timestamptz<(before_control->>'observed_at')::timestamptz
             OR (control_read->>'observed_at')::timestamptz>(after_control->>'observed_at')::timestamptz THEN RETURN false; END IF;
        END LOOP;
      ELSE
        IF source_resolution->'disarm' IS DISTINCT FROM 'null'::jsonb OR source_resolution->'release' IS DISTINCT FROM 'null'::jsonb
           OR before_control->'state' IS DISTINCT FROM after_control->'state'
           OR before_control->>'event_sha256' IS DISTINCT FROM after_control->>'event_sha256' THEN RETURN false; END IF;
        IF source_resolution->>'classification'='own_expired' THEN
          IF before_control->'state'->>'owner_sha256' IS DISTINCT FROM source_resolution->>'original_owner_sha256'
             OR before_control->'state'->'owner_epoch' IS DISTINCT FROM source_resolution->'original_owner_epoch'
             OR (before_control->'state'->>'lease_until')::timestamptz>(before_control->>'observed_at')::timestamptz THEN RETURN false; END IF;
        ELSIF before_control->'state'->>'owner_sha256' IS NOT DISTINCT FROM source_resolution->>'original_owner_sha256'
           OR NOT coalesce(before_control->'state'->>'owner_sha256' ~ '^[a-f0-9]{64}$',false)
           OR (before_control->'state'->>'owner_epoch')::bigint<=(source_resolution->>'original_owner_epoch')::bigint THEN RETURN false; END IF;
      END IF;
      source_receipt := receipt;
    WHEN 'ledger_retirement' THEN
      IF receipt_keys<>4 OR receipt->>'version' IS DISTINCT FROM 'ctcc.native_filled_flat_ledger_resources_retired.v1'
         OR receipt->>'reservation_id' IS DISTINCT FROM rid OR receipt->>'original_event_key' IS DISTINCT FROM r.original_event_key
         OR receipt->>'request_sha256' IS DISTINCT FROM r.request_sha256 THEN RETURN false; END IF;
      ledger_receipt := receipt;
    WHEN 'close_retirement' THEN
      IF receipt_keys<>8 OR receipt->>'version' IS DISTINCT FROM 'ctcc.native_filled_close_resources_retired.v1'
         OR receipt->>'reservation_id' IS DISTINCT FROM rid OR receipt->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
         OR receipt->>'closure_receipt_sha256' IS DISTINCT FROM c->>'closure_receipt_sha256'
         OR receipt->>'close_intent_outcome_id' IS DISTINCT FROM close_intent.outcome_id
         OR receipt->>'close_request_sha256' IS DISTINCT FROM close_intent.capture_json::jsonb->>'close_request_sha256'
         OR receipt->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR receipt->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb THEN RETURN false; END IF;
      close_receipt := receipt;
    WHEN 'root_finalized_receipt' THEN
      IF receipt_keys<>4 OR receipt->>'version' IS DISTINCT FROM 'ctcc.native_filled_pair_resources_finalized.v1'
         OR receipt->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
         OR receipt->>'closure_receipt_sha256' IS DISTINCT FROM c->>'closure_receipt_sha256'
         OR NOT coalesce(receipt->>'prepared_cleanup_sha256' ~ '^[a-f0-9]{64}$',false) THEN RETURN false; END IF;
      finalized_receipt := receipt;
    WHEN 'root_retirement_receipt' THEN
      IF receipt_keys<>4 OR receipt->>'version' IS DISTINCT FROM 'ctcc.native_filled_pair_authentication_retired.v1'
         OR receipt->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
         OR receipt->>'closure_receipt_sha256' IS DISTINCT FROM c->>'closure_receipt_sha256'
         OR NOT coalesce(receipt->>'retirement_receipt_sha256' ~ '^[a-f0-9]{64}$',false) THEN RETURN false; END IF;
      retired_receipt := receipt;
    ELSE RETURN false;
    END CASE;
  END LOOP;
  IF source_receipt->'Root_finalized_receipt' IS DISTINCT FROM jsonb_build_array(finalized_receipt->>'version',closed.outcome_id,
       c->>'closure_receipt_sha256',finalized_receipt->>'prepared_cleanup_sha256')
     OR source_receipt->'R7_retirement_receipt' IS DISTINCT FROM jsonb_build_array('ctcc.native_filled_flat_r7_source_chain_retired.v1',rid,r.original_event_key,r.request_sha256) THEN RETURN false; END IF;
  expected_prepared := jsonb_build_object('version','ctcc.native_filled_flat_cleanup_prepared.v1',
    'reservation_id',rid,'closure_outcome_id',closed.outcome_id,'closure_receipt_sha256',c->>'closure_receipt_sha256',
    'source_cleanup_receipt_sha256',source_receipt->>'control_resolution_receipt_sha256',
    'forensics_outcome_id',forensic.outcome_id,'forensics_sha256',f->>'forensics_sha256',
    'original_event_key',r.original_event_key,'request_sha256',r.request_sha256,
    'both_post_socket_observations',root_resources->'socket_observations',
    'execution_authority',false,'order_retry_authority',false);
  IF encode(sha256(convert_to(public.qualification_native_canonical_v55(expected_prepared),'UTF8')),'hex')
       IS DISTINCT FROM finalized_receipt->>'prepared_cleanup_sha256' THEN RETURN false; END IF;
  -- Rebuild the actual R7 retirement DATA hashed by the final Root producer.
  -- This joins final Source, actual ledger/nativeclose retirement and Root's
  -- finalized receipt; a prepared-cleanup marker cannot stand in for them.
  expected_retirement := jsonb_build_object('version','ctcc.native_filled_flat_authentication_retired.v1',
    'reservation_id',rid,'closure_outcome_id',closed.outcome_id,'closure_receipt_sha256',c->>'closure_receipt_sha256',
    'source_retirement_receipt_sha256',c->>'source_cleanup_receipt_sha256',
    'ledger_retirement',jsonb_build_array(ledger_receipt->>'version',rid,r.original_event_key,r.request_sha256),
    'close_retirement_receipt_sha256',c->>'close_retirement_sha256',
    'root_finalized_receipt',jsonb_build_array(finalized_receipt->>'version',closed.outcome_id,
      c->>'closure_receipt_sha256',finalized_receipt->>'prepared_cleanup_sha256'),
    'execution_authority',false,'order_retry_authority',false);
  IF encode(sha256(convert_to(public.qualification_native_canonical_v55(expected_retirement),'UTF8')),'hex')
       IS DISTINCT FROM retired_receipt->>'retirement_receipt_sha256' THEN RETURN false; END IF;
  -- Current finite reporting policy publishes exact observed closed-trade
  -- facts while visibly retaining unknown funding/path/reference metrics.
  -- No metric-completeness or performance/edge assertion grants eligibility.
  final_group := public.qualification_native_manifest_v55(rid,f->>'final_manifest_outcome_id',
    closed.capture_json::jsonb->>'original_origin',request->>'credential_session_sha256',forensic.recorded_at);
  final_pages := final_group->'pages'; candidate := analysis->'inputs'->'candidate';
  terminal := public.qualification_native_plain_v55(data->>'terminal_assessment_json');
  IF encode(sha256(convert_to(data->>'terminal_assessment_json','UTF8')),'hex') IS DISTINCT FROM c->>'closure_receipt_sha256'
     OR candidate->>'instrument_id' IS DISTINCT FROM request->'risk_inputs'->'instrument'->>'instrument_id'
     OR candidate->>'original_evidence_sha256' IS DISTINCT FROM request->'origin'->>'evidence_sha256'
     OR candidate->>'direction' IS DISTINCT FROM r.direction OR candidate->>'contract_kind' IS DISTINCT FROM 'linear_quote_settled'
     OR candidate->>'original_source_sha256' IS DISTINCT FROM (request->'origin'->>'original_candidate_json')::jsonb->'result'->'entry_zone'->>'source_sha256'
     OR (candidate->>'recorded_at')::timestamptz IS DISTINCT FROM ((request->'origin'->>'original_candidate_json')::jsonb->'result'->>'evaluated_at')::timestamptz
     OR public.qualification_native_number_v55(candidate->'entry')<>public.qualification_native_number_v55(request->'coverage'->'candidate'->'entry')
     OR public.qualification_native_number_v55(candidate->'stop_loss')<>public.qualification_native_number_v55(request->'coverage'->'candidate'->'stop_loss')
     OR public.qualification_native_number_v55(candidate->'take_profit')<>public.qualification_native_number_v55((closed.capture_json::jsonb->>'exact_order_json')::jsonb->'attachAlgoOrds'->0->'tpTriggerPx')
     OR public.qualification_native_number_v55(candidate->'planned_contracts')<>public.qualification_native_number_v55(request->'risk_inputs'->'requested_contracts')
     OR public.qualification_native_number_v55(candidate->'contract_value_base')<>public.qualification_native_number_v55(request->'risk_inputs'->'instrument'->'contract_value') THEN RETURN false; END IF;
  FOR receipt IN SELECT value FROM jsonb_array_elements(analysis->'inputs'->'coverage') LOOP
    receipt_name := receipt->>'stream';
    IF receipt->>'report_id' IS DISTINCT FROM r.report_id OR receipt->>'candidate_sha256' IS DISTINCT FROM analysis->>'candidate_sha256'
       OR receipt->>'instrument_id' IS DISTINCT FROM candidate->>'instrument_id' OR receipt->>'account_id' IS DISTINCT FROM r.account_id
       OR receipt->>'environment' IS DISTINCT FROM 'demo' OR receipt->>'source_sha256' IS DISTINCT FROM c->>'closure_receipt_sha256'
       OR receipt->>'status' IS DISTINCT FROM (CASE WHEN receipt_name IN ('fills','fees') THEN 'complete' ELSE 'unknown' END)
       OR (receipt->>'started_at')::timestamptz IS DISTINCT FROM (candidate->>'recorded_at')::timestamptz
       OR (receipt->>'ended_at')::timestamptz IS DISTINCT FROM (analysis->'inputs'->>'observed_at')::timestamptz
       OR (receipt->>'recorded_at')::timestamptz IS DISTINCT FROM (final_group->>'last_at')::timestamptz THEN RETURN false; END IF;
  END LOOP;
  IF (SELECT array_agg(value->>'stream' ORDER BY value->>'stream') FROM jsonb_array_elements(analysis->'inputs'->'coverage')) IS DISTINCT FROM ARRAY['fees','fills','funding','path'] THEN RETURN false; END IF;
  FOR fill_source IN SELECT value FROM jsonb_array_elements(final_pages) WHERE value->>'role' IN
      ('entry_fills_recent','entry_fills_history','close_fills_recent','close_fills_history') LOOP
    FOR raw_row IN SELECT value FROM jsonb_array_elements(fill_source->'data') LOOP
      IF fill_data ? (raw_row->>'billId') AND fill_data->(raw_row->>'billId') IS DISTINCT FROM raw_row THEN RETURN false; END IF;
      fill_data := fill_data||jsonb_build_object(raw_row->>'billId',raw_row);
    END LOOP;
  END LOOP;
  IF jsonb_array_length(analysis->'inputs'->'fills')<>(SELECT count(*) FROM jsonb_object_keys(fill_data))
     OR jsonb_array_length(analysis->'inputs'->'cashflows')<>jsonb_array_length(analysis->'inputs'->'fills') THEN RETURN false; END IF;
  FOR evidence IN SELECT value FROM jsonb_array_elements(analysis->'inputs'->'fills') LOOP
    raw_row := fill_data->substr(evidence->>'evidence_id',6);
    IF raw_row IS NULL OR evidence->>'evidence_id' IS DISTINCT FROM 'fill_'||(raw_row->>'billId')
       OR evidence->>'report_id' IS DISTINCT FROM r.report_id OR evidence->>'candidate_sha256' IS DISTINCT FROM analysis->>'candidate_sha256'
       OR evidence->>'instrument_id' IS DISTINCT FROM candidate->>'instrument_id' OR evidence->>'account_id' IS DISTINCT FROM r.account_id
       OR evidence->>'environment' IS DISTINCT FROM 'demo' OR evidence->>'order_id' IS DISTINCT FROM raw_row->>'ordId'
       OR evidence->>'side' IS DISTINCT FROM raw_row->>'side'
       OR public.qualification_native_number_v55(evidence->'contracts')<>public.qualification_native_number_v55(raw_row->'fillSz')
       OR public.qualification_native_number_v55(evidence->'price')<>public.qualification_native_number_v55(raw_row->'fillPx')
       OR extract(epoch FROM (evidence->>'occurred_at')::timestamptz)*1000<>public.qualification_native_clock_v55(raw_row->'fillTime')
       OR evidence->>'role' IS DISTINCT FROM (CASE WHEN raw_row->>'ordId'=terminal->>'entry_order_id' THEN 'entry' ELSE 'exit' END)
       OR evidence->'reference_price' IS DISTINCT FROM 'null'::jsonb OR evidence->'reference_source_sha256' IS DISTINCT FROM 'null'::jsonb
       OR evidence->'exit_reason' IS DISTINCT FROM (CASE WHEN raw_row->>'ordId'=terminal->>'entry_order_id' THEN 'null'::jsonb ELSE '"other"'::jsonb END)
       OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(final_pages) p WHERE p->'meta'->>'body_sha256'=evidence->>'source_sha256'
           AND p->'data' @> jsonb_build_array(raw_row) AND (p->'meta'->>'eof_at')::timestamptz=(evidence->>'recorded_at')::timestamptz) THEN RETURN false; END IF;
    SELECT value INTO receipt FROM jsonb_array_elements(data->'source_links') WHERE value->>'fill_evidence_id'=evidence->>'evidence_id';
    IF (SELECT count(*) FROM jsonb_array_elements(data->'source_links') x WHERE x->>'fill_evidence_id'=evidence->>'evidence_id')<>1
       OR receipt->>'fee_evidence_id' IS DISTINCT FROM 'fee_'||(raw_row->>'billId')
       OR receipt->>'bill_id' IS DISTINCT FROM raw_row->>'billId' OR receipt->>'trade_id' IS DISTINCT FROM raw_row->>'tradeId'
       OR receipt->>'order_id' IS DISTINCT FROM raw_row->>'ordId' OR jsonb_array_length(receipt->'source_pages')<>2
       OR (SELECT array_agg(value->>'role' ORDER BY value->>'role') FROM jsonb_array_elements(receipt->'source_pages')) IS DISTINCT FROM
           (CASE WHEN evidence->>'role'='entry' THEN ARRAY['entry_fills_history','entry_fills_recent'] ELSE ARRAY['close_fills_history','close_fills_recent'] END)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(receipt->'source_pages') s WHERE NOT EXISTS(
           SELECT 1 FROM jsonb_array_elements(final_pages) p WHERE p->>'role'=s->>'role'
             AND p->'meta'->>'body_sha256'=s->>'raw_body_sha256' AND p->'data' @> jsonb_build_array(raw_row))) THEN RETURN false; END IF;
    SELECT value INTO fee_row FROM jsonb_array_elements(analysis->'inputs'->'cashflows') WHERE value->>'fill_id'=evidence->>'evidence_id';
    IF (SELECT count(*) FROM jsonb_array_elements(analysis->'inputs'->'cashflows') x WHERE x->>'fill_id'=evidence->>'evidence_id')<>1
       OR fee_row->>'evidence_id' IS DISTINCT FROM 'fee_'||(raw_row->>'billId') OR fee_row->>'kind' IS DISTINCT FROM 'fee'
       OR fee_row->>'currency' IS DISTINCT FROM r.settlement_currency OR fee_row->>'source_sha256' IS DISTINCT FROM evidence->>'source_sha256'
       OR fee_row->>'report_id' IS DISTINCT FROM evidence->>'report_id' OR fee_row->>'candidate_sha256' IS DISTINCT FROM evidence->>'candidate_sha256'
       OR fee_row->>'instrument_id' IS DISTINCT FROM evidence->>'instrument_id' OR fee_row->>'account_id' IS DISTINCT FROM evidence->>'account_id'
       OR fee_row->>'environment' IS DISTINCT FROM 'demo' OR fee_row->>'occurred_at' IS DISTINCT FROM evidence->>'occurred_at'
       OR fee_row->>'recorded_at' IS DISTINCT FROM evidence->>'recorded_at'
       OR public.qualification_native_number_v55(fee_row->'amount')<>public.qualification_native_number_v55(raw_row->'fee') THEN RETURN false; END IF;
    fee_total := fee_total+public.qualification_native_number_v55(raw_row->'fee'); known_fills := known_fills+1;
    fill_data := fill_data-(raw_row->>'billId');
  END LOOP;
  IF fill_data<>'{}'::jsonb OR jsonb_array_length(data->'source_links')<>known_fills THEN RETURN false; END IF;
  FOREACH metric_name IN ARRAY ARRAY['funding','pnl','net_cash_pnl_to_date','mfe','mae','max_favorable_r','max_adverse_r','exit_slippage_bps'] LOOP
    metric := analysis->metric_name;
    IF metric->'value' IS DISTINCT FROM 'null'::jsonb OR metric->'numerator' IS DISTINCT FROM 'null'::jsonb
       OR metric->'denominator' IS DISTINCT FROM 'null'::jsonb OR jsonb_typeof(metric->'unknown_reason') IS DISTINCT FROM 'string' THEN RETURN false; END IF;
  END LOOP;
  quantity := public.qualification_native_number_v55(candidate->'planned_contracts');
  contract_value := public.qualification_native_number_v55(candidate->'contract_value_base');
  planned_entry := public.qualification_native_number_v55(candidate->'entry'); stop_price := public.qualification_native_number_v55(candidate->'stop_loss');
  SELECT public.qualification_native_number_v55(value->'data'->0->'avgPx') INTO entry_average FROM jsonb_array_elements(final_pages) WHERE value->>'role'='entry_order_after';
  SELECT public.qualification_native_number_v55(value->'data'->0->'avgPx') INTO close_average FROM jsonb_array_elements(final_pages) WHERE value->>'role'='close_order_after';
  sign_value := CASE candidate->>'direction' WHEN 'long' THEN 1 WHEN 'short' THEN -1 ELSE NULL END;
  IF sign_value IS NULL OR quantity<=0 OR contract_value<=0 OR planned_entry<=0 OR stop_price<=0 THEN RETURN false; END IF;
  expected_metrics := jsonb_build_object('entry_contracts',jsonb_build_array(quantity,1),'exit_contracts',jsonb_build_array(quantity,1),
    'remaining_contracts',jsonb_build_array(0,1),'actual_fill',jsonb_build_array(entry_average,1),
    'exit_price',jsonb_build_array(close_average,1),'fees',jsonb_build_array(fee_total,1),
    'gross_realized_pnl',jsonb_build_array(sign_value*quantity*contract_value*(close_average-entry_average),1),
    'original_risk',jsonb_build_array(quantity*contract_value*abs(planned_entry-stop_price),1),
    'entry_slippage_bps',jsonb_build_array(sign_value*(entry_average-planned_entry)*10000,planned_entry));
  FOR metric_name, expected_metric IN SELECT key,value FROM jsonb_each(expected_metrics) LOOP
    metric := analysis->metric_name;
    IF NOT coalesce(metric->>'numerator' ~ '^-?[0-9]{1,300}$',false) OR NOT coalesce(metric->>'denominator' ~ '^[1-9][0-9]{0,299}$',false)
       OR metric->'value'='null'::jsonb OR metric->'value' IS NULL OR metric->'unknown_reason' IS DISTINCT FROM 'null'::jsonb
       OR (metric->>'numerator')::numeric*(expected_metric->>1)::numeric<>(metric->>'denominator')::numeric*(expected_metric->>0)::numeric THEN RETURN false; END IF;
  END LOOP;
  seen_head := closed.outcome_id; sequence_no := closed.sequence; revision_no := closed.ledger_revision;
  FOR q IN SELECT * FROM public.qualification_submission_outcomes WHERE reservation_id=rid AND sequence>closed.sequence AND sequence<=o.sequence ORDER BY sequence LOOP
    sequence_no := sequence_no+1;
    IF q.sequence<>sequence_no OR q.previous_sha256 IS DISTINCT FROM seen_head OR q.ledger_revision<=revision_no
       OR q.intent_transition_id<>closed.intent_transition_id OR q.intent_sha256<>closed.intent_sha256
       OR q.exchange_request_sha256<>closed.exchange_request_sha256
       OR encode(sha256(convert_to(q.capture_json,'UTF8')),'hex')<>q.capture_sha256
       OR encode(sha256(convert_to(q.outcome_json,'UTF8')),'hex')<>q.outcome_id THEN RETURN false; END IF;
    seen_head := q.outcome_id; revision_no := q.ledger_revision;
  END LOOP;
  RETURN seen_head=o.outcome_id;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
""".replace("__REPORT_POLICY_SHA256__", REPORT_POLICY_SHA256)

_NATIVE_SPOOL_BRANCH = """        IF NEW.eligible IS TRUE THEN
          SELECT * INTO o FROM public.qualification_submission_outcomes WHERE outcome_id=NEW.outcome_id;
          SELECT * INTO r FROM public.qualification_reservations WHERE reservation_id=NEW.reservation_id;
          j := public.qualification_native_plain_v55(o.capture_json);
          IF r.report_id IS DISTINCT FROM NEW.report_id OR NEW.created_at IS DISTINCT FROM o.recorded_at
             OR encode(sha256(NEW.report_bytes),'hex') IS DISTINCT FROM NEW.report_sha256
             OR encode(sha256(convert_to(NEW.policy_json,'UTF8')),'hex') IS DISTINCT FROM NEW.policy_sha256
             OR NOT public.qualification_native_report_eligible_v55(NEW.outcome_id,NEW.reservation_id,NEW.report_sha256,NEW.policy_sha256)
             OR NEW.report_bytes IS DISTINCT FROM convert_to((SELECT capture_json::jsonb->>'forensics_json'
               FROM public.qualification_submission_outcomes WHERE outcome_id=j->>'forensics_outcome_id'),'UTF8') THEN
            RAISE EXCEPTION 'native_terminal_spool_denied';
          END IF;
          RETURN NEW;
        END IF;
"""
SPOOL_SQL = LEGACY_SPOOL_SQL.replace(
    "      BEGIN\n", "      BEGIN\n" + _NATIVE_SPOOL_BRANCH, 1
)
_NATIVE_PROJECTION_BRANCH = """      DECLARE s record;
      BEGIN
        SELECT * INTO s FROM public.qualification_report_spool WHERE spool_id=NEW.spool_id;
        IF s.spool_id IS NOT NULL AND s.eligible IS TRUE AND NEW.report_sha256=s.report_sha256
           AND NEW.policy_sha256=s.policy_sha256 AND NEW.queue_namespace=s.queue_namespace AND NEW.verified_at>=s.created_at
           AND public.qualification_native_report_eligible_v55(s.outcome_id,s.reservation_id,s.report_sha256,s.policy_sha256) THEN
          RETURN NEW;
        END IF;
"""
PROJECTION_SQL = LEGACY_PROJECTION_SQL.replace(
    "      BEGIN\n", _NATIVE_PROJECTION_BRANCH, 1
)

PROJECTION_ELIGIBLE_SQL = r"""CREATE FUNCTION public.qualification_native_projection_eligible_v55(
  sid text, report_sha text, policy_sha text, namespace text, verified_at timestamptz) RETURNS boolean AS $ctcc$
DECLARE s record;
BEGIN
  SELECT * INTO s FROM public.qualification_report_spool WHERE spool_id=sid;
  RETURN coalesce(s.spool_id IS NOT NULL AND s.eligible IS TRUE AND s.report_sha256=report_sha
    AND s.policy_sha256=policy_sha AND s.queue_namespace=namespace AND verified_at>=s.created_at
    AND public.qualification_native_report_eligible_v55(s.outcome_id,s.reservation_id,report_sha,policy_sha),false);
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""
SPOOL_CHECK = "eligible=false OR (encode(sha256(report_bytes),'hex')=report_sha256 AND encode(sha256(convert_to(policy_json,'UTF8')),'hex')=policy_sha256 AND spool_id=encode(sha256(convert_to(public.qualification_native_canonical_v55(jsonb_build_object('outcome_id',outcome_id,'policy_sha256',policy_sha256,'queue_namespace',queue_namespace,'report_sha256',report_sha256)),'UTF8')),'hex') AND public.qualification_native_report_eligible_v55(outcome_id,reservation_id,report_sha256,policy_sha256))"
PROJECTION_CHECK = "public.qualification_native_projection_eligible_v55(spool_id,report_sha256,policy_sha256,queue_namespace,verified_at)"
FUNCTIONS = (
    (
        "qualification_native_report_eligible_v55",
        "text, text, text, text",
        "boolean",
        ELIGIBLE_SQL,
        "$ctcc$",
        True,
    ),
    (
        "qualification_native_projection_eligible_v55",
        "text, text, text, text, timestamp with time zone",
        "boolean",
        PROJECTION_ELIGIBLE_SQL,
        "$ctcc$",
        True,
    ),
    ("qualification_spool_binding", "", "trigger", SPOOL_SQL, "$$", False),
    ("qualification_projection_binding", "", "trigger", PROJECTION_SQL, "$$", False),
)


def body_sha(sql, delimiter):
    return hashlib.sha256(sql.split(delimiter)[1].encode()).hexdigest()


def statements():
    if (
        SPOOL_SQL.replace(_NATIVE_SPOOL_BRANCH, "", 1) != LEGACY_SPOOL_SQL
        or PROJECTION_SQL.replace(_NATIVE_PROJECTION_BRANCH, "      BEGIN\n", 1)
        != LEGACY_PROJECTION_SQL
    ):
        raise ValueError("native_terminal_report_legacy0042_changed")
    return (
        ELIGIBLE_SQL,
        PROJECTION_ELIGIBLE_SQL,
        SPOOL_SQL,
        PROJECTION_SQL,
        "ALTER TABLE public.qualification_report_spool DROP CONSTRAINT ck_qualification_report_spool_transport_unverified, ADD CONSTRAINT ck_qualification_report_spool_transport_unverified CHECK ("
        + SPOOL_CHECK
        + ")",
        "ALTER TABLE public.qualification_report_projection_receipts DROP CONSTRAINT ck_qualification_report_projection_receipts_transport_unverified, ADD CONSTRAINT ck_qualification_report_projection_receipts_transport_unverified CHECK ("
        + PROJECTION_CHECK
        + ")",
    )


def restore_statements():
    statements()
    return (
        "ALTER TABLE public.qualification_report_spool DROP CONSTRAINT ck_qualification_report_spool_transport_unverified, ADD CONSTRAINT ck_qualification_report_spool_transport_unverified CHECK (eligible=false)",
        "ALTER TABLE public.qualification_report_projection_receipts DROP CONSTRAINT ck_qualification_report_projection_receipts_transport_unverified, ADD CONSTRAINT ck_qualification_report_projection_receipts_transport_unverified CHECK (false)",
        LEGACY_SPOOL_SQL,
        LEGACY_PROJECTION_SQL,
        "DROP FUNCTION public.qualification_native_projection_eligible_v55(text,text,text,text,timestamptz)",
        "DROP FUNCTION public.qualification_native_report_eligible_v55(text,text,text,text)",
    )
