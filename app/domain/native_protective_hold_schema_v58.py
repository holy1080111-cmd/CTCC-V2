"""Additive DB0058 SPEC for original-OCO exit; importing installs nothing.

Uses the existing immutable raw/page/outcome journal and reservation lock.
The original zero-FOK/owned-close branches stay byte-for-byte ancestors. No
JSON `passed` or local-close intent can establish an original protective exit.
Root's sole migration must install these statements, exact profile catalog
pins, and trigger_sql(), and retain the nonempty downgrade refusal.
"""

from hashlib import sha256

from app.domain import native_filled_flat_closure_schema_v55 as ancestor

HOLD_VERSION = "ctcc.native_protected_hold.v1"
TERMINAL_VERSION = "ctcc.native_protective_flat_closure.v1"
TRANSITION_REASON = "post_submit_native_original_oco_flat_v58"
FUNCTION = "qualification_native_protective_flat_data_v58"
FUNCTION_IDENTITY = "text, timestamp with time zone"
_ANCHOR = ancestor._ANCHOR
_BRANCH = """  IF NOT native_ok AND OLD.state='uncertain' AND NEW.state='reconciled_flat'
     AND OLD.state_revision=3 AND NEW.state_revision=4 AND OLD.environment='demo'
     AND OLD.request_json::jsonb->>'contract_version'='ctcc-reservation-request-native-v4' THEN
    native_ok := public.qualification_native_protective_flat_data_v58(OLD.reservation_id,NEW.updated_at);
  END IF;
"""


def trigger_sql(previous_sql=ancestor.CURRENT_SQL):
    if (
        type(previous_sql) is not str
        or previous_sql != ancestor.CURRENT_SQL
        or previous_sql.count(_ANCHOR) != 1
    ):
        raise ValueError("native_protective58_trigger_ancestor_changed")
    return previous_sql.replace(_ANCHOR, _BRANCH + _ANCHOR, 1)


CURRENT_SQL = trigger_sql()
CURRENT_BODY_SHA256 = sha256(CURRENT_SQL.split("$$")[1].encode()).hexdigest()
INDEXES = (
    "CREATE UNIQUE INDEX qualification_native_protected_hold_once_v58 ON public.qualification_submission_outcomes (reservation_id) WHERE (capture_json::jsonb ->> 'version') = 'ctcc.native_protected_hold.v1'",
    "CREATE UNIQUE INDEX qualification_native_protective_flat_once_v58 ON public.qualification_submission_outcomes (reservation_id) WHERE (capture_json::jsonb ->> 'version') = 'ctcc.native_protective_flat_closure.v1'",
)
_LOCK = ancestor._LOCK.replace(
    "ctcc.native_owned_filled_flat_closure.v1", TERMINAL_VERSION
)
_WITNESS = ancestor._FILLED_WITNESS.replace(
    "ctcc.native_owned_filled_flat_closure.v1", TERMINAL_VERSION
).replace("__FILLED_TRIGGER_BODY_SHA256__", CURRENT_BODY_SHA256)
_DECLARATIONS = """
  r record; hold_row record; original_row record;
  held jsonb; original_data jsonb; original_group jsonb; final_group jsonb; pages jsonb;
  old_order jsonb; order_row jsonb; exit_row jsonb; old_algo jsonb; algo jsonb; bracket jsonb;
  entry_fills jsonb; exit_fills jsonb; original_fills jsonb; merged jsonb; bill_rows jsonb;
  row_value jsonb; other jsonb; pos jsonb; risk jsonb; config jsonb;
  size numeric; amount numeric; weighted numeric; fee numeric; first_fill numeric; last_fill numeric;
  child_id text; opposite text; algo_kind text; key_name text; prefix text;
  expected_query jsonb; groups jsonb[];
"""
_END = r"""
  SELECT * INTO hold_row FROM public.qualification_submission_outcomes
    WHERE reservation_id=rid AND outcome_id=witness->>'hold_outcome_id';
  held := public.qualification_native_plain_v55(hold_row.capture_json);
  IF hold_row.outcome_id IS NULL OR hold_row.sequence>=o.sequence OR hold_row.status<>'uncertain'
     OR hold_row.observation_kind<>'reconciliation' OR held->>'version'<>'ctcc.native_protected_hold.v1'
     OR held->>'reservation_id' IS DISTINCT FROM rid
     OR held->>'request_sha256' IS DISTINCT FROM r.request_sha256
     OR held->>'intent_sha256' IS DISTINCT FROM o.intent_sha256
     OR held->'risk_hold_retained' IS DISTINCT FROM 'true'::jsonb
     OR held->'execution_authority' IS DISTINCT FROM 'false'::jsonb
     OR held->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
     OR (SELECT count(*) FROM public.qualification_submission_outcomes WHERE reservation_id=rid
         AND capture_json::jsonb->>'version'='ctcc.native_protected_hold.v1')<>1
     OR EXISTS(SELECT 1 FROM public.qualification_submission_outcomes WHERE reservation_id=rid
         AND capture_json::jsonb->>'version'='ctcc.native_controlled_close_intent.v1') THEN RETURN false; END IF;
  SELECT * INTO original_row FROM public.qualification_submission_outcomes
    WHERE reservation_id=rid AND outcome_id=held->>'original_filled_outcome_id';
  original_data := public.qualification_native_plain_v55(original_row.capture_json);
  IF original_row.outcome_id IS NULL OR original_row.sequence>=hold_row.sequence
     OR original_data->>'version' IS DISTINCT FROM 'ctcc.native_verified_reconciliation_data.v1'
     OR original_data->'assessment'->>'classification' IS DISTINCT FROM 'filled_and_protected_hold_retained'
     OR original_data->'risk_hold_retained' IS DISTINCT FROM 'true'::jsonb
     OR original_data->>'manifest_outcome_id' IS DISTINCT FROM held->>'original_manifest_outcome_id'
     OR witness->'risk_hold_retained' IS DISTINCT FROM 'false'::jsonb
     OR witness->'metrics_complete' IS DISTINCT FROM 'false'::jsonb THEN RETURN false; END IF;
  original_group := public.qualification_native_manifest_v55(rid,held->>'original_manifest_outcome_id',
    witness->>'original_origin',request->>'credential_session_sha256',hold_row.recorded_at);
  final_group := public.qualification_native_manifest_v55(rid,witness->>'manifest_outcome_id',
    witness->>'original_origin',request->>'credential_session_sha256',closing_at);
  IF (original_group->>'last_at')::timestamptz >= (final_group->>'first_at')::timestamptz
     OR original_group->>'read_session'=final_group->>'read_session'
     OR original_group->'manifest'->>'assessment_sha256' IS DISTINCT FROM held->>'original_assessment_sha256'
     OR final_group->'manifest'->>'assessment_sha256' IS DISTINCT FROM witness->>'assessment_sha256' THEN RETURN false; END IF;
  bracket := expected->'attachAlgoOrds'->0;
  size := public.qualification_native_number_v55(expected->'sz');
  IF size<=0 OR jsonb_array_length(expected->'attachAlgoOrds')<>1 THEN RETURN false; END IF;
  opposite := CASE expected->>'side' WHEN 'buy' THEN 'sell' ELSE 'buy' END;
  groups := ARRAY[original_group,final_group];
  FOREACH group_value IN ARRAY groups LOOP
    pages := group_value->'pages';
    config := public.qualification_native_rows_v55(pages,'config_before','/api/v5/account/config','{}',NULL);
    IF jsonb_array_length(config)<>1 OR config IS DISTINCT FROM public.qualification_native_rows_v55(pages,'config_after','/api/v5/account/config','{}',NULL)
       OR config->0->>'uid' IS DISTINCT FROM r.account_id OR config->0->>'mainUid' IS DISTINCT FROM witness->>'original_main_uid'
       OR config->0->>'acctLv' IS DISTINCT FROM '2' OR config->0->>'posMode' IS DISTINCT FROM inner_intent->'execution_binding'->>'position_mode' THEN RETURN false; END IF;
  END LOOP;
  old_order := public.qualification_native_rows_v55(original_group->'pages','order_after','/api/v5/trade/order',
    jsonb_build_object('instId',expected->>'instId','clOrdId',expected->>'clOrdId'),NULL)->0;
  IF old_order->>'state' IS DISTINCT FROM 'filled' OR old_order->>'ordType' IS DISTINCT FROM 'fok'
     OR public.qualification_native_number_v55(old_order->'accFillSz')<>size
     OR public.qualification_native_number_v55(old_order->'sz')<>size
     OR old_order->>'clOrdId' IS DISTINCT FROM expected->>'clOrdId'
     OR old_order->>'instId' IS DISTINCT FROM expected->>'instId'
     OR old_order->>'side' IS DISTINCT FROM expected->>'side'
     OR old_order->>'posSide' IS DISTINCT FROM expected->>'posSide'
     OR old_order->>'tdMode' IS DISTINCT FROM 'isolated' THEN RETURN false; END IF;
  old_algo := public.qualification_native_rows_v55(original_group->'pages','algo_oco','/api/v5/trade/orders-algo-pending','{"ordType":"oco"}','algoId');
  IF jsonb_array_length(old_algo)<>1 THEN RETURN false; END IF;
  old_algo := old_algo->0;
  IF old_algo->>'state' IS DISTINCT FROM 'live' OR old_algo->>'ordType' IS DISTINCT FROM 'oco'
     OR old_algo->>'algoClOrdId' IS DISTINCT FROM inner_intent->>'protection_client_order_id'
     OR old_algo->>'instId' IS DISTINCT FROM expected->>'instId' OR old_algo->>'posSide' IS DISTINCT FROM expected->>'posSide'
     OR old_algo->>'side' IS DISTINCT FROM opposite OR old_algo->>'tdMode' IS DISTINCT FROM 'isolated'
     OR public.qualification_native_number_v55(old_algo->'sz')<>size THEN RETURN false; END IF;
  FOREACH key_name IN ARRAY ARRAY['tpTriggerPx','slTriggerPx','tpOrdPx','slOrdPx','tpTriggerPxType','slTriggerPxType'] LOOP
    IF old_algo->key_name IS DISTINCT FROM bracket->key_name THEN RETURN false; END IF;
  END LOOP;
  IF bracket->>'tpTriggerPxType' IS DISTINCT FROM 'mark' OR bracket->>'slTriggerPxType' IS DISTINCT FROM 'mark'
     OR bracket->>'tpOrdPx' IS DISTINCT FROM '-1' OR bracket->>'slOrdPx' IS DISTINCT FROM '-1' THEN RETURN false; END IF;
  pages := final_group->'pages';
  algo := public.qualification_native_rows_v55(pages,'protection_before','/api/v5/trade/order-algo',jsonb_build_object('algoId',old_algo->>'algoId'),NULL);
  IF jsonb_array_length(algo)<>1 OR algo IS DISTINCT FROM public.qualification_native_rows_v55(pages,'protection_after','/api/v5/trade/order-algo',jsonb_build_object('algoId',old_algo->>'algoId'),NULL) THEN RETURN false; END IF;
  algo := algo->0;
  FOREACH key_name IN ARRAY ARRAY['algoId','algoClOrdId','instId','instType','tdMode','posSide','side','ordType','sz','tpTriggerPx','slTriggerPx','tpOrdPx','slOrdPx','tpTriggerPxType','slTriggerPxType','cTime'] LOOP
    IF algo->key_name IS DISTINCT FROM old_algo->key_name THEN RETURN false; END IF;
  END LOOP;
  IF algo->>'state' IS DISTINCT FROM 'effective' OR NOT coalesce(algo->>'actualSide' IN ('tp','sl'),false)
     OR NOT coalesce(algo->>'failCode' IN ('','0'),false) OR jsonb_typeof(algo->'ordIdList') IS DISTINCT FROM 'array'
     OR jsonb_array_length(algo->'ordIdList')<>1 OR jsonb_typeof(algo->'ordIdList'->0) IS DISTINCT FROM 'string'
     OR algo->'subAlgoIdList' IS DISTINCT FROM '[]'::jsonb
     OR public.qualification_native_number_v55(algo->'actualSz')<>size THEN RETURN false; END IF;
  child_id := algo->'ordIdList'->>0;
  IF NOT coalesce(child_id ~ '^[1-9][0-9]{0,39}$',false) OR NOT coalesce((algo->>'ordId') IN ('',child_id),false) THEN RETURN false; END IF;
  expected_query := jsonb_build_object('instId',expected->>'instId','ordId',child_id);
  exit_row := public.qualification_native_rows_v55(pages,'exit_order_before','/api/v5/trade/order',expected_query,NULL);
  IF jsonb_array_length(exit_row)<>1 OR exit_row IS DISTINCT FROM public.qualification_native_rows_v55(pages,'exit_order_after','/api/v5/trade/order',expected_query,NULL) THEN RETURN false; END IF;
  exit_row := exit_row->0;
  IF exit_row->>'ordId' IS DISTINCT FROM child_id OR exit_row->>'algoId' IS DISTINCT FROM old_algo->>'algoId'
     OR exit_row->>'instId' IS DISTINCT FROM expected->>'instId' OR exit_row->>'instType' IS DISTINCT FROM 'SWAP'
     OR exit_row->>'tdMode' IS DISTINCT FROM 'isolated' OR exit_row->>'posSide' IS DISTINCT FROM expected->>'posSide'
     OR exit_row->>'side' IS DISTINCT FROM opposite OR exit_row->>'ordType' IS DISTINCT FROM 'market'
     OR exit_row->>'state' IS DISTINCT FROM 'filled'
     OR public.qualification_native_number_v55(exit_row->'sz')<>size
     OR public.qualification_native_number_v55(exit_row->'accFillSz')<>size
     OR (expected->>'posSide'='net' AND (exit_row->>'reduceOnly'<>'true' OR algo->>'reduceOnly'<>'true')) THEN RETURN false; END IF;
  expected_query := jsonb_build_object('instId',expected->>'instId','clOrdId',expected->>'clOrdId');
  FOREACH role_name IN ARRAY ARRAY['entry_order_before','entry_order_after'] LOOP
    order_row := public.qualification_native_rows_v55(pages,role_name,'/api/v5/trade/order',expected_query,NULL);
    IF jsonb_array_length(order_row)<>1 OR order_row->0 IS DISTINCT FROM old_order THEN RETURN false; END IF;
  END LOOP;
  FOREACH prefix IN ARRAY ARRAY['entry','exit'] LOOP
    expected_query := jsonb_build_object('instType','SWAP','instId',expected->>'instId','ordId',CASE prefix WHEN 'entry' THEN old_order->>'ordId' ELSE child_id END);
    merged := public.qualification_native_rows_v55(pages,prefix||'_fills_recent','/api/v5/trade/fills',expected_query,'billId')
      ||public.qualification_native_rows_v55(pages,prefix||'_fills_history','/api/v5/trade/fills-history',expected_query,'billId');
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(merged) x GROUP BY x->>'tradeId' HAVING count(DISTINCT x)>1)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(merged) x GROUP BY x->>'billId' HAVING count(DISTINCT x)>1) THEN RETURN false; END IF;
    total := 0; weighted := 0; first_fill := NULL; last_fill := NULL;
    FOR row_value IN SELECT DISTINCT value FROM jsonb_array_elements(merged) LOOP
      IF row_value->>'ordId' IS DISTINCT FROM expected_query->>'ordId'
         OR row_value->>'instId' IS DISTINCT FROM expected->>'instId' OR row_value->>'instType' IS DISTINCT FROM 'SWAP'
         OR row_value->>'posSide' IS DISTINCT FROM expected->>'posSide'
         OR row_value->>'side' IS DISTINCT FROM CASE prefix WHEN 'entry' THEN expected->>'side' ELSE opposite END
         OR row_value->>'feeCcy' IS DISTINCT FROM r.settlement_currency
         OR row_value->>'tradeId' !~ '^[1-9][0-9]{0,39}$' OR row_value->>'billId' !~ '^[1-9][0-9]{0,39}$' THEN RETURN false; END IF;
      amount := public.qualification_native_number_v55(row_value->'fillSz');
      price := public.qualification_native_number_v55(row_value->'fillPx');
      fee := public.qualification_native_number_v55(row_value->'fee');
      IF amount<=0 OR price<=0 THEN RETURN false; END IF;
      total := total+amount; weighted := weighted+amount*price;
      first_fill := least(first_fill,public.qualification_native_clock_v55(row_value->'fillTime'));
      last_fill := greatest(last_fill,public.qualification_native_clock_v55(row_value->'fillTime'));
      IF prefix='exit' AND public.qualification_native_clock_v55(row_value->'fillTime')<public.qualification_native_clock_v55(algo->'triggerTime') THEN RETURN false; END IF;
    END LOOP;
    IF total<>size OR weighted/total<>public.qualification_native_number_v55(CASE prefix WHEN 'entry' THEN old_order->'avgPx' ELSE exit_row->'avgPx' END) THEN RETURN false; END IF;
    IF prefix='entry' THEN
      entry_fills := merged;
      window_begin := first_fill::text;
      original_fills := public.qualification_native_rows_v55(original_group->'pages','fills_recent','/api/v5/trade/fills',expected_query,'billId')
        ||public.qualification_native_rows_v55(original_group->'pages','fills_history','/api/v5/trade/fills-history',expected_query,'billId');
      IF EXISTS((SELECT value FROM jsonb_array_elements(merged) EXCEPT SELECT value FROM jsonb_array_elements(original_fills))
        UNION ALL (SELECT value FROM jsonb_array_elements(original_fills) EXCEPT SELECT value FROM jsonb_array_elements(merged))) THEN RETURN false; END IF;
    ELSE exit_fills := merged; window_end := last_fill::text; END IF;
  END LOOP;
  IF public.qualification_native_clock_v55(algo->'triggerTime')<window_begin::numeric
     OR public.qualification_native_clock_v55(exit_row->'cTime')<public.qualification_native_clock_v55(algo->'triggerTime')
     OR public.qualification_native_clock_v55(exit_row->'uTime')<window_end::numeric
     OR public.qualification_native_number_v55(algo->'actualPx')<>public.qualification_native_number_v55(exit_row->'avgPx') THEN RETURN false; END IF;
  window_end := floor(extract(epoch FROM (final_group->>'first_at')::timestamptz)*1000)::bigint::text;
  expected_query := jsonb_build_object('instType','SWAP','begin',window_begin,'end',window_end);
  merged := public.qualification_native_rows_v55(pages,'exit_all_fills_recent','/api/v5/trade/fills',expected_query,'billId')
    ||public.qualification_native_rows_v55(pages,'exit_all_fills_history','/api/v5/trade/fills-history',expected_query,'billId');
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(merged) x GROUP BY x->>'tradeId' HAVING count(DISTINCT x)>1)
     OR EXISTS((SELECT value FROM jsonb_array_elements(merged) EXCEPT SELECT value FROM jsonb_array_elements(entry_fills||exit_fills))
       UNION ALL (SELECT value FROM jsonb_array_elements(entry_fills||exit_fills) EXCEPT SELECT value FROM jsonb_array_elements(merged))) THEN RETURN false; END IF;
  pos := public.qualification_native_rows_v55(pages,'positions','/api/v5/account/positions','{}',NULL);
  risk := public.qualification_native_rows_v55(pages,'account_risk','/api/v5/account/account-position-risk','{}',NULL);
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(pos) x WHERE public.qualification_native_number_v55(x->'pos')<>0)
     OR jsonb_array_length(risk)<>1 OR jsonb_typeof(risk->0->'posData') IS DISTINCT FROM 'array'
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(risk->0->'posData') x WHERE public.qualification_native_number_v55(x->'pos')<>0)
     OR public.qualification_native_rows_v55(pages,'pending_orders','/api/v5/trade/orders-pending','{}','ordId')<>'[]'::jsonb THEN RETURN false; END IF;
  FOREACH algo_kind IN ARRAY ARRAY['conditional','oco','chase','trigger','move_order_stop','iceberg','twap','smart_iceberg'] LOOP
    IF public.qualification_native_rows_v55(pages,'algo_'||algo_kind,'/api/v5/trade/orders-algo-pending',jsonb_build_object('ordType',algo_kind),'algoId')<>'[]'::jsonb THEN RETURN false; END IF;
  END LOOP;
  other := public.qualification_native_rows_v55(pages,'balance','/api/v5/account/balance','{}',NULL);
  IF jsonb_array_length(other)<>1 OR jsonb_typeof(other->0->'details') IS DISTINCT FROM 'array'
     OR jsonb_array_length(other->0->'details')<>1 OR other->0->'details'->0->>'ccy' IS DISTINCT FROM r.settlement_currency
     OR public.qualification_native_number_v55(other->0->'details'->0->'eq')<=0
     OR public.qualification_native_number_v55(other->0->'details'->0->'availEq')<0
     OR public.qualification_native_number_v55(other->0->'details'->0->'liab')<>0
     OR public.qualification_native_number_v55(other->0->'details'->0->'borrowFroz')<>0 THEN RETURN false; END IF;
  IF public.qualification_native_clock_v55(risk->0->'ts')<public.qualification_native_clock_v55(other->0->'uTime') THEN RETURN false; END IF;
  other := public.qualification_native_rows_v55(pages,'leverage','/api/v5/account/leverage-info',jsonb_build_object('instId',expected->>'instId','mgnMode','isolated'),NULL);
  SELECT coalesce(jsonb_agg(value),'[]'::jsonb) INTO other FROM jsonb_array_elements(other) WHERE value->>'instId'=expected->>'instId' AND value->>'mgnMode'='isolated' AND value->>'posSide'=expected->>'posSide';
  IF jsonb_array_length(other)<>1 OR public.qualification_native_number_v55(other->0->'lever')<>public.qualification_native_number_v55(to_jsonb(request->'risk_inputs'->>'requested_leverage')) THEN RETURN false; END IF;
  expected_query := jsonb_build_object('begin',window_begin,'end',window_end);
  bill_rows := public.qualification_native_rows_v55(pages,'exit_bills_recent','/api/v5/account/bills',expected_query,'billId')
    ||public.qualification_native_rows_v55(pages,'exit_bills_archive','/api/v5/account/bills-archive',expected_query,'billId');
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(bill_rows) x GROUP BY x->>'billId' HAVING count(DISTINCT x)>1) THEN RETURN false; END IF;
  FOR row_value IN SELECT DISTINCT value FROM jsonb_array_elements(bill_rows) LOOP
    IF row_value->>'instId' IS DISTINCT FROM expected->>'instId' OR row_value->>'ccy' IS DISTINCT FROM r.settlement_currency
       OR row_value->>'type' NOT IN ('2','8') OR public.qualification_native_clock_v55(row_value->'ts') NOT BETWEEN window_begin::numeric AND window_end::numeric
       OR (row_value->>'type'='2' AND row_value->>'ordId' NOT IN (old_order->>'ordId',child_id)) THEN RETURN false; END IF;
    fee := public.qualification_native_number_v55(row_value->'balChg');
  END LOOP;
  RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;
"""
PREDICATE_SQL = (
    "CREATE FUNCTION public."
    + FUNCTION
    + "(rid text, closing_at timestamptz) RETURNS boolean AS $ctcc$\nDECLARE\n"
    + ancestor._DECLARATIONS
    + _DECLARATIONS
    + "  group_value jsonb; window_begin text; window_end text; total numeric; price numeric;\nBEGIN\n"
    + ancestor._FILLED_START
    + _LOCK
    + _WITNESS
    + "      SELECT * INTO issued FROM"
    + ancestor._original(ancestor._ORIGINAL_IDENTITY)
    + "      FOR q IN SELECT * FROM public.qualification_submission_outcomes"
    + ancestor._CHAIN
    + _END
)
PREDICATE_BODY_SHA256 = sha256(PREDICATE_SQL.split("$ctcc$")[1].encode()).hexdigest()
FUNCTION_IDENTITIES = ((FUNCTION, FUNCTION_IDENTITY, "boolean", PREDICATE_BODY_SHA256),)


def statements():
    ancestor.statements()
    if (
        CURRENT_SQL.replace(_BRANCH, "", 1) != ancestor.CURRENT_SQL
        or PREDICATE_SQL.count("$ctcc$") != 2
    ):
        raise ValueError("native_protective58_schema_changed")
    return (*INDEXES, PREDICATE_SQL)


def restore_statements():
    """Migration caller must separately prove no normal-hold evidence exists."""
    statements()
    return (
        "DROP INDEX public.qualification_native_protective_flat_once_v58",
        "DROP INDEX public.qualification_native_protected_hold_once_v58",
        "DROP FUNCTION public." + FUNCTION + "(" + FUNCTION_IDENTITY + ")",
    )
