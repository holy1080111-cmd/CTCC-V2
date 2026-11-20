"""Additive original-OCO reporting SPEC; importing installs no SQL.

The existing55 report function/signature/grants remain the dispatch boundary.
Only physical58 receives the explicit new branch and equivalent compilable
numeric spelling; reversing both reconstructs the exact55 function. There is
no fabricated own-close intent or preflight.
"""

import json
from hashlib import sha256

from app.domain import native_closed_report_schema_v55 as ancestor
from app.domain import native_protective_hold_schema_v58 as hold

FORENSICS_VERSION = "ctcc.native_original_oco_forensics_readback.v1"
CLEANUP_VERSION = "ctcc.native_original_oco_cleanup.v1"
READY_VERSION = "ctcc.native_original_oco_report_ready.v1"
REPORT_POLICY_VERSION = "ctcc.native_original_oco_observed_unknowns.v58"
# This is the fixed source-owned ancestor literal, never caller/raw input.
# Keep SPEC construction stdlib-only for independent hermetic host readback.
_policy = json.loads(ancestor.REPORT_POLICY_JSON)
_policy.update(
    version=REPORT_POLICY_VERSION,
    eligibility="original_oco_closed_flat_and_retired_read_resources_and_exact_two_group_forensic_replay",
)
REPORT_POLICY_JSON = json.dumps(
    _policy, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
)
REPORT_POLICY_SHA256 = sha256(REPORT_POLICY_JSON.encode()).hexdigest()
del _policy

# PostgreSQL ARE repetition bounds stop at255. These equivalent bounded
# spellings preserve the existing1..300 digit language on physical58. The
# frozen55 source and restore SQL retain their original exact bytes.
LEGACY_METRIC_NUMERATOR_REGEX = r"^-?[0-9]{1,300}$"
LEGACY_METRIC_DENOMINATOR_REGEX = r"^[1-9][0-9]{0,299}$"
METRIC_NUMERATOR_REGEX = r"^-?[0-9]{1,255}[0-9]{0,45}$"
METRIC_DENOMINATOR_REGEX = r"^[1-9][0-9]{0,254}[0-9]{0,45}$"


def _compiled_numeric_spelling(sql):
    for original, compiled in (
        (LEGACY_METRIC_NUMERATOR_REGEX, METRIC_NUMERATOR_REGEX),
        (LEGACY_METRIC_DENOMINATOR_REGEX, METRIC_DENOMINATOR_REGEX),
    ):
        if sql.count(original) != 1:
            raise ValueError("protective_report58_numeric_ancestor_changed")
        sql = sql.replace(original, compiled, 1)
    return sql


_COMMON_START = (
    "     OR data->>'version' IS DISTINCT FROM 'ctcc.native_closed_trade_forensics.v1'"
)
_COMMON_END = " THEN RETURN false; END IF;\n  FOREACH response_capture"
_MATH_START = (
    "  -- Current finite reporting policy publishes exact observed closed-trade"
)
_MATH_END = "\n  seen_head := closed.outcome_id;"
_EXTRA_DECLARATIONS = "  cleanup_receipt jsonb; cleanup_group jsonb; read_row jsonb; socket_row jsonb; page_row jsonb; selector_row jsonb; pool_row jsonb;\n"


def _common_checks():
    raw = ancestor.ELIGIBLE_SQL
    if raw.count(_COMMON_START) != 1 or raw.count(_COMMON_END) != 1:
        raise ValueError("protective_report58_ancestor_checks_changed")
    checks = raw[raw.index(_COMMON_START) : raw.index(_COMMON_END)]
    checks = checks.replace(
        "ctcc.native_closed_trade_forensics.v1", "ctcc.native_original_oco_forensics.v1"
    )
    checks = checks.replace(
        "analysis->>'exit_reason' IS DISTINCT FROM 'other'",
        "analysis->>'exit_reason' IS DISTINCT FROM (CASE terminal->>'trigger_side' WHEN 'tp' THEN 'take_profit' WHEN 'sl' THEN 'stop_loss' ELSE NULL END)",
    )
    return "  IF false\n" + checks + " THEN RETURN false; END IF;\n"


def _common_math():
    raw = ancestor.ELIGIBLE_SQL
    if raw.count(_MATH_START) != 1 or raw.count(_MATH_END) != 1:
        raise ValueError("protective_report58_ancestor_math_changed")
    math = raw[raw.index(_MATH_START) : raw.index(_MATH_END)]
    math = math.replace("close_fills_", "exit_fills_").replace(
        "close_order_after", "exit_order_after"
    )
    math = math.replace(
        "ELSE '\"other\"'::jsonb END)",
        "ELSE to_jsonb(CASE terminal->>'trigger_side' WHEN 'tp' THEN 'take_profit' WHEN 'sl' THEN 'stop_loss' ELSE NULL END) END)",
    )
    old = "OR jsonb_array_length(receipt->'source_pages')<>2"
    if math.count(old) != 1:
        raise ValueError("protective_report58_source_page_count_changed")
    math = math.replace(
        old,
        "OR jsonb_array_length(receipt->'source_pages') NOT BETWEEN 1 AND 2\n"
        "       OR (SELECT count(DISTINCT jsonb_build_array(value->>'role',value->>'raw_body_sha256')) FROM jsonb_array_elements(receipt->'source_pages'))<>jsonb_array_length(receipt->'source_pages')\n"
        "       OR (SELECT count(*) FROM jsonb_array_elements(final_pages) p WHERE p->>'role' IN ('entry_fills_recent','entry_fills_history','exit_fills_recent','exit_fills_history') AND p->'data' @> jsonb_build_array(raw_row))<>jsonb_array_length(receipt->'source_pages')",
        1,
    )
    old_roles = """OR (SELECT array_agg(value->>'role' ORDER BY value->>'role') FROM jsonb_array_elements(receipt->'source_pages')) IS DISTINCT FROM
           (CASE WHEN evidence->>'role'='entry' THEN ARRAY['entry_fills_history','entry_fills_recent'] ELSE ARRAY['exit_fills_history','exit_fills_recent'] END)"""
    if math.count(old_roles) != 1:
        raise ValueError("protective_report58_source_page_roles_changed")
    math = math.replace(
        old_roles,
        """OR NOT ((SELECT array_agg(value->>'role' ORDER BY value->>'role') FROM jsonb_array_elements(receipt->'source_pages')) <@
           (CASE WHEN evidence->>'role'='entry' THEN ARRAY['entry_fills_history','entry_fills_recent'] ELSE ARRAY['exit_fills_history','exit_fills_recent'] END))""",
        1,
    )
    return math


_HEADER = (
    r"""
  -- New58 DATA eligibility only. The original55 branch below is untouched.
  SELECT * INTO r FROM public.qualification_reservations WHERE reservation_id=rid;
  SELECT * INTO o FROM public.qualification_submission_outcomes WHERE outcome_id=oid AND reservation_id=rid;
  w := public.qualification_native_plain_v55(o.capture_json);
  IF w->>'version'='ctcc.native_original_oco_report_ready.v1' THEN
    request := r.request_json::jsonb;
    IF (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM '0058'
       OR r.reservation_id IS NULL OR r.environment<>'demo' OR r.state<>'reconciled_flat' OR r.state_revision<>4
       OR request->>'contract_version' IS DISTINCT FROM 'ctcc-reservation-request-native-v4'
       OR o.outcome_id IS NULL OR o.observation_kind<>'reconciliation' OR o.status<>'acknowledged'
       OR w->>'reservation_id' IS DISTINCT FROM rid OR w->>'request_sha256' IS DISTINCT FROM r.request_sha256
       OR w->>'intent_sha256' IS DISTINCT FROM o.intent_sha256
       OR w->>'report_sha256' IS DISTINCT FROM report_sha OR w->>'policy_sha256' IS DISTINCT FROM policy_sha
       OR w->>'report_policy_version' IS DISTINCT FROM '__POLICY_VERSION__'
       OR w->>'report_policy_sha256' IS DISTINCT FROM '__POLICY_SHA__'
       OR w->'execution_authority' IS DISTINCT FROM 'false'::jsonb
       OR w->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb THEN RETURN false; END IF;
    lock_hex := encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||
      convert_to(r.environment,'UTF8')||decode('00','hex')||convert_to(r.account_id,'UTF8')),'hex');
    PERFORM pg_advisory_xact_lock((('x'||substr(lock_hex,1,16))::bit(64))::bigint);
    PERFORM 1 FROM public.demo_account_controls WHERE environment=r.environment AND account_id=r.account_id FOR UPDATE;
    PERFORM 1 FROM public.qualification_account_scopes WHERE environment=r.environment AND account_id=r.account_id AND settlement_currency=r.settlement_currency FOR UPDATE;
    IF NOT public.qualification_native_protective_flat_data_v58(rid,r.updated_at) THEN RETURN false; END IF;
    SELECT * INTO closed FROM public.qualification_submission_outcomes WHERE outcome_id=w->>'closure_outcome_id' AND reservation_id=rid;
    SELECT * INTO clean FROM public.qualification_submission_outcomes WHERE outcome_id=w->>'cleanup_outcome_id' AND reservation_id=rid;
    SELECT * INTO forensic FROM public.qualification_submission_outcomes WHERE outcome_id=w->>'forensics_outcome_id' AND reservation_id=rid;
    c := public.qualification_native_plain_v55(clean.capture_json);
    f := public.qualification_native_plain_v55(forensic.capture_json);
    data := public.qualification_native_plain_v55(f->>'forensics_json'); analysis := data->'analysis';
    terminal := public.qualification_native_plain_v55(data->>'terminal_assessment_json');
    IF closed.outcome_id IS NULL OR closed.recorded_at IS DISTINCT FROM r.updated_at
       OR closed.capture_json::jsonb->>'version' IS DISTINCT FROM 'ctcc.native_protective_flat_closure.v1'
       OR closed.capture_json::jsonb->>'closure_body_sha256' IS DISTINCT FROM '__CLOSURE_SHA__'
       OR closed.capture_json::jsonb->>'forensics_outcome_id' IS DISTINCT FROM forensic.outcome_id
       OR closed.capture_json::jsonb->>'forensics_sha256' IS DISTINCT FROM report_sha
       OR NOT EXISTS(SELECT 1 FROM public.qualification_reservation_transitions t WHERE t.reservation_id=rid
           AND t.state_revision=4 AND t.reason_code='post_submit_native_original_oco_flat_v58'
           AND t.from_state='uncertain' AND t.to_state='reconciled_flat' AND t.occurred_at=closed.recorded_at
           AND t.evidence_json::jsonb->>'outcome_id'=closed.outcome_id)
       OR EXISTS(SELECT 1 FROM public.qualification_submission_outcomes WHERE reservation_id=rid
           AND capture_json::jsonb->>'version'='ctcc.native_controlled_close_intent.v1')
       OR clean.outcome_id IS NULL OR clean.sequence<=closed.sequence OR clean.sequence>=o.sequence
       OR clean.observation_kind<>'reconciliation' OR clean.status<>'acknowledged'
       OR c->>'version' IS DISTINCT FROM 'ctcc.native_original_oco_cleanup.v1'
       OR c->>'reservation_id' IS DISTINCT FROM rid OR c->>'request_sha256' IS DISTINCT FROM r.request_sha256
       OR c->>'intent_sha256' IS DISTINCT FROM o.intent_sha256 OR c->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
       OR c->>'closure_receipt_sha256' IS DISTINCT FROM closed.capture_json::jsonb->>'assessment_sha256'
       OR c->>'forensics_outcome_id' IS DISTINCT FROM forensic.outcome_id
       OR c->>'forensics_sha256' IS DISTINCT FROM report_sha
       OR c->'read_resources_retired' IS DISTINCT FROM 'true'::jsonb
       OR c->'execution_authority' IS DISTINCT FROM 'false'::jsonb
       OR c->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
       OR forensic.outcome_id IS NULL OR forensic.sequence>=closed.sequence
       OR forensic.observation_kind<>'reconciliation' OR forensic.status<>'uncertain'
       OR f->>'version' IS DISTINCT FROM 'ctcc.native_original_oco_forensics_readback.v1'
       OR f->>'reservation_id' IS DISTINCT FROM rid OR f->>'request_sha256' IS DISTINCT FROM r.request_sha256
       OR f->>'intent_sha256' IS DISTINCT FROM o.intent_sha256
       OR f->>'final_manifest_outcome_id' IS DISTINCT FROM closed.capture_json::jsonb->>'manifest_outcome_id'
       OR f->>'assessment_sha256' IS DISTINCT FROM c->>'closure_receipt_sha256'
       OR encode(sha256(convert_to(f->>'forensics_json','UTF8')),'hex') IS DISTINCT FROM report_sha
       OR f->>'forensics_sha256' IS DISTINCT FROM report_sha
       OR octet_length(f->>'forensics_json') NOT BETWEEN 1 AND 140000
       OR terminal->>'classification' IS DISTINCT FROM 'original_oco_full_exit_flat'
       OR terminal->>'trigger_side' NOT IN ('tp','sl') THEN RETURN false; END IF;
    SELECT * INTO original_filled FROM public.qualification_submission_outcomes WHERE reservation_id=rid
      AND outcome_id=(SELECT capture_json::jsonb->>'original_filled_outcome_id' FROM public.qualification_submission_outcomes
        WHERE outcome_id=closed.capture_json::jsonb->>'hold_outcome_id' AND reservation_id=rid);
    IF f->>'original_manifest_outcome_id' IS DISTINCT FROM original_filled.capture_json::jsonb->>'manifest_outcome_id'
       OR (SELECT count(*) FROM public.qualification_submission_outcomes WHERE reservation_id=rid
           AND capture_json::jsonb->>'version'='ctcc.native_original_oco_report_ready.v1')<>1 THEN RETURN false; END IF;
""".replace("__POLICY_VERSION__", REPORT_POLICY_VERSION)
    .replace("__POLICY_SHA__", REPORT_POLICY_SHA256)
    .replace("__CLOSURE_SHA__", hold.CURRENT_BODY_SHA256)
)

_CHAIN = ancestor.ELIGIBLE_SQL[
    ancestor.ELIGIBLE_SQL.index(_MATH_END) : ancestor.ELIGIBLE_SQL.index(
        "\nEXCEPTION WHEN OTHERS"
    )
]
_CLEANUP = r"""
    IF octet_length(c->>'cleanup_receipt_json') NOT BETWEEN 1 AND 140000
       OR encode(sha256(convert_to(c->>'cleanup_receipt_json','UTF8')),'hex') IS DISTINCT FROM c->>'cleanup_receipt_sha256' THEN RETURN false; END IF;
    cleanup_receipt := public.qualification_native_plain_v55(c->>'cleanup_receipt_json');
    IF public.qualification_native_canonical_v55(cleanup_receipt) IS DISTINCT FROM c->>'cleanup_receipt_json'
       OR cleanup_receipt->>'version' IS DISTINCT FROM 'ctcc.native_original_oco_read_resources_retired.v1'
       OR cleanup_receipt->>'reservation_id' IS DISTINCT FROM rid
       OR cleanup_receipt->>'closure_outcome_id' IS DISTINCT FROM closed.outcome_id
       OR cleanup_receipt->>'request_sha256' IS DISTINCT FROM r.request_sha256
       OR cleanup_receipt->>'intent_sha256' IS DISTINCT FROM closed.intent_sha256
       OR cleanup_receipt->>'forensics_outcome_id' IS DISTINCT FROM forensic.outcome_id
       OR cleanup_receipt->>'forensics_sha256' IS DISTINCT FROM report_sha
       OR cleanup_receipt->>'manifest_outcome_id' IS DISTINCT FROM c->>'manifest_outcome_id'
       OR cleanup_receipt->'read_only_purpose' IS DISTINCT FROM 'true'::jsonb
       OR cleanup_receipt->'original_selection_retired' IS DISTINCT FROM 'true'::jsonb
       OR cleanup_receipt->'execution_authority' IS DISTINCT FROM 'false'::jsonb
       OR cleanup_receipt->'order_retry_authority' IS DISTINCT FROM 'false'::jsonb
       OR (cleanup_receipt->>'completed_at')::timestamptz>clean.recorded_at
       OR (cleanup_receipt->>'started_at')::timestamptz<(closed.recorded_at)
       OR (cleanup_receipt->>'completed_at')::timestamptz<(cleanup_receipt->>'started_at')::timestamptz
       OR (cleanup_receipt->>'completed_monotonic_ns')::numeric<(cleanup_receipt->>'started_monotonic_ns')::numeric
       OR (cleanup_receipt->>'completed_monotonic_ns')::numeric-(cleanup_receipt->>'started_monotonic_ns')::numeric>5000000000
       OR (cleanup_receipt->>'completed_utc_ns')::numeric<(cleanup_receipt->>'started_utc_ns')::numeric
       OR (cleanup_receipt->>'completed_utc_ns')::numeric-(cleanup_receipt->>'started_utc_ns')::numeric>5000000000
       OR floor((cleanup_receipt->>'started_utc_ns')::numeric/1000)<>extract(epoch FROM (cleanup_receipt->>'started_at')::timestamptz)*1000000
       OR floor((cleanup_receipt->>'completed_utc_ns')::numeric/1000)<>extract(epoch FROM (cleanup_receipt->>'completed_at')::timestamptz)*1000000
       OR NOT coalesce(cleanup_receipt->>'original_role_route_sha256' ~ '^[a-f0-9]{64}$',false)
       OR NOT coalesce(cleanup_receipt->>'original_server_incarnation_sha256' ~ '^[a-f0-9]{64}$',false)
       OR jsonb_typeof(cleanup_receipt->'closed_gets') IS DISTINCT FROM 'array'
       OR jsonb_typeof(cleanup_receipt->'retired_pools') IS DISTINCT FROM 'array'
       OR jsonb_array_length(cleanup_receipt->'retired_pools')<>2 THEN RETURN false; END IF;
    cleanup_group := public.qualification_native_manifest_v55(rid,c->>'manifest_outcome_id',
      closed.capture_json::jsonb->>'original_origin',request->>'credential_session_sha256',clean.recorded_at);
    IF jsonb_array_length(cleanup_receipt->'closed_gets')<>jsonb_array_length(cleanup_group->'pages')+1 THEN RETURN false; END IF;
    FOR read_row IN SELECT value FROM jsonb_array_elements(cleanup_receipt->'closed_gets') LOOP
      socket_row := read_row->'closed_observation';
      IF jsonb_typeof(socket_row) IS DISTINCT FROM 'array' OR jsonb_array_length(socket_row)<>5
         OR socket_row->>0 IS DISTINCT FROM 'ctcc.native_socket_closed_observation.v1'
         OR socket_row->3 IS DISTINCT FROM '-1'::jsonb
         OR socket_row->>4 IS DISTINCT FROM 'native_callback_tail_and_sdk_pool_closed'
         OR jsonb_typeof(socket_row->1) IS DISTINCT FROM 'number' OR jsonb_typeof(socket_row->2) IS DISTINCT FROM 'number'
         OR (socket_row->>1)::numeric<(cleanup_receipt->>'started_utc_ns')::numeric
         OR (socket_row->>1)::numeric>(cleanup_receipt->>'completed_utc_ns')::numeric
         OR (socket_row->>2)::numeric<(cleanup_receipt->>'started_monotonic_ns')::numeric
         OR (socket_row->>2)::numeric>(cleanup_receipt->>'completed_monotonic_ns')::numeric THEN RETURN false; END IF;
    END LOOP;
    selector_row := cleanup_receipt->'closed_gets'->0;
    IF selector_row->>'role' IS DISTINCT FROM 'identity_selector'
       OR selector_row->>'path' IS DISTINCT FROM '/api/v5/account/config' OR selector_row->'parameters' IS DISTINCT FROM '[]'::jsonb
       OR NOT coalesce(selector_row->>'body_sha256' ~ '^[a-f0-9]{64}$',false) THEN RETURN false; END IF;
    FOR page_row IN SELECT value FROM jsonb_array_elements(cleanup_group->'pages') LOOP
      IF (SELECT count(*) FROM jsonb_array_elements(cleanup_receipt->'closed_gets') x
          WHERE x->>'role'=page_row->>'role' AND x->>'path'=page_row->'meta'->>'path'
            AND x->'parameters'=page_row->'meta'->'parameters'
            AND x->>'body_sha256'=page_row->'meta'->>'body_sha256'
            AND x->>'request_signature_sha256'=page_row->'meta'->>'request_signature_sha256'
            AND x->>'read_session_id'=page_row->'meta'->>'read_session_id')<>1 THEN RETURN false; END IF;
    END LOOP;
    FOR pool_row IN SELECT value FROM jsonb_array_elements(cleanup_receipt->'retired_pools') LOOP
      IF pool_row->>'kind' IS DISTINCT FROM 'sqlalchemy.pool.NullPool'
         OR NOT coalesce(pool_row->>'original_pool_sha256' ~ '^[a-f0-9]{64}$',false)
         OR NOT coalesce(pool_row->>'retired_pool_sha256' ~ '^[a-f0-9]{64}$',false)
         OR pool_row->>'original_pool_sha256'=pool_row->>'retired_pool_sha256' THEN RETURN false; END IF;
    END LOOP;
    IF (SELECT array_agg(value->>'role' ORDER BY value->>'role') FROM jsonb_array_elements(cleanup_receipt->'retired_pools'))
         IS DISTINCT FROM ARRAY['custody','frame_reader'] THEN RETURN false; END IF;
"""
_BRANCH = (
    _HEADER
    + _CLEANUP
    + _common_checks()
    + _compiled_numeric_spelling(_common_math())
    + _CHAIN
    + "\n  END IF;\n"
)
_BEGIN = "\nBEGIN\n"
if ancestor.ELIGIBLE_SQL.count(_BEGIN) != 1:
    raise ValueError("protective_report58_ancestor_begin_changed")
ELIGIBLE_SQL = _compiled_numeric_spelling(
    ancestor.ELIGIBLE_SQL.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    ).replace(_BEGIN, _EXTRA_DECLARATIONS + _BEGIN + _BRANCH, 1)
)
ELIGIBLE_BODY_SHA256 = sha256(ELIGIBLE_SQL.split("$ctcc$")[1].encode()).hexdigest()


def statements():
    if (
        ELIGIBLE_SQL.replace(_BRANCH, "", 1)
        .replace(_EXTRA_DECLARATIONS, "", 1)
        .replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
        .replace(METRIC_NUMERATOR_REGEX, LEGACY_METRIC_NUMERATOR_REGEX, 1)
        .replace(METRIC_DENOMINATOR_REGEX, LEGACY_METRIC_DENOMINATOR_REGEX, 1)
        != ancestor.ELIGIBLE_SQL
    ):
        raise ValueError("protective_report58_original55_changed")
    return (ELIGIBLE_SQL,)


def restore_statements():
    statements()
    return (
        ancestor.ELIGIBLE_SQL.replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        ),
    )
