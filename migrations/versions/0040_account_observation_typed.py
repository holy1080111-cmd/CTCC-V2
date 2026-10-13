"""Separate immutable B3 typed source-time observations; DENY-only evidence.

Revision ID: 0040
Revises: 0039

The historical DB0021/0036/0037 chain and its V1/V2 receipt bytes are neither
rewritten nor extended. A typed batch must reference an original DB0020
terminal; only the application can replay its raw B1 chain and prove B2a
predecessor bytes. This schema guards durable bindings, not account authority.
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

# Fixed reviewed policy bytes, not an import of mutable application code.
_TYPED_POLICY_SHA256 = (
    "959fb552875d01b06fdd0545dc914a4fa36a880320c7c6ce8bc796e6ffdcdb21"
)
_PERSISTENCE_POLICY_SHA256 = (
    "d4e9a8e4fea2de6d4b534c7be4d3ab5e3713409dd22640f6adbca5e2e483cd8e"
)

_TABLES = (
    "demo_account_observation_typed_batches",
    "demo_account_observation_typed_facts",
    "demo_account_observation_typed_coverage",
    "demo_account_observation_typed_findings",
)


def _batch_guard() -> str:
    """Return historical SQL with hashes rendered without f-string regex risk."""
    return """
      CREATE FUNCTION public.account_observation_typed_batch_guard() RETURNS trigger AS $$
      DECLARE j JSONB; r JSONB; a JSONB; prior RECORD; src RECORD;
              lock_key BIGINT; prefix TEXT; maximum BIGINT; scope_hash TEXT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        SELECT * INTO prior FROM public.demo_account_observation_typed_batches
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency
          ORDER BY sequence DESC LIMIT 1;
        SELECT * INTO src FROM public.demo_account_capture_events
          WHERE capture_id=NEW.capture_id AND sequence=NEW.source_sequence
          FOR KEY SHARE;
        NEW.db_recorded_at := clock_timestamp();
        j := NEW.document_json::jsonb;
        r := NEW.receipt_json::jsonb;
        scope_hash := encode(sha256(convert_to('["'||NEW.environment||'","'||NEW.account_id||'","'||NEW.settlement_currency||'"]','UTF8')),'hex');
        IF jsonb_typeof(j) IS DISTINCT FROM 'object'
          OR jsonb_typeof(r) IS DISTINCT FROM 'object'
          OR src.sequence IS NULL
          OR ROW(src.environment,src.account_id,src.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR src.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
          OR src.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
          OR NEW.db_recorded_at < src.db_recorded_at
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.account_observation_typed_batch.v1'
          OR r->>'schema_version' IS DISTINCT FROM 'ctcc.observed_account_execution_window.v3'
          OR j->>'policy_sha256' IS DISTINCT FROM '__PERSISTENCE_POLICY_SHA__'
          OR j->>'persistence_policy_sha256' IS DISTINCT FROM '__PERSISTENCE_POLICY_SHA__'
          OR j->>'typed_policy_sha256' IS DISTINCT FROM '__TYPED_POLICY_SHA__'
          OR r->>'policy_sha256' IS DISTINCT FROM '__TYPED_POLICY_SHA__'
          OR j->>'scope_sha256' IS DISTINCT FROM scope_hash
          OR r->>'scope_sha256' IS DISTINCT FROM scope_hash
          OR j->'reference'->>'capture_id' IS DISTINCT FROM NEW.capture_id
          OR j->'reference'->>'head_sha256' IS DISTINCT FROM src.event_sha256
          OR COALESCE(j->'reference'->>'plan_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->'reference'->>'packet_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->'reference'->>'session_binding_sha256','') !~ '^[a-f0-9]{64}$'
          OR jsonb_typeof(j->'sequence') IS DISTINCT FROM 'number'
          OR (j->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
          OR j->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256
          OR j->>'receipt_sha256' IS DISTINCT FROM encode(sha256(convert_to(NEW.receipt_json,'UTF8')),'hex')
          OR encode(sha256(convert_to(NEW.document_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
          OR jsonb_typeof(j->'window') IS DISTINCT FROM 'object'
          OR j->'window' IS DISTINCT FROM r->'window'
          OR jsonb_typeof(j->'window'->'started_at') IS DISTINCT FROM 'string'
          OR jsonb_typeof(j->'window'->'ended_at') IS DISTINCT FROM 'string'
          OR (j->'window'->>'started_at')::timestamptz > (j->'window'->>'ended_at')::timestamptz
          OR (j->'window'->>'ended_at')::timestamptz - (j->'window'->>'started_at')::timestamptz > interval '7 days'
          OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR j->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR r->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR r->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR r->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR r->>'admission' IS DISTINCT FROM 'DENY'
          OR jsonb_typeof(j->'anchor_json') IS DISTINCT FROM 'string'
          OR jsonb_typeof((j->>'anchor_json')::jsonb) IS DISTINCT FROM 'object'
          OR (j->>'anchor_json')::jsonb IS DISTINCT FROM r->'continuation_anchor'
          OR j->>'anchor_sha256' IS DISTINCT FROM encode(sha256(convert_to(j->>'anchor_json','UTF8')),'hex')
          OR NEW.sequence <> COALESCE(prior.sequence,0)+1
          OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256 THEN
          RAISE EXCEPTION 'account_observation_typed_append_denied';
        END IF;
        a := r->'continuation_anchor';
        IF a->>'schema_version' IS DISTINCT FROM 'ctcc.account_observation_typed_continuation.v1'
          OR a->>'policy_sha256' IS DISTINCT FROM '__PERSISTENCE_POLICY_SHA__'
          OR a->>'typed_policy_sha256' IS DISTINCT FROM '__TYPED_POLICY_SHA__'
          OR a->>'scope_sha256' IS DISTINCT FROM scope_hash
          OR jsonb_typeof(a->'through_sequence') IS DISTINCT FROM 'number'
          OR (a->>'through_sequence')::bigint IS DISTINCT FROM NEW.sequence
          OR jsonb_typeof(a->'previous_sequence') IS DISTINCT FROM 'number'
          OR (a->>'previous_sequence')::bigint IS DISTINCT FROM NEW.sequence-1
          OR a->>'previous_event_sha256' IS DISTINCT FROM NEW.previous_sha256
          OR a->>'current_source_membership_sha256' IS DISTINCT FROM j->>'source_membership_sha256'
          OR a->>'current_coverage_membership_sha256' IS DISTINCT FROM j->>'coverage_membership_sha256'
          OR a->>'previous_anchor_sha256' IS DISTINCT FROM
             (CASE WHEN prior.sequence IS NULL THEN NULL
                   ELSE prior.document_json::jsonb->>'anchor_sha256' END)
          OR (prior.sequence IS NOT NULL AND
              (prior.document_json::jsonb->>'policy_sha256' IS DISTINCT FROM '__PERSISTENCE_POLICY_SHA__'
               OR prior.document_json::jsonb->>'typed_policy_sha256' IS DISTINCT FROM '__TYPED_POLICY_SHA__'))
          OR jsonb_typeof(a->'verified_witnesses') IS DISTINCT FROM 'array'
          OR jsonb_typeof(a->'required_proof_budget_exceeded') IS DISTINCT FROM 'boolean'
          OR jsonb_typeof(a->'source_facts_through_sequence') IS DISTINCT FROM 'number'
          OR jsonb_typeof(a->'coverage_through_sequence') IS DISTINCT FROM 'number'
          OR jsonb_typeof(a->'unresolved_findings_through_sequence') IS DISTINCT FROM 'number' THEN
          RAISE EXCEPTION 'account_observation_typed_anchor_denied';
        END IF;
        FOR prefix, maximum IN SELECT * FROM (VALUES
          ('source',16384::bigint),('coverage',128::bigint),('finding',16384::bigint)
        ) AS limits(prefix,maximum) LOOP
          IF jsonb_typeof(j->(prefix||'_membership_count')) IS DISTINCT FROM 'number'
            OR NOT COALESCE(j->>(prefix||'_membership_count') ~ '^(0|[1-9][0-9]*)$',false)
            OR (j->>(prefix||'_membership_count'))::bigint NOT BETWEEN 0 AND maximum
            OR NOT COALESCE(j->>(prefix||'_membership_sha256') ~ '^[a-f0-9]{64}$',false) THEN
            RAISE EXCEPTION 'account_observation_typed_membership_shape_denied';
          END IF;
        END LOOP;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """.replace("__PERSISTENCE_POLICY_SHA__", _PERSISTENCE_POLICY_SHA256).replace(
        "__TYPED_POLICY_SHA__", _TYPED_POLICY_SHA256
    )


def _member_guard() -> str:
    return """
      CREATE FUNCTION public.account_observation_typed_member_guard() RETURNS trigger AS $$
      DECLARE parent JSONB; j JSONB; prefix TEXT; capture TEXT;
      BEGIN
        SELECT document_json::jsonb,capture_id INTO parent,capture
          FROM public.demo_account_observation_typed_batches
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency AND sequence=NEW.batch_sequence
          FOR KEY SHARE;
        IF parent IS NULL THEN RAISE EXCEPTION 'account_observation_typed_member_parent_missing'; END IF;
        IF TG_TABLE_NAME='demo_account_observation_typed_facts' THEN
          prefix := 'source'; j := NEW.fact_json::jsonb;
          IF jsonb_typeof(j) IS DISTINCT FROM 'object'
            OR j ? 'generation_at'
            OR jsonb_typeof(j->'family') IS DISTINCT FROM 'string'
            OR NOT COALESCE(jsonb_typeof(j->'product') IN ('string','null'),false)
            OR ROW(j->>'family',j->>'product',j->>'identity_sha256',j->>'row_sha256')
               IS DISTINCT FROM ROW(NEW.family,NEW.product,NEW.identity_sha256,NEW.row_sha256)
            OR j->>'source_ts_semantics' IS DISTINCT FROM NEW.source_ts_semantics
            OR jsonb_typeof(j->'source_ts_at') IS DISTINCT FROM 'string'
            OR (j->>'source_ts_at')::timestamptz IS DISTINCT FROM NEW.source_ts_at
            OR NOT COALESCE(jsonb_typeof(j->'fill_at') IN ('string','null'),false)
            OR (j->>'fill_at')::timestamptz IS DISTINCT FROM NEW.fill_at
            OR (NEW.family='bills' AND NEW.fill_at IS NOT NULL)
            OR jsonb_typeof(j->'metadata_observations') IS DISTINCT FROM 'array'
            OR jsonb_array_length(j->'metadata_observations') IS DISTINCT FROM 1
            OR j->'metadata_observations'->0->>'capture_id' IS DISTINCT FROM capture
            OR j->'metadata_observations'->0->>'row_sha256' IS DISTINCT FROM NEW.metadata_sha256 THEN
            RAISE EXCEPTION 'account_observation_typed_fact_binding_denied';
          END IF;
        ELSIF TG_TABLE_NAME='demo_account_observation_typed_coverage' THEN
          prefix := 'coverage'; j := NEW.coverage_json::jsonb;
          IF j IS DISTINCT FROM jsonb_build_object(
                 'family',NEW.family,'product',NEW.product,'time_domain',NEW.time_domain,
                 'started_at',j->>'started_at','ended_at',j->>'ended_at')
            OR jsonb_typeof(j->'started_at') IS DISTINCT FROM 'string'
            OR jsonb_typeof(j->'ended_at') IS DISTINCT FROM 'string'
            OR (j->>'started_at')::timestamptz IS DISTINCT FROM NEW.started_at
            OR (j->>'ended_at')::timestamptz IS DISTINCT FROM NEW.ended_at THEN
            RAISE EXCEPTION 'account_observation_typed_coverage_binding_denied';
          END IF;
        ELSIF TG_TABLE_NAME='demo_account_observation_typed_findings' THEN
          prefix := 'finding'; j := NEW.finding_json::jsonb;
          IF jsonb_typeof(j) IS DISTINCT FROM 'object'
            OR jsonb_typeof(j->'kind') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'account_observation_typed_finding_binding_denied';
          END IF;
        ELSE RAISE EXCEPTION 'account_observation_typed_member_table_invalid';
        END IF;
        IF NEW.ordinal NOT BETWEEN 1 AND (parent->>(prefix||'_membership_count'))::bigint THEN
          RAISE EXCEPTION 'account_observation_typed_late_or_excess_member_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """


def _membership_guard() -> str:
    return """
      CREATE FUNCTION public.account_observation_typed_membership_guard() RETURNS trigger AS $$
      DECLARE j JSONB; a JSONB; prior_anchor JSONB;
              member RECORD; actual RECORD; expected BIGINT;
      BEGIN
        j := NEW.document_json::jsonb;
        a := NEW.receipt_json::jsonb->'continuation_anchor';
        IF NEW.sequence>1 THEN
          SELECT receipt_json::jsonb->'continuation_anchor' INTO prior_anchor
            FROM public.demo_account_observation_typed_batches
            WHERE environment=NEW.environment AND account_id=NEW.account_id
              AND settlement_currency=NEW.settlement_currency
              AND sequence=NEW.sequence-1;
          IF prior_anchor IS NULL THEN
            RAISE EXCEPTION 'account_observation_typed_predecessor_missing';
          END IF;
        END IF;
        FOR member IN SELECT * FROM (VALUES
          ('demo_account_observation_typed_facts','fact_json','source'),
          ('demo_account_observation_typed_coverage','coverage_json','coverage'),
          ('demo_account_observation_typed_findings','finding_json','finding')
        ) AS tables(table_name,json_column,prefix) LOOP
          EXECUTE format('SELECT count(*) AS n,min(ordinal) AS first,max(ordinal) AS last,encode(sha256(convert_to(''[''||COALESCE(string_agg(%I,'','' ORDER BY ordinal),'''')||'']'',''UTF8'')),''hex'') AS digest FROM public.%I WHERE environment=$1 AND account_id=$2 AND settlement_currency=$3 AND batch_sequence=$4',member.json_column,member.table_name)
            INTO actual USING NEW.environment,NEW.account_id,NEW.settlement_currency,NEW.sequence;
          expected := (j->>(member.prefix||'_membership_count'))::bigint;
          IF actual.n IS DISTINCT FROM expected
            OR (expected>0 AND (actual.first IS DISTINCT FROM 1::bigint OR actual.last IS DISTINCT FROM expected))
            OR actual.digest IS DISTINCT FROM j->>(member.prefix||'_membership_sha256') THEN
            RAISE EXCEPTION 'account_observation_typed_membership_incomplete';
          END IF;
        END LOOP;
        IF (a->>'source_facts_through_sequence')::bigint IS DISTINCT FROM
             COALESCE((prior_anchor->>'source_facts_through_sequence')::bigint,0)
             +(j->>'source_membership_count')::bigint
          OR (a->>'coverage_through_sequence')::bigint IS DISTINCT FROM
             COALESCE((prior_anchor->>'coverage_through_sequence')::bigint,0)
             +(j->>'coverage_membership_count')::bigint
          OR (a->>'unresolved_findings_through_sequence')::bigint IS DISTINCT FROM
             COALESCE((prior_anchor->>'unresolved_findings_through_sequence')::bigint,0)
             +(j->>'finding_membership_count')::bigint THEN
          RAISE EXCEPTION 'account_observation_typed_prefix_count_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """


def upgrade():
    op.execute("""
      CREATE TABLE public.demo_account_observation_typed_batches (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, sequence BIGINT NOT NULL,
        capture_id VARCHAR(32) NOT NULL, source_sequence BIGINT NOT NULL,
        previous_sha256 VARCHAR(64), event_sha256 VARCHAR(64) NOT NULL,
        document_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
        db_recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_demo_account_observation_typed_batches PRIMARY KEY(environment,account_id,settlement_currency,sequence),
        CONSTRAINT uq_account_observation_typed_capture UNIQUE(capture_id),
        CONSTRAINT fk_account_observation_typed_source FOREIGN KEY(capture_id,source_sequence) REFERENCES public.demo_account_capture_events(capture_id,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_typed_batches_scope CHECK(environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'),
        CONSTRAINT ck_demo_account_observation_typed_batches_sequence_bound CHECK(sequence BETWEEN 1 AND 9223372036854775807 AND source_sequence BETWEEN 1 AND 8192),
        CONSTRAINT ck_demo_account_observation_typed_batches_chain CHECK(event_sha256 ~ '^[a-f0-9]{64}$' AND ((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 ~ '^[a-f0-9]{64}$'))),
        CONSTRAINT ck_demo_account_observation_typed_batches_byte_bound CHECK(octet_length(document_json) BETWEEN 1 AND 262144 AND octet_length(receipt_json) BETWEEN 1 AND 8388608)
      )
    """)
    op.execute("""
      CREATE TABLE public.demo_account_observation_typed_facts (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, batch_sequence BIGINT NOT NULL,
        ordinal BIGINT NOT NULL, family VARCHAR(8) NOT NULL, product VARCHAR(16),
        identity_sha256 VARCHAR(64) NOT NULL, row_sha256 VARCHAR(64) NOT NULL,
        metadata_sha256 VARCHAR(64), source_ts_at TIMESTAMPTZ NOT NULL,
        source_ts_semantics VARCHAR(40) NOT NULL, fill_at TIMESTAMPTZ,
        fact_json TEXT NOT NULL,
        CONSTRAINT pk_demo_account_observation_typed_facts PRIMARY KEY(environment,account_id,settlement_currency,batch_sequence,ordinal),
        CONSTRAINT fk_observation_typed_fact_batch FOREIGN KEY(environment,account_id,settlement_currency,batch_sequence) REFERENCES public.demo_account_observation_typed_batches(environment,account_id,settlement_currency,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_typed_facts_bound CHECK(ordinal BETWEEN 1 AND 16384 AND octet_length(fact_json) BETWEEN 1 AND 262144),
        CONSTRAINT ck_demo_account_observation_typed_facts_digests CHECK(identity_sha256 ~ '^[a-f0-9]{64}$' AND row_sha256 ~ '^[a-f0-9]{64}$' AND (metadata_sha256 IS NULL OR metadata_sha256 ~ '^[a-f0-9]{64}$')),
        CONSTRAINT ck_demo_account_observation_typed_facts_source_time_domain CHECK((family='fills' AND source_ts_semantics='fill_record_generated_at') OR (family='bills' AND source_ts_semantics='balance_update_completed_at'))
      )
    """)
    op.execute(
        "CREATE INDEX ix_observation_typed_fact_identity ON public.demo_account_observation_typed_facts(environment,account_id,settlement_currency,identity_sha256)"
    )
    op.execute(
        "CREATE INDEX ix_observation_typed_fact_source_ts ON public.demo_account_observation_typed_facts(environment,account_id,settlement_currency,family,product,source_ts_at)"
    )
    op.execute("""
      CREATE TABLE public.demo_account_observation_typed_coverage (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, batch_sequence BIGINT NOT NULL,
        ordinal BIGINT NOT NULL, family VARCHAR(8) NOT NULL, product VARCHAR(16) NOT NULL,
        time_domain VARCHAR(40) NOT NULL,
        started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ NOT NULL,
        coverage_json TEXT NOT NULL,
        CONSTRAINT pk_demo_account_observation_typed_coverage PRIMARY KEY(environment,account_id,settlement_currency,batch_sequence,ordinal),
        CONSTRAINT fk_observation_typed_coverage_batch FOREIGN KEY(environment,account_id,settlement_currency,batch_sequence) REFERENCES public.demo_account_observation_typed_batches(environment,account_id,settlement_currency,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_typed_coverage_bound CHECK(ordinal BETWEEN 1 AND 128 AND started_at <= ended_at AND octet_length(coverage_json) BETWEEN 1 AND 262144),
        CONSTRAINT ck_demo_account_observation_typed_coverage_source_time_domain CHECK((family='fills' AND time_domain='fill_record_generated_at') OR (family='bills' AND time_domain='balance_update_completed_at'))
      )
    """)
    op.execute(
        "CREATE INDEX ix_observation_typed_coverage_domain ON public.demo_account_observation_typed_coverage(environment,account_id,settlement_currency,family,product,time_domain,ended_at)"
    )
    op.execute("""
      CREATE TABLE public.demo_account_observation_typed_findings (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, batch_sequence BIGINT NOT NULL,
        ordinal BIGINT NOT NULL, finding_json TEXT NOT NULL,
        CONSTRAINT pk_demo_account_observation_typed_findings PRIMARY KEY(environment,account_id,settlement_currency,batch_sequence,ordinal),
        CONSTRAINT fk_observation_typed_finding_batch FOREIGN KEY(environment,account_id,settlement_currency,batch_sequence) REFERENCES public.demo_account_observation_typed_batches(environment,account_id,settlement_currency,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_typed_findings_bound CHECK(ordinal BETWEEN 1 AND 16384 AND octet_length(finding_json) BETWEEN 1 AND 262144)
      )
    """)
    op.execute(_batch_guard())
    op.execute(_member_guard())
    op.execute(_membership_guard())
    for name in (
        "account_observation_typed_batch_guard",
        "account_observation_typed_member_guard",
        "account_observation_typed_membership_guard",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER account_observation_typed_batch_guard BEFORE INSERT ON public.demo_account_observation_typed_batches FOR EACH ROW EXECUTE FUNCTION public.account_observation_typed_batch_guard()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER account_observation_typed_membership_guard AFTER INSERT ON public.demo_account_observation_typed_batches DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.account_observation_typed_membership_guard()"
    )
    for table in _TABLES:
        if table != "demo_account_observation_typed_batches":
            op.execute(
                f"CREATE TRIGGER account_observation_typed_member_guard BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.account_observation_typed_member_guard()"
            )
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.qualification_ledger_immutable()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.qualification_ledger_immutable()"
        )


def downgrade():
    # The source lock excludes concurrent FK changes while checking emptiness.
    op.execute(
        "LOCK TABLE public.demo_account_capture_events, "
        + ", ".join("public." + table for table in _TABLES)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_account_observation_typed_batches)
          OR EXISTS(SELECT 1 FROM public.demo_account_observation_typed_facts)
          OR EXISTS(SELECT 1 FROM public.demo_account_observation_typed_coverage)
          OR EXISTS(SELECT 1 FROM public.demo_account_observation_typed_findings) THEN
          RAISE EXCEPTION 'account_observation_typed_downgrade_requires_empty';
        END IF;
      END $$;
    """)
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE public.{table}")
    op.execute("DROP FUNCTION public.account_observation_typed_membership_guard()")
    op.execute("DROP FUNCTION public.account_observation_typed_member_guard()")
    op.execute("DROP FUNCTION public.account_observation_typed_batch_guard()")
