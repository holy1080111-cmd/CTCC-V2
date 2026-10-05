"""Immutable observed execution-window history; no risk/submit authority.

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE demo_account_observation_batches (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, sequence BIGINT NOT NULL,
        capture_id VARCHAR(32) NOT NULL, source_sequence BIGINT NOT NULL,
        previous_sha256 VARCHAR(64), event_sha256 VARCHAR(64) NOT NULL,
        document_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
        db_recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_demo_account_observation_batches PRIMARY KEY(environment,account_id,settlement_currency,sequence),
        CONSTRAINT uq_account_observation_capture UNIQUE(capture_id),
        CONSTRAINT fk_account_observation_source FOREIGN KEY(capture_id,source_sequence) REFERENCES demo_account_capture_events(capture_id,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_batches_scope CHECK(environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'),
        CONSTRAINT ck_demo_account_observation_batches_sequence_bound CHECK(sequence BETWEEN 1 AND 9223372036854775807 AND source_sequence BETWEEN 1 AND 8192),
        CONSTRAINT ck_demo_account_observation_batches_chain CHECK(event_sha256 ~ '^[a-f0-9]{64}$' AND ((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 ~ '^[a-f0-9]{64}$'))),
        CONSTRAINT ck_demo_account_observation_batches_byte_bound CHECK(octet_length(document_json) BETWEEN 1 AND 262144 AND octet_length(receipt_json) BETWEEN 1 AND 8388608)
      )
    """)
    op.execute("""
      CREATE FUNCTION account_observation_batch_guard() RETURNS trigger AS $$
      DECLARE j JSONB; r JSONB; prior RECORD; src RECORD; lock_key BIGINT; prefix TEXT; maximum BIGINT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        SELECT * INTO prior FROM demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency ORDER BY sequence DESC LIMIT 1;
        SELECT * INTO src FROM demo_account_capture_events WHERE capture_id=NEW.capture_id AND sequence=NEW.source_sequence;
        j := NEW.document_json::jsonb; r := NEW.receipt_json::jsonb;
        IF src.sequence IS NULL
          OR ROW(src.environment,src.account_id,src.settlement_currency) IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR src.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
          OR src.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.account_observation_batch.v1'
          OR j->'reference'->>'capture_id' IS DISTINCT FROM NEW.capture_id
          OR j->'reference'->>'head_sha256' IS DISTINCT FROM src.event_sha256
          OR jsonb_typeof(j->'sequence') IS DISTINCT FROM 'number'
          OR (j->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
          OR j->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256
          OR j->>'receipt_sha256' IS DISTINCT FROM encode(sha256(convert_to(NEW.receipt_json,'UTF8')),'hex')
          OR encode(sha256(convert_to(NEW.document_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
          OR r->>'schema_version' IS DISTINCT FROM 'ctcc.observed_account_execution_window.v1'
          OR j->>'policy_sha256' IS DISTINCT FROM r->>'policy_sha256'
          OR j->'window' IS DISTINCT FROM r->'window'
          OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
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
            RAISE EXCEPTION 'account_observation_append_denied';
        END IF;
        FOR prefix, maximum IN SELECT * FROM (VALUES ('source',16384::bigint),('coverage',128::bigint),('finding',16384::bigint)) AS limits(prefix,maximum) LOOP
          IF jsonb_typeof(j->(prefix||'_membership_count')) IS DISTINCT FROM 'number'
            OR NOT COALESCE(j->>(prefix||'_membership_count') ~ '^(0|[1-9][0-9]*)$',false)
            OR (j->>(prefix||'_membership_count'))::bigint NOT BETWEEN 0 AND maximum
            OR NOT COALESCE(j->>(prefix||'_membership_sha256') ~ '^[a-f0-9]{64}$',false) THEN
            RAISE EXCEPTION 'account_observation_membership_shape_denied';
          END IF;
        END LOOP;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """)
    op.execute(
        "CREATE TRIGGER account_observation_batch_guard BEFORE INSERT ON demo_account_observation_batches FOR EACH ROW EXECUTE FUNCTION account_observation_batch_guard()"
    )
    op.execute(
        "CREATE TRIGGER account_observation_batch_immutable BEFORE UPDATE OR DELETE ON demo_account_observation_batches FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable()"
    )
    op.execute(
        "CREATE TRIGGER account_observation_batch_no_truncate BEFORE TRUNCATE ON demo_account_observation_batches FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable()"
    )
    op.execute("""
      CREATE TABLE demo_account_observation_facts (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, batch_sequence BIGINT NOT NULL,
        ordinal BIGINT NOT NULL, family VARCHAR(8) NOT NULL, product VARCHAR(16),
        identity_sha256 VARCHAR(64) NOT NULL, row_sha256 VARCHAR(64) NOT NULL,
        metadata_sha256 VARCHAR(64), generation_at TIMESTAMP WITH TIME ZONE NOT NULL,
        fill_at TIMESTAMP WITH TIME ZONE, fact_json TEXT NOT NULL,
        CONSTRAINT pk_demo_account_observation_facts PRIMARY KEY(environment,account_id,settlement_currency,batch_sequence,ordinal),
        CONSTRAINT fk_observation_fact_batch FOREIGN KEY(environment,account_id,settlement_currency,batch_sequence) REFERENCES demo_account_observation_batches(environment,account_id,settlement_currency,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_facts_bound CHECK(ordinal BETWEEN 1 AND 16384 AND octet_length(fact_json) BETWEEN 1 AND 262144),
        CONSTRAINT ck_demo_account_observation_facts_digests CHECK(identity_sha256 ~ '^[a-f0-9]{64}$' AND row_sha256 ~ '^[a-f0-9]{64}$' AND (metadata_sha256 IS NULL OR metadata_sha256 ~ '^[a-f0-9]{64}$'))
      )
    """)
    op.execute(
        "CREATE INDEX ix_observation_fact_identity ON demo_account_observation_facts(environment,account_id,settlement_currency,identity_sha256)"
    )
    op.execute(
        "CREATE INDEX ix_observation_fact_generation ON demo_account_observation_facts(environment,account_id,settlement_currency,generation_at)"
    )
    op.execute("""
      CREATE TABLE demo_account_observation_coverage (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, batch_sequence BIGINT NOT NULL,
        ordinal BIGINT NOT NULL, family VARCHAR(8) NOT NULL, product VARCHAR(16) NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL, ended_at TIMESTAMP WITH TIME ZONE NOT NULL,
        coverage_json TEXT NOT NULL,
        CONSTRAINT pk_demo_account_observation_coverage PRIMARY KEY(environment,account_id,settlement_currency,batch_sequence,ordinal),
        CONSTRAINT fk_observation_coverage_batch FOREIGN KEY(environment,account_id,settlement_currency,batch_sequence) REFERENCES demo_account_observation_batches(environment,account_id,settlement_currency,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_coverage_bound CHECK(ordinal BETWEEN 1 AND 128 AND started_at <= ended_at AND octet_length(coverage_json) BETWEEN 1 AND 262144)
      )
    """)
    op.execute(
        "CREATE INDEX ix_observation_coverage_domain ON demo_account_observation_coverage(environment,account_id,settlement_currency,family,product,ended_at)"
    )
    op.execute("""
      CREATE TABLE demo_account_observation_findings (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, batch_sequence BIGINT NOT NULL,
        ordinal BIGINT NOT NULL, finding_json TEXT NOT NULL,
        CONSTRAINT pk_demo_account_observation_findings PRIMARY KEY(environment,account_id,settlement_currency,batch_sequence,ordinal),
        CONSTRAINT fk_observation_finding_batch FOREIGN KEY(environment,account_id,settlement_currency,batch_sequence) REFERENCES demo_account_observation_batches(environment,account_id,settlement_currency,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_observation_findings_bound CHECK(ordinal BETWEEN 1 AND 16384 AND octet_length(finding_json) BETWEEN 1 AND 262144)
      )
    """)
    op.execute("""
      CREATE FUNCTION account_observation_member_guard() RETURNS trigger AS $$
      DECLARE parent JSONB; j JSONB; prefix TEXT; capture TEXT;
      BEGIN
        SELECT document_json::jsonb, capture_id INTO parent,capture FROM demo_account_observation_batches
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency AND sequence=NEW.batch_sequence;
        IF parent IS NULL THEN RAISE EXCEPTION 'account_observation_member_parent_missing'; END IF;
        IF TG_TABLE_NAME='demo_account_observation_facts' THEN
          prefix := 'source'; j := NEW.fact_json::jsonb;
          IF jsonb_typeof(j) IS DISTINCT FROM 'object'
            OR jsonb_typeof(j->'family') IS DISTINCT FROM 'string'
            OR NOT COALESCE(jsonb_typeof(j->'product') IN ('string','null'),false)
            OR ROW(j->>'family',j->>'product',j->>'identity_sha256',j->>'row_sha256') IS DISTINCT FROM ROW(NEW.family,NEW.product,NEW.identity_sha256,NEW.row_sha256)
            OR jsonb_typeof(j->'generation_at') IS DISTINCT FROM 'string'
            OR (j->>'generation_at')::timestamptz IS DISTINCT FROM NEW.generation_at
            OR NOT COALESCE(jsonb_typeof(j->'fill_at') IN ('string','null'),false)
            OR (j->>'fill_at')::timestamptz IS DISTINCT FROM NEW.fill_at
            OR jsonb_typeof(j->'metadata_observations') IS DISTINCT FROM 'array'
            OR jsonb_array_length(j->'metadata_observations') IS DISTINCT FROM 1
            OR j->'metadata_observations'->0->>'capture_id' IS DISTINCT FROM capture
            OR j->'metadata_observations'->0->>'row_sha256' IS DISTINCT FROM NEW.metadata_sha256 THEN
            RAISE EXCEPTION 'account_observation_fact_binding_denied';
          END IF;
        ELSIF TG_TABLE_NAME='demo_account_observation_coverage' THEN
          prefix := 'coverage'; j := NEW.coverage_json::jsonb;
          IF j IS DISTINCT FROM jsonb_build_object('family',NEW.family,'product',NEW.product,
               'started_at',j->>'started_at','ended_at',j->>'ended_at')
            OR jsonb_typeof(j->'started_at') IS DISTINCT FROM 'string'
            OR jsonb_typeof(j->'ended_at') IS DISTINCT FROM 'string'
            OR (j->>'started_at')::timestamptz IS DISTINCT FROM NEW.started_at
            OR (j->>'ended_at')::timestamptz IS DISTINCT FROM NEW.ended_at THEN
            RAISE EXCEPTION 'account_observation_coverage_binding_denied';
          END IF;
        ELSIF TG_TABLE_NAME='demo_account_observation_findings' THEN
          prefix := 'finding'; j := NEW.finding_json::jsonb;
          IF jsonb_typeof(j) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'account_observation_finding_binding_denied';
          END IF;
        ELSE RAISE EXCEPTION 'account_observation_member_table_invalid';
        END IF;
        -- The immutable parent commits a complete contiguous ordinal set. Once
        -- committed, extra rows can only hit its PK or exceed this fixed bound.
        IF NEW.ordinal NOT BETWEEN 1 AND (parent->>(prefix||'_membership_count'))::bigint THEN
          RAISE EXCEPTION 'account_observation_late_or_excess_member_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
      CREATE FUNCTION account_observation_membership_guard() RETURNS trigger AS $$
      DECLARE j JSONB; member RECORD; actual RECORD; expected BIGINT;
      BEGIN
        j := NEW.document_json::jsonb;
        FOR member IN SELECT * FROM (VALUES
          ('demo_account_observation_facts','fact_json','source'),
          ('demo_account_observation_coverage','coverage_json','coverage'),
          ('demo_account_observation_findings','finding_json','finding')
        ) AS tables(table_name,json_column,prefix) LOOP
          EXECUTE format('SELECT count(*) AS n,min(ordinal) AS first,max(ordinal) AS last,encode(sha256(convert_to(''[''||COALESCE(string_agg(%I,'','' ORDER BY ordinal),'''')||'']'',''UTF8'')),''hex'') AS digest FROM %I WHERE environment=$1 AND account_id=$2 AND settlement_currency=$3 AND batch_sequence=$4',member.json_column,member.table_name)
            INTO actual USING NEW.environment,NEW.account_id,NEW.settlement_currency,NEW.sequence;
          expected := (j->>(member.prefix||'_membership_count'))::bigint;
          IF actual.n IS DISTINCT FROM expected
            OR (expected>0 AND (actual.first IS DISTINCT FROM 1::bigint OR actual.last IS DISTINCT FROM expected))
            OR actual.digest IS DISTINCT FROM j->>(member.prefix||'_membership_sha256') THEN
            RAISE EXCEPTION 'account_observation_membership_incomplete';
          END IF;
        END LOOP;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER account_observation_membership_guard AFTER INSERT ON demo_account_observation_batches DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION account_observation_membership_guard()"
    )
    for suffix in ("facts", "coverage", "findings"):
        table = "demo_account_observation_" + suffix
        op.execute(
            f"CREATE TRIGGER observation_member_guard BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION account_observation_member_guard()"
        )
        op.execute(
            f"CREATE TRIGGER observation_member_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable()"
        )
        op.execute(
            f"CREATE TRIGGER observation_member_no_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable()"
        )


def downgrade():
    op.execute(
        "LOCK TABLE demo_account_capture_events, demo_account_observation_batches, demo_account_observation_facts, demo_account_observation_coverage, demo_account_observation_findings IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT 1 FROM demo_account_observation_batches) OR EXISTS(SELECT 1 FROM demo_account_observation_facts) OR EXISTS(SELECT 1 FROM demo_account_observation_coverage) OR EXISTS(SELECT 1 FROM demo_account_observation_findings) THEN RAISE EXCEPTION 'account_observation_downgrade_requires_empty'; END IF; END $$"""
    )
    op.drop_table("demo_account_observation_findings")
    op.drop_table("demo_account_observation_coverage")
    op.drop_table("demo_account_observation_facts")
    op.drop_table("demo_account_observation_batches")
    op.execute("DROP FUNCTION account_observation_membership_guard()")
    op.execute("DROP FUNCTION account_observation_member_guard()")
    op.execute("DROP FUNCTION account_observation_batch_guard()")
