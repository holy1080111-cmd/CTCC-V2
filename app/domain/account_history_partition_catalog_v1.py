"""Sole DB0050 declarations and stdlib catalog policy; records grant no authority.

The initial native baseline deliberately denies formal deployment. Structural
DDL diagnostics permit a first real capture, never native or account admission.
"""

import copy
import json
import re

from app.domain import account_history_partition_v1 as records

PG17_DEPARSED_BASELINE = None
PG17_DEPARSED_BASELINE_STATUS = "PENDING_NATIVE_PG17_REVIEW"
CATALOG_SCHEMA = "ctcc.account_history_partition_actual_catalog.v1"
TABLES = (
    "demo_account_history_native_members",
    "demo_account_history_partition_checkpoints",
)
PARENTS = (
    "qualification_account_scopes",
    "demo_account_controls",
    "demo_account_observation_batches",
    "demo_account_capture_events",
)
SCOPE_COLUMNS = ("environment", "account_id", "settlement_currency")
MAX_READ_BYTES = records.MAX_CHAIN_BYTES
canonical = records.canonical
digest = records.digest


class AccountHistoryPartitionError(ValueError):
    """Fixed public codes; never include private original contents."""


def deny(code="account_history_partition_invalid"):
    raise AccountHistoryPartitionError(code) from None


def _columns(*extra):
    return (
        ("environment", "character varying(8)", False),
        ("account_id", "character varying(32)", False),
        ("settlement_currency", "character varying(16)", False),
        *extra,
    )


_SCOPE_CHECK = (
    "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency='USDT'"
)
TABLE_SPECS = {
    TABLES[0]: {
        "columns": _columns(
            ("b3_sequence", "bigint", False),
            ("capture_id", "character varying(32)", False),
            ("source_sequence", "bigint", False),
            ("b3_event_sha256", "character varying(64)", False),
            ("b3_document_json", "text", False),
            ("registration_sha256", "character varying(64)", False),
            ("registration_json", "text", False),
            ("native_invocation_id", "character varying(32)", False),
            ("custody_bundle_id", "character varying(32)", False),
            ("recorded_at", "timestamp with time zone", False),
        ),
        "primary_key": (*SCOPE_COLUMNS, "b3_sequence"),
        "checks": {
            "ck_history_member_scope": _SCOPE_CHECK,
            "ck_history_member_sequence": "b3_sequence BETWEEN 1 AND 9223372036854775807 AND source_sequence BETWEEN 1 AND 8192",
            "ck_history_member_identity": "capture_id ~ '^[a-f0-9]{32}$' AND native_invocation_id ~ '^[a-f0-9]{32}$' AND custody_bundle_id ~ '^[a-f0-9]{32}$'",
            "ck_history_member_hashes": "b3_event_sha256 ~ '^[a-f0-9]{64}$' AND registration_sha256 ~ '^[a-f0-9]{64}$'",
            "ck_history_member_bytes": "octet_length(b3_document_json) BETWEEN 1 AND 262144 AND octet_length(registration_json) BETWEEN 1 AND 8388608",
        },
        "foreign_keys": (
            ("fk_history_member_scope", SCOPE_COLUMNS, PARENTS[0], SCOPE_COLUMNS),
            (
                "fk_history_member_b3",
                (*SCOPE_COLUMNS, "b3_sequence"),
                PARENTS[2],
                (*SCOPE_COLUMNS, "sequence"),
            ),
            (
                "fk_history_member_b1",
                ("capture_id", "source_sequence"),
                PARENTS[3],
                ("capture_id", "sequence"),
            ),
        ),
        "unique": (
            ("uq_history_member_capture", ("capture_id",)),
            (
                "uq_history_member_invocation",
                ("environment", "account_id", "native_invocation_id"),
            ),
            (
                "uq_history_member_custody",
                ("environment", "account_id", "custody_bundle_id"),
            ),
        ),
    },
    TABLES[1]: {
        "columns": _columns(
            ("partition_number", "bigint", False),
            ("first_sequence", "bigint", False),
            ("last_sequence", "bigint", False),
            ("terminal_event_sha256", "character varying(64)", False),
            ("previous_checkpoint_sha256", "character varying(64)", True),
            ("checkpoint_sha256", "character varying(64)", False),
            ("checkpoint_json", "text", False),
            ("recorded_at", "timestamp with time zone", False),
        ),
        "primary_key": (*SCOPE_COLUMNS, "partition_number"),
        "checks": {
            "ck_history_checkpoint_scope": _SCOPE_CHECK,
            "ck_history_checkpoint_span": "partition_number BETWEEN 1 AND 288230376151711743 AND first_sequence=32*(partition_number-1)+1 AND last_sequence=32*partition_number",
            "ck_history_checkpoint_hashes": "terminal_event_sha256 ~ '^[a-f0-9]{64}$' AND checkpoint_sha256 ~ '^[a-f0-9]{64}$' AND ((partition_number=1 AND previous_checkpoint_sha256 IS NULL) OR (partition_number>1 AND previous_checkpoint_sha256 IS NOT NULL AND previous_checkpoint_sha256 ~ '^[a-f0-9]{64}$'))",
            "ck_history_checkpoint_bytes": "octet_length(checkpoint_json) BETWEEN 1 AND 8388608",
        },
        "foreign_keys": (
            ("fk_history_checkpoint_scope", SCOPE_COLUMNS, PARENTS[0], SCOPE_COLUMNS),
            (
                "fk_history_checkpoint_first",
                (*SCOPE_COLUMNS, "first_sequence"),
                PARENTS[2],
                (*SCOPE_COLUMNS, "sequence"),
            ),
            (
                "fk_history_checkpoint_last",
                (*SCOPE_COLUMNS, "last_sequence"),
                PARENTS[2],
                (*SCOPE_COLUMNS, "sequence"),
            ),
        ),
        "unique": (
            ("uq_history_checkpoint_hash", (*SCOPE_COLUMNS, "checkpoint_sha256")),
        ),
    },
}


def _array(values):
    return ",".join("'" + value.replace("'", "''") + "'" for value in values)


def _table_sql(name, spec):
    fields = [
        column
        + " "
        + dtype
        + ("" if nullable else " NOT NULL")
        + (" DEFAULT clock_timestamp()" if column == "recorded_at" else "")
        for column, dtype, nullable in spec["columns"]
    ]
    fields.append(f"CONSTRAINT pk_{name} PRIMARY KEY ({','.join(spec['primary_key'])})")
    fields.extend(
        f"CONSTRAINT {key} CHECK ({expression})"
        for key, expression in spec["checks"].items()
    )
    fields.extend(
        f"CONSTRAINT {key} FOREIGN KEY ({','.join(columns)}) REFERENCES public.{target} ({','.join(target_columns)}) ON DELETE RESTRICT"
        for key, columns, target, target_columns in spec["foreign_keys"]
    )
    fields.extend(
        f"CONSTRAINT {key} UNIQUE ({','.join(columns)})"
        for key, columns in spec["unique"]
    )
    return f"CREATE TABLE public.{name} (" + ",".join(fields) + ")"


TABLE_SQL = tuple(_table_sql(name, spec) for name, spec in TABLE_SPECS.items())
INDEX_SQL = ()
UID_SQL = "('x'||substr(encode(pg_catalog.sha256(pg_catalog.convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||pg_catalog.convert_to(p_environment,'UTF8')||decode('00','hex')||pg_catalog.convert_to(p_account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint"

# Function bodies are fixed DB0050 declarations. Their aggregate identity is
# pinned by migration0050, independently of the later native deparser seal.
_JSON_BODY = "DECLARE rebuilt text; ch text; encoded text; value_text text; item record;\n i integer; code integer; low_code integer; kind text; frame jsonb;\n stack jsonb[]; stack_length integer; node_count integer; level integer;\n key_count integer; key_index integer; v jsonb;\nBEGIN\n    rebuilt:=''; stack:=ARRAY[jsonb_build_array('node',p_value,0)]; node_count:=0;\n    WHILE cardinality(stack)>0 LOOP\n      IF octet_length(rebuilt)>8388608 THEN RAISE EXCEPTION 'account_history_partition_wire_bound'; END IF;\n      stack_length:=cardinality(stack); frame:=stack[stack_length]; stack:=stack[1:stack_length-1];\n      IF frame->>0='literal' THEN rebuilt:=rebuilt||(frame->>1); CONTINUE; END IF;\n      v:=frame->1; level:=(frame->>2)::integer; kind:=jsonb_typeof(v);\n      IF frame->>0<>'key' THEN node_count:=node_count+1; END IF;\n      IF (frame->>0<>'key' AND level>32) OR node_count>262144 OR cardinality(stack)>8388608 THEN\n        RAISE EXCEPTION 'account_history_partition_wire_bound'; END IF;\n      IF kind='object' THEN\n        rebuilt:=rebuilt||'{'; stack:=array_append(stack,jsonb_build_array('literal','}'));\n        SELECT count(*) INTO key_count FROM jsonb_each(v); key_index:=0;\n        FOR item IN SELECT key,value FROM jsonb_each(v) ORDER BY key COLLATE \"C\" DESC LOOP\n          key_index:=key_index+1;\n          stack:=array_append(stack,jsonb_build_array('node',item.value,level+1));\n          stack:=array_append(stack,jsonb_build_array('literal',':'));\n          stack:=array_append(stack,jsonb_build_array('key',to_jsonb(item.key),level+1));\n          IF key_index<key_count THEN stack:=array_append(stack,jsonb_build_array('literal',',')); END IF;\n        END LOOP;\n      ELSIF kind='array' THEN\n        rebuilt:=rebuilt||'['; stack:=array_append(stack,jsonb_build_array('literal',']'));\n        FOR item IN SELECT value,ordinality FROM jsonb_array_elements(v) WITH ORDINALITY ORDER BY ordinality DESC LOOP\n          stack:=array_append(stack,jsonb_build_array('node',item.value,level+1));\n          IF item.ordinality>1 THEN stack:=array_append(stack,jsonb_build_array('literal',',')); END IF;\n        END LOOP;\n      ELSIF kind='string' THEN\n        value_text:=v#>>'{}'; encoded:='\"';\n        FOR i IN 1..length(value_text) LOOP\n          ch:=substr(value_text,i,1); code:=ascii(ch);\n          IF code=34 OR code=92 THEN encoded:=encoded||chr(92)||ch;\n          ELSIF code=8 THEN encoded:=encoded||chr(92)||'b';\n          ELSIF code=9 THEN encoded:=encoded||chr(92)||'t';\n          ELSIF code=10 THEN encoded:=encoded||chr(92)||'n';\n          ELSIF code=12 THEN encoded:=encoded||chr(92)||'f';\n          ELSIF code=13 THEN encoded:=encoded||chr(92)||'r';\n          ELSIF code<32 OR code>=127 THEN\n            IF code<=65535 THEN encoded:=encoded||chr(92)||'u'||lpad(to_hex(code),4,'0');\n            ELSE code:=code-65536; low_code:=56320+(code%1024);\n              encoded:=encoded||chr(92)||'u'||lpad(to_hex(55296+(code/1024)),4,'0')||chr(92)||'u'||lpad(to_hex(low_code),4,'0'); END IF;\n          ELSE encoded:=encoded||ch; END IF;\n        END LOOP;\n        rebuilt:=rebuilt||encoded||'\"';\n      ELSIF kind='number' THEN\n        IF v::text !~ '^-?(0|[1-9][0-9]*)$' OR v::text='-0' THEN\n          RAISE EXCEPTION 'account_history_partition_integer'; END IF;\n        rebuilt:=rebuilt||v::text;\n      ELSIF kind IN ('boolean','null') THEN rebuilt:=rebuilt||v::text;\n      ELSE RAISE EXCEPTION 'account_history_partition_wire_type'; END IF;\n    END LOOP;\n  RETURN rebuilt;\nEND\n"

_LOCK_BODY = r"""
DECLARE c public.demo_account_controls%ROWTYPE; q public.qualification_account_scopes%ROWTYPE;
BEGIN
 IF p_environment IS DISTINCT FROM 'demo' OR p_account_id !~ '^[0-9]{1,32}$'
   OR p_currency IS DISTINCT FROM 'USDT' THEN RAISE EXCEPTION 'account_history_partition_scope'; END IF;
 IF current_user=session_user OR current_setting('role')<>'none'
   OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user
     AND (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls))
   OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname<>session_user AND pg_has_role(session_user,r.oid,'MEMBER'))
   OR has_schema_privilege(session_user,'public','CREATE')
   OR has_database_privilege(session_user,current_database(),'CREATE') THEN
   RAISE EXCEPTION 'account_history_partition_role_denied'; END IF;
 IF current_setting('transaction_isolation')<>'read committed'
   OR current_setting('TimeZone')<>'UTC' OR current_setting('synchronous_commit')<>'on'
   OR current_setting('session_replication_role')<>'origin' OR pg_is_in_recovery()
   OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
   RAISE EXCEPTION 'account_history_partition_session_denied'; END IF;
 PERFORM pg_catalog.pg_advisory_xact_lock(__UID_SQL__);
 SELECT * INTO c FROM public.demo_account_controls WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE;
 SELECT * INTO q FROM public.qualification_account_scopes WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency FOR UPDATE;
 IF c.account_id IS NULL OR q.account_id IS NULL THEN RAISE EXCEPTION 'account_history_partition_parent_missing'; END IF;
 IF p_control_revision IS NOT NULL AND (c.control_revision<>p_control_revision OR q.account_revision<>p_account_revision OR q.ledger_revision<>p_ledger_revision
   OR NOT c.emergency_stop OR c.arm_request_id IS NOT NULL OR p_expires IS NULL
   OR p_expires<=clock_timestamp() OR p_expires>c.lease_until) THEN
   RAISE EXCEPTION 'account_history_partition_fence_changed'; END IF;
 IF (p_control_revision IS NULL)<>(p_account_revision IS NULL) OR (p_control_revision IS NULL)<>(p_ledger_revision IS NULL) THEN RAISE EXCEPTION 'account_history_partition_fence_invalid'; END IF;
 PERFORM 1 FROM public.demo_account_observation_batches WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency ORDER BY sequence DESC LIMIT 1 FOR UPDATE;
 LOCK TABLE public.qualification_account_scopes, public.demo_account_controls, public.demo_account_observation_batches, public.demo_account_capture_events,
   public.demo_account_history_native_members, public.demo_account_history_partition_checkpoints IN ROW EXCLUSIVE MODE NOWAIT;
END
""".replace("__UID_SQL__", UID_SQL)

_MEMBER_BODY = r"""
DECLARE b public.demo_account_observation_batches%ROWTYPE; e public.demo_account_capture_events%ROWTYPE;
 s jsonb; l jsonb; d jsonb; a jsonb; k text; item record; previous_stamp jsonb; stamp jsonb;
 expected jsonb; root_a jsonb; root_b jsonb; total numeric; sealed bigint;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'account_history_partition_immutable'; END IF;
 SELECT * INTO b FROM public.demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency AND sequence=NEW.b3_sequence;
 SELECT * INTO e FROM public.demo_account_capture_events WHERE capture_id=NEW.capture_id AND sequence=NEW.source_sequence;
 IF b.sequence IS NULL OR e.sequence IS NULL OR b.capture_id<>NEW.capture_id OR b.source_sequence<>NEW.source_sequence
   OR b.event_sha256<>NEW.b3_event_sha256 OR b.document_json<>NEW.b3_document_json
   OR e.environment<>NEW.environment OR e.account_id<>NEW.account_id OR e.settlement_currency<>NEW.settlement_currency
   OR e.event_sha256 IS DISTINCT FROM b.document_json::jsonb->'reference'->>'head_sha256'
   OR EXISTS(SELECT 1 FROM public.demo_account_capture_events x WHERE x.capture_id=NEW.capture_id AND x.sequence>NEW.source_sequence)
   OR (SELECT max(sequence) FROM public.demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency)<>NEW.b3_sequence THEN
   RAISE EXCEPTION 'account_history_partition_original_source_changed'; END IF;
 SELECT max(last_sequence) INTO sealed FROM public.demo_account_history_partition_checkpoints WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency;
 IF NEW.b3_sequence<=coalesce(sealed,0) THEN RAISE EXCEPTION 'account_history_partition_no_backfill'; END IF;
 d:=public.account_history_partition_v1_json(NEW.b3_document_json);
 s:=public.account_history_partition_v1_json(NEW.registration_json);
 IF jsonb_typeof(s->'native_linkage_json') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'account_history_partition_linkage'; END IF;
 l:=public.account_history_partition_v1_json(s->>'native_linkage_json');
 a:=public.account_history_partition_v1_json(d->>'anchor_json');
 IF encode(sha256(convert_to(NEW.b3_document_json,'UTF8')),'hex')<>NEW.b3_event_sha256
   OR encode(sha256(convert_to(NEW.registration_json,'UTF8')),'hex')<>NEW.registration_sha256
   OR d->>'anchor_sha256' IS DISTINCT FROM encode(sha256(convert_to(d->>'anchor_json','UTF8')),'hex')
   OR d->'sequence' IS DISTINCT FROM to_jsonb(NEW.b3_sequence)
   OR d->'scope_sha256' IS DISTINCT FROM to_jsonb(encode(sha256(convert_to('["demo","'||NEW.account_id||'","USDT"]','UTF8')),'hex'))
   OR d->'account_complete' IS DISTINCT FROM 'false'::jsonb OR d->'execution_authority' IS DISTINCT FROM 'false'::jsonb
   OR d->>'admission' IS DISTINCT FROM 'DENY' THEN RAISE EXCEPTION 'account_history_partition_source_binding'; END IF;
 IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(l) key) IS DISTINCT FROM ARRAY[__LINKAGE_KEYS__]::text[]
   OR l->>'schema_version' IS DISTINCT FROM '__LINKAGE_SCHEMA__'
   OR l->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
   OR l->'b3_sequence' IS DISTINCT FROM to_jsonb(NEW.b3_sequence)
   OR l->>'b3_event_sha256' IS DISTINCT FROM NEW.b3_event_sha256
   OR l->'b3_anchor_sha256' IS DISTINCT FROM d->'anchor_sha256'
   OR l->'latest_source_reference' IS DISTINCT FROM d->'reference' THEN RAISE EXCEPTION 'account_history_partition_linkage'; END IF;
 FOREACH k IN ARRAY ARRAY['native_invocation_id','custody_bundle_id'] LOOP
   IF jsonb_typeof(l->k) IS DISTINCT FROM 'string' OR l->>k !~ '^[a-f0-9]{32}$' THEN RAISE EXCEPTION 'account_history_partition_linkage'; END IF;
 END LOOP;
 IF l->>'native_invocation_id' IS DISTINCT FROM NEW.native_invocation_id OR l->>'custody_bundle_id' IS DISTINCT FROM NEW.custody_bundle_id THEN RAISE EXCEPTION 'account_history_partition_linkage'; END IF;
 FOREACH k IN ARRAY ARRAY['bundle_sha256','proof_sha256','native_policy_sha256','source_manifest_sha256'] LOOP
   IF jsonb_typeof(l->k) IS DISTINCT FROM 'string' OR l->>k !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'account_history_partition_linkage'; END IF;
 END LOOP;
 root_a:=l->'raw_root'; root_b:=l->'companion_root';
 FOREACH stamp IN ARRAY ARRAY[root_a,root_b] LOOP
   IF jsonb_typeof(stamp) IS DISTINCT FROM 'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(stamp) key) IS DISTINCT FROM ARRAY['device','inode','path']::text[]
     OR jsonb_typeof(stamp->'path') IS DISTINCT FROM 'string' OR length(stamp->>'path') NOT BETWEEN 1 AND 4096
     OR stamp->>'path' !~ '^(/|[A-Za-z]:[/\\])' OR stamp->>'path' ~ '(^|[/\\])\.\.([/\\]|$)'
     OR EXISTS(SELECT 1 FROM jsonb_each(stamp) WHERE key IN ('device','inode') AND (jsonb_typeof(value)<>'number' OR value::text !~ '^(0|[1-9][0-9]*)$' OR value::text::numeric>=340282366920938463463374607431768211456)) THEN
     RAISE EXCEPTION 'account_history_partition_root_locator'; END IF;
 END LOOP;
 IF root_a->>'path'=root_b->>'path' OR (root_a->'device'=root_b->'device' AND root_a->'inode'=root_b->'inode') THEN RAISE EXCEPTION 'account_history_partition_distinct_roots'; END IF;
 FOREACH k IN ARRAY ARRAY['raw_inventory','companion_inventory'] LOOP
   IF jsonb_typeof(l->k) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_each(l->k)) NOT BETWEEN 1 AND 8192 THEN RAISE EXCEPTION 'account_history_partition_inventory'; END IF;
   total:=0;
   FOR item IN SELECT key,value FROM jsonb_each(l->k) LOOP
     IF item.key='' OR item.key ~ '(^/|\\|:|(^|/)\.{1,2}(/|$)|(^|/)\.git(/|$)|(^|/)\.env)' OR jsonb_typeof(item.value) IS DISTINCT FROM 'array' OR jsonb_array_length(item.value)<>2
       OR jsonb_typeof(item.value->0) IS DISTINCT FROM 'number' OR item.value->>0 !~ '^[1-9][0-9]*$'
       OR (item.value->>0)::numeric>9223372036854775807 OR jsonb_typeof(item.value->1) IS DISTINCT FROM 'string' OR item.value->>1 !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'account_history_partition_inventory'; END IF;
     total:=total+(item.value->>0)::numeric;
   END LOOP;
   IF (SELECT count(*) FROM jsonb_object_keys(l->k))<>(SELECT count(DISTINCT lower(key)) FROM jsonb_object_keys(l->k) key)
     OR total>CASE WHEN k='raw_inventory' THEN 73531392 ELSE 4718592 END THEN RAISE EXCEPTION 'account_history_partition_inventory'; END IF;
 END LOOP;
 IF NOT (l->'raw_inventory' ?& ARRAY['source.bundle.json','accepted.json','attempt.claim.json']) OR l->'raw_inventory'->'source.bundle.json'->>1 IS DISTINCT FROM l->>'bundle_sha256' THEN RAISE EXCEPTION 'account_history_partition_custody_locator'; END IF;
 previous_stamp:=NULL;
 FOREACH k IN ARRAY ARRAY['original_started','native_started','source_readback_complete'] LOOP
   stamp:=l->k;
   IF jsonb_typeof(stamp) IS DISTINCT FROM 'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(stamp) key) IS DISTINCT FROM ARRAY['monotonic_ns','utc_ns']::text[]
     OR EXISTS(SELECT 1 FROM jsonb_each(stamp) WHERE jsonb_typeof(value)<>'number' OR value::text !~ '^[1-9][0-9]*$' OR value::text::numeric>9223372036854775807)
     OR (previous_stamp IS NOT NULL AND ((stamp->>'utc_ns')::numeric<(previous_stamp->>'utc_ns')::numeric OR (stamp->>'monotonic_ns')::numeric<(previous_stamp->>'monotonic_ns')::numeric)) THEN
     RAISE EXCEPTION 'account_history_partition_clock'; END IF;
   previous_stamp:=stamp;
 END LOOP;
 expected:=jsonb_build_object('schema_version','__MEMBER_SCHEMA__','policy_sha256','__POLICY__','scope',jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency),
   'b3_sequence',NEW.b3_sequence,'b3_event_sha256',NEW.b3_event_sha256,'b3_anchor_sha256',d->'anchor_sha256','b3_receipt_sha256',d->'receipt_sha256',
   'source_reference',d->'reference','native_linkage_json',s->'native_linkage_json')||'__UNKNOWNS__'::jsonb;
 IF s IS DISTINCT FROM expected THEN RAISE EXCEPTION 'account_history_partition_registration'; END IF;
 RETURN NEW;
END
"""

_JSON_CHECK_BODY = r"""
BEGIN
 IF p_raw IS NULL OR octet_length(p_raw) NOT BETWEEN 1 AND 8388608 OR NOT (p_raw IS JSON OBJECT WITH UNIQUE KEYS) THEN
   RAISE EXCEPTION 'account_history_partition_json'; END IF;
 IF public.account_history_partition_v1_encode(p_raw::jsonb) IS DISTINCT FROM p_raw THEN
   RAISE EXCEPTION 'account_history_partition_noncanonical'; END IF;
 RETURN p_raw::jsonb;
END
"""

_CHECKPOINT_BODY = r"""
DECLARE entry record; b public.demo_account_observation_batches%ROWTYPE; m public.demo_account_history_native_members%ROWTYPE;
 d jsonb; a jsonb; s jsonb; previous jsonb:=NULL; expected jsonb; member jsonb; members jsonb; missing jsonb;
 prior_event text:=NULL; prior_anchor text:=NULL; previous_sha text:=NULL; root text; genesis_event text; genesis_anchor text;
 cumulative jsonb; counts jsonb; missing_total bigint:=0; overflow boolean:=false; source_policy text:=NULL;
 n bigint:=1; number bigint; k text; total bigint;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'account_history_partition_immutable'; END IF;
 SELECT coalesce(sum(octet_length(checkpoint_json)),0)+octet_length(NEW.checkpoint_json) INTO total FROM public.demo_account_history_partition_checkpoints WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency;
 IF total>67108864 THEN RAISE EXCEPTION 'account_history_partition_cumulative_bound'; END IF;
 IF NEW.partition_number<>1+coalesce((SELECT max(partition_number) FROM public.demo_account_history_partition_checkpoints WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency),0)
   OR NEW.last_sequence<>(SELECT max(sequence) FROM public.demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency) THEN
   RAISE EXCEPTION 'account_history_partition_checkpoint_head'; END IF;
 cumulative:=jsonb_build_object('source_facts_through_sequence',0,'coverage_through_sequence',0,'unresolved_findings_through_sequence',0);
 root:=encode(sha256(convert_to(public.account_history_partition_v1_encode(jsonb_build_array('__POLICY__',encode(sha256(convert_to('["demo","'||NEW.account_id||'","USDT"]','UTF8')),'hex'),'original_B3_partition_genesis')),'UTF8')),'hex');
 FOR entry IN SELECT partition_number,checkpoint_sha256,checkpoint_json FROM public.demo_account_history_partition_checkpoints WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency
   UNION ALL SELECT NEW.partition_number,NEW.checkpoint_sha256,NEW.checkpoint_json ORDER BY partition_number LOOP
   IF entry.partition_number<>n OR encode(sha256(convert_to(entry.checkpoint_json,'UTF8')),'hex')<>entry.checkpoint_sha256 THEN RAISE EXCEPTION 'account_history_partition_checkpoint_gap'; END IF;
   s:=public.account_history_partition_v1_json(entry.checkpoint_json);
   IF jsonb_typeof(s->'members') IS DISTINCT FROM 'array' OR jsonb_array_length(s->'members')<>32 THEN RAISE EXCEPTION 'account_history_partition_exact32'; END IF;
   missing:='[]'::jsonb; members:='[]'::jsonb;
   FOR number IN SELECT pg_catalog.generate_series(32*(n-1)+1,32*n) LOOP
     member:=s->'members'->((number-1)%32)::integer;
     SELECT * INTO b FROM public.demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency AND sequence=number;
     IF b.sequence IS NULL OR member->>'b3_document_json' IS DISTINCT FROM b.document_json THEN RAISE EXCEPTION 'account_history_partition_member_source'; END IF;
     d:=public.account_history_partition_v1_json(b.document_json); a:=public.account_history_partition_v1_json(d->>'anchor_json');
     IF encode(sha256(convert_to(b.document_json,'UTF8')),'hex')<>b.event_sha256 OR d->'sequence' IS DISTINCT FROM to_jsonb(number)
       OR d->>'previous_sha256' IS DISTINCT FROM prior_event OR a->>'previous_anchor_sha256' IS DISTINCT FROM prior_anchor
       OR a->'through_sequence' IS DISTINCT FROM to_jsonb(number) OR a->'previous_sequence' IS DISTINCT FROM to_jsonb(number-1)
       OR a->>'previous_event_sha256' IS DISTINCT FROM prior_event OR d->>'anchor_sha256' IS DISTINCT FROM encode(sha256(convert_to(d->>'anchor_json','UTF8')),'hex')
       OR d->'account_complete' IS DISTINCT FROM 'false'::jsonb OR d->'execution_authority' IS DISTINCT FROM 'false'::jsonb
       OR d->>'admission' IS DISTINCT FROM 'DENY' THEN RAISE EXCEPTION 'account_history_partition_global_source_chain'; END IF;
     IF number=1 THEN source_policy:=d->>'policy_sha256'; genesis_event:=b.event_sha256; genesis_anchor:=d->>'anchor_sha256'; END IF;
     IF d->>'policy_sha256' IS DISTINCT FROM source_policy OR a->>'policy_sha256' IS DISTINCT FROM source_policy
       OR d->>'scope_sha256' IS DISTINCT FROM encode(sha256(convert_to('["demo","'||NEW.account_id||'","USDT"]','UTF8')),'hex')
       OR a->'scope_sha256' IS DISTINCT FROM d->'scope_sha256' THEN RAISE EXCEPTION 'account_history_partition_source_policy'; END IF;
     counts:=jsonb_build_object('source_facts_through_sequence',coalesce((d->>'source_membership_count')::bigint,-1)+(cumulative->>'source_facts_through_sequence')::bigint,
       'coverage_through_sequence',coalesce((d->>'coverage_membership_count')::bigint,-1)+(cumulative->>'coverage_through_sequence')::bigint,
       'unresolved_findings_through_sequence',coalesce((d->>'finding_membership_count')::bigint,-1)+(cumulative->>'unresolved_findings_through_sequence')::bigint);
     FOREACH k IN ARRAY ARRAY['source_facts_through_sequence','coverage_through_sequence','unresolved_findings_through_sequence'] LOOP
       IF a->k IS DISTINCT FROM counts->k OR jsonb_typeof(a->k) IS DISTINCT FROM 'number' OR a->>k !~ '^(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'account_history_partition_cumulative_counts'; END IF;
     END LOOP;
     IF a->'required_proof_budget_exceeded' NOT IN ('true'::jsonb,'false'::jsonb) THEN RAISE EXCEPTION 'account_history_partition_overflow_unknown'; END IF;
     overflow:=overflow OR (a->>'required_proof_budget_exceeded')::boolean;
     SELECT * INTO m FROM public.demo_account_history_native_members WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency AND b3_sequence=number;
     IF m.b3_sequence IS NULL THEN
       IF member->'native_registration_json' IS DISTINCT FROM 'null'::jsonb THEN RAISE EXCEPTION 'account_history_partition_registration_unknown'; END IF;
       missing:=missing||to_jsonb(number); missing_total:=missing_total+1;
     ELSE
       IF member->>'native_registration_json' IS DISTINCT FROM m.registration_json OR m.b3_event_sha256<>b.event_sha256
         OR encode(sha256(convert_to(m.registration_json,'UTF8')),'hex')<>m.registration_sha256 THEN RAISE EXCEPTION 'account_history_partition_registration_changed'; END IF;
     END IF;
     root:=encode(sha256(convert_to(public.account_history_partition_v1_encode(jsonb_build_array(root,number,b.event_sha256,d->'anchor_sha256',d->'receipt_sha256',d->'source_membership_sha256',d->'coverage_membership_sha256',d->'finding_membership_sha256',m.registration_sha256)),'UTF8')),'hex');
     members:=members||jsonb_build_object('b3_document_json',b.document_json,'native_registration_json',m.registration_json);
     prior_event:=b.event_sha256; prior_anchor:=d->>'anchor_sha256'; cumulative:=counts;
   END LOOP;
   expected:=jsonb_build_object('schema_version','__CHECKPOINT_SCHEMA__','policy_sha256','__POLICY__','b3_policy_sha256',source_policy,'scope',jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency),
     'partition_number',n,'first_sequence',32*(n-1)+1,'last_sequence',32*n,'member_count',32,'previous_checkpoint_sha256',previous_sha,
     'previous_terminal_event_sha256',previous->'terminal_event_sha256','previous_terminal_anchor_sha256',previous->'terminal_anchor_sha256',
     'genesis_event_sha256',genesis_event,'genesis_anchor_sha256',genesis_anchor,'terminal_event_sha256',prior_event,'terminal_anchor_sha256',prior_anchor,
     'ordered_membership_sha256',root,'cumulative_counts',cumulative,'missing_native_registration_sequences',missing,'native_registration_count',32-jsonb_array_length(missing),
     'missing_native_registrations_through_sequence',missing_total,'required_proof_budget_exceeded',overflow,'members',members)||'__UNKNOWNS__'::jsonb;
   IF s IS DISTINCT FROM expected THEN RAISE EXCEPTION 'account_history_partition_checkpoint_changed'; END IF;
   previous:=expected; previous_sha:=entry.checkpoint_sha256; n:=n+1;
 END LOOP;
 IF NEW.previous_checkpoint_sha256 IS DISTINCT FROM previous->>'previous_checkpoint_sha256'
   OR NEW.terminal_event_sha256 IS DISTINCT FROM prior_event
   OR NEW.first_sequence<>(previous->>'first_sequence')::bigint OR NEW.last_sequence<>(previous->>'last_sequence')::bigint THEN
   RAISE EXCEPTION 'account_history_partition_checkpoint_columns'; END IF;
 RETURN NEW;
END
"""

_APPEND_BODY = r"""
DECLARE b public.demo_account_observation_batches%ROWTYPE; current_cp public.demo_account_history_partition_checkpoints%ROWTYPE;
 s jsonb; l jsonb; v jsonb; existing text;
BEGIN
 PERFORM public.account_history_partition_v1_lock(p_environment,p_account_id,p_currency,p_control_revision,p_account_revision,p_ledger_revision,p_expires);
 IF p_control_revision IS NULL OR p_account_revision IS NULL OR p_ledger_revision IS NULL OR p_control_revision<1 OR p_account_revision<0 THEN RAISE EXCEPTION 'account_history_partition_fence_invalid'; END IF;
 SELECT * INTO b FROM public.demo_account_observation_batches WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency ORDER BY sequence DESC LIMIT 1;
 IF b.sequence IS NULL OR b.event_sha256 IS DISTINCT FROM p_expected_b3_sha THEN RAISE EXCEPTION 'account_history_partition_head_changed'; END IF;
 SELECT * INTO current_cp FROM public.demo_account_history_partition_checkpoints WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency ORDER BY partition_number DESC LIMIT 1;
 IF current_cp.checkpoint_sha256 IS DISTINCT FROM p_previous_checkpoint_sha THEN RAISE EXCEPTION 'account_history_partition_checkpoint_cas'; END IF;
 s:=public.account_history_partition_v1_json(p_registration); l:=public.account_history_partition_v1_json(s->>'native_linkage_json');
 SELECT registration_json INTO existing FROM public.demo_account_history_native_members WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency AND b3_sequence=b.sequence;
 IF existing IS NOT NULL THEN
   IF existing<>p_registration THEN RAISE EXCEPTION 'account_history_partition_registration_conflict'; END IF;
   RAISE EXCEPTION 'account_history_partition_already_recorded_no_reissue';
 END IF;
 INSERT INTO public.demo_account_history_native_members(environment,account_id,settlement_currency,b3_sequence,capture_id,source_sequence,b3_event_sha256,b3_document_json,registration_sha256,registration_json,native_invocation_id,custody_bundle_id)
   VALUES(p_environment,p_account_id,p_currency,b.sequence,b.capture_id,b.source_sequence,b.event_sha256,b.document_json,encode(sha256(convert_to(p_registration,'UTF8')),'hex'),p_registration,l->>'native_invocation_id',l->>'custody_bundle_id');
 IF p_checkpoint IS NOT NULL THEN
   v:=public.account_history_partition_v1_json(p_checkpoint);
   INSERT INTO public.demo_account_history_partition_checkpoints(environment,account_id,settlement_currency,partition_number,first_sequence,last_sequence,terminal_event_sha256,previous_checkpoint_sha256,checkpoint_sha256,checkpoint_json)
     VALUES(p_environment,p_account_id,p_currency,(v->>'partition_number')::bigint,(v->>'first_sequence')::bigint,(v->>'last_sequence')::bigint,v->>'terminal_event_sha256',v->>'previous_checkpoint_sha256',encode(sha256(convert_to(p_checkpoint,'UTF8')),'hex'),p_checkpoint);
 END IF;
 -- Immediate row guards plus deferred immutable-source pair checks both run;
 -- commit uncertainty is never converted into a retry or a replacement row.
 IF p_expires<=clock_timestamp() THEN RAISE EXCEPTION 'account_history_partition_expired_before_commit'; END IF;
 RETURN encode(sha256(convert_to(p_registration,'UTF8')),'hex');
END
"""

_READ_BODY = r"""
DECLARE result jsonb; total numeric; h public.demo_account_observation_batches%ROWTYPE;
BEGIN
 PERFORM public.account_history_partition_v1_lock(p_environment,p_account_id,p_currency,NULL,NULL,NULL,NULL);
 SELECT coalesce(sum(octet_length(document_json)),0) INTO total FROM public.demo_account_observation_batches WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency;
 total:=total+coalesce((SELECT sum(octet_length(registration_json)) FROM public.demo_account_history_native_members WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency),0)
   +coalesce((SELECT sum(octet_length(checkpoint_json)) FROM public.demo_account_history_partition_checkpoints WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency),0);
 IF total>67108864 THEN RAISE EXCEPTION 'account_history_partition_read_bound'; END IF;
 SELECT * INTO h FROM public.demo_account_observation_batches WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency ORDER BY sequence DESC LIMIT 1;
 SELECT jsonb_build_object('scope',jsonb_build_array(p_environment,p_account_id,p_currency),'head_sequence',h.sequence,'head_sha256',h.event_sha256,
   'control_revision',(SELECT control_revision FROM public.demo_account_controls WHERE environment=p_environment AND account_id=p_account_id),
   'account_revision',(SELECT account_revision FROM public.qualification_account_scopes WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency),
   'ledger_revision',(SELECT ledger_revision FROM public.qualification_account_scopes WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency),'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text,
   'members',coalesce((SELECT jsonb_agg(jsonb_build_object('sequence',b3_sequence,'record_json',registration_json,'record_sha256',registration_sha256,'recorded_at',recorded_at) ORDER BY b3_sequence) FROM public.demo_account_history_native_members WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency),'[]'::jsonb),
   'checkpoints',coalesce((SELECT jsonb_agg(jsonb_build_object('sequence',partition_number,'record_json',checkpoint_json,'record_sha256',checkpoint_sha256,'recorded_at',recorded_at) ORDER BY partition_number) FROM public.demo_account_history_partition_checkpoints WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency),'[]'::jsonb),
   'b3_documents',coalesce((SELECT jsonb_agg(document_json ORDER BY sequence) FROM public.demo_account_observation_batches WHERE environment=p_environment AND account_id=p_account_id AND settlement_currency=p_currency),'[]'::jsonb)) INTO result;
 RETURN result;
END
"""
_IMMUTABLE_BODY = "BEGIN RAISE EXCEPTION 'account_history_partition_immutable'; END"

_LINKAGE_KEYS = (
    "schema_version",
    "scope",
    "b3_sequence",
    "b3_event_sha256",
    "b3_anchor_sha256",
    "latest_source_reference",
    "native_invocation_id",
    "custody_bundle_id",
    "bundle_sha256",
    "proof_sha256",
    "native_policy_sha256",
    "source_manifest_sha256",
    "raw_inventory",
    "companion_inventory",
    "raw_root",
    "companion_root",
    "original_started",
    "native_started",
    "source_readback_complete",
)
_SUBSTITUTIONS = {
    "__POLICY__": records.POLICY_SHA256,
    "__LINKAGE_SCHEMA__": records.LINKAGE_SCHEMA,
    "__MEMBER_SCHEMA__": records.MEMBER_SCHEMA,
    "__CHECKPOINT_SCHEMA__": records.CHECKPOINT_SCHEMA,
    "__LINKAGE_KEYS__": _array(sorted(_LINKAGE_KEYS)),
    "__UNKNOWNS__": json.dumps(records._unknowns(), sort_keys=True, ensure_ascii=True),
}


def _body(raw):
    for key, value in _SUBSTITUTIONS.items():
        raw = raw.replace(key, value)
    return raw


FUNCTION_SPECS = (
    (
        "account_history_partition_v1_encode",
        "p_value jsonb",
        "text",
        "IMMUTABLE",
        _JSON_BODY,
    ),
    (
        "account_history_partition_v1_json",
        "p_raw text",
        "jsonb",
        "IMMUTABLE",
        _JSON_CHECK_BODY,
    ),
    (
        "account_history_partition_v1_lock",
        "p_environment text,p_account_id text,p_currency text,p_control_revision bigint,p_account_revision bigint,p_ledger_revision bigint,p_expires timestamp with time zone",
        "void",
        "VOLATILE",
        _LOCK_BODY,
    ),
    (
        "account_history_partition_v1_member_guard",
        "",
        "trigger",
        "VOLATILE",
        _MEMBER_BODY,
    ),
    (
        "account_history_partition_v1_checkpoint_guard",
        "",
        "trigger",
        "VOLATILE",
        _CHECKPOINT_BODY,
    ),
    (
        "account_history_partition_v1_immutable",
        "",
        "trigger",
        "VOLATILE",
        _IMMUTABLE_BODY,
    ),
    (
        "account_history_partition_v1_append",
        "p_environment text,p_account_id text,p_currency text,p_expected_b3_sha text,p_previous_checkpoint_sha text,p_registration text,p_checkpoint text,p_control_revision bigint,p_account_revision bigint,p_ledger_revision bigint,p_expires timestamp with time zone",
        "text",
        "VOLATILE",
        _APPEND_BODY,
    ),
    (
        "account_history_partition_v1_read",
        "p_environment text,p_account_id text,p_currency text",
        "jsonb",
        "VOLATILE",
        _READ_BODY,
    ),
)
FUNCTION_SIGNATURES = {
    name: ", ".join(part.split(" ", 1)[1] for part in args.split(",")) if args else ""
    for name, args, *_ in FUNCTION_SPECS
}
FUNCTION_SQL = {
    name: f"CREATE FUNCTION public.{name}({args}) RETURNS {returns} LANGUAGE plpgsql {volatility} SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $hp50${_body(body)}$hp50$"
    for name, args, returns, volatility, body in FUNCTION_SPECS
}
TRIGGER_SPECS = tuple(
    (name, table, events, level, function, deferred)
    for table, prefix, function in (
        (TABLES[0], "history_member", "account_history_partition_v1_member_guard"),
        (
            TABLES[1],
            "history_checkpoint",
            "account_history_partition_v1_checkpoint_guard",
        ),
    )
    for name, events, level, function, deferred in (
        (prefix + "_insert", "BEFORE INSERT", "ROW", function, False),
        (
            prefix + "_immutable",
            "BEFORE UPDATE OR DELETE",
            "ROW",
            "account_history_partition_v1_immutable",
            False,
        ),
        (
            prefix + "_truncate",
            "BEFORE TRUNCATE",
            "STATEMENT",
            "account_history_partition_v1_immutable",
            False,
        ),
        (
            prefix + "_deferred",
            "AFTER INSERT",
            "ROW",
            "account_history_partition_v1_pair_guard",
            True,
        ),
    )
)
_PAIR_BODY = r"""
BEGIN
 IF TG_TABLE_NAME='demo_account_history_native_members' THEN
   IF NOT EXISTS(SELECT 1 FROM public.demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency AND sequence=NEW.b3_sequence AND event_sha256=NEW.b3_event_sha256 AND document_json=NEW.b3_document_json) THEN
     RAISE EXCEPTION 'account_history_partition_deferred_source_changed'; END IF;
 ELSE
   IF NOT EXISTS(SELECT 1 FROM public.demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency AND sequence=NEW.last_sequence AND event_sha256=NEW.terminal_event_sha256) THEN
     RAISE EXCEPTION 'account_history_partition_deferred_source_changed'; END IF;
 END IF;
 RETURN NEW;
END
"""
FUNCTION_SPECS = (
    *FUNCTION_SPECS,
    ("account_history_partition_v1_pair_guard", "", "trigger", "VOLATILE", _PAIR_BODY),
)
FUNCTION_SIGNATURES["account_history_partition_v1_pair_guard"] = ""
FUNCTION_SQL["account_history_partition_v1_pair_guard"] = (
    f"CREATE FUNCTION public.account_history_partition_v1_pair_guard() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $hp50${_PAIR_BODY}$hp50$"
)
TRIGGER_SQL = tuple(
    f"CREATE {'CONSTRAINT ' if deferred else ''}TRIGGER {name} {events} ON public.{table} "
    + ("DEFERRABLE INITIALLY DEFERRED " if deferred else "")
    + f"FOR EACH {level} EXECUTE FUNCTION public.{function}()"
    for name, table, events, level, function, deferred in TRIGGER_SPECS
)
CATALOG_SQL = "\nSELECT jsonb_build_object(\n  'schema_version','ctcc.account_history_partition_actual_catalog.v1',\n  'parents',coalesce((SELECT jsonb_agg(jsonb_build_object('name',c.relname,'oid',CAST(c.oid AS bigint),'namespace',n.nspname,'namespace_oid',CAST(n.oid AS bigint),'owner_oid',CAST(c.relowner AS bigint),'kind',c.relkind) ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(ARRAY['qualification_account_scopes','demo_account_controls','demo_account_observation_batches','demo_account_capture_events'])),'[]'::jsonb),\n 'acl_rows',coalesce((SELECT jsonb_agg(jsonb_build_object('kind',kind,'object',object,'owner_oid',owner_oid,'grantee_oid',grantee_oid,'privilege',privilege,'grantable',grantable) ORDER BY kind,object,grantee_oid,privilege) FROM (\n SELECT 'table'::text kind,c.relname::text object,CAST(c.relowner AS bigint) owner_oid,CAST(x.grantee AS bigint) grantee_oid,x.privilege_type privilege,x.is_grantable grantable FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(CASE WHEN c.relacl IS NULL THEN acldefault('r',c.relowner) WHEN cardinality(c.relacl)=0 THEN NULL::aclitem[] WHEN array_ndims(c.relacl)<>1 OR array_lower(c.relacl,1)<>1 THEN NULL::aclitem[] WHEN array_position(c.relacl,NULL) IS NOT NULL THEN NULL::aclitem[] ELSE c.relacl END) x WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints'])\n UNION ALL SELECT 'column',c.relname||'.'||a.attname,CAST(c.relowner AS bigint),CAST(x.grantee AS bigint),x.privilege_type,x.is_grantable FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid CROSS JOIN LATERAL aclexplode(CASE WHEN a.attacl IS NULL THEN NULL::aclitem[] WHEN cardinality(a.attacl)=0 THEN NULL::aclitem[] WHEN array_ndims(a.attacl)<>1 OR array_lower(a.attacl,1)<>1 THEN NULL::aclitem[] WHEN array_position(a.attacl,NULL) IS NOT NULL THEN NULL::aclitem[] ELSE a.attacl END) x WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints']) AND a.attnum>0 AND NOT a.attisdropped\n UNION ALL SELECT 'function',p.proname,CAST(p.proowner AS bigint),CAST(x.grantee AS bigint),x.privilege_type,x.is_grantable FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(CASE WHEN p.proacl IS NULL THEN acldefault('f',p.proowner) WHEN cardinality(p.proacl)=0 THEN NULL::aclitem[] WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN NULL::aclitem[] WHEN array_position(p.proacl,NULL) IS NOT NULL THEN NULL::aclitem[] ELSE p.proacl END) x WHERE n.nspname='public' AND p.proname LIKE 'account_history_partition_v1_%') grants),'[]'::jsonb),\n 'column_acl_valid',NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints']) AND a.attnum>0 AND NOT a.attisdropped AND NOT (CASE WHEN a.attacl IS NULL OR cardinality(a.attacl)=0 THEN true WHEN array_ndims(a.attacl)<>1 OR array_lower(a.attacl,1)<>1 THEN false ELSE array_position(a.attacl,NULL) IS NULL END)),\n 'server',jsonb_build_object('version_num',current_setting('server_version_num'),\n    'version',version(),'database',current_database(),'current_user',current_user,\n    'session_user',session_user,'role_setting',current_setting('role'),'timezone',current_setting('TimeZone'),'search_path',current_setting('search_path'),\n    'isolation',current_setting('transaction_isolation'),'synchronous_commit',current_setting('synchronous_commit'),\n    'fsync',current_setting('fsync'),'full_page_writes',current_setting('full_page_writes'),\n    'replication_role',current_setting('session_replication_role'),'in_recovery',pg_is_in_recovery()),\n  'role',(SELECT jsonb_build_object('oid',CAST(oid AS bigint),'name',rolname,'can_login',rolcanlogin,'superuser',rolsuper,\n    'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,'bypassrls',rolbypassrls,\n    'other_membership',EXISTS(SELECT 1 FROM pg_roles x WHERE x.oid<>r.oid AND pg_has_role(current_user,x.oid,'MEMBER')),\n    'can_create',has_schema_privilege(current_user,'public','CREATE') OR has_database_privilege(current_user,current_database(),'CREATE'))\n    FROM pg_roles r WHERE rolname=current_user),\n  'tables',coalesce((SELECT jsonb_agg(jsonb_build_object('name',c.relname,'oid',CAST(c.oid AS bigint),'owner_oid',CAST(c.relowner AS bigint),\n    'namespace',n.nspname,'namespace_oid',CAST(n.oid AS bigint),'owner_superuser',(SELECT rolsuper FROM pg_roles WHERE oid=c.relowner),'owner',pg_get_userbyid(c.relowner),'kind',c.relkind,'persistence',c.relpersistence,\n    'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,'acl',c.relacl::text,'acl_valid',(CASE WHEN c.relacl IS NULL OR cardinality(c.relacl)=0 THEN true WHEN array_ndims(c.relacl)<>1 OR array_lower(c.relacl,1)<>1 THEN false ELSE array_position(c.relacl,NULL) IS NULL END),\n    'table_access',has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')\n      OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'),\n    'public_access',EXISTS(SELECT 1 FROM aclexplode(CASE WHEN c.relacl IS NULL THEN acldefault('r',c.relowner) WHEN cardinality(c.relacl)=0 THEN NULL::aclitem[] WHEN array_ndims(c.relacl)<>1 OR array_lower(c.relacl,1)<>1 THEN NULL::aclitem[] WHEN array_position(c.relacl,NULL) IS NOT NULL THEN NULL::aclitem[] ELSE c.relacl END) WHERE grantee=0))\n    ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace\n    WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints'])), '[]'::jsonb),\n  'columns',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',a.attname,\n    'position',a.attnum,'type',format_type(a.atttypid,a.atttypmod),'nullable',NOT a.attnotnull,\n    'default',pg_get_expr(d.adbin,d.adrelid),'generated',a.attgenerated,'identity',a.attidentity)\n    ORDER BY c.relname,a.attnum) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace\n    JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum\n    WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints']) AND a.attnum>0 AND NOT a.attisdropped), '[]'::jsonb),\n  'constraints',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',x.conname,\n    'kind',x.contype,'table_oid',CAST(c.oid AS bigint),'target_oid',CAST(NULLIF(x.confrelid,0) AS bigint),'target_namespace',(SELECT nspname FROM pg_namespace WHERE oid=(SELECT relnamespace FROM pg_class WHERE oid=x.confrelid)),'definition',pg_get_constraintdef(x.oid,true),'validated',x.convalidated,\n    'deferrable',x.condeferrable,'deferred',x.condeferred,'delete_action',x.confdeltype,\n    'columns',(SELECT jsonb_agg(a.attname ORDER BY ord) FROM unnest(x.conkey) WITH ORDINALITY z(attnum,ord)\n      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=z.attnum),\n    'target',CASE WHEN x.confrelid=0 THEN NULL ELSE (SELECT relname FROM pg_class WHERE oid=x.confrelid) END,\n    'target_columns',(SELECT jsonb_agg(a.attname ORDER BY ord) FROM unnest(x.confkey) WITH ORDINALITY z(attnum,ord)\n      JOIN pg_attribute a ON a.attrelid=x.confrelid AND a.attnum=z.attnum))\n    ORDER BY c.relname,x.conname) FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid\n    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints'])), '[]'::jsonb),\n  'indexes',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',ic.relname,\n    'definition',pg_get_indexdef(i.indexrelid),'unique',i.indisunique,'primary',i.indisprimary,\n    'valid',i.indisvalid,'ready',i.indisready,'method',am.amname,\n    'predicate',pg_get_expr(i.indpred,i.indrelid),'expressions',pg_get_expr(i.indexprs,i.indrelid),\n    'columns',(SELECT jsonb_agg(a.attname ORDER BY ord) FROM unnest(i.indkey) WITH ORDINALITY z(attnum,ord)\n      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=z.attnum))\n    ORDER BY c.relname,ic.relname) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid\n    JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_class ic ON ic.oid=i.indexrelid\n    JOIN pg_am am ON am.oid=ic.relam WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints'])), '[]'::jsonb),\n  'functions',coalesce((SELECT jsonb_agg(jsonb_build_object('name',p.proname,'oid',CAST(p.oid AS bigint),\n    'namespace_oid',CAST(n.oid AS bigint),'namespace',n.nspname,\n    'arguments',oidvectortypes(p.proargtypes),'identity_arguments',pg_get_function_identity_arguments(p.oid),\n    'returns',pg_get_function_result(p.oid),'owner_oid',CAST(p.proowner AS bigint),'owner',pg_get_userbyid(p.proowner),\n    'language',l.lanname,'security_definer',p.prosecdef,'volatility',p.provolatile,'config',p.proconfig,\n    'kind',p.prokind,'leakproof',p.proleakproof,'strict',p.proisstrict,'parallel',p.proparallel,\n    'source',p.prosrc,'definition',pg_get_functiondef(p.oid),'acl',p.proacl::text,'acl_valid',(CASE WHEN p.proacl IS NULL OR cardinality(p.proacl)=0 THEN true WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN false ELSE array_position(p.proacl,NULL) IS NULL END),\n    'can_execute',has_function_privilege(current_user,p.oid,'EXECUTE'),\n    'public_execute',EXISTS(SELECT 1 FROM aclexplode(CASE WHEN p.proacl IS NULL THEN acldefault('f',p.proowner) WHEN cardinality(p.proacl)=0 THEN NULL::aclitem[] WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN NULL::aclitem[] WHEN array_position(p.proacl,NULL) IS NOT NULL THEN NULL::aclitem[] ELSE p.proacl END) WHERE grantee=0 AND privilege_type='EXECUTE'))\n    ORDER BY p.proname,oidvectortypes(p.proargtypes)) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace\n    JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='public' AND p.proname LIKE 'account_history_partition_v1_%'), '[]'::jsonb),\n  'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'name',t.tgname,\n    'function',p.proname,'function_oid',CAST(t.tgfoid AS bigint),\n    'function_namespace_oid',CAST(fn.oid AS bigint),'function_namespace',fn.nspname,\n    'enabled',t.tgenabled,'type',t.tgtype,'deferrable',t.tgdeferrable,\n    'deferred',t.tginitdeferred,'constraint',t.tgconstraint<>0,'arguments',encode(t.tgargs,'hex'),\n    'when',pg_get_expr(t.tgqual,t.tgrelid),'definition',pg_get_triggerdef(t.oid,true))\n    ORDER BY c.relname,t.tgname) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid\n    JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid\n    JOIN pg_namespace fn ON fn.oid=p.pronamespace\n    WHERE n.nspname='public' AND c.relname=ANY(ARRAY['demo_account_history_native_members','demo_account_history_partition_checkpoints']) AND NOT t.tgisinternal), '[]'::jsonb)\n)\n"


def ddl_identity():
    """Frozen effective0050 DDL, independent of subsequent baseline sealing."""
    return digest(canonical([TABLE_SQL, INDEX_SQL, FUNCTION_SQL, TRIGGER_SQL]))


def expected_account_history_partition_schema():
    return copy.deepcopy(
        {
            "schema_version": "ctcc.account_history_partition_schema.v1",
            "revision": "0050",
            "tables": TABLE_SPECS,
            "functions": {
                name: {
                    "arguments": FUNCTION_SIGNATURES[name],
                    "returns": returns,
                    "volatility": volatility.lower()[0],
                    "source": _body(body),
                    "security_definer": True,
                    "search_path": "pg_catalog, public, pg_temp",
                }
                for name, _, returns, volatility, body in FUNCTION_SPECS
            },
            "triggers": TRIGGER_SPECS,
            "ddl_sha256": ddl_identity(),
            "native_baseline_status": PG17_DEPARSED_BASELINE_STATUS,
            "native_deparser_baseline": PG17_DEPARSED_BASELINE,
            "native_acceptance": False,
            "execution_authority": False,
        }
    )


def _oid(value):
    if type(value) is not int or not 1 <= value <= 2**32 - 1:
        deny("account_history_partition_oid_type")
    return value


def _rows(catalog, key, identity):
    rows = catalog[key]
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        deny("account_history_partition_catalog_shape")
    result = {}
    for row in rows:
        selected = tuple(row[name] for name in identity)
        if selected in result:
            deny("account_history_partition_catalog_duplicate")
        result[selected] = row
    return result


def deparser_sha256(catalog):
    # Dynamic OIDs are independently compared by actual identity, never sealed
    # as cross-cluster literals. Only genuine PG deparser output is sealable.
    constraints = [
        {
            key: value
            for key, value in row.items()
            if key not in {"table_oid", "target_oid"}
        }
        for row in catalog["constraints"]
    ]
    return digest(
        canonical({"constraints": constraints, "indexes": catalog["indexes"]})
    )


def verify_account_history_partition_catalog(catalog, *, require_native_baseline=True):
    """Strict structural diagnostics plus separately mandatory formal seal."""
    try:
        if (
            type(require_native_baseline) is not bool
            or type(catalog) is not dict
            or set(catalog)
            != {
                "schema_version",
                "server",
                "role",
                "parents",
                "tables",
                "columns",
                "constraints",
                "indexes",
                "functions",
                "triggers",
                "acl_rows",
                "column_acl_valid",
            }
            or catalog["schema_version"] != CATALOG_SCHEMA
        ):
            deny("account_history_partition_catalog_shape")
        server = catalog["server"]
        if (
            type(server["version_num"]) is not str
            or re.fullmatch(r"17[0-9]{4}", server["version_num"]) is None
        ):
            deny("account_history_partition_pg17_required")
        _oid(catalog["role"]["oid"])
        parents = _rows(catalog, "parents", ("name",))
        if set(parents) != {(name,) for name in PARENTS}:
            deny("account_history_partition_parent_drift")
        for row in parents.values():
            for key in ("oid", "namespace_oid", "owner_oid"):
                _oid(row[key])
            if row["namespace"] != "public" or row["kind"] != "r":
                deny("account_history_partition_parent_drift")
        tables = _rows(catalog, "tables", ("name",))
        if set(tables) != {(name,) for name in TABLES}:
            deny("account_history_partition_table_drift")
        owners = {_oid(row["owner_oid"]) for row in tables.values()}
        if len(owners) != 1 or {row["owner_oid"] for row in parents.values()} != owners:
            deny("account_history_partition_owner_drift")
        owner = next(iter(owners))
        public_oids = {_oid(row["namespace_oid"]) for row in parents.values()}
        for row in tables.values():
            _oid(row["oid"])
            public_oids.add(_oid(row["namespace_oid"]))
            if (
                row["namespace"] != "public"
                or row["owner_superuser"] is not True
                or row["kind"] != "r"
                or row["persistence"] != "p"
                or any(
                    row[key] is not False
                    for key in ("rls", "force_rls", "public_access")
                )
                or row["acl_valid"] is not True
            ):
                deny("account_history_partition_table_drift")
        if len(public_oids) != 1 or catalog["column_acl_valid"] is not True:
            deny("account_history_partition_acl_denied")
        columns = _rows(catalog, "columns", ("table", "name"))
        if set(columns) != {
            (table, name)
            for table, spec in TABLE_SPECS.items()
            for name, _, _ in spec["columns"]
        }:
            deny("account_history_partition_column_drift")
        constraints = _rows(catalog, "constraints", ("table", "name"))
        expected_constraints = set()
        expected_indexes = {}
        for table, spec in TABLE_SPECS.items():
            for position, (name, dtype, nullable) in enumerate(spec["columns"], 1):
                row = columns[table, name]
                if (
                    row["type"] != dtype
                    or row["nullable"] is not nullable
                    or row["position"] != position
                    or type(row["position"]) is not int
                    or row["generated"] != ""
                    or row["identity"] != ""
                    or row["default"]
                    != ("clock_timestamp()" if name == "recorded_at" else None)
                ):
                    deny("account_history_partition_column_drift")
            primary = "pk_" + table
            specifications = {
                primary: ("p", list(spec["primary_key"]), None, None, False)
            }
            specifications.update(
                {name: ("c", None, None, None, False) for name in spec["checks"]}
            )
            specifications.update(
                {
                    name: ("u", list(keys), None, None, False)
                    for name, keys in spec["unique"]
                }
            )
            specifications.update(
                {
                    name: ("f", list(keys), target, list(target_keys), False)
                    for name, keys, target, target_keys in spec["foreign_keys"]
                }
            )
            specifications.update(
                {
                    name: ("t", None, None, None, True)
                    for name, selected, _, _, _, deferred in TRIGGER_SPECS
                    if selected == table and deferred
                }
            )
            for name, (
                kind,
                keys,
                target,
                target_keys,
                deferred,
            ) in specifications.items():
                expected_constraints.add((table, name))
                row = constraints.get((table, name))
                if (
                    row is None
                    or row["kind"] != kind
                    or row["validated"] is not True
                    or row["deferrable"] is not deferred
                    or row["deferred"] is not deferred
                    or _oid(row["table_oid"]) != tables[table,]["oid"]
                ):
                    deny("account_history_partition_constraint_drift")
                if kind != "c" and row["columns"] != keys:
                    deny("account_history_partition_constraint_drift")
                if row["target"] != target or row["target_columns"] != target_keys:
                    deny("account_history_partition_foreign_key_drift")
                if kind == "f":
                    if (
                        row["delete_action"] != "r"
                        or _oid(row["target_oid"]) != parents[target,]["oid"]
                        or row["target_namespace"] != "public"
                    ):
                        deny("account_history_partition_foreign_key_drift")
                elif (
                    row["target_oid"] is not None or row["target_namespace"] is not None
                ):
                    deny("account_history_partition_foreign_key_drift")
                if type(row["definition"]) is not str or not row["definition"]:
                    deny("account_history_partition_constraint_drift")
                if kind == "t" and (
                    row["definition"] != "TRIGGER DEFERRABLE INITIALLY DEFERRED"
                    or row["delete_action"] != " "
                ):
                    deny("account_history_partition_constraint_drift")
            expected_indexes[table, primary] = (list(spec["primary_key"]), True)
            expected_indexes.update(
                {(table, name): (list(keys), False) for name, keys in spec["unique"]}
            )
        if set(constraints) != expected_constraints:
            deny("account_history_partition_constraint_drift")
        indexes = _rows(catalog, "indexes", ("table", "name"))
        if set(indexes) != set(expected_indexes):
            deny("account_history_partition_index_drift")
        for key, (keys, primary) in expected_indexes.items():
            row = indexes[key]
            if (
                row["columns"] != keys
                or row["unique"] is not True
                or row["primary"] is not primary
                or row["method"] != "btree"
                or row["valid"] is not True
                or row["ready"] is not True
                or row["predicate"] is not None
                or row["expressions"] is not None
                or type(row["definition"]) is not str
            ):
                deny("account_history_partition_index_drift")
        functions = _rows(catalog, "functions", ("name",))
        if set(functions) != {(name,) for name in FUNCTION_SQL}:
            deny("account_history_partition_function_drift")
        for name, _, returns, volatility, body in FUNCTION_SPECS:
            row = functions[name,]
            _oid(row["oid"])
            if (
                _oid(row["namespace_oid"]) not in public_oids
                or row["namespace"] != "public"
                or _oid(row["owner_oid"]) != owner
                or row["source"] != _body(body)
                or row["returns"] != returns
                or row["arguments"] != FUNCTION_SIGNATURES[name]
                or row["language"] != "plpgsql"
                or row["security_definer"] is not True
                or row["volatility"] != volatility.lower()[0]
                or row["config"] != ["search_path=pg_catalog, public, pg_temp"]
                or row["kind"] != "f"
                or row["parallel"] != "u"
                or row["leakproof"] is not False
                or row["strict"] is not False
                or row["public_execute"] is not False
                or row["acl_valid"] is not True
            ):
                deny("account_history_partition_function_drift")
        triggers = _rows(catalog, "triggers", ("table", "name"))
        if set(triggers) != {(table, name) for name, table, *_ in TRIGGER_SPECS}:
            deny("account_history_partition_trigger_drift")
        for name, table, events, level, function, deferred in TRIGGER_SPECS:
            row = triggers[table, name]
            kind = (
                (1 if level == "ROW" else 0)
                | (2 if events.startswith("BEFORE") else 0)
                | sum(
                    bit
                    for event, bit in (
                        ("INSERT", 4),
                        ("DELETE", 8),
                        ("UPDATE", 16),
                        ("TRUNCATE", 32),
                    )
                    if event in events
                )
            )
            target = functions[function,]
            if (
                row["function"] != function
                or _oid(row["function_oid"]) != target["oid"]
                or _oid(row["function_namespace_oid"]) != target["namespace_oid"]
                or row["function_namespace"] != "public"
                or row["enabled"] != "O"
                or type(row["type"]) is not int
                or row["type"] != kind
                or row["deferrable"] is not deferred
                or row["deferred"] is not deferred
                or row["constraint"] is not deferred
                or row["arguments"] != ""
                or row["when"] is not None
            ):
                deny("account_history_partition_trigger_identity_drift")
        grants = _rows(
            catalog, "acl_rows", ("kind", "object", "grantee_oid", "privilege")
        )
        for row in grants.values():
            if (
                _oid(row["owner_oid"]) != owner
                or type(row["grantee_oid"]) is not int
                or not 0 <= row["grantee_oid"] < 2**32
                or type(row["grantable"]) is not bool
            ):
                deny("account_history_partition_acl_denied")
            if row["grantee_oid"] != owner and (
                row["kind"] != "function"
                or row["object"]
                not in {
                    "account_history_partition_v1_append",
                    "account_history_partition_v1_read",
                }
                or row["grantee_oid"] == 0
                or row["privilege"] != "EXECUTE"
                or row["grantable"] is not False
            ):
                deny("account_history_partition_acl_denied")
        if require_native_baseline:
            if (
                PG17_DEPARSED_BASELINE is None
                or PG17_DEPARSED_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
            ):
                deny("account_history_partition_native_baseline_pending")
            if deparser_sha256(catalog) != PG17_DEPARSED_BASELINE:
                deny("account_history_partition_native_baseline_drift")
        return expected_account_history_partition_schema()
    except AccountHistoryPartitionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        deny("account_history_partition_catalog_shape")


def verify_runtime_role(catalog):
    """Mandatory actual session gate; no public flag may bypass it."""
    try:
        r, s = catalog["role"], catalog["server"]
        if (
            s["current_user"] != s["session_user"]
            or r["name"] != s["session_user"]
            or s["role_setting"] != "none"
            or r["can_login"] is not True
            or any(
                r[key] is not False
                for key in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "other_membership",
                    "can_create",
                )
            )
        ):
            deny("account_history_partition_role_denied")
        if (
            s["timezone"] != "UTC"
            or s["search_path"] != "pg_catalog, public, pg_temp"
            or s["isolation"] != "read committed"
            or s["replication_role"] != "origin"
            or s["in_recovery"] is not False
            or any(
                s[key] != "on"
                for key in ("synchronous_commit", "fsync", "full_page_writes")
            )
        ):
            deny("account_history_partition_role_denied")
        if any(
            row["owner_oid"] == r["oid"] or row["table_access"] is not False
            for row in catalog["tables"]
        ):
            deny("account_history_partition_role_denied")
        for row in catalog["functions"]:
            expected = row["name"] in {
                "account_history_partition_v1_append",
                "account_history_partition_v1_read",
            }
            if row["can_execute"] is not expected or row["owner_oid"] == r["oid"]:
                deny("account_history_partition_role_denied")
    except AccountHistoryPartitionError:
        raise
    except (KeyError, TypeError, AttributeError):
        deny("account_history_partition_role_denied")
