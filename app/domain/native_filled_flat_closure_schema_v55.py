"""Unsealed sole55 filled-flat DATA predicate over corrected live zero branch.

The helpers read the existing immutable journal. They mint no owner, permit,
lease, Arm, report delivery or exchange write. Native ownership and a distinct
committed readback are required by the application in addition to this trigger.
"""

import hashlib

from app.domain import native_zero_fok_closure_schema_v55 as zero

FUNCTION = zero.FUNCTION
ORIGINAL_BODY_SHA256 = zero.ORIGINAL_BODY_SHA256
ORIGINAL_SQL = zero.ORIGINAL_SQL
HISTORICAL_UNCOMPILED_ZERO_BODY_SHA256 = zero.HISTORICAL_UNCOMPILED_BODY_SHA256
ZERO_BODY_SHA256 = "2c6deca6c5fd420aca430d0054d7cf19aafcf45a1c0ccd9f59faa795bc6da09c"
WITNESS_VERSION = "ctcc.native_owned_filled_flat_closure.v1"
TRANSITION_REASON = "post_submit_native_filled_flat_v1"

PLAIN_SQL = r"""CREATE FUNCTION public.qualification_native_plain_v55(raw text) RETURNS jsonb AS $ctcc$
DECLARE n record; clock text; v jsonb;
BEGIN
  IF octet_length(raw) NOT BETWEEN 1 AND 524288 THEN
    RAISE EXCEPTION 'native_filled_plain_bound';
  END IF;
  FOR n IN WITH RECURSIVE nodes(value,depth) AS (
    SELECT raw::json,0 UNION ALL
    SELECT ch.value,p.depth+1 FROM nodes p CROSS JOIN LATERAL (
      SELECT value FROM json_each(CASE WHEN json_typeof(p.value)='object' THEN p.value ELSE '{}'::json END)
      UNION ALL
      SELECT value FROM json_array_elements(CASE WHEN json_typeof(p.value)='array' THEN p.value ELSE '[]'::json END)
    ) ch
  ) SELECT value,depth,json_typeof(value) AS kind FROM nodes LOOP
    IF n.depth>24 OR (n.kind='number' AND n.value::text !~ '^-?(0|[1-9][0-9]*)$')
       OR (n.kind='array' AND json_array_length(n.value)>4096) THEN
      RAISE EXCEPTION 'native_filled_plain_shape';
    END IF;
    IF n.kind='object' AND (SELECT count(*)>128 OR count(*)<>count(DISTINCT key COLLATE "C") FROM json_each(n.value)) THEN
      RAISE EXCEPTION 'native_filled_plain_duplicate';
    END IF;
  END LOOP;
  RETURN raw::jsonb;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""

CANONICAL_SQL = r"""CREATE FUNCTION public.qualification_native_canonical_v55(j jsonb) RETURNS text AS $ctcc$
DECLARE result text;
BEGIN
  CASE jsonb_typeof(j)
  WHEN 'object' THEN
    SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||public.qualification_native_canonical_v55(value),',' ORDER BY key COLLATE "C"),'')||'}'
      INTO result FROM jsonb_each(j);
  WHEN 'array' THEN
    SELECT '['||coalesce(string_agg(public.qualification_native_canonical_v55(value),',' ORDER BY ord),'')||']'
      INTO result FROM jsonb_array_elements(j) WITH ORDINALITY x(value,ord);
  WHEN 'string' THEN
    IF NOT ((j#>>'{}') COLLATE "C" ~ '^[ -~]*$') THEN RAISE EXCEPTION 'native_filled_canonical_ascii'; END IF;
    result := j::text;
  WHEN 'number' THEN
    IF j::text !~ '^-?(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'native_filled_canonical_integer'; END IF;
    result := j::text;
  ELSE result := j::text;
  END CASE;
  RETURN result;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""

MANIFEST_SQL = r"""CREATE FUNCTION public.qualification_native_manifest_v55(
  rid text, mid text, origin text, original_session text, before_at timestamptz) RETURNS jsonb AS $ctcc$
DECLARE m record; p record; q record; node record; part_id text; part jsonb;
  manifest jsonb; item jsonb; meta jsonb; payload jsonb; query jsonb;
  pages jsonb := '[]'::jsonb; used text[] := ARRAY[]::text[]; body bytea;
  page_no integer := 0; offset_no integer; total_bytes bigint := 0;
  first_at timestamptz; last_at timestamptz; previous_at timestamptz;
  first_mono numeric; previous_mono numeric; read_session text; credential text;
  clock_name text; clock_value text;
BEGIN
  SELECT * INTO m FROM public.qualification_submission_outcomes WHERE outcome_id=mid AND reservation_id=rid;
  manifest := public.qualification_native_plain_v55(m.capture_json);
  IF m.outcome_id IS NULL OR m.observation_kind<>'reconciliation' OR m.status<>'uncertain'
     OR manifest->>'version' IS DISTINCT FROM 'ctcc.native_read_manifest.v1'
     OR manifest->>'reservation_id' IS DISTINCT FROM rid OR manifest->>'computation' IS DISTINCT FROM 'completed'
     OR manifest->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR manifest->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_typeof(manifest->'pages') IS DISTINCT FROM 'array'
     OR jsonb_array_length(manifest->'pages') NOT BETWEEN 1 AND 256 THEN
    RAISE EXCEPTION 'native_filled_complete_manifest';
  END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(manifest->'pages') LOOP
    SELECT * INTO p FROM public.qualification_submission_outcomes WHERE outcome_id=item->>'metadata' AND reservation_id=rid;
    meta := public.qualification_native_plain_v55(p.capture_json);
    IF p.outcome_id IS NULL OR p.sequence>=m.sequence OR p.observation_kind<>'reconciliation' OR p.status<>'uncertain'
       OR p.outcome_id=ANY(used) OR meta->>'version' IS DISTINCT FROM 'ctcc.native_read_page_metadata.v1'
       OR meta->>'capture_id' IS DISTINCT FROM manifest->>'capture_id'
       OR meta->>'role' IS DISTINCT FROM item->>'role' OR (meta->>'page_index')::integer IS DISTINCT FROM page_no
       OR meta->'status' IS DISTINCT FROM '200'::jsonb OR jsonb_typeof(item->'parts') IS DISTINCT FROM 'array'
       OR jsonb_array_length(item->'parts') NOT BETWEEN 1 AND 8 THEN
      RAISE EXCEPTION 'native_filled_manifest_metadata';
    END IF;
    used := array_append(used,p.outcome_id);
    FOREACH clock_name IN ARRAY ARRAY['page_index','body_bytes','started_monotonic_ns','headers_monotonic_ns','eof_monotonic_ns'] LOOP
      IF jsonb_typeof(meta->clock_name) IS DISTINCT FROM 'number' OR NOT coalesce((meta->clock_name)::text ~ '^(0|[1-9][0-9]*)$',false) THEN RAISE EXCEPTION 'native_filled_metadata_integer'; END IF;
    END LOOP;
    FOREACH clock_name IN ARRAY ARRAY['read_session_id','credential_session_sha256','original_credential_session_sha256','certificate_sha256','request_signature_sha256','body_sha256'] LOOP
      IF jsonb_typeof(meta->clock_name) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'native_filled_metadata_string'; END IF;
    END LOOP;
    FOREACH clock_name IN ARRAY ARRAY['started_at','headers_at','eof_at'] LOOP
      IF jsonb_typeof(meta->clock_name) IS DISTINCT FROM 'string' OR NOT coalesce(meta->>clock_name ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?\+00:00$',false) THEN RAISE EXCEPTION 'native_filled_metadata_utc'; END IF;
    END LOOP;
    IF page_no=0 THEN
      first_at := (meta->>'started_at')::timestamptz;
      first_mono := (meta->>'started_monotonic_ns')::numeric;
      read_session := meta->>'read_session_id'; credential := meta->>'credential_session_sha256';
    END IF;
    last_at := (meta->>'eof_at')::timestamptz;
    IF NOT coalesce((read_session ~ '^[a-f0-9]{64}$' AND credential ~ '^[a-f0-9]{64}$'
       AND credential<>original_session AND meta->>'read_session_id'=read_session
       AND meta->>'credential_session_sha256'=credential AND meta->>'original_credential_session_sha256'=original_session
       AND first_at<=(meta->>'started_at')::timestamptz AND (meta->>'started_at')::timestamptz<=(meta->>'headers_at')::timestamptz
       AND (meta->>'headers_at')::timestamptz<=last_at AND last_at<=before_at AND last_at-first_at<=interval '60 seconds'
       AND (meta->>'started_monotonic_ns')::numeric<=(meta->>'headers_monotonic_ns')::numeric
       AND (meta->>'headers_monotonic_ns')::numeric<=(meta->>'eof_monotonic_ns')::numeric
       AND (meta->>'eof_monotonic_ns')::numeric-first_mono<=60000000000
       AND (previous_at IS NULL OR previous_at<(meta->>'started_at')::timestamptz)
       AND (previous_mono IS NULL OR previous_mono<(meta->>'started_monotonic_ns')::numeric)),false) THEN
      RAISE EXCEPTION 'native_filled_actual_new_read_chronology';
    END IF;
    IF meta->>'tls_hostname' IS DISTINCT FROM split_part(origin,'/',3)
       OR split_part(meta->>'request_url','?',1) IS DISTINCT FROM origin||(meta->>'path')
       OR NOT coalesce(meta->>'certificate_sha256' ~ '^[a-f0-9]{64}$',false)
       OR NOT coalesce(meta->>'request_signature_sha256' ~ '^[a-f0-9]{64}$',false)
       OR jsonb_typeof(meta->'parameters') IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'native_filled_route';
    END IF;
    SELECT coalesce(jsonb_object_agg(value->>0,value->>1),'{}'::jsonb) INTO query FROM jsonb_array_elements(meta->'parameters');
    IF (SELECT count(*) FROM jsonb_object_keys(query))<>jsonb_array_length(meta->'parameters')
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(meta->'parameters') x
          WHERE jsonb_typeof(x)<>'array' OR jsonb_array_length(x)<>2
             OR jsonb_typeof(x->0)<>'string' OR jsonb_typeof(x->1)<>'string'
             OR (x->>0) !~ '^[A-Za-z][A-Za-z0-9_]*$' OR (x->>1) !~ '^[A-Za-z0-9_.-]+$') THEN
      RAISE EXCEPTION 'native_filled_query_shape';
    END IF;
    IF meta->>'request_url' IS DISTINCT FROM origin||(meta->>'path')||(CASE WHEN query='{}'::jsonb THEN '' ELSE '?'||
       (SELECT string_agg((value->>0)||'='||(value->>1),'&' ORDER BY ord) FROM jsonb_array_elements(meta->'parameters') WITH ORDINALITY x(value,ord)) END) THEN
      RAISE EXCEPTION 'native_filled_exact_outgoing_query';
    END IF;
    body := ''::bytea; offset_no := 0;
    FOR part_id IN SELECT jsonb_array_elements_text(item->'parts') LOOP
      SELECT * INTO q FROM public.qualification_submission_outcomes WHERE outcome_id=part_id AND reservation_id=rid;
      part := public.qualification_native_plain_v55(q.capture_json);
      IF q.outcome_id IS NULL OR q.outcome_id=ANY(used) OR q.sequence<=p.sequence OR q.sequence>=m.sequence
         OR q.observation_kind<>'reconciliation' OR q.status<>'uncertain'
         OR part->>'version' IS DISTINCT FROM 'ctcc.native_read_raw_part.v1'
         OR part->>'capture_id' IS DISTINCT FROM manifest->>'capture_id' OR (part->>'page_index')::integer IS DISTINCT FROM page_no
         OR part->>'metadata_outcome_id' IS DISTINCT FROM p.outcome_id OR (part->>'offset')::integer IS DISTINCT FROM offset_no
         OR jsonb_typeof(part->'page_index') IS DISTINCT FROM 'number' OR jsonb_typeof(part->'offset') IS DISTINCT FROM 'number'
         OR jsonb_typeof(part->'raw_hex') IS DISTINCT FROM 'string'
         OR NOT coalesce((octet_length(part->>'raw_hex') BETWEEN 2 AND 131072 AND octet_length(part->>'raw_hex')%2=0
            AND part->>'raw_hex' ~ '^[a-f0-9]+$'),false)
         OR encode(sha256(decode(part->>'raw_hex','hex')),'hex') IS DISTINCT FROM part->>'raw_sha256' THEN
        RAISE EXCEPTION 'native_filled_raw_part';
      END IF;
      used := array_append(used,q.outcome_id); body := body||decode(part->>'raw_hex','hex'); offset_no := octet_length(body);
      IF offset_no>524288 THEN RAISE EXCEPTION 'native_filled_raw_bound'; END IF;
    END LOOP;
    total_bytes := total_bytes+offset_no;
    IF total_bytes>67108864 OR offset_no IS DISTINCT FROM (meta->>'body_bytes')::integer
       OR encode(sha256(body),'hex') IS DISTINCT FROM meta->>'body_sha256'
       OR meta->>'body_sha256' IS DISTINCT FROM item->>'body_sha256' THEN RAISE EXCEPTION 'native_filled_raw_digest'; END IF;
    payload := public.qualification_native_plain_v55(convert_from(body,'UTF8'));
    IF payload->'code' IS DISTINCT FROM '"0"'::jsonb OR jsonb_typeof(payload->'data') IS DISTINCT FROM 'array'
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(payload->'data') x WHERE jsonb_typeof(x)<>'object') THEN
      RAISE EXCEPTION 'native_filled_raw_response';
    END IF;
    FOR node IN WITH RECURSIVE nodes(value) AS (
      SELECT payload UNION ALL SELECT ch.value FROM nodes parent_node CROSS JOIN LATERAL (
        SELECT value FROM jsonb_each(CASE WHEN jsonb_typeof(parent_node.value)='object' THEN parent_node.value ELSE '{}'::jsonb END)
        UNION ALL SELECT value FROM jsonb_array_elements(CASE WHEN jsonb_typeof(parent_node.value)='array' THEN parent_node.value ELSE '[]'::jsonb END)) ch
    ) SELECT value FROM nodes WHERE jsonb_typeof(value)='object' LOOP
      FOREACH clock_name IN ARRAY ARRAY['cTime','uTime','fillTime','ts'] LOOP
        IF node.value ? clock_name AND node.value->clock_name NOT IN ('null'::jsonb,'""'::jsonb) THEN
          clock_value := node.value->>clock_name;
          IF jsonb_typeof(node.value->clock_name)<>'string' OR clock_value !~ '^[0-9]{1,16}$'
             OR clock_value::numeric<=0 OR clock_value::numeric>extract(epoch FROM last_at)*1000 THEN
            RAISE EXCEPTION 'native_filled_source_clock';
          END IF;
        END IF;
      END LOOP;
    END LOOP;
    pages := pages||jsonb_build_array(jsonb_build_object('role',item->>'role','meta',meta,'query',query,'data',payload->'data'));
    previous_at := last_at; previous_mono := (meta->>'eof_monotonic_ns')::numeric; page_no := page_no+1;
  END LOOP;
  RETURN jsonb_build_object('manifest',manifest,'pages',pages,'first_at',first_at,'last_at',last_at,
    'first_mono',first_mono,'last_mono',previous_mono,'read_session',read_session,'credential',credential);
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""

ROWS_SQL = r"""CREATE FUNCTION public.qualification_native_rows_v55(
  pages jsonb, role_name text, route text, base_query jsonb, cursor_name text) RETURNS jsonb AS $ctcc$
DECLARE p jsonb; row jsonb; result jsonb := '[]'::jsonb; expected jsonb; after_id text;
  seen text[] := ARRAY[]::text[]; identifier text; last_id numeric; count_pages integer := 0; terminal boolean := false;
BEGIN
  FOR p IN SELECT value FROM jsonb_array_elements(pages) WHERE value->>'role'=role_name LOOP
    count_pages := count_pages+1;
    IF terminal OR count_pages>(CASE WHEN cursor_name IS NULL THEN 1 ELSE 16 END) THEN RAISE EXCEPTION 'native_filled_page_count'; END IF;
    expected := base_query;
    IF cursor_name IS NOT NULL THEN expected := expected||'{"limit":"100"}'::jsonb; END IF;
    IF after_id IS NOT NULL THEN expected := expected||jsonb_build_object('after',after_id); END IF;
    IF p->'meta'->>'path' IS DISTINCT FROM route OR p->'query' IS DISTINCT FROM expected THEN RAISE EXCEPTION 'native_filled_exact_page_query'; END IF;
    IF cursor_name IS NULL THEN result := p->'data'; terminal := true; CONTINUE; END IF;
    IF jsonb_array_length(p->'data')>100 THEN RAISE EXCEPTION 'native_filled_cursor_limit'; END IF;
    IF p->'data'='[]'::jsonb THEN terminal := true; CONTINUE; END IF;
    last_id := NULL;
    FOR row IN SELECT value FROM jsonb_array_elements(p->'data') LOOP
      identifier := row->>cursor_name;
      IF jsonb_typeof(row->cursor_name) IS DISTINCT FROM 'string' OR NOT coalesce(identifier ~ '^[1-9][0-9]{0,39}$',false) OR identifier=ANY(seen)
         OR (last_id IS NOT NULL AND identifier::numeric>=last_id)
         OR (after_id IS NOT NULL AND identifier::numeric>=after_id::numeric) THEN RAISE EXCEPTION 'native_filled_cursor_identity'; END IF;
      seen := array_append(seen,identifier); last_id := identifier::numeric; result := result||jsonb_build_array(row);
    END LOOP;
    after_id := identifier;
  END LOOP;
  IF count_pages=0 OR NOT terminal THEN RAISE EXCEPTION 'native_filled_terminal_empty_page_required'; END IF;
  RETURN result;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""

NUMBER_SQL = r"""CREATE FUNCTION public.qualification_native_number_v55(j jsonb) RETURNS numeric AS $ctcc$
BEGIN
  IF jsonb_typeof(j) IS DISTINCT FROM 'string' OR NOT coalesce(j#>>'{}' ~ '^-?(0|[1-9][0-9]{0,23})(\.[0-9]{1,24})?$',false) THEN
    RAISE EXCEPTION 'native_filled_finite_decimal';
  END IF;
  RETURN (j#>>'{}')::numeric;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""

CLOCK_SQL = r"""CREATE FUNCTION public.qualification_native_clock_v55(j jsonb) RETURNS numeric AS $ctcc$
BEGIN
  IF jsonb_typeof(j) IS DISTINCT FROM 'string' OR NOT coalesce(j#>>'{}' ~ '^[1-9][0-9]{0,15}$',false) THEN
    RAISE EXCEPTION 'native_filled_required_source_clock';
  END IF;
  RETURN (j#>>'{}')::numeric;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""

# The original UID/Control/scope/original20/B1/outcome replay is reused as an
# exact finite text fragment. It is authenticated before substitution and is
# deliberately not a second, weaker reimplementation of original identity.
_ZERO_BODY = zero.CURRENT_SQL.split("$$")[1]
_DECLARATIONS = _ZERO_BODY.split("DECLARE\n", 1)[1].split("BEGIN\n", 1)[0]
_ORIGINAL_LOCK = _ZERO_BODY.split("      request := OLD.request_json::jsonb;", 1)[
    1
].split("      inner_intent :=", 1)[0]
_ORIGINAL_IDENTITY = _ZERO_BODY.split("      SELECT * INTO issued FROM", 1)[1].split(
    "      -- All retained outcomes", 1
)[0]
_ORIGINAL_OUTCOMES = _ZERO_BODY.split(
    "      FOR q IN SELECT * FROM public.qualification_submission_outcomes", 1
)[1].split("      SELECT * INTO m FROM", 1)[0]

_FILLED_START = r"""
  SELECT * INTO r FROM public.qualification_reservations WHERE reservation_id=rid;
  IF r.reservation_id IS NULL OR r.environment<>'demo'
     OR NOT ((r.state='uncertain' AND r.state_revision=3) OR
       (r.state='reconciled_flat' AND r.state_revision=4 AND r.updated_at=closing_at))
     OR r.request_json::jsonb->>'contract_version' IS DISTINCT FROM 'ctcc-reservation-request-native-v4'
     OR r.request_json::jsonb->'origin'->>'original_event_key' IS DISTINCT FROM r.original_event_key
     OR r.request_json::jsonb->'scope'->>'environment' IS DISTINCT FROM r.environment
     OR r.request_json::jsonb->'scope'->>'account_id' IS DISTINCT FROM r.account_id
     OR r.request_json::jsonb->'scope'->>'settlement_currency' IS DISTINCT FROM r.settlement_currency
     OR r.coverage_json::jsonb IS DISTINCT FROM r.request_json::jsonb->'coverage'
     OR encode(sha256(convert_to(r.request_json,'UTF8')),'hex') IS DISTINCT FROM r.request_sha256 THEN
    RETURN false;
  END IF;
  request := r.request_json::jsonb;
"""

_FILLED_WITNESS = r"""
  inner_intent := public.qualification_native_plain_v55(public.qualification_native_plain_v55(t.evidence_json)->>'intent_json');
  expected := inner_intent->'exact_order'; witness := public.qualification_native_plain_v55(o.capture_json);
  IF inner_intent->>'version' IS DISTINCT FROM 'ctcc-demo-submit-intent-native-v2'
     OR inner_intent->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR witness->>'version' IS DISTINCT FROM 'ctcc.native_owned_filled_flat_closure.v1'
     OR witness->>'reservation_id' IS DISTINCT FROM r.reservation_id
     OR witness->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR witness->>'intent_sha256' IS DISTINCT FROM o.intent_sha256
     OR witness->>'original_credential_session_sha256' IS DISTINCT FROM request->>'credential_session_sha256'
     OR witness->>'issued_row_sha256' IS DISTINCT FROM request->>'issued_row_sha256'
     OR witness->>'closure_body_sha256' IS DISTINCT FROM '__FILLED_TRIGGER_BODY_SHA256__'
     OR public.qualification_native_plain_v55(witness->>'exact_order_json') IS DISTINCT FROM expected
     OR encode(sha256(convert_to(witness->>'exact_order_json','UTF8')),'hex') IS DISTINCT FROM o.exchange_request_sha256
     OR witness->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR witness->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR expected->>'ordType' IS DISTINCT FROM 'fok' OR expected->>'tdMode' IS DISTINCT FROM 'isolated'
     OR expected->>'instId' IS DISTINCT FROM r.instrument_id
     OR expected->>'side' IS DISTINCT FROM (CASE r.direction WHEN 'long' THEN 'buy' WHEN 'short' THEN 'sell' ELSE NULL END)
     OR expected->'reduceOnly' IS DISTINCT FROM 'false'::jsonb OR expected->>'side' NOT IN ('buy','sell')
     OR expected->>'posSide' IS DISTINCT FROM inner_intent->'execution_binding'->>'position_side'
     OR expected->>'clOrdId' IS DISTINCT FROM 'ctcc'||left(r.reservation_id,28)
     OR inner_intent->>'protection_client_order_id' IS DISTINCT FROM 'CTA'||left(r.reservation_id,29) THEN
    RETURN false;
  END IF;
"""

_FILLED_END = r"""
  SELECT * INTO d FROM public.qualification_submission_outcomes
    WHERE reservation_id=rid AND outcome_id=witness->>'terminal_data_outcome_id';
  terminal := public.qualification_native_plain_v55(d.capture_json);
  assessment := public.qualification_native_plain_v55(terminal->>'assessment_json');
  IF d.outcome_id IS NULL OR d.sequence<>o.sequence-1 OR d.outcome_id IS DISTINCT FROM o.previous_sha256
     OR d.observation_kind<>'reconciliation' OR d.status<>'uncertain'
     OR terminal->>'version' IS DISTINCT FROM 'ctcc.native_closed_trade_readback.v1'
     OR terminal->'risk_hold_retained' IS DISTINCT FROM 'true'::jsonb
     OR terminal->'closure_transition_pending' IS DISTINCT FROM 'true'::jsonb
     OR terminal->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR terminal->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR terminal->>'manifest_outcome_id' IS DISTINCT FROM witness->>'manifest_outcome_id'
     OR terminal->>'assessment_sha256' IS DISTINCT FROM witness->>'assessment_sha256'
     OR encode(sha256(convert_to(terminal->>'assessment_json','UTF8')),'hex') IS DISTINCT FROM witness->>'assessment_sha256'
     OR assessment->>'version' IS DISTINCT FROM 'ctcc.native_closed_trade_data.v1'
     OR assessment->>'classification' IS DISTINCT FROM 'own_entry_and_close_filled_flat'
     OR assessment->>'reservation_id' IS DISTINCT FROM rid OR assessment->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR assessment->>'entry_intent_sha256' IS DISTINCT FROM o.intent_sha256
     OR assessment->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
     OR assessment->'release_authority' IS DISTINCT FROM 'false'::jsonb
     OR assessment->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR assessment->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb THEN RETURN false; END IF;
  IF (SELECT count(*) FROM public.qualification_submission_outcomes
      WHERE reservation_id=rid AND capture_json::jsonb->>'version'='ctcc.native_controlled_close_intent.v1')<>1 THEN RETURN false; END IF;
  SELECT * INTO k FROM public.qualification_submission_outcomes
    WHERE reservation_id=rid AND outcome_id=witness->>'close_intent_outcome_id';
  close_document := public.qualification_native_plain_v55(k.capture_json);
  close_expected := public.qualification_native_plain_v55(close_document->>'exact_close_order_json');
  SELECT * INTO f FROM public.qualification_submission_outcomes
    WHERE reservation_id=rid AND outcome_id=close_document->>'original_filled_outcome_id';
  filled_document := public.qualification_native_plain_v55(f.capture_json);
  IF k.outcome_id IS NULL OR k.sequence>=d.sequence OR k.observation_kind<>'reconciliation' OR k.status<>'uncertain'
     OR close_document->>'version' IS DISTINCT FROM 'ctcc.native_controlled_close_intent.v1'
     OR close_document->>'reservation_id' IS DISTINCT FROM rid
     OR close_document->>'original_event_key' IS DISTINCT FROM r.original_event_key
     OR close_document->>'original_entry_request_sha256' IS DISTINCT FROM r.request_sha256
     OR close_document->>'original_entry_intent_sha256' IS DISTINCT FROM o.intent_sha256
     OR close_document->>'original_credential_session_sha256' IS DISTINCT FROM request->>'credential_session_sha256'
     OR encode(sha256(convert_to(close_document->>'exact_close_order_json','UTF8')),'hex') IS DISTINCT FROM close_document->>'close_request_sha256'
     OR assessment->>'close_intent_sha256' IS DISTINCT FROM k.capture_sha256
     OR assessment->>'preflight_sha256' IS DISTINCT FROM close_document->>'preflight_sha256'
     OR close_document->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR close_document->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR f.outcome_id IS NULL OR f.sequence>=k.sequence OR f.observation_kind<>'reconciliation' OR f.status<>'uncertain'
     OR filled_document->>'version' IS DISTINCT FROM 'ctcc.native_verified_reconciliation_data.v1'
     OR filled_document->'risk_hold_retained' IS DISTINCT FROM 'true'::jsonb
     OR filled_document->'filled_lifecycle_pending' IS DISTINCT FROM 'true'::jsonb
     OR filled_document->'assessment'->>'classification' IS DISTINCT FROM 'filled_and_protected_hold_retained'
     OR filled_document->'assessment'->>'reservation_id' IS DISTINCT FROM rid
     OR filled_document->'assessment'->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR filled_document->'assessment'->>'intent_sha256' IS DISTINCT FROM o.intent_sha256 THEN RETURN false; END IF;
  close_payload := jsonb_build_object('instId',expected->>'instId','tdMode','isolated','ordType','market',
    'side',CASE expected->>'side' WHEN 'buy' THEN 'sell' ELSE 'buy' END,'posSide',expected->>'posSide',
    'sz',expected->>'sz','clOrdId','CTC'||left(rid,29));
  IF inner_intent->'execution_binding'->>'position_mode'='net_mode' AND expected->>'posSide'='net' THEN
    close_payload := close_payload||'{"reduceOnly":true}'::jsonb;
  ELSIF inner_intent->'execution_binding'->>'position_mode'<>'long_short_mode'
      OR NOT ((close_payload->>'side'='sell' AND expected->>'posSide'='long') OR
              (close_payload->>'side'='buy' AND expected->>'posSide'='short')) THEN RETURN false; END IF;
  IF close_expected IS DISTINCT FROM close_payload OR close_document->>'close_client_order_id' IS DISTINCT FROM close_expected->>'clOrdId' THEN RETURN false; END IF;
  original_group := public.qualification_native_manifest_v55(rid,filled_document->>'manifest_outcome_id',witness->>'original_origin',request->>'credential_session_sha256',f.recorded_at);
  preflight_group := public.qualification_native_manifest_v55(rid,close_document->>'preflight_manifest_outcome_id',witness->>'original_origin',request->>'credential_session_sha256',k.recorded_at);
  final_group := public.qualification_native_manifest_v55(rid,witness->>'manifest_outcome_id',witness->>'original_origin',request->>'credential_session_sha256',d.recorded_at);
  IF final_group->'manifest'->>'purpose' IS DISTINCT FROM 'native_controlled_close_final.v1'
     OR final_group->'manifest'->>'close_intent_outcome_id' IS DISTINCT FROM k.outcome_id
     OR final_group->'manifest'->>'assessment_sha256' IS DISTINCT FROM witness->>'assessment_sha256'
     OR preflight_group->'manifest'->>'purpose' IS DISTINCT FROM 'native_controlled_close_preflight.v1'
     OR preflight_group->'manifest'->>'assessment_sha256' IS DISTINCT FROM close_document->>'preflight_sha256'
     OR preflight_group->'manifest'->>'original_filled_outcome_id' IS DISTINCT FROM f.outcome_id
     OR preflight_group->'manifest'->>'original_filled_receipt_sha256' IS DISTINCT FROM original_group->'manifest'->>'assessment_sha256'
     OR preflight_group->'manifest'->>'exact_close_order_sha256' IS DISTINCT FROM close_document->>'close_request_sha256'
     OR close_document->>'read_credential_session_sha256' IS DISTINCT FROM preflight_group->>'credential'
     OR close_document->>'position_mode' IS DISTINCT FROM inner_intent->'execution_binding'->>'position_mode'
     OR close_document->>'protection_client_order_id' IS DISTINCT FROM inner_intent->>'protection_client_order_id'
     OR close_document->>'closing_semantics' IS DISTINCT FROM (CASE WHEN expected->>'posSide'='net' THEN 'net_explicit_reduce_only' ELSE 'hedge_exact_closing_side_and_position_side' END)
     OR original_group->'manifest'->>'durable_lineage_sha256' IS DISTINCT FROM filled_document->'assessment'->>'durable_lineage_sha256'
     OR preflight_group->'manifest'->>'durable_lineage_sha256' IS DISTINCT FROM original_group->'manifest'->>'durable_lineage_sha256'
     OR final_group->'manifest'->>'durable_lineage_sha256' IS DISTINCT FROM original_group->'manifest'->>'durable_lineage_sha256'
     OR original_group->'manifest'->>'assessment_sha256' IS DISTINCT FROM
       encode(sha256(convert_to(public.qualification_native_canonical_v55(filled_document->'assessment'),'UTF8')),'hex')
     OR assessment->>'original_assessment_sha256' IS DISTINCT FROM original_group->'manifest'->>'assessment_sha256'
     OR (original_group->>'last_at')::timestamptz >= (preflight_group->>'first_at')::timestamptz
     OR (preflight_group->>'last_at')::timestamptz >= (final_group->>'first_at')::timestamptz
     OR (original_group->>'last_mono')::numeric >= (preflight_group->>'first_mono')::numeric
     OR (preflight_group->>'last_mono')::numeric >= (final_group->>'first_mono')::numeric
     OR original_group->>'credential'=preflight_group->>'credential'
     OR preflight_group->>'credential'=final_group->>'credential'
     OR original_group->>'credential'=final_group->>'credential' THEN RETURN false; END IF;
  size := public.qualification_native_number_v55(expected->'sz');
  leverage := (request->'risk_inputs'->>'requested_leverage')::numeric;
  IF size<=0 OR size<>public.qualification_native_number_v55(request->'risk_inputs'->'requested_contracts')
     OR public.qualification_native_number_v55(expected->'px')<>public.qualification_native_number_v55(request->'coverage'->'execution'->'entry')
     OR expected->>'instId' IS DISTINCT FROM request->'risk_inputs'->'instrument'->>'instrument_id'
     OR jsonb_typeof(request->'risk_inputs'->'requested_leverage') IS DISTINCT FROM 'number'
     OR NOT coalesce(request->'risk_inputs'->>'requested_leverage' ~ '^[1-9][0-9]{0,2}$',false)
     OR leverage<=0 OR leverage>125 THEN RETURN false; END IF;
  FOR group_no IN 1..3 LOOP
    group_value := CASE group_no WHEN 1 THEN original_group WHEN 2 THEN preflight_group ELSE final_group END;
    pages := group_value->'pages';
    SELECT array_agg(DISTINCT value->>'role' ORDER BY value->>'role') INTO actual_roles FROM jsonb_array_elements(pages);
    required_roles := ARRAY['config_before','config_after','balance','positions','account_risk','pending_orders','leverage',
      'algo_conditional','algo_oco','algo_chase','algo_trigger','algo_move_order_stop','algo_iceberg','algo_twap','algo_smart_iceberg'];
    IF group_no=3 THEN
      required_roles := required_roles||ARRAY['entry_order_before','close_order_before','entry_order_after','close_order_after',
        'entry_fills_recent','entry_fills_history','close_fills_recent','close_fills_history','original_protection_detail',
        'final_fills_recent_all','final_fills_history_all','bills'];
      IF pages->0->>'role'<>'config_before' OR pages->1->>'role'<>'entry_order_before' OR pages->2->>'role'<>'close_order_before'
         OR pages->(jsonb_array_length(pages)-3)->>'role'<>'entry_order_after'
         OR pages->(jsonb_array_length(pages)-2)->>'role'<>'close_order_after'
         OR pages->(jsonb_array_length(pages)-1)->>'role'<>'config_after' THEN RETURN false; END IF;
    ELSE
      required_roles := required_roles||ARRAY['order_before','order_after','fills_recent','fills_history'];
      IF group_no=2 THEN required_roles := required_roles||ARRAY['close_fills_recent_all','close_fills_history_all']; END IF;
      IF pages->0->>'role'<>'config_before' OR pages->1->>'role'<>'order_before'
         OR pages->(jsonb_array_length(pages)-2)->>'role'<>'order_after'
         OR pages->(jsonb_array_length(pages)-1)->>'role'<>'config_after' THEN RETURN false; END IF;
    END IF;
    IF NOT (actual_roles @> required_roles AND actual_roles <@ required_roles) THEN RETURN false; END IF;
    config := public.qualification_native_rows_v55(pages,'config_before','/api/v5/account/config','{}'::jsonb,NULL);
    IF config IS DISTINCT FROM public.qualification_native_rows_v55(pages,'config_after','/api/v5/account/config','{}'::jsonb,NULL)
       OR jsonb_array_length(config)<>1 OR config->0->>'uid' IS DISTINCT FROM r.account_id
       OR jsonb_typeof(config->0->'uid') IS DISTINCT FROM 'string' OR jsonb_typeof(config->0->'mainUid') IS DISTINCT FROM 'string'
       OR config->0->'acctLv' IS DISTINCT FROM '"2"'::jsonb
       OR config->0->>'mainUid' IS DISTINCT FROM witness->>'original_main_uid' OR config->0->>'acctLv' IS DISTINCT FROM '2'
       OR config->0->>'posMode' IS DISTINCT FROM inner_intent->'execution_binding'->>'position_mode' THEN RETURN false; END IF;
    balance := public.qualification_native_rows_v55(pages,'balance','/api/v5/account/balance','{}'::jsonb,NULL);
    risk := public.qualification_native_rows_v55(pages,'account_risk','/api/v5/account/account-position-risk','{}'::jsonb,NULL);
    positions := public.qualification_native_rows_v55(pages,'positions','/api/v5/account/positions','{}'::jsonb,NULL);
    IF jsonb_array_length(balance)<>1 OR jsonb_array_length(risk)<>1
       OR jsonb_typeof(balance->0->'details') IS DISTINCT FROM 'array' OR jsonb_array_length(balance->0->'details')<>1
       OR balance->0->'details'->0->>'ccy' IS DISTINCT FROM r.settlement_currency
       OR public.qualification_native_number_v55(balance->0->'details'->0->'eq')<=0
       OR public.qualification_native_number_v55(balance->0->'details'->0->'availEq')<0
       OR public.qualification_native_number_v55(balance->0->'details'->0->'availEq')>public.qualification_native_number_v55(balance->0->'details'->0->'eq')
       OR public.qualification_native_number_v55(balance->0->'details'->0->'liab')<>0
       OR public.qualification_native_number_v55(balance->0->'details'->0->'borrowFroz')<>0
       OR jsonb_typeof(risk->0->'balData') IS DISTINCT FROM 'array' OR jsonb_array_length(risk->0->'balData')<>1
       OR risk->0->'balData'->0->>'ccy' IS DISTINCT FROM r.settlement_currency
       OR public.qualification_native_number_v55(risk->0->'balData'->0->'eq')<=0
       OR jsonb_typeof(risk->0->'posData') IS DISTINCT FROM 'array'
       OR (balance->0->>'uTime') !~ '^[1-9][0-9]{0,15}$' OR (risk->0->>'ts') !~ '^[1-9][0-9]{0,15}$'
       OR (balance->0->'details'->0->>'uTime') !~ '^[1-9][0-9]{0,15}$'
       OR (balance->0->>'uTime')::numeric>(risk->0->>'ts')::numeric
       OR (balance->0->'details'->0->>'uTime')::numeric>(risk->0->>'ts')::numeric THEN RETURN false; END IF;
    active := '[]'::jsonb; anchors := '[]'::jsonb;
    FOR page IN SELECT value FROM jsonb_array_elements(positions) LOOP
      IF (page->>'cTime') !~ '^[1-9][0-9]{0,15}$' OR (page->>'uTime') !~ '^[1-9][0-9]{0,15}$'
         OR (page->>'cTime')::numeric>(page->>'uTime')::numeric OR (page->>'uTime')::numeric>(risk->0->>'ts')::numeric THEN RETURN false; END IF;
      IF public.qualification_native_number_v55(page->'pos')<>0 THEN active := active||jsonb_build_array(page); END IF;
    END LOOP;
    FOR page IN SELECT value FROM jsonb_array_elements(risk->0->'posData') LOOP
      IF public.qualification_native_number_v55(page->'pos')<>0 THEN anchors := anchors||jsonb_build_array(page); END IF;
    END LOOP;
    IF jsonb_array_length(active)<>(CASE WHEN group_no=3 THEN 0 ELSE 1 END)
       OR jsonb_array_length(anchors)<>jsonb_array_length(active) THEN RETURN false; END IF;
    IF group_no<3 THEN
      position_row := active->0;
      IF position_row->>'posId' IS DISTINCT FROM close_document->>'position_id'
         OR jsonb_typeof(position_row->'posId') IS DISTINCT FROM 'string'
         OR position_row->>'instId' IS DISTINCT FROM expected->>'instId' OR position_row->>'instType' IS DISTINCT FROM 'SWAP'
         OR position_row->>'mgnMode' IS DISTINCT FROM 'isolated' OR position_row->>'posSide' IS DISTINCT FROM expected->>'posSide'
         OR position_row->>'ccy' IS DISTINCT FROM r.settlement_currency
         OR public.qualification_native_number_v55(position_row->'pos')<>(CASE WHEN expected->>'posSide'='net' AND expected->>'side'='sell' THEN -size ELSE size END)
         OR public.qualification_native_number_v55(position_row->'lever')<>leverage
         OR public.qualification_native_number_v55(position_row->'margin')<=0
         OR public.qualification_native_number_v55(position_row->'margin')>public.qualification_native_number_v55(request->'coverage'->'margin_amount')
         OR anchors->0->>'posId' IS DISTINCT FROM position_row->>'posId'
         OR anchors->0->'posId' IS DISTINCT FROM position_row->'posId'
         OR anchors->0->>'instId' IS DISTINCT FROM position_row->>'instId'
         OR anchors->0->>'instType' IS DISTINCT FROM position_row->>'instType'
         OR anchors->0->>'mgnMode' IS DISTINCT FROM position_row->>'mgnMode'
         OR anchors->0->>'posSide' IS DISTINCT FROM position_row->>'posSide'
         OR public.qualification_native_number_v55(anchors->0->'pos')<>public.qualification_native_number_v55(position_row->'pos') THEN RETURN false; END IF;
    END IF;
    IF public.qualification_native_rows_v55(pages,'pending_orders','/api/v5/trade/orders-pending','{}'::jsonb,'ordId')<>'[]'::jsonb THEN RETURN false; END IF;
    protection := NULL;
    FOREACH role_name IN ARRAY ARRAY['conditional','oco','chase','trigger','move_order_stop','iceberg','twap','smart_iceberg'] LOOP
      algos := public.qualification_native_rows_v55(pages,'algo_'||role_name,'/api/v5/trade/orders-algo-pending',jsonb_build_object('ordType',role_name),'algoId');
      IF group_no=3 OR role_name<>'oco' THEN
        IF algos<>'[]'::jsonb THEN RETURN false; END IF;
      ELSE
        IF jsonb_array_length(algos)<>1 THEN RETURN false; END IF;
        protection := algos->0;
      END IF;
    END LOOP;
    rows_value := public.qualification_native_rows_v55(pages,'leverage','/api/v5/account/leverage-info',jsonb_build_object('instId',expected->>'instId','mgnMode','isolated'),NULL);
    IF (SELECT count(*) FROM jsonb_array_elements(rows_value) x WHERE x->>'instId'=expected->>'instId'
        AND x->>'mgnMode'='isolated' AND x->>'posSide'=expected->>'posSide'
        AND public.qualification_native_number_v55(x->'lever')=leverage)<>1 THEN RETURN false; END IF;
    IF group_no=3 THEN
      rows_value := public.qualification_native_rows_v55(pages,'original_protection_detail','/api/v5/trade/order-algo',jsonb_build_object('algoId',close_document->>'protection_algo_id'),NULL);
      IF jsonb_array_length(rows_value)<>1 THEN RETURN false; END IF;
      protection := rows_value->0;
    END IF;
    bracket := expected->'attachAlgoOrds'->0;
    IF protection->>'algoId' IS DISTINCT FROM close_document->>'protection_algo_id'
       OR protection->>'algoClOrdId' IS DISTINCT FROM inner_intent->>'protection_client_order_id'
       OR protection->>'instId' IS DISTINCT FROM expected->>'instId' OR protection->>'instType' IS DISTINCT FROM 'SWAP'
       OR protection->>'tdMode' IS DISTINCT FROM 'isolated' OR protection->>'posSide' IS DISTINCT FROM expected->>'posSide'
       OR protection->>'side' IS DISTINCT FROM close_expected->>'side' OR protection->>'ordType' IS DISTINCT FROM 'oco'
       OR protection->>'state' IS DISTINCT FROM (CASE WHEN group_no=3 THEN 'canceled' ELSE 'live' END)
       OR public.qualification_native_number_v55(protection->'sz')<>size
       OR public.qualification_native_number_v55(protection->'slTriggerPx')<>public.qualification_native_number_v55(bracket->'slTriggerPx')
       OR public.qualification_native_number_v55(protection->'tpTriggerPx')<>public.qualification_native_number_v55(bracket->'tpTriggerPx')
       OR protection->>'slOrdPx' IS DISTINCT FROM bracket->>'slOrdPx' OR protection->>'tpOrdPx' IS DISTINCT FROM bracket->>'tpOrdPx'
       OR protection->>'slTriggerPxType' IS DISTINCT FROM bracket->>'slTriggerPxType'
       OR protection->>'tpTriggerPxType' IS DISTINCT FROM bracket->>'tpTriggerPxType'
       OR (expected->>'posSide'='net' AND protection->'reduceOnly' IS DISTINCT FROM '"true"'::jsonb)
       OR public.qualification_native_clock_v55(protection->'cTime')<=0
       OR (protection->>'cTime') !~ '^[1-9][0-9]{0,15}$' THEN RETURN false; END IF;
    IF group_no=1 THEN original_protection := protection;
    ELSIF protection->>'cTime' IS DISTINCT FROM original_protection->>'cTime' THEN RETURN false; END IF;
    IF group_no=3 AND (protection->>'failCode' IS DISTINCT FROM '' OR (protection->>'uTime') !~ '^[1-9][0-9]{0,15}$'
       OR public.qualification_native_number_v55(protection->'lever')<>leverage) THEN RETURN false; END IF;
    FOR order_no IN 1..(CASE WHEN group_no=3 THEN 2 ELSE 1 END) LOOP
      order_expected := CASE WHEN order_no=2 THEN close_expected ELSE expected END;
      prefix := CASE WHEN group_no<3 THEN '' WHEN order_no=2 THEN 'close_' ELSE 'entry_' END;
      rows_value := public.qualification_native_rows_v55(pages,prefix||'order_before','/api/v5/trade/order',
        jsonb_build_object('instId',order_expected->>'instId','clOrdId',order_expected->>'clOrdId'),NULL);
      last_rows := public.qualification_native_rows_v55(pages,prefix||'order_after','/api/v5/trade/order',
        jsonb_build_object('instId',order_expected->>'instId','clOrdId',order_expected->>'clOrdId'),NULL);
      IF jsonb_array_length(rows_value)<>1 OR jsonb_array_length(last_rows)<>1 THEN RETURN false; END IF;
      order_row := last_rows->0; first_order := rows_value->0;
      IF EXISTS(SELECT 1 FROM unnest(ARRAY['ordId','instId','instType','side','posSide','tdMode','ordType','clOrdId',
           'sz','accFillSz','avgPx','cTime','state','lever','reduceOnly']) selected(field_name) WHERE order_row->selected.field_name IS DISTINCT FROM first_order->selected.field_name)
         OR order_row->>'instId' IS DISTINCT FROM order_expected->>'instId' OR order_row->>'clOrdId' IS DISTINCT FROM order_expected->>'clOrdId'
         OR order_row->>'side' IS DISTINCT FROM order_expected->>'side' OR order_row->>'posSide' IS DISTINCT FROM order_expected->>'posSide'
         OR order_row->>'tdMode' IS DISTINCT FROM 'isolated' OR order_row->>'ordType' IS DISTINCT FROM order_expected->>'ordType'
         OR order_row->>'instType' IS DISTINCT FROM 'SWAP' OR order_row->>'state' IS DISTINCT FROM 'filled'
         OR jsonb_typeof(order_row->'ordId') IS DISTINCT FROM 'string' OR jsonb_typeof(order_row->'reduceOnly') IS DISTINCT FROM 'string'
         OR NOT coalesce(order_row->>'ordId' ~ '^[1-9][0-9]{0,39}$',false)
         OR public.qualification_native_number_v55(order_row->'sz')<>size OR public.qualification_native_number_v55(order_row->'accFillSz')<>size
         OR public.qualification_native_number_v55(order_row->'lever')<>leverage
         OR (order_row->>'cTime') !~ '^[1-9][0-9]{0,15}$' OR (order_row->>'uTime') !~ '^[1-9][0-9]{0,15}$'
         OR (order_row->>'cTime')::numeric>(first_order->>'uTime')::numeric
         OR (first_order->>'uTime')::numeric>(order_row->>'uTime')::numeric THEN RETURN false; END IF;
      IF order_no=1 THEN
        IF order_row->>'reduceOnly' IS DISTINCT FROM 'false'
           OR public.qualification_native_number_v55(order_row->'px')<>public.qualification_native_number_v55(expected->'px')
           OR public.qualification_native_number_v55(first_order->'px')<>public.qualification_native_number_v55(expected->'px') THEN RETURN false; END IF;
        IF group_no=1 THEN original_order := order_row;
        ELSIF order_row->>'ordId' IS DISTINCT FROM original_order->>'ordId' OR order_row->>'cTime' IS DISTINCT FROM original_order->>'cTime' THEN RETURN false; END IF;
      ELSE
        IF order_row->>'ordId'=original_order->>'ordId' OR order_row->>'px' NOT IN ('','0') OR first_order->>'px' NOT IN ('','0')
           OR (expected->>'posSide'='net' AND order_row->>'reduceOnly' IS DISTINCT FROM 'true')
           OR (original_order->>'uTime')::numeric>(order_row->>'cTime')::numeric THEN RETURN false; END IF;
        final_close := order_row;
      END IF;
      IF order_no=1 AND close_document->>'original_entry_order_id' IS DISTINCT FROM order_row->>'ordId' THEN RETURN false; END IF;
      fills := '{}'::jsonb; total := 0; weighted := 0; signed_fees := 0; paid_fees := 0; trade_ids := ARRAY[]::text[];
      FOREACH suffix IN ARRAY ARRAY['recent','history'] LOOP
        role_name := CASE WHEN group_no<3 THEN 'fills_' ELSE prefix||'fills_' END||suffix;
        rows_value := public.qualification_native_rows_v55(pages,role_name,CASE suffix WHEN 'recent' THEN '/api/v5/trade/fills' ELSE '/api/v5/trade/fills-history' END,
          jsonb_build_object('instType','SWAP','instId',expected->>'instId','ordId',order_row->>'ordId'),'billId');
        FOR page IN SELECT value FROM jsonb_array_elements(rows_value) LOOP
          IF fills ? (page->>'billId') AND fills->(page->>'billId') IS DISTINCT FROM page THEN RETURN false; END IF;
          fills := fills||jsonb_build_object(page->>'billId',page);
        END LOOP;
      END LOOP;
      IF fills='{}'::jsonb THEN RETURN false; END IF;
      FOR page IN SELECT value FROM jsonb_each(fills) LOOP
        quantity := public.qualification_native_number_v55(page->'fillSz'); price := public.qualification_native_number_v55(page->'fillPx');
        IF page->'ordId' IS DISTINCT FROM order_row->'ordId' OR page->>'instId' IS DISTINCT FROM expected->>'instId'
           OR page->>'instType' IS DISTINCT FROM 'SWAP' OR page->>'side' IS DISTINCT FROM order_expected->>'side'
           OR page->>'posSide' IS DISTINCT FROM expected->>'posSide' OR page->>'feeCcy' IS DISTINCT FROM r.settlement_currency
           OR jsonb_typeof(page->'tradeId') IS DISTINCT FROM 'string' OR NOT coalesce(page->>'tradeId' ~ '^[1-9][0-9]{0,39}$',false) OR page->>'tradeId'=ANY(trade_ids)
           OR quantity<=0 OR price<=0 OR (page->>'fillTime') !~ '^[1-9][0-9]{0,15}$' OR (page->>'ts') !~ '^[1-9][0-9]{0,15}$'
           OR (page->>'fillTime')::numeric<(order_row->>'cTime')::numeric OR (page->>'fillTime')::numeric>(order_row->>'uTime')::numeric
           OR (page->>'fillTime')::numeric>(page->>'ts')::numeric
           OR (order_no=1 AND ((expected->>'side'='buy' AND price>public.qualification_native_number_v55(expected->'px'))
                OR (expected->>'side'='sell' AND price<public.qualification_native_number_v55(expected->'px')))) THEN RETURN false; END IF;
        trade_ids := array_append(trade_ids,page->>'tradeId'); total := total+quantity; weighted := weighted+quantity*price;
        signed_fees := signed_fees+public.qualification_native_number_v55(page->'fee');
        paid_fees := paid_fees+greatest(0,-public.qualification_native_number_v55(page->'fee'));
      END LOOP;
      IF total<>size OR weighted<>public.qualification_native_number_v55(order_row->'avgPx')*total THEN RETURN false; END IF;
      IF group_no=1 THEN original_fills := fills;
      ELSIF order_no=1 AND fills IS DISTINCT FROM original_fills THEN RETURN false; END IF;
      IF group_no<3 THEN
        IF public.qualification_native_number_v55(position_row->'avgPx')*total<>weighted
           OR EXISTS(SELECT 1 FROM jsonb_each(fills) x WHERE (x.value->>'fillTime')::numeric>(position_row->>'uTime')::numeric) THEN RETURN false; END IF;
        average := public.qualification_native_number_v55(order_row->'avgPx');
        stop_price := public.qualification_native_number_v55(request->'coverage'->'execution'->'stop_loss');
        target_price := public.qualification_native_number_v55(bracket->'tpTriggerPx');
        cost := public.qualification_native_number_v55(request->'coverage'->'execution'->'cost_per_base');
        planned_entry := public.qualification_native_number_v55(request->'coverage'->'execution'->'entry');
        base_size := size*public.qualification_native_number_v55(request->'coverage'->'execution'->'contract_value');
        IF cost<0 OR base_size<=0 OR (expected->>'side'='buy' AND NOT (stop_price<average AND average<target_price))
           OR (expected->>'side'='sell' AND NOT (target_price<average AND average<stop_price))
           OR abs(target_price-planned_entry)-cost<=0 OR abs(average-stop_price)+cost<=0
           OR (abs(target_price-average)-cost)*(abs(planned_entry-stop_price)+cost)
               < (abs(target_price-planned_entry)-cost)*(abs(average-stop_price)+cost)
           OR paid_fees>base_size*cost
           OR base_size*(abs(average-stop_price)+cost)>public.qualification_native_number_v55(request->'coverage'->'risk_amount')
           OR base_size*average>public.qualification_native_number_v55(request->'coverage'->'notional_amount')
           OR base_size*average>leverage*public.qualification_native_number_v55(request->'coverage'->'margin_amount') THEN RETURN false; END IF;
      ELSIF order_no=1 THEN final_entry_fills := fills;
      ELSE final_close_fills := fills; END IF;
    END LOOP;
    IF group_no=2 OR group_no=3 THEN
      role_name := CASE WHEN group_no=2 THEN 'close_fills_recent_all' ELSE 'final_fills_recent_all' END;
      SELECT value->'query' INTO query FROM jsonb_array_elements(pages) WHERE value->>'role'=role_name LIMIT 1;
      window_begin := query->>'begin'; window_end := query->>'end';
      IF window_begin !~ '^[1-9][0-9]{0,15}$' OR window_end !~ '^[1-9][0-9]{0,15}$'
         OR window_begin::numeric<>(original_order->>'cTime')::numeric-1 OR window_begin::numeric>=window_end::numeric THEN RETURN false; END IF;
      all_fills := '{}'::jsonb;
      FOREACH suffix IN ARRAY ARRAY['recent','history'] LOOP
        role_name := CASE WHEN group_no=2 THEN 'close_fills_' ELSE 'final_fills_' END||suffix||'_all';
        rows_value := public.qualification_native_rows_v55(pages,role_name,CASE suffix WHEN 'recent' THEN '/api/v5/trade/fills' ELSE '/api/v5/trade/fills-history' END,
          jsonb_build_object('instType','SWAP','instId',expected->>'instId','begin',window_begin,'end',window_end),'billId');
        FOR page IN SELECT value FROM jsonb_array_elements(rows_value) LOOP
          IF (page->>'ts') !~ '^[1-9][0-9]{0,15}$' OR (page->>'fillTime') !~ '^[1-9][0-9]{0,15}$'
             OR (page->>'ts')::numeric NOT BETWEEN window_begin::numeric AND window_end::numeric
             OR (page->>'fillTime')::numeric>(page->>'ts')::numeric OR page->>'instType' IS DISTINCT FROM 'SWAP'
             OR page->>'instId' IS DISTINCT FROM expected->>'instId'
             OR (all_fills ? (page->>'billId') AND all_fills->(page->>'billId') IS DISTINCT FROM page) THEN RETURN false; END IF;
          all_fills := all_fills||jsonb_build_object(page->>'billId',page);
        END LOOP;
      END LOOP;
      IF all_fills IS DISTINCT FROM (CASE WHEN group_no=2 THEN original_fills ELSE final_entry_fills||final_close_fills END) THEN RETURN false; END IF;
      IF EXISTS(SELECT 1 FROM jsonb_array_elements(pages) x WHERE x->>'role' IN
         ('close_fills_recent_all','close_fills_history_all','final_fills_recent_all','final_fills_history_all')
         AND window_end::numeric>extract(epoch FROM (x->'meta'->>'started_at')::timestamptz)*1000) THEN RETURN false; END IF;
      IF group_no=3 THEN
        rows_value := public.qualification_native_rows_v55(pages,'bills','/api/v5/account/bills-archive',jsonb_build_object('begin',window_begin,'end',window_end),'billId');
        FOR page IN SELECT value FROM jsonb_array_elements(rows_value) LOOP
          IF (page->>'ts') !~ '^[1-9][0-9]{0,15}$' OR (page->>'ts')::numeric NOT BETWEEN window_begin::numeric AND window_end::numeric THEN RETURN false; END IF;
        END LOOP;
        SELECT max((value->>'fillTime')::numeric) INTO final_fill_at FROM jsonb_each(final_close_fills);
        IF final_fill_at>window_end::numeric OR final_fill_at>(balance->0->>'uTime')::numeric
           OR final_fill_at>(risk->0->>'ts')::numeric OR final_fill_at>(protection->>'uTime')::numeric
           OR EXISTS(SELECT 1 FROM jsonb_each(final_close_fills) x WHERE final_entry_fills ? x.key
              OR EXISTS(SELECT 1 FROM jsonb_each(final_entry_fills) y WHERE y.value->>'tradeId'=x.value->>'tradeId'))
           OR assessment->>'entry_order_id' IS DISTINCT FROM original_order->>'ordId'
           OR assessment->>'close_order_id' IS DISTINCT FROM final_close->>'ordId'
           OR assessment->>'entry_client_order_id' IS DISTINCT FROM expected->>'clOrdId'
           OR assessment->>'close_client_order_id' IS DISTINCT FROM close_expected->>'clOrdId'
           OR assessment->>'protection_algo_id' IS DISTINCT FROM close_document->>'protection_algo_id'
           OR assessment->>'protection_client_order_id' IS DISTINCT FROM inner_intent->>'protection_client_order_id' THEN RETURN false; END IF;
      END IF;
    END IF;
  END LOOP;
  RETURN true;
EXCEPTION WHEN OTHERS THEN
  RETURN false;
END;
"""

_FILLED_DECLARATIONS = """  r record; d record; k record; f record;
  terminal jsonb; close_document jsonb; close_expected jsonb; close_payload jsonb; filled_document jsonb;
  original_group jsonb; preflight_group jsonb; final_group jsonb; group_value jsonb; pages jsonb;
  config jsonb; balance jsonb; risk jsonb; positions jsonb; active jsonb; anchors jsonb; position_row jsonb;
  protection jsonb; original_protection jsonb; bracket jsonb; algos jsonb; rows_value jsonb; last_rows jsonb;
  original_order jsonb; order_row jsonb; first_order jsonb; final_close jsonb; order_expected jsonb;
  fills jsonb; original_fills jsonb; final_entry_fills jsonb; final_close_fills jsonb; all_fills jsonb;
  required_roles text[]; actual_roles text[]; trade_ids text[];
  group_no integer; order_no integer; prefix text; suffix text; window_begin text; window_end text;
  size numeric; leverage numeric; quantity numeric; price numeric; total numeric; weighted numeric; signed_fees numeric; paid_fees numeric; final_fill_at numeric;
  average numeric; stop_price numeric; target_price numeric; cost numeric; planned_entry numeric; base_size numeric;
"""


def _original(fragment):
    return fragment.replace("OLD.", "r.").replace("NEW.updated_at", "closing_at")


_LOCK = _original(_ORIGINAL_LOCK)
_LOCK = _LOCK.replace(
    "ORDER BY sequence DESC LIMIT 1;",
    "AND capture_json::jsonb->>'version'='ctcc.native_owned_filled_flat_closure.v1' ORDER BY sequence DESC LIMIT 1;",
)
_LOCK = _LOCK.replace(
    "OR s.updated_at IS DISTINCT FROM closing_at OR o.recorded_at IS DISTINCT FROM closing_at",
    "OR s.updated_at<closing_at OR o.recorded_at IS DISTINCT FROM closing_at",
)
_LOCK = _LOCK.replace(
    "OR s.ledger_revision IS DISTINCT FROM o.ledger_revision",
    "OR s.ledger_revision<o.ledger_revision OR (r.state='uncertain' AND (s.updated_at<>closing_at OR s.ledger_revision<>o.ledger_revision))",
)
_LOCK = _LOCK.replace(
    "OR prior.occurred_at IS DISTINCT FROM r.updated_at",
    "OR prior.occurred_at>closing_at OR (r.state='uncertain' AND prior.occurred_at IS DISTINCT FROM r.updated_at)",
)
_LOCK = _LOCK.replace(
    "OR o.status IS DISTINCT FROM 'rejected'",
    "OR o.status IS DISTINCT FROM 'acknowledged'",
)
_CHAIN = _original(_ORIGINAL_OUTCOMES).replace(
    "WHERE reservation_id=r.reservation_id ORDER BY sequence",
    "WHERE reservation_id=r.reservation_id AND sequence<=o.sequence ORDER BY sequence",
)


def _required_clocks(sql):
    for reference in (
        "page",
        "order_row",
        "first_order",
        "original_order",
        "position_row",
        "protection",
        "x.value",
        "y.value",
        "value",
    ):
        for member in ("cTime", "uTime", "fillTime", "ts"):
            sql = sql.replace(
                "(" + reference + "->>'" + member + "')::numeric",
                "public.qualification_native_clock_v55("
                + reference
                + "->'"
                + member
                + "')",
            )
    for reference, member in (
        ("balance->0", "uTime"),
        ("balance->0->'details'->0", "uTime"),
        ("risk->0", "ts"),
    ):
        sql = sql.replace(
            "(" + reference + "->>'" + member + "')::numeric",
            "public.qualification_native_clock_v55("
            + reference
            + "->'"
            + member
            + "')",
        )
    return sql


FILLED_SQL = _required_clocks(
    "CREATE FUNCTION public.qualification_native_filled_flat_data_v55(rid text, closing_at timestamptz) RETURNS boolean AS $ctcc$\nDECLARE\n"
    + _DECLARATIONS
    + _FILLED_DECLARATIONS
    + "BEGIN\n"
    + _FILLED_START
    + _LOCK
    + _FILLED_WITNESS
    + "      SELECT * INTO issued FROM"
    + _original(_ORIGINAL_IDENTITY)
    + "      FOR q IN SELECT * FROM public.qualification_submission_outcomes"
    + _CHAIN
    + _FILLED_END
    + "$ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;\n"
)

_ANCHOR = "  IF (to_jsonb(NEW) - ARRAY['state','state_revision','updated_at']) IS DISTINCT FROM"
_BRANCH = """  IF NOT native_ok AND OLD.state='uncertain' AND NEW.state='reconciled_flat'
     AND OLD.state_revision=3 AND NEW.state_revision=4 AND OLD.environment='demo'
     AND OLD.request_json::jsonb->>'contract_version'='ctcc-reservation-request-native-v4' THEN
    native_ok := public.qualification_native_filled_flat_data_v55(OLD.reservation_id,NEW.updated_at);
  END IF;
"""
CURRENT_SQL = zero.CURRENT_SQL.replace(_ANCHOR, _BRANCH + _ANCHOR)
CURRENT_BODY_SHA256 = hashlib.sha256(CURRENT_SQL.split("$$")[1].encode()).hexdigest()
FILLED_SQL = FILLED_SQL.replace("__FILLED_TRIGGER_BODY_SHA256__", CURRENT_BODY_SHA256)
FILLED_BODY_SHA256 = hashlib.sha256(FILLED_SQL.split("$ctcc$")[1].encode()).hexdigest()
HELPERS = (
    PLAIN_SQL,
    CANONICAL_SQL,
    MANIFEST_SQL,
    ROWS_SQL,
    NUMBER_SQL,
    CLOCK_SQL,
    FILLED_SQL,
)
HELPER_IDENTITIES = (
    ("qualification_native_plain_v55", "text", "jsonb"),
    ("qualification_native_canonical_v55", "jsonb", "text"),
    (
        "qualification_native_manifest_v55",
        "text, text, text, text, timestamp with time zone",
        "jsonb",
    ),
    ("qualification_native_rows_v55", "jsonb, text, text, jsonb, text", "jsonb"),
    ("qualification_native_number_v55", "jsonb", "numeric"),
    ("qualification_native_clock_v55", "jsonb", "numeric"),
    (
        "qualification_native_filled_flat_data_v55",
        "text, timestamp with time zone",
        "boolean",
    ),
)


def statements():
    zero.statements()
    if (
        zero.CURRENT_BODY_SHA256 != ZERO_BODY_SHA256
        or zero.CURRENT_SQL.count(_ANCHOR) != 1
    ):
        raise ValueError("native_filled55_zero_ancestor_changed")
    if CURRENT_SQL.replace(_BRANCH, "", 1) != zero.CURRENT_SQL:
        raise ValueError("native_filled55_zero_branch_changed")
    if len(HELPERS) != len(HELPER_IDENTITIES) or any(
        sql.count("$ctcc$") != 2 for sql in HELPERS
    ):
        raise ValueError("native_filled55_finite_helpers_changed")
    return (*HELPERS, CURRENT_SQL)


def restore_statements():
    statements()
    return (
        zero.CURRENT_SQL,
        *(
            "DROP FUNCTION public." + name + "(" + args + ")"
            for name, args, _ in reversed(HELPER_IDENTITIES)
        ),
    )


def restore_original_statements():
    return (*restore_statements(), *zero.restore_statements())
