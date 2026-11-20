"""UNBOUND controlled micro close intent and irrevocable one-shot claim SPEC.

The root migration composes these statements explicitly. Neither a saved
intent nor these DATA rows can submit. The original live operator consent,
fresh source, restricted reconciler and physical gate remain separate checks.
"""

import hashlib
import json
import re
from types import MappingProxyType

from app.domain import native_live_lifecycle_schema_v1 as lifecycle
from app.domain.native_live_ledger_schema_v1 import _named_table_spec
from app.domain.native_live_legacy_safety_v1 import CLEAR_PREDICATE_SQL


def _source_canonical(value):
    # Only the fixed source statements below are serialized here. Runtime
    # documents use the independent bounded strict source-primitives parser.
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _source_sha(value):
    return hashlib.sha256(value).hexdigest()


SCHEMA = "ctcc.native_live.controlled_close_intent.v1"
CLAIM_SCHEMA = "ctcc.native_live.controlled_close_claim.v1"
INTENT_DOMAIN = b"ctcc-native-live-controlled-close-intent-v1\x00"
CLAIM_DOMAIN = b"ctcc-native-live-controlled-close-claim-v1\x00"
TABLES = (
    "native_live_controlled_close_intents_v1",
    "native_live_controlled_close_claims_v1",
)
FUNCTIONS = (
    "native_live_controlled_close_prepare_v1",
    "native_live_controlled_close_claim_v1",
    "native_live_controlled_close_immutable_v1",
)
EXECUTABLE_FUNCTIONS = FUNCTIONS[:2]
ACTIONS = ("close", "cancel_original_protection")

_INTENTS = """CREATE TABLE public.native_live_controlled_close_intents_v1 (
 close_intent_id varchar(64) NOT NULL CHECK(close_intent_id ~ '^[a-f0-9]{64}$'),
 reservation_id varchar(64) NOT NULL REFERENCES public.native_live_reservations_v1(reservation_id) ON DELETE RESTRICT,
 environment varchar(4) NOT NULL CHECK(environment='live'), account_uid varchar(40) NOT NULL,
 key_id varchar(64) NOT NULL CHECK(key_id ~ '^[a-f0-9]{64}$'),
 close_client_order_id varchar(32) NOT NULL CHECK(close_client_order_id ~ '^CTCCX[A-Za-z0-9]{1,27}$'),
 original_intent_sha256 varchar(64) NOT NULL CHECK(original_intent_sha256 ~ '^[a-f0-9]{64}$'),
 consent_sha256 varchar(64) NOT NULL CHECK(consent_sha256 ~ '^[a-f0-9]{64}$'),
 signature varchar(64) NOT NULL CHECK(signature ~ '^[a-f0-9]{64}$'),
 expected_control_revision bigint NOT NULL CHECK(expected_control_revision>0),
 consent_expires_at timestamptz NOT NULL,
 close_intent_json text NOT NULL CHECK(octet_length(close_intent_json) BETWEEN 1 AND 65536),
 close_intent_sha256 varchar(64) NOT NULL CHECK(close_intent_sha256 ~ '^[a-f0-9]{64}$'),
 request_json text NOT NULL CHECK(octet_length(request_json) BETWEEN 1 AND 8192),
 request_sha256 varchar(64) NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 close_deadline timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(close_deadline>created_at AND close_deadline<=consent_expires_at),
 PRIMARY KEY(close_intent_id), UNIQUE(reservation_id), UNIQUE(environment,account_uid,close_client_order_id),
 FOREIGN KEY(environment,account_uid,key_id) REFERENCES public.native_live_lifecycle_keys_v1(environment,account_uid,key_id) ON DELETE RESTRICT
)"""
_CLAIMS = """CREATE TABLE public.native_live_controlled_close_claims_v1 (
 close_intent_id varchar(64) NOT NULL REFERENCES public.native_live_controlled_close_intents_v1(close_intent_id) ON DELETE RESTRICT,
 action varchar(32) NOT NULL CHECK(action IN ('close','cancel_original_protection')),
 signature varchar(64) NOT NULL CHECK(signature ~ '^[a-f0-9]{64}$'),
 claim_json text NOT NULL CHECK(octet_length(claim_json) BETWEEN 1 AND 32768),
 request_json text NOT NULL CHECK(octet_length(request_json) BETWEEN 1 AND 8192),
 request_sha256 varchar(64) NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 request_deadline timestamptz NOT NULL,
 claim_sha256 varchar(64) NOT NULL CHECK(claim_sha256 ~ '^[a-f0-9]{64}$'),
 request_wire_sha256 varchar(64) NOT NULL CHECK(request_wire_sha256 ~ '^[a-f0-9]{64}$'),
 claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(close_intent_id,action),
 CHECK(request_deadline>claimed_at)
)"""


def _authenticate(schema, domain, maximum=65536):
    label = domain[:-1].decode("ascii")
    return f"""
 IF octet_length(p) NOT BETWEEN 1 AND {maximum} OR octet_length(p)<>char_length(p)
 OR NOT coalesce(s ~ '^[a-f0-9]{{64}}$',false) THEN RAISE EXCEPTION 'native_live_close_proof_rejected'; END IF;
 j:=p::jsonb;
 IF jsonb_typeof(j)<>'object' OR j->>'schema' IS DISTINCT FROM '{schema}'
 OR j->>'environment' IS DISTINCT FROM 'live' OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
 THEN RAISE EXCEPTION 'native_live_close_proof_rejected'; END IF;
 SELECT * INTO k FROM public.native_live_lifecycle_keys_v1 WHERE environment='live' AND account_uid=j->>'account_uid';
 IF k.account_uid IS NULL THEN RAISE EXCEPTION 'native_live_close_actor_denied'; END IF;
 {lifecycle._ACTOR}
 IF j->>'key_id' IS DISTINCT FROM k.key_id OR j->>'main_uid' IS DISTINCT FROM k.main_uid
 OR j->>'origin' IS DISTINCT FROM k.origin OR (j->>'database_oid')::oid IS DISTINCT FROM k.database_oid
 OR (j->>'owner_oid')::oid IS DISTINCT FROM k.owner_oid OR (j->>'reconciler_oid')::oid IS DISTINCT FROM k.reconciler_oid
 THEN RAISE EXCEPTION 'native_live_close_proof_rejected'; END IF;
 pad:=k.key_material||decode(repeat('00',32),'hex'); inner_pad:=pad; outer_pad:=pad;
 FOR n IN 0..63 LOOP inner_pad:=set_byte(inner_pad,n,get_byte(pad,n)#54); outer_pad:=set_byte(outer_pad,n,get_byte(pad,n)#92); END LOOP;
 actual:=sha256(outer_pad||sha256(inner_pad||convert_to('{label}','UTF8')||decode('00','hex')||convert_to(p,'UTF8')));
 supplied:=decode(s,'hex');
 FOR n IN 0..31 LOOP difference:=difference | (get_byte(actual,n)#get_byte(supplied,n)); END LOOP;
 IF difference<>0 THEN RAISE EXCEPTION 'native_live_close_proof_rejected'; END IF;
 """


_DECLARE = """DECLARE j jsonb; k public.native_live_lifecycle_keys_v1; c public.native_live_controls_v1;
 original_reservation public.native_live_reservations_v1; original_intent public.native_live_intents_v1;
 pad bytea; inner_pad bytea; outer_pad bytea; actual bytea; supplied bytea; difference integer:=0; n integer;"""

PREPARE_SQL = f"""CREATE FUNCTION public.native_live_controlled_close_prepare_v1(p text,s text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 {_DECLARE}
 protected_proof public.native_live_lifecycle_proofs_v1; q jsonb; observed timestamptz; expires timestamptz;
 consent_expires timestamptz; consent_microseconds bigint; digest text; close_id text;
 BEGIN
 {_authenticate(SCHEMA, INTENT_DOMAIN)}
 SELECT * INTO c FROM public.native_live_lifecycle_control_fence_v1(k.account_uid);
 IF NOT ({CLEAR_PREDICATE_SQL}) THEN RAISE EXCEPTION 'native_live_close_legacy_exposure'; END IF;
 SELECT * INTO original_reservation FROM public.native_live_reservations_v1 WHERE reservation_id=j->>'reservation_id' FOR UPDATE;
 SELECT * INTO original_intent FROM public.native_live_intents_v1 WHERE reservation_id=original_reservation.reservation_id;
 IF original_reservation.reservation_id IS NULL OR original_intent.reservation_id IS NULL
 OR original_reservation.account_uid IS DISTINCT FROM k.account_uid OR original_reservation.environment<>'live'
 OR original_reservation.state NOT IN ('reserved','uncertain')
 OR original_intent.environment IS DISTINCT FROM 'live' OR original_intent.account_uid IS DISTINCT FROM k.account_uid
 OR c.policy_sha256 IS DISTINCT FROM j->>'policy_sha256' OR c.armed IS DISTINCT FROM false OR c.emergency_stop IS DISTINCT FROM false
 OR c.arm_id IS NOT NULL OR c.arm_expires_at IS NOT NULL OR c.lease_until<=clock_timestamp()
 OR jsonb_typeof(j->'expected_control_revision') IS DISTINCT FROM 'number'
 OR mod((j->>'expected_control_revision')::numeric,1)<>0
 OR c.revision IS DISTINCT FROM (j->>'expected_control_revision')::bigint
 OR original_intent.intent_sha256 IS DISTINCT FROM j->>'original_intent_sha256'
 OR j->>'original_credential_session_sha256' IS DISTINCT FROM original_intent.intent_json::jsonb->>'credential_session_sha256'
 OR j->>'credential_session_sha256' IS DISTINCT FROM c.credential_session_sha256
 OR j->>'original_process_sha256' IS DISTINCT FROM original_intent.intent_json::jsonb->>'process_sha256'
 OR j->>'process_sha256' IS DISTINCT FROM c.process_sha256
 OR j->>'candidate_sha256' IS DISTINCT FROM original_reservation.candidate_sha256
 OR j->>'report_sha256' IS DISTINCT FROM original_reservation.report_sha256
 OR j->>'policy_sha256' IS DISTINCT FROM original_intent.intent_json::jsonb->>'policy_sha256'
 OR j->>'position_side' IS DISTINCT FROM 'net'
 OR original_intent.intent_json::jsonb->>'position_side' IS DISTINCT FROM 'net'
 OR NOT coalesce((j->>'consent_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'operator_roundtrip_id') ~ '^[a-f0-9]{{64}}$',false)
 OR j->'maximum_close_submissions' IS DISTINCT FROM '1'::jsonb
 THEN RAISE EXCEPTION 'native_live_close_scope_denied'; END IF;
 SELECT * INTO protected_proof FROM public.native_live_lifecycle_proofs_v1
 WHERE reservation_id=original_reservation.reservation_id ORDER BY sequence DESC LIMIT 1;
 IF protected_proof.reservation_id IS NULL OR protected_proof.outcome<>'PROTECTED_HOLD'
 OR protected_proof.proof_sha256 IS DISTINCT FROM j->>'protected_proof_sha256'
 OR protected_proof.observed_at<clock_timestamp()-interval '5 seconds'
 OR protected_proof.observed_at>clock_timestamp()
 OR protected_proof.proof_json::jsonb->'protection_verified' IS DISTINCT FROM 'true'::jsonb
 OR protected_proof.proof_json::jsonb->>'credential_session_sha256' IS DISTINCT FROM j->>'credential_session_sha256'
 OR protected_proof.proof_json::jsonb->>'protection_algo_id' IS DISTINCT FROM j->>'protection_algo_id'
 OR protected_proof.proof_json::jsonb->>'entry_order_id' IS DISTINCT FROM j->>'entry_order_id'
 OR protected_proof.proof_json::jsonb->>'protection_client_id' IS DISTINCT FROM original_intent.protection_client_id
 OR j->>'protection_client_id' IS DISTINCT FROM original_intent.protection_client_id
 OR protected_proof.proof_json::jsonb->>'entry_order_state' IS DISTINCT FROM 'filled'
 OR (protected_proof.proof_json::jsonb->>'entry_filled_contracts')::numeric IS DISTINCT FROM (original_intent.intent_json::jsonb->>'contracts')::numeric
 OR NOT coalesce((j->>'protection_algo_id') ~ '^[1-9][0-9]{{0,39}}$',false)
 OR NOT coalesce((j->>'entry_order_id') ~ '^[1-9][0-9]{{0,39}}$',false)
 OR NOT coalesce((j->>'raw_custody_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'public_quote_sha256') ~ '^[a-f0-9]{{64}}$',false)
 THEN RAISE EXCEPTION 'native_live_close_fresh_protection_denied'; END IF;
 observed:=(j->>'observed_at')::timestamptz; expires:=(j->>'close_deadline')::timestamptz;
 IF jsonb_typeof(j->'consent_received'->'utc_ns') IS DISTINCT FROM 'number'
 OR jsonb_typeof(j->'consent_received'->'monotonic_ns') IS DISTINCT FROM 'number'
 OR mod((j->'consent_received'->>'utc_ns')::numeric,1)<>0
 OR mod((j->'consent_received'->>'monotonic_ns')::numeric,1)<>0
 OR (j->'consent_received'->>'utc_ns')::numeric<=0 OR (j->'consent_received'->>'monotonic_ns')::numeric<0
 OR jsonb_typeof(j->'consent_expiry_monotonic_ns') IS DISTINCT FROM 'number'
 OR (j->'consent_received'->>'monotonic_ns')::numeric+120000000000 IS DISTINCT FROM (j->>'consent_expiry_monotonic_ns')::numeric
 THEN RAISE EXCEPTION 'native_live_close_consent_denied'; END IF;
 consent_microseconds:=trunc(((j->'consent_received'->>'utc_ns')::numeric+999)/1000)::bigint;
 consent_expires:='epoch'::timestamptz+(consent_microseconds::text||' microseconds')::interval+interval '120 seconds';
 IF consent_expires IS DISTINCT FROM (j->>'consent_expires_at')::timestamptz
 OR observed IS NULL OR observed>clock_timestamp() OR observed<clock_timestamp()-interval '5 seconds'
 OR observed<protected_proof.observed_at
 OR expires IS NULL OR expires<=clock_timestamp() OR expires>consent_expires OR expires>c.lease_until
 OR expires>observed+interval '5 seconds'
 OR jsonb_typeof(j->'observation') IS DISTINCT FROM 'object'
 OR jsonb_typeof(j->'observation'->'utc_ns') IS DISTINCT FROM 'number'
 OR jsonb_typeof(j->'observation'->'monotonic_ns') IS DISTINCT FROM 'number'
 OR mod((j->'observation'->>'utc_ns')::numeric,1)<>0
 OR mod((j->'observation'->>'monotonic_ns')::numeric,1)<>0
 OR observed IS DISTINCT FROM 'epoch'::timestamptz+(trunc(((j->'observation'->>'utc_ns')::numeric+999)/1000)::text||' microseconds')::interval
 OR (j->'observation'->>'utc_ns')::numeric<(j->'consent_received'->>'utc_ns')::numeric
 OR (j->'observation'->>'monotonic_ns')::numeric<(j->'consent_received'->>'monotonic_ns')::numeric
 OR jsonb_typeof(j->'close_deadline_monotonic_ns') IS DISTINCT FROM 'number'
 OR mod((j->>'close_deadline_monotonic_ns')::numeric,1)<>0
 OR (j->>'close_deadline_monotonic_ns')::numeric<=(j->'observation'->>'monotonic_ns')::numeric
 OR (j->>'close_deadline_monotonic_ns')::numeric>(j->'observation'->>'monotonic_ns')::numeric+5000000000
 OR (j->>'close_deadline_monotonic_ns')::numeric>(j->>'consent_expiry_monotonic_ns')::numeric
 OR expires IS DISTINCT FROM 'epoch'::timestamptz+(trunc(((j->'observation'->>'utc_ns')::numeric+(j->>'close_deadline_monotonic_ns')::numeric-(j->'observation'->>'monotonic_ns')::numeric+999)/1000)::text||' microseconds')::interval
 THEN RAISE EXCEPTION 'native_live_close_deadline_denied'; END IF;
 q:=(j->>'request_json')::jsonb;
 IF jsonb_typeof(q) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(q))<>9
 OR EXISTS(SELECT 1 FROM jsonb_each(q) request_field WHERE request_field.key<>'reduceOnly' AND jsonb_typeof(request_field.value) IS DISTINCT FROM 'string')
 OR q->>'instId' IS DISTINCT FROM original_intent.intent_json::jsonb->>'instrument_id'
 OR q->>'tdMode' IS DISTINCT FROM 'isolated' OR q->>'posSide' IS DISTINCT FROM 'net'
 OR q->>'ordType' IS DISTINCT FROM 'fok' OR q->'reduceOnly' IS DISTINCT FROM 'true'::jsonb
 OR q->>'side' IS DISTINCT FROM CASE original_intent.intent_json::jsonb->>'direction' WHEN 'long' THEN 'sell' WHEN 'short' THEN 'buy' END
 OR (q->>'sz')::numeric IS DISTINCT FROM (original_intent.intent_json::jsonb->>'contracts')::numeric
 OR (q->>'px')::numeric IS DISTINCT FROM (original_intent.intent_json::jsonb->>'stop_loss')::numeric
 OR q->>'clOrdId' IS DISTINCT FROM j->>'close_client_order_id'
 OR NOT coalesce((q->>'clOrdId') ~ '^CTCCX[A-Za-z0-9]{{1,27}}$',false)
 OR j->>'request_sha256' IS DISTINCT FROM encode(sha256(convert_to(j->>'request_json','UTF8')),'hex')
 THEN RAISE EXCEPTION 'native_live_close_request_denied'; END IF;
 digest:=encode(sha256(convert_to(p,'UTF8')),'hex'); close_id:=j->>'close_intent_id';
 IF NOT coalesce(close_id ~ '^[a-f0-9]{{64}}$',false) THEN RAISE EXCEPTION 'native_live_close_scope_denied'; END IF;
 INSERT INTO public.native_live_controlled_close_intents_v1
 (close_intent_id,reservation_id,environment,account_uid,key_id,close_client_order_id,original_intent_sha256,
 consent_sha256,signature,expected_control_revision,consent_expires_at,close_intent_json,close_intent_sha256,request_json,request_sha256,close_deadline)
 VALUES(close_id,original_reservation.reservation_id,'live',k.account_uid,k.key_id,q->>'clOrdId',original_intent.intent_sha256,
 j->>'consent_sha256',s,c.revision,consent_expires,p,digest,j->>'request_json',j->>'request_sha256',expires);
 RETURN digest;
 END $body$"""

CLAIM_SQL = f"""CREATE FUNCTION public.native_live_controlled_close_claim_v1(p text,s text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 {_DECLARE}
 close_intent public.native_live_controlled_close_intents_v1; parent jsonb; q jsonb;
 digest text; observed timestamptz; expires timestamptz; consent_expires timestamptz;
 close_action text; first_claim public.native_live_controlled_close_claims_v1;
 BEGIN
 {_authenticate(CLAIM_SCHEMA, CLAIM_DOMAIN, 32768)}
 SELECT * INTO c FROM public.native_live_lifecycle_control_fence_v1(k.account_uid);
 IF NOT ({CLEAR_PREDICATE_SQL}) THEN RAISE EXCEPTION 'native_live_close_legacy_exposure'; END IF;
 SELECT * INTO close_intent FROM public.native_live_controlled_close_intents_v1
 WHERE close_intent_id=j->>'close_intent_id' FOR UPDATE;
 SELECT * INTO original_reservation FROM public.native_live_reservations_v1
 WHERE reservation_id=close_intent.reservation_id FOR UPDATE;
 SELECT * INTO original_intent FROM public.native_live_intents_v1 WHERE reservation_id=close_intent.reservation_id;
 parent:=close_intent.close_intent_json::jsonb;
 observed:=(j->>'observed_at')::timestamptz; expires:=(j->>'request_deadline')::timestamptz;
 consent_expires:=close_intent.consent_expires_at; close_action:=j->>'action';
 IF close_intent.close_intent_id IS NULL OR original_reservation.reservation_id IS NULL OR original_intent.reservation_id IS NULL
 OR close_intent.account_uid IS DISTINCT FROM k.account_uid OR close_intent.environment IS DISTINCT FROM 'live'
 OR close_intent.key_id IS DISTINCT FROM k.key_id
 OR close_intent.close_intent_sha256 IS DISTINCT FROM j->>'close_intent_sha256'
 OR original_reservation.account_uid IS DISTINCT FROM k.account_uid OR original_reservation.environment IS DISTINCT FROM 'live'
 OR original_reservation.state NOT IN ('reserved','uncertain') OR original_reservation.resolution_sha256 IS NOT NULL
 OR original_intent.intent_sha256 IS DISTINCT FROM close_intent.original_intent_sha256
 OR j->>'reservation_id' IS DISTINCT FROM close_intent.reservation_id
 OR j->>'original_intent_sha256' IS DISTINCT FROM close_intent.original_intent_sha256
 OR j->>'original_credential_session_sha256' IS DISTINCT FROM parent->>'original_credential_session_sha256'
 OR j->>'original_process_sha256' IS DISTINCT FROM parent->>'original_process_sha256'
 OR j->>'credential_session_sha256' IS DISTINCT FROM c.credential_session_sha256
 OR j->>'credential_session_sha256' IS DISTINCT FROM parent->>'credential_session_sha256'
 OR j->>'process_sha256' IS DISTINCT FROM c.process_sha256 OR j->>'process_sha256' IS DISTINCT FROM parent->>'process_sha256'
 OR j->>'candidate_sha256' IS DISTINCT FROM parent->>'candidate_sha256'
 OR j->>'report_sha256' IS DISTINCT FROM parent->>'report_sha256'
 OR j->>'policy_sha256' IS DISTINCT FROM parent->>'policy_sha256'
 OR c.armed IS DISTINCT FROM false OR c.emergency_stop IS DISTINCT FROM false
 OR c.arm_id IS NOT NULL OR c.arm_expires_at IS NOT NULL OR c.lease_until<=clock_timestamp()
 OR jsonb_typeof(j->'expected_control_revision') IS DISTINCT FROM 'number'
 OR mod((j->>'expected_control_revision')::numeric,1)<>0
 OR c.revision IS DISTINCT FROM (j->>'expected_control_revision')::bigint
 OR c.revision IS DISTINCT FROM close_intent.expected_control_revision
 OR j->>'consent_sha256' IS DISTINCT FROM close_intent.consent_sha256
 OR j->>'operator_roundtrip_id' IS DISTINCT FROM parent->>'operator_roundtrip_id'
 OR NOT coalesce(close_action IN ('close','cancel_original_protection'),false)
 OR observed IS NULL OR observed>clock_timestamp() OR observed<clock_timestamp()-interval '5 seconds'
 OR expires IS NULL OR expires<=clock_timestamp() OR expires>observed+interval '5 seconds'
 OR expires>consent_expires OR expires>c.lease_until
 OR NOT coalesce((j->>'request_wire_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'raw_custody_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR jsonb_typeof(j->'observation') IS DISTINCT FROM 'object'
 OR jsonb_typeof(j->'observation'->'utc_ns') IS DISTINCT FROM 'number'
 OR jsonb_typeof(j->'observation'->'monotonic_ns') IS DISTINCT FROM 'number'
 OR mod((j->'observation'->>'utc_ns')::numeric,1)<>0 OR mod((j->'observation'->>'monotonic_ns')::numeric,1)<>0
 OR observed IS DISTINCT FROM 'epoch'::timestamptz+(trunc(((j->'observation'->>'utc_ns')::numeric+999)/1000)::text||' microseconds')::interval
 OR (j->'observation'->>'utc_ns')::numeric<(parent->'observation'->>'utc_ns')::numeric
 OR (j->'observation'->>'monotonic_ns')::numeric<(parent->'observation'->>'monotonic_ns')::numeric
 OR jsonb_typeof(j->'request_deadline_monotonic_ns') IS DISTINCT FROM 'number'
 OR mod((j->>'request_deadline_monotonic_ns')::numeric,1)<>0
 OR (j->>'request_deadline_monotonic_ns')::numeric<=(j->'observation'->>'monotonic_ns')::numeric
 OR (j->>'request_deadline_monotonic_ns')::numeric>(j->'observation'->>'monotonic_ns')::numeric+5000000000
 OR (j->>'request_deadline_monotonic_ns')::numeric>(parent->>'consent_expiry_monotonic_ns')::numeric
 THEN RAISE EXCEPTION 'native_live_close_claim_denied'; END IF;
 IF octet_length(j->>'request_json') NOT BETWEEN 1 AND 8192
 OR octet_length(j->>'request_json')<>char_length(j->>'request_json')
 OR j->>'request_sha256' IS DISTINCT FROM encode(sha256(convert_to(j->>'request_json','UTF8')),'hex')
 THEN RAISE EXCEPTION 'native_live_close_request_denied'; END IF;
 q:=(j->>'request_json')::jsonb;
 IF close_action='close' THEN
 IF j->>'request_json' IS DISTINCT FROM close_intent.request_json
 OR j->>'request_sha256' IS DISTINCT FROM close_intent.request_sha256
 OR expires IS DISTINCT FROM close_intent.close_deadline
 OR (j->>'request_deadline_monotonic_ns')::numeric IS DISTINCT FROM (parent->>'close_deadline_monotonic_ns')::numeric
 OR close_intent.close_deadline<=clock_timestamp()
 OR NOT coalesce((j->>'public_quote_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT EXISTS(SELECT 1 FROM public.native_live_lifecycle_proofs_v1 latest
 WHERE latest.reservation_id=close_intent.reservation_id AND latest.proof_sha256=parent->>'protected_proof_sha256'
 AND latest.outcome='PROTECTED_HOLD' AND latest.observed_at>=clock_timestamp()-interval '5 seconds'
 AND NOT EXISTS(SELECT 1 FROM public.native_live_lifecycle_proofs_v1 newer WHERE newer.reservation_id=latest.reservation_id AND newer.sequence>latest.sequence))
 THEN RAISE EXCEPTION 'native_live_close_claim_denied'; END IF;
 ELSE
 SELECT * INTO first_claim FROM public.native_live_controlled_close_claims_v1
 WHERE close_intent_id=close_intent.close_intent_id AND action='close';
 IF first_claim.close_intent_id IS NULL
 OR expires IS DISTINCT FROM 'epoch'::timestamptz+(trunc(((j->'observation'->>'utc_ns')::numeric+(j->>'request_deadline_monotonic_ns')::numeric-(j->'observation'->>'monotonic_ns')::numeric+999)/1000)::text||' microseconds')::interval
 OR j->>'close_claim_sha256' IS DISTINCT FROM first_claim.claim_sha256
 OR j->'full_account_verified' IS DISTINCT FROM 'true'::jsonb
 OR j->'fills_complete' IS DISTINCT FROM 'true'::jsonb OR j->'flat_verified' IS DISTINCT FROM 'true'::jsonb
 OR j->'unknown_exposure' IS DISTINCT FROM 'false'::jsonb
 OR j->'position_count' IS DISTINCT FROM '0'::jsonb
 OR j->'pending_order_count' IS DISTINCT FROM '0'::jsonb
 OR j->'pending_algo_count' IS DISTINCT FROM '1'::jsonb
 OR j->>'close_order_state' IS DISTINCT FROM 'filled'
 OR NOT coalesce((j->>'close_order_id') ~ '^[1-9][0-9]{{0,39}}$',false)
 OR j->>'close_order_id' IS NOT DISTINCT FROM parent->>'entry_order_id'
 OR j->>'close_client_order_id' IS DISTINCT FROM close_intent.close_client_order_id
 OR (j->>'close_filled_contracts')::numeric IS DISTINCT FROM (original_intent.intent_json::jsonb->>'contracts')::numeric
 OR j->>'entry_order_id' IS DISTINCT FROM parent->>'entry_order_id'
 OR j->>'protection_algo_id' IS DISTINCT FROM parent->>'protection_algo_id'
 OR j->>'protection_client_id' IS DISTINCT FROM original_intent.protection_client_id
 OR NOT coalesce((j->>'account_config_before_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'account_config_after_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR jsonb_typeof(q) IS DISTINCT FROM 'array' OR jsonb_array_length(q)<>1
 OR jsonb_typeof(q->0) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(q->0))<>2
 OR jsonb_typeof(q->0->'instId') IS DISTINCT FROM 'string' OR jsonb_typeof(q->0->'algoId') IS DISTINCT FROM 'string'
 OR q->0->>'instId' IS DISTINCT FROM original_intent.intent_json::jsonb->>'instrument_id'
 OR q->0->>'algoId' IS DISTINCT FROM parent->>'protection_algo_id'
 THEN RAISE EXCEPTION 'native_live_close_cancel_original_denied'; END IF;
 END IF;
 digest:=encode(sha256(convert_to(p,'UTF8')),'hex');
 INSERT INTO public.native_live_controlled_close_claims_v1
 (close_intent_id,action,signature,claim_json,claim_sha256,request_json,request_sha256,request_deadline,request_wire_sha256)
 VALUES(close_intent.close_intent_id,close_action,s,p,digest,j->>'request_json',j->>'request_sha256',expires,j->>'request_wire_sha256');
 RETURN digest;
 END $body$"""

IMMUTABLE_SQL = """CREATE FUNCTION public.native_live_controlled_close_immutable_v1() RETURNS trigger
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE own oid;
 BEGIN
 SELECT datdba INTO own FROM pg_catalog.pg_database WHERE datname=current_database();
 IF TG_OP<>'INSERT' OR current_user::text IS DISTINCT FROM (SELECT rolname::text FROM pg_catalog.pg_roles WHERE oid=own)
 OR (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID) IS DISTINCT FROM own
 THEN RAISE EXCEPTION 'native_live_controlled_close_immutable'; END IF;
 IF TG_TABLE_NAME='native_live_controlled_close_intents_v1' THEN
 IF NEW.close_intent_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.close_intent_json,'UTF8')),'hex')
 OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.request_json,'UTF8')),'hex')
 OR NEW.close_intent_id IS DISTINCT FROM NEW.close_intent_json::jsonb->>'close_intent_id'
 OR NEW.reservation_id IS DISTINCT FROM NEW.close_intent_json::jsonb->>'reservation_id'
 OR NEW.environment IS DISTINCT FROM NEW.close_intent_json::jsonb->>'environment'
 OR NEW.account_uid IS DISTINCT FROM NEW.close_intent_json::jsonb->>'account_uid'
 OR NEW.key_id IS DISTINCT FROM NEW.close_intent_json::jsonb->>'key_id'
 OR NEW.close_client_order_id IS DISTINCT FROM NEW.close_intent_json::jsonb->>'close_client_order_id'
 OR NEW.original_intent_sha256 IS DISTINCT FROM NEW.close_intent_json::jsonb->>'original_intent_sha256'
 OR NEW.consent_sha256 IS DISTINCT FROM NEW.close_intent_json::jsonb->>'consent_sha256'
 OR NEW.expected_control_revision IS DISTINCT FROM (NEW.close_intent_json::jsonb->>'expected_control_revision')::bigint
 OR NEW.consent_expires_at IS DISTINCT FROM (NEW.close_intent_json::jsonb->>'consent_expires_at')::timestamptz
 OR NEW.request_json IS DISTINCT FROM NEW.close_intent_json::jsonb->>'request_json'
 OR NEW.request_sha256 IS DISTINCT FROM NEW.close_intent_json::jsonb->>'request_sha256'
 OR NEW.close_deadline IS DISTINCT FROM (NEW.close_intent_json::jsonb->>'close_deadline')::timestamptz
 THEN RAISE EXCEPTION 'native_live_controlled_close_digest_changed'; END IF;
 NEW.created_at:=clock_timestamp();
 ELSIF TG_TABLE_NAME='native_live_controlled_close_claims_v1' THEN
 IF NEW.close_intent_id IS DISTINCT FROM NEW.claim_json::jsonb->>'close_intent_id'
 OR NEW.action IS DISTINCT FROM NEW.claim_json::jsonb->>'action'
 OR NEW.request_json IS DISTINCT FROM NEW.claim_json::jsonb->>'request_json'
 OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.request_json,'UTF8')),'hex')
 OR NEW.request_deadline IS DISTINCT FROM (NEW.claim_json::jsonb->>'request_deadline')::timestamptz
 OR NEW.request_wire_sha256 IS DISTINCT FROM NEW.claim_json::jsonb->>'request_wire_sha256'
 OR NEW.claim_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.claim_json,'UTF8')),'hex')
 THEN RAISE EXCEPTION 'native_live_controlled_close_digest_changed'; END IF;
 NEW.claimed_at:=clock_timestamp();
 ELSE RAISE EXCEPTION 'native_live_controlled_close_immutable'; END IF;
 RETURN NEW;
 END $body$"""

_NAMED = tuple(
    _named_table_spec(table, sql)
    for table, sql in zip(TABLES, (_INTENTS, _CLAIMS), strict=True)
)
TABLE_CHECKS = {table: item[1] for table, item in zip(TABLES, _NAMED, strict=True)}
TABLE_UNIQUES = {table: item[2] for table, item in zip(TABLES, _NAMED, strict=True)}
UPGRADE_SQL = (
    _NAMED[0][0].replace(
        "FOREIGN KEY(environment,account_uid,key_id)",
        "CONSTRAINT fk_native_live_controlled_close_intents_v1_key FOREIGN KEY(environment,account_uid,key_id)",
    ),
    _NAMED[1][0].replace(
        " REFERENCES public.native_live_controlled_close_intents_v1",
        " CONSTRAINT fk_native_live_controlled_close_claims_v1_close REFERENCES public.native_live_controlled_close_intents_v1",
    ),
    PREPARE_SQL,
    CLAIM_SQL,
    IMMUTABLE_SQL,
    *(
        f"REVOKE ALL ON FUNCTION public.{name}{signature} FROM PUBLIC"
        for name, signature in zip(
            FUNCTIONS, ("(text,text)", "(text,text)", "()"), strict=True
        )
    ),
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.native_live_controlled_close_immutable_v1()"
        for table in TABLES
    ),
)
DOWNGRADE_SQL = (
    """DO $body$ BEGIN IF EXISTS(SELECT 1 FROM public.native_live_controlled_close_intents_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_controlled_close_claims_v1)
 THEN RAISE EXCEPTION 'native_live_controlled_close_preserve_intents_and_claims'; END IF; END $body$""",
    *(
        f"DROP TRIGGER {table}_immutable ON public.{table}"
        for table in reversed(TABLES)
    ),
    "DROP FUNCTION public.native_live_controlled_close_claim_v1(text,text)",
    "DROP FUNCTION public.native_live_controlled_close_prepare_v1(text,text)",
    "DROP FUNCTION public.native_live_controlled_close_immutable_v1()",
    *(f"DROP TABLE public.{table}" for table in reversed(TABLES)),
)
SOURCE_PRIMARY_KEYS = {
    TABLES[0]: ("close_intent_id",),
    TABLES[1]: ("close_intent_id", "action"),
}
SOURCE_FOREIGN_KEYS = {
    (TABLES[0], "fk_" + TABLES[0] + "_reservation"): (
        ("reservation_id",),
        "native_live_reservations_v1",
        ("reservation_id",),
    ),
    (TABLES[0], "fk_native_live_controlled_close_intents_v1_key"): (
        ("environment", "account_uid", "key_id"),
        "native_live_lifecycle_keys_v1",
        ("environment", "account_uid", "key_id"),
    ),
    (TABLES[1], "fk_native_live_controlled_close_claims_v1_close"): (
        ("close_intent_id",),
        TABLES[0],
        ("close_intent_id",),
    ),
}
SOURCE_COLUMNS = {
    TABLES[0]: (
        ("close_intent_id", 1043, 68, True, None),
        ("reservation_id", 1043, 68, True, None),
        ("environment", 1043, 8, True, None),
        ("account_uid", 1043, 44, True, None),
        ("key_id", 1043, 68, True, None),
        ("close_client_order_id", 1043, 36, True, None),
        ("original_intent_sha256", 1043, 68, True, None),
        ("consent_sha256", 1043, 68, True, None),
        ("signature", 1043, 68, True, None),
        ("expected_control_revision", 20, -1, True, None),
        ("consent_expires_at", 1184, -1, True, None),
        ("close_intent_json", 25, -1, True, None),
        ("close_intent_sha256", 1043, 68, True, None),
        ("request_json", 25, -1, True, None),
        ("request_sha256", 1043, 68, True, None),
        ("close_deadline", 1184, -1, True, None),
        ("created_at", 1184, -1, True, "clock_timestamp()"),
    ),
    TABLES[1]: (
        ("close_intent_id", 1043, 68, True, None),
        ("action", 1043, 36, True, None),
        ("signature", 1043, 68, True, None),
        ("claim_json", 25, -1, True, None),
        ("request_json", 25, -1, True, None),
        ("request_sha256", 1043, 68, True, None),
        ("request_deadline", 1184, -1, True, None),
        ("claim_sha256", 1043, 68, True, None),
        ("request_wire_sha256", 1043, 68, True, None),
        ("claimed_at", 1184, -1, True, "clock_timestamp()"),
    ),
}


def additional_reconciler_grant_sql_v1(name):
    if (
        type(name) is not str
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is None
        or name == "public"
        or name.startswith("pg_")
    ):
        raise ValueError("native_live_close_role_identifier_refused")
    return (
        *(f'GRANT SELECT ON TABLE public.{table} TO "{name}"' for table in TABLES),
        *(
            f'GRANT EXECUTE ON FUNCTION public.{function}(text,text) TO "{name}"'
            for function in EXECUTABLE_FUNCTIONS
        ),
    )


def _lifecycle_append_override_v1():
    original = lifecycle.APPEND_SQL
    previous = " OR j->>'exit_kind' IS DISTINCT FROM 'original_oco'"
    controlled = """ OR NOT coalesce(j->>'exit_kind'='original_oco' OR (j->>'exit_kind'='controlled_micro_close'
 AND coalesce((j->>'exit_order_id') ~ '^[1-9][0-9]{0,39}$',false)
 AND EXISTS(SELECT 1 FROM public.native_live_controlled_close_intents_v1 close_parent
 JOIN public.native_live_controlled_close_claims_v1 close_claim ON close_claim.close_intent_id=close_parent.close_intent_id AND close_claim.action='close'
 WHERE close_parent.reservation_id=r.reservation_id AND close_parent.environment='live' AND close_parent.account_uid=k.account_uid
 AND close_parent.key_id=k.key_id AND close_parent.original_intent_sha256=i.intent_sha256
 AND close_parent.close_intent_sha256=j->>'controlled_close_intent_sha256'
 AND close_claim.claim_sha256=j->>'controlled_close_claim_sha256'
 AND close_parent.close_client_order_id=j->>'exit_client_order_id'
 AND j->>'exit_order_id' IS DISTINCT FROM j->>'entry_order_id'
 AND close_parent.consent_sha256=j->>'controlled_close_consent_sha256'
 AND (NOT EXISTS(SELECT 1 FROM public.native_live_controlled_close_claims_v1 cleanup
 WHERE cleanup.close_intent_id=close_parent.close_intent_id AND cleanup.action='cancel_original_protection')
 OR EXISTS(SELECT 1 FROM public.native_live_controlled_close_claims_v1 cleanup
 WHERE cleanup.close_intent_id=close_parent.close_intent_id AND cleanup.action='cancel_original_protection'
 AND cleanup.claim_sha256=j->>'controlled_cancel_claim_sha256')))),false)"""
    # Fixed SOURCE replacement only. Never inspect or accept a database body.
    if original.count(previous) != 1:
        raise ValueError("native_live_close_ancestor_append_changed")
    return original.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
        previous, controlled, 1
    )


def _lifecycle_catalog_override_v1():
    complete = _source_canonical(
        [
            {"table": table, "name": name, "expression": expression}
            for tables, checks in (
                (lifecycle.TABLES, lifecycle.TABLE_CHECKS),
                (TABLES, TABLE_CHECKS),
            )
            for table in tables
            for name, expression in checks[table]
        ]
    ).decode("ascii")
    previous = "$checks$" + lifecycle._CHECK_SOURCE + "$checks$"
    if lifecycle.CATALOG_SQL.count(previous) != 1 or "$checks$" in complete:
        raise ValueError("native_live_close_ancestor_catalog_changed")
    return lifecycle.CATALOG_SQL.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    ).replace(previous, "$checks$" + complete + "$checks$", 1)


LIFECYCLE_APPEND_SQL = _lifecycle_append_override_v1()
LIFECYCLE_CATALOG_SQL = _lifecycle_catalog_override_v1()
LIFECYCLE_FUNCTION_OVERRIDES = MappingProxyType(
    {
        "native_live_lifecycle_append_v1": LIFECYCLE_APPEND_SQL,
        "native_live_lifecycle_catalog_v1": LIFECYCLE_CATALOG_SQL,
    }
)
UPGRADE_SQL = (*UPGRADE_SQL, LIFECYCLE_APPEND_SQL, LIFECYCLE_CATALOG_SQL)
# Ancestor functions are restored only after the existing retained-row refusal.
DOWNGRADE_SQL = (
    DOWNGRADE_SQL[0],
    lifecycle.APPEND_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
    lifecycle.CATALOG_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
    *DOWNGRADE_SQL[1:],
)
SPEC_SHA256 = _source_sha(
    _source_canonical(
        {"schema": SCHEMA, "upgrade": UPGRADE_SQL, "downgrade": DOWNGRADE_SQL}
    )
)
