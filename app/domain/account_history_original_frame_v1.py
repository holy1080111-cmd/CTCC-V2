"""New restricted raw-B1 read capability; no native/account/order issuer.

The new function is not covered by old metadata-reader grants. Installation
revokes PUBLIC and grants nobody automatically. Runtime native seal is pending.
"""

from app.domain import account_history_original_source_catalog_v1 as source_guard
from app.domain import account_history_partition_catalog_v1 as parent
from app.domain import account_history_partition_v1 as records

SCHEMA = "ctcc.account_original_history_frame_wire.v1"
FUNCTION = "account_history_original_frame_v1_read"
ARGUMENTS = (
    "p_environment text,p_account_id text,p_currency text,p_through bigint,"
    "p_head text,p_after bigint,p_previous text,p_control_revision bigint,"
    "p_account_revision bigint,p_ledger_revision bigint,p_expires timestamp with time zone"
)
SIGNATURE = ", ".join(part.split(" ", 1)[1] for part in ARGUMENTS.split(","))
MAX_RAW_FRAME_BYTES = 64 * 1024 * 1024
MAX_WIRE_BYTES = 64 * 1024 * 1024
MAX_WITNESSES = 31
ROW_WIRE_ALLOWANCE = 4096
SOURCE_WIRE_ALLOWANCE = 4096
HEADER_WIRE_ALLOWANCE = 65536
PG17_NATIVE_BASELINE_SHA256 = (
    "0743e54edd02450dc0d9e4744715ba25fa59872a086f81ef95d3cff1fdfb4343"
)
PG17_NATIVE_BASELINE_STATUS = "SEALED_FROM_REVIEWED_NATIVE_PG17"

BODY = r"""
DECLARE
  head_row public.demo_account_observation_batches%ROWTYPE;
  current_row public.demo_account_observation_batches%ROWTYPE;
  witness_row public.demo_account_observation_batches%ROWTYPE;
  terminal public.demo_account_capture_events%ROWTYPE;
  d jsonb; r jsonb; a jsonb; w jsonb; wd jsonb; ref jsonb; measure record;
  sd jsonb; descriptor jsonb; reference_key text;
  descriptor_sequence bigint; descriptor_source_sequence bigint;
  descriptor_capture text; descriptor_event text;
  source_catalog jsonb; original_source_catalog jsonb;
  sources jsonb[] := ARRAY[]::jsonb[];
  chains jsonb := '[]'::jsonb;
  wire_rows jsonb; output jsonb;
  member_text text; checkpoint_text text; member_hash text; checkpoint_hash text;
  prior_sequence bigint := 0; witness_sequence bigint;
  source_bytes numeric := 0; event_count numeric := 0;
  metadata_bytes numeric; wire_bound numeric;
  expected_scope text; db_observed_at timestamptz;
BEGIN
  IF p_environment IS DISTINCT FROM 'demo' OR p_account_id IS NULL
    OR p_account_id !~ '^[0-9]{1,32}$' OR p_currency IS DISTINCT FROM 'USDT'
    OR p_through IS NULL OR p_through<1 OR p_head IS NULL
    OR p_head !~ '^[a-f0-9]{64}$' OR p_after IS NULL
    OR p_after<0 OR p_after>=p_through OR p_control_revision IS NULL
    OR p_control_revision<1 OR p_account_revision IS NULL OR p_account_revision<0
    OR p_ledger_revision IS NULL OR p_ledger_revision<0 OR p_expires IS NULL
    OR NOT isfinite(p_expires) THEN
    RAISE EXCEPTION 'original_frame_arguments';
  END IF;
  IF (p_after=0 AND p_previous IS NOT NULL)
    OR (p_after>0 AND (p_previous IS NULL OR p_previous !~ '^[a-f0-9]{64}$')) THEN
    RAISE EXCEPTION 'original_frame_cursor';
  END IF;
  PERFORM public.account_history_partition_v1_lock(
    p_environment,p_account_id,p_currency,p_control_revision,p_account_revision,
    p_ledger_revision,p_expires);
  -- Preserve UID -> Control -> scope -> head ordering. The stronger source
  -- locks exclude ordinary DML and source table DDL through BOTH phases.
  -- They do NOT claim to prevent CREATE OR REPLACE of a trigger function.
  __SOURCE_LOCK_SQL__
  __SOURCE_ASSERT_SQL__
  original_source_catalog:=source_catalog;
  IF p_expires<=clock_timestamp() OR NOT EXISTS(
    SELECT 1 FROM public.demo_account_controls WHERE environment=p_environment
      AND account_id=p_account_id AND lease_until IS NOT NULL
      AND lease_until>=p_expires AND emergency_stop AND arm_request_id IS NULL
  ) THEN RAISE EXCEPTION 'original_frame_original_lease'; END IF;
  SELECT * INTO head_row FROM public.demo_account_observation_batches
    WHERE environment=p_environment AND account_id=p_account_id
      AND settlement_currency=p_currency ORDER BY sequence DESC LIMIT 1;
  IF head_row.sequence IS DISTINCT FROM p_through
    OR head_row.event_sha256 IS DISTINCT FROM p_head THEN
    RAISE EXCEPTION 'original_frame_head_changed';
  END IF;
  IF p_after>0 AND NOT EXISTS(
    SELECT 1 FROM public.demo_account_observation_batches
      WHERE environment=p_environment AND account_id=p_account_id
      AND settlement_currency=p_currency AND sequence=p_after
      AND event_sha256=p_previous
  ) THEN RAISE EXCEPTION 'original_frame_predecessor'; END IF;
  SELECT * INTO current_row FROM public.demo_account_observation_batches
    WHERE environment=p_environment AND account_id=p_account_id
      AND settlement_currency=p_currency AND sequence=p_after+1;
  IF current_row.sequence IS NULL
    OR current_row.previous_sha256 IS DISTINCT FROM p_previous THEN
    RAISE EXCEPTION 'original_frame_gap';
  END IF;
  expected_scope:=encode(sha256(convert_to(
    '["demo","'||p_account_id||'","USDT"]','UTF8')),'hex');
  d:=public.account_history_partition_v1_json(current_row.document_json);
  r:=public.account_history_partition_v1_json(current_row.receipt_json);
  a:=public.account_history_partition_v1_json(d->>'anchor_json');
  IF d->>'schema_version' IS DISTINCT FROM 'ctcc.account_observation_batch.v1'
    OR d->>'scope_sha256' IS DISTINCT FROM expected_scope
    OR d->'sequence' IS DISTINCT FROM to_jsonb(current_row.sequence)
    OR d->'previous_sha256' IS DISTINCT FROM coalesce(to_jsonb(current_row.previous_sha256),'null'::jsonb)
    OR encode(sha256(convert_to(current_row.document_json,'UTF8')),'hex')
       IS DISTINCT FROM current_row.event_sha256
    OR encode(sha256(convert_to(current_row.receipt_json,'UTF8')),'hex')
       IS DISTINCT FROM d->>'receipt_sha256'
    OR encode(sha256(convert_to(d->>'anchor_json','UTF8')),'hex')
       IS DISTINCT FROM d->>'anchor_sha256'
    OR r->'continuation_anchor' IS DISTINCT FROM a
    OR r->>'scope_sha256' IS DISTINCT FROM expected_scope
    OR d->'account_complete' IS DISTINCT FROM 'false'::jsonb
    OR d->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR d->>'admission' IS DISTINCT FROM 'DENY'
    OR r->'account_complete' IS DISTINCT FROM 'false'::jsonb
    OR r->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR r->>'admission' IS DISTINCT FROM 'DENY'
    OR jsonb_typeof(a->'verified_witnesses') IS DISTINCT FROM 'array'
    OR jsonb_array_length(a->'verified_witnesses')>31
    OR d->'reference'->>'capture_id' IS DISTINCT FROM current_row.capture_id THEN
    RAISE EXCEPTION 'original_frame_original_b3_binding';
  END IF;
  sd:=d; descriptor_sequence:=current_row.sequence;
  descriptor_source_sequence:=current_row.source_sequence;
  descriptor_capture:=current_row.capture_id; descriptor_event:=current_row.event_sha256;
  __APPEND_BOUNDED_DESCRIPTOR__
  -- Only the ORIGINAL receipt supplies witnesses. The caller cannot choose
  -- arbitrary captures or foreign-account source identifiers.
  FOR w IN SELECT value FROM jsonb_array_elements(a->'verified_witnesses') LOOP
    IF p_expires<=clock_timestamp() THEN RAISE EXCEPTION 'original_frame_expired'; END IF;
    IF jsonb_typeof(w) IS DISTINCT FROM 'object'
      OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(w) key)
         IS DISTINCT FROM ARRAY['coverage_membership_sha256','event_sha256','sequence','source_membership_sha256']::text[]
      OR jsonb_typeof(w->'sequence') IS DISTINCT FROM 'number'
      OR w->>'sequence' !~ '^[1-9][0-9]{0,18}$'
      OR (w->>'sequence')::numeric>9223372036854775807 THEN
      RAISE EXCEPTION 'original_frame_witness_shape';
    END IF;
    FOREACH reference_key IN ARRAY ARRAY['event_sha256','source_membership_sha256','coverage_membership_sha256'] LOOP
      IF jsonb_typeof(w->reference_key) IS DISTINCT FROM 'string'
        OR w->>reference_key !~ '^[a-f0-9]{64}$' THEN
        RAISE EXCEPTION 'original_frame_witness_shape';
      END IF;
    END LOOP;
    witness_sequence:=(w->>'sequence')::bigint;
    IF witness_sequence<=prior_sequence OR witness_sequence>=current_row.sequence THEN
      RAISE EXCEPTION 'original_frame_witness_order';
    END IF;
    SELECT * INTO witness_row FROM public.demo_account_observation_batches
      WHERE environment=p_environment AND account_id=p_account_id
        AND settlement_currency=p_currency AND sequence=witness_sequence;
    IF witness_row.sequence IS NULL THEN RAISE EXCEPTION 'original_frame_witness_gap'; END IF;
    wd:=public.account_history_partition_v1_json(witness_row.document_json);
    IF encode(sha256(convert_to(witness_row.document_json,'UTF8')),'hex')
         IS DISTINCT FROM witness_row.event_sha256
      OR w->>'event_sha256' IS DISTINCT FROM witness_row.event_sha256
      OR w->>'source_membership_sha256' IS DISTINCT FROM wd->>'source_membership_sha256'
      OR w->>'coverage_membership_sha256' IS DISTINCT FROM wd->>'coverage_membership_sha256'
      OR wd->>'scope_sha256' IS DISTINCT FROM expected_scope
      OR wd->'sequence' IS DISTINCT FROM to_jsonb(witness_row.sequence)
      OR wd->'reference'->>'capture_id' IS DISTINCT FROM witness_row.capture_id THEN
      RAISE EXCEPTION 'original_frame_witness_pin';
    END IF;
    sd:=wd; descriptor_sequence:=witness_row.sequence;
    descriptor_source_sequence:=witness_row.source_sequence;
    descriptor_capture:=witness_row.capture_id; descriptor_event:=witness_row.event_sha256;
    __APPEND_BOUNDED_DESCRIPTOR__
    prior_sequence:=witness_sequence;
  END LOOP;
  SELECT registration_json,registration_sha256 INTO member_text,member_hash
    FROM public.demo_account_history_native_members WHERE environment=p_environment
      AND account_id=p_account_id AND settlement_currency=p_currency
      AND b3_sequence=current_row.sequence;
  SELECT checkpoint_json,checkpoint_sha256 INTO checkpoint_text,checkpoint_hash
    FROM public.demo_account_history_partition_checkpoints WHERE environment=p_environment
      AND account_id=p_account_id AND settlement_currency=p_currency
      AND last_sequence=current_row.sequence;
  IF (member_text IS NOT NULL AND encode(sha256(convert_to(member_text,'UTF8')),'hex')
       IS DISTINCT FROM member_hash)
    OR (checkpoint_text IS NOT NULL AND encode(sha256(convert_to(checkpoint_text,'UTF8')),'hex')
       IS DISTINCT FROM checkpoint_hash) THEN
    RAISE EXCEPTION 'original_frame_optional_hash';
  END IF;
  metadata_bytes:=octet_length(current_row.document_json)::numeric
    +octet_length(current_row.receipt_json)::numeric
    +coalesce(octet_length(member_text),0)::numeric
    +coalesce(octet_length(checkpoint_text),0)::numeric;
  -- PHASE1: reserve ALL current/witness bytes and framing before any raw hex
  -- or raw JSON array allocation. Missing fields become zero only in counts.
  FOREACH ref IN ARRAY sources LOOP
    IF p_expires<=clock_timestamp() THEN RAISE EXCEPTION 'original_frame_expired'; END IF;
    -- The exact bounded descriptor was validated BEFORE array_append.
    SELECT count(*) AS count_rows,min(sequence) AS first_sequence,max(sequence) AS last_sequence,
      bool_and(environment=p_environment AND account_id=p_account_id
        AND settlement_currency=p_currency) AS exact_scope,
      sum(octet_length(event_json)::numeric
        +coalesce(octet_length(raw_body),0)::numeric
        +coalesce(octet_length(packet_payload),0)::numeric) AS raw_bytes,
      max(db_recorded_at) AS last_recorded
    INTO measure FROM public.demo_account_capture_events
      WHERE capture_id=(ref->'reference'->>'capture_id');
    IF measure.count_rows<>(ref->>'source_sequence')::bigint
      OR measure.count_rows<1 OR measure.count_rows>8192
      OR measure.first_sequence<>1
      OR measure.last_sequence<>(ref->>'source_sequence')::bigint
      OR measure.exact_scope IS DISTINCT FROM true OR measure.raw_bytes IS NULL
      OR measure.raw_bytes<1 OR measure.raw_bytes>67108864
      OR measure.last_recorded>clock_timestamp() THEN
      RAISE EXCEPTION 'original_frame_source_gap_scope_or_bytes';
    END IF;
    SELECT * INTO terminal FROM public.demo_account_capture_events
      WHERE capture_id=(ref->'reference'->>'capture_id')
        AND sequence=(ref->>'source_sequence')::bigint;
    IF terminal.event_sha256 IS DISTINCT FROM ref->'reference'->>'head_sha256'
      OR terminal.chain_bytes IS DISTINCT FROM measure.raw_bytes::bigint
      OR (public.account_history_partition_v1_json(terminal.event_json))->>'kind'
         IS DISTINCT FROM 'terminal'
      OR (public.account_history_partition_v1_json(terminal.event_json))->>'outcome'
         IS DISTINCT FROM 'complete_recorded' THEN
      RAISE EXCEPTION 'original_frame_source_terminal';
    END IF;
    source_bytes:=source_bytes+measure.raw_bytes;
    event_count:=event_count+measure.count_rows;
    IF source_bytes+metadata_bytes>67108864 THEN
      RAISE EXCEPTION 'original_frame_raw_budget';
    END IF;
  END LOOP;
  wire_bound:=2*source_bytes+6*metadata_bytes+4096*event_count
    +4096*cardinality(sources)+65536;
  IF wire_bound>67108864 THEN RAISE EXCEPTION 'original_frame_wire_budget'; END IF;
  IF p_expires<=clock_timestamp() THEN RAISE EXCEPTION 'original_frame_expired'; END IF;
  -- PHASE2: only after complete preflight, export exact original bytes. The
  -- aggregate preserves sequence; no sorting/dedup repairs the external data.
  FOREACH ref IN ARRAY sources LOOP
    IF p_expires<=clock_timestamp() THEN RAISE EXCEPTION 'original_frame_expired'; END IF;
    SELECT jsonb_agg(jsonb_build_object(
      'event_hex',encode(convert_to(e.event_json,'UTF8'),'hex'),
      'raw_hex',CASE WHEN e.raw_body IS NULL THEN NULL ELSE encode(e.raw_body,'hex') END,
      'packet_hex',CASE WHEN e.packet_payload IS NULL THEN NULL ELSE encode(e.packet_payload,'hex') END,
      'db_recorded_at',e.db_recorded_at) ORDER BY e.sequence)
    INTO wire_rows FROM public.demo_account_capture_events e
      WHERE e.capture_id=(ref->'reference'->>'capture_id')
        AND e.environment=p_environment AND e.account_id=p_account_id
        AND e.settlement_currency=p_currency;
    IF jsonb_array_length(wire_rows)<>(ref->>'source_sequence')::bigint THEN
      RAISE EXCEPTION 'original_frame_source_changed';
    END IF;
    chains:=chains||jsonb_build_array(ref||jsonb_build_object('rows',wire_rows));
    IF p_expires<=clock_timestamp() THEN RAISE EXCEPTION 'original_frame_expired'; END IF;
  END LOOP;
  db_observed_at:=clock_timestamp();
  IF p_expires<=db_observed_at THEN RAISE EXCEPTION 'original_frame_expired'; END IF;
  output:=jsonb_build_object(
    'schema_version','ctcc.account_original_history_frame_wire.v1',
    'scope',jsonb_build_array(p_environment,p_account_id,p_currency),
    'through_sequence',p_through,'head_sha256',p_head,'after_sequence',p_after,
    'previous_sha256',p_previous,'next_after',current_row.sequence,
    'has_more',current_row.sequence<p_through,
    'control_revision',p_control_revision,'account_revision',p_account_revision,
    'ledger_revision',p_ledger_revision,'backend_pid',pg_backend_pid(),
    'transaction_id',pg_current_xact_id()::text,'db_observed_at',db_observed_at,
    'source_raw_bytes',source_bytes,'metadata_utf8_bytes',metadata_bytes,
    'wire_upper_bound_bytes',wire_bound,
    'b3_recorded_at',current_row.db_recorded_at,
    'document_hex',encode(convert_to(current_row.document_json,'UTF8'),'hex'),
    'receipt_hex',encode(convert_to(current_row.receipt_json,'UTF8'),'hex'),
    'native_registration_hex',CASE WHEN member_text IS NULL THEN NULL
      ELSE encode(convert_to(member_text,'UTF8'),'hex') END,
    'checkpoint_hex',CASE WHEN checkpoint_text IS NULL THEN NULL
      ELSE encode(convert_to(checkpoint_text,'UTF8'),'hex') END,
    'chains',chains,'account_complete',false,'execution_authority',false,'admission','DENY');
  IF octet_length(output::text)>67108864 THEN RAISE EXCEPTION 'original_frame_wire_budget'; END IF;
  __SOURCE_ASSERT_SQL__
  IF source_catalog IS DISTINCT FROM original_source_catalog THEN
    RAISE EXCEPTION 'original_frame_source_catalog_changed';
  END IF;
  IF p_expires<=clock_timestamp() THEN RAISE EXCEPTION 'original_frame_expired'; END IF;
  RETURN output;
END
"""
_APPEND_DESCRIPTOR = r"""
  -- Every copied value has a fixed ASCII or bigint bound before append. No
  -- arbitrary original reference keys or membership objects enter sources.
  ref:=sd->'reference';
  IF jsonb_typeof(ref) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'original_frame_source_reference';
  END IF;
  IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(ref) key)
      IS DISTINCT FROM ARRAY['capture_id','head_sha256','packet_sha256','plan_sha256','session_binding_sha256']::text[] THEN
    RAISE EXCEPTION 'original_frame_source_reference';
  END IF;
  IF jsonb_typeof(ref->'capture_id') IS DISTINCT FROM 'string'
    OR ref->>'capture_id' !~ '^[a-f0-9]{32}$'
    OR ref->>'capture_id' IS DISTINCT FROM descriptor_capture THEN
    RAISE EXCEPTION 'original_frame_source_reference';
  END IF;
  FOREACH reference_key IN ARRAY ARRAY['head_sha256','packet_sha256','plan_sha256','session_binding_sha256'] LOOP
    IF jsonb_typeof(ref->reference_key) IS DISTINCT FROM 'string'
      OR ref->>reference_key !~ '^[a-f0-9]{64}$' THEN
      RAISE EXCEPTION 'original_frame_source_reference';
    END IF;
  END LOOP;
  FOREACH reference_key IN ARRAY ARRAY['source_membership_sha256','coverage_membership_sha256'] LOOP
    IF jsonb_typeof(sd->reference_key) IS DISTINCT FROM 'string'
      OR sd->>reference_key !~ '^[a-f0-9]{64}$' THEN
      RAISE EXCEPTION 'original_frame_source_membership';
    END IF;
  END LOOP;
  IF descriptor_sequence IS NULL OR descriptor_sequence<1
    OR descriptor_source_sequence IS NULL OR descriptor_source_sequence NOT BETWEEN 1 AND 8192
    OR descriptor_event IS NULL OR descriptor_event !~ '^[a-f0-9]{64}$' THEN
    RAISE EXCEPTION 'original_frame_source_descriptor';
  END IF;
  descriptor:=jsonb_build_object('sequence',descriptor_sequence,'event_sha256',descriptor_event,
    'reference',ref,'source_sequence',descriptor_source_sequence,
    'source_membership_sha256',sd->>'source_membership_sha256',
    'coverage_membership_sha256',sd->>'coverage_membership_sha256');
  -- The independent fixed allowance used in the WHOLE frame reservation is
  -- checked against the actual bounded descriptor before it is retained.
  IF octet_length(descriptor::text)>4096 THEN
    RAISE EXCEPTION 'original_frame_source_descriptor_budget';
  END IF;
  sources:=array_append(sources,descriptor);
"""
BODY = (
    BODY.replace("__APPEND_BOUNDED_DESCRIPTOR__", _APPEND_DESCRIPTOR)
    .replace("__SOURCE_LOCK_SQL__", source_guard.LOCK_SQL)
    .replace("__SOURCE_ASSERT_SQL__", source_guard.ASSERT_SQL)
)
DDL = (
    f"CREATE FUNCTION public.{FUNCTION}({ARGUMENTS}) RETURNS jsonb LANGUAGE plpgsql "
    "VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp "
    f"AS $of52${BODY}$of52$"
)
DDL_SHA256 = records.digest(DDL.encode("utf-8"))
CATALOG_SQL = f"""
SELECT jsonb_build_object(
  'functions',coalesce((SELECT jsonb_agg(jsonb_build_object(
    'name',p.proname,'oid',p.oid::bigint,'namespace',n.nspname,
    'namespace_oid',n.oid::bigint,'owner_oid',p.proowner::bigint,
    'arguments',oidvectortypes(p.proargtypes),
    'identity_arguments',pg_get_function_identity_arguments(p.oid),
    'returns',pg_get_function_result(p.oid),'language',l.lanname,
    'source',p.prosrc,'definition',pg_get_functiondef(p.oid),
    'security_definer',p.prosecdef,'volatility',p.provolatile,
    'config',p.proconfig,'kind',p.prokind,'parallel',p.proparallel,
    'strict',p.proisstrict,'leakproof',p.proleakproof,
    'can_execute',has_function_privilege(current_user,p.oid,'EXECUTE'),
    'acl',(SELECT jsonb_agg(jsonb_build_object(
      'grantee_oid',a.grantee::bigint,'grantor_oid',a.grantor::bigint,
      'privilege',a.privilege_type,'grantable',a.is_grantable)
      ORDER BY a.grantee,a.privilege_type)
      FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a)
    ) ORDER BY p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='public'
      AND p.proname='{FUNCTION}'),'[]'::jsonb),
  'source_catalog',({source_guard.CATALOG_SELECT}),
  'schema_usage',has_schema_privilege(current_user,'public','USAGE'),
  'source_table_access',EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname IN ('demo_account_capture_events',
      'demo_account_observation_batches','demo_account_history_native_members',
      'demo_account_history_partition_checkpoints','qualification_account_scopes','demo_account_controls',
      'demo_account_observation_facts','demo_account_observation_coverage','demo_account_observation_findings')
    AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
      OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))))
"""


def _checked_catalog_row(value, previous, approved_collector_role_oid):
    # Common exact structure and ACL checks; this helper alone is not a seal.
    try:
        if type(value) is not dict or set(value) != {
            "functions",
            "source_table_access",
            "source_catalog",
            "schema_usage",
        }:
            parent.deny("original_frame_catalog_shape")
        source_guard.verify_source_catalog(
            value["source_catalog"], previous, require_native_baseline=False
        )
        rows = value["functions"]
        if type(rows) is not list or len(rows) != 1:
            parent.deny("original_frame_function_drift")
        row = rows[0]
        keys = {
            "name",
            "oid",
            "namespace",
            "namespace_oid",
            "owner_oid",
            "arguments",
            "identity_arguments",
            "returns",
            "language",
            "source",
            "definition",
            "security_definer",
            "volatility",
            "config",
            "kind",
            "parallel",
            "strict",
            "leakproof",
            "can_execute",
            "acl",
        }
        if type(row) is not dict or set(row) != keys:
            parent.deny("original_frame_catalog_shape")
        owner = previous["tables"][0]["owner_oid"]
        namespace = previous["tables"][0]["namespace_oid"]
        if (
            row["name"] != FUNCTION
            or row["namespace"] != "public"
            or type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["namespace_oid"] != namespace
            or row["owner_oid"] != owner
            or row["arguments"] != SIGNATURE
            or row["identity_arguments"] != ARGUMENTS.replace(",", ", ")
            or row["returns"] != "jsonb"
            or row["language"] != "plpgsql"
            or row["source"] != BODY
            or type(row["definition"]) is not str
            or row["security_definer"] is not True
            or row["volatility"] != "v"
            or row["config"] != ["search_path=pg_catalog, public, pg_temp"]
            or row["kind"] != "f"
            or row["parallel"] != "u"
            or row["strict"] is not False
            or row["leakproof"] is not False
            or type(row["can_execute"]) is not bool
            or type(value["source_table_access"]) is not bool
            or value["schema_usage"] is not True
        ):
            parent.deny("original_frame_function_drift")
        expected = {owner}
        if approved_collector_role_oid is not None:
            if (
                type(approved_collector_role_oid) is not int
                or not 0 < approved_collector_role_oid < 2**32
                or approved_collector_role_oid == owner
            ):
                parent.deny("original_frame_approved_collector_invalid")
            expected.add(approved_collector_role_oid)
        acl = row["acl"]
        if type(acl) is not list or len(acl) != len(expected):
            parent.deny("original_frame_acl_drift")
        seen = set()
        for grant in acl:
            if type(grant) is not dict or set(grant) != {
                "grantee_oid",
                "grantor_oid",
                "privilege",
                "grantable",
            }:
                parent.deny("original_frame_acl_drift")
            grantee = grant["grantee_oid"]
            if (
                type(grantee) is not int
                or grantee not in expected
                or grantee in seen
                or grant["grantor_oid"] != owner
                or grant["privilege"] != "EXECUTE"
                or grant["grantable"] is not False
            ):
                parent.deny("original_frame_acl_drift")
            seen.add(grantee)
        if seen != expected or row["can_execute"] is not (
            previous["role"]["oid"] in expected
        ):
            parent.deny("original_frame_acl_drift")
        return row
    except parent.AccountHistoryPartitionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        parent.deny("original_frame_catalog_shape")


def _verify_runtime_role(value, previous, approved_collector_role_oid):
    role, server = previous["role"], previous["server"]
    if (
        approved_collector_role_oid is None
        or role["oid"] != approved_collector_role_oid
        or role["can_login"] is not True
        or server["current_user"] != server["session_user"]
        or role["name"] != server["session_user"]
        or server["role_setting"] != "none"
        or value["source_table_access"] is not False
    ):
        parent.deny("original_frame_runtime_role_denied")
    if any(
        role[name] is not False
        for name in (
            "superuser",
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "other_membership",
            "can_create",
        )
    ):
        parent.deny("original_frame_runtime_role_denied")
    if (
        any(
            server[name] != expected_setting
            for name, expected_setting in (
                ("timezone", "UTC"),
                ("search_path", "pg_catalog, public, pg_temp"),
                ("isolation", "read committed"),
                ("replication_role", "origin"),
                ("synchronous_commit", "on"),
                ("fsync", "on"),
                ("full_page_writes", "on"),
            )
        )
        or server["in_recovery"] is not False
    ):
        parent.deny("original_frame_runtime_role_denied")


def _verify_sealed_catalog(value, previous, row):
    # Both callers require the same observed source and function deparser pins.
    source_guard.verify_source_catalog(value["source_catalog"], previous)
    if (
        PG17_NATIVE_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
        or PG17_NATIVE_BASELINE_SHA256 is None
        or records.digest(row["definition"].encode("utf-8"))
        != PG17_NATIVE_BASELINE_SHA256
    ):
        parent.deny("original_frame_native_baseline_pending")


def verify_catalog(
    value, previous, *, approved_collector_role_oid=None, require_native_baseline=True
):
    """Retain the strict restricted-LOGIN runtime gate and original default."""
    try:
        if type(require_native_baseline) is not bool:
            parent.deny("original_frame_catalog_shape")
        row = _checked_catalog_row(value, previous, approved_collector_role_oid)
        if require_native_baseline:
            _verify_runtime_role(value, previous, approved_collector_role_oid)
            _verify_sealed_catalog(value, previous, row)
        return DDL_SHA256
    except parent.AccountHistoryPartitionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        parent.deny("original_frame_catalog_shape")


def expected_owner_installation_schema():
    """A declaration only; observed catalogs must pass the strict installer API."""
    return {
        "schema_version": "ctcc.original_history_frame_owner_installation.v1",
        "revision": "0052",
        "function": FUNCTION,
        "signature": SIGNATURE,
        "ddl_sha256": DDL_SHA256,
        "source_contract_sha256": source_guard.CONTRACT_SHA256,
        "source_native_baseline_status": source_guard.PG17_NATIVE_BASELINE_STATUS,
        "source_native_baseline_sha256": source_guard.PG17_NATIVE_BASELINE_SHA256,
        "frame_native_baseline_status": PG17_NATIVE_BASELINE_STATUS,
        "frame_native_baseline_sha256": PG17_NATIVE_BASELINE_SHA256,
        "execute_acl": "OWNER_ONLY_NO_COLLECTOR_OR_PUBLIC_GRANT",
        "runtime_admission": False,
        "native_history_prefix_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }


def verify_owner_installation_catalog(value, previous):
    """Verify a sealed, owner-only installation; never a collector capability.

    This API reads the original actual catalogs without changing their role,
    permissions, server facts, native definitions or OIDs. Unlike the runtime
    verifier, it requires the real migration owner and rejects every reader
    grant. It has no switch to omit either native deparser seal.
    """
    try:
        parent.verify_account_history_partition_catalog(previous)
        row = _checked_catalog_row(value, previous, None)
        role, server = previous["role"], previous["server"]
        if (
            role["oid"] != row["owner_oid"]
            or role["name"] != server["current_user"]
            or server["current_user"] != server["session_user"]
            or server["role_setting"] != "none"
            or role["superuser"] is not True
            or role["can_login"] is not True
            or value["source_table_access"] is not True
            or row["can_execute"] is not True
            or any(
                server[name] != expected_setting
                for name, expected_setting in (
                    ("timezone", "UTC"),
                    ("search_path", "pg_catalog, public, pg_temp"),
                    ("isolation", "read committed"),
                    ("replication_role", "origin"),
                    ("synchronous_commit", "on"),
                    ("fsync", "on"),
                    ("full_page_writes", "on"),
                )
            )
            or server["in_recovery"] is not False
        ):
            parent.deny("original_frame_installer_role_denied")
        _verify_sealed_catalog(value, previous, row)
        return expected_owner_installation_schema()
    except parent.AccountHistoryPartitionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        parent.deny("original_frame_catalog_shape")
