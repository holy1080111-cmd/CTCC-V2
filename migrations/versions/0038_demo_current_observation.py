"""Add an immutable V7 current diagnostic index, separate from B3 history.

Revision ID: 0038
Revises: 0037

No account-complete, flat-start or execution authority is issued. An existing
current observation prevents downgrade until it is separately preserved.
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

# Frozen policy identities: migration replay must not import mutable app code.
_POLICY_SHA256 = "c7ed2206012e125a10e06283c98a1edc5c9a0dbf1add662be15f0f18952fa631"
_SOURCE_POLICY_SHA256 = (
    "c2a01ced25dfd1fd12aebd784b28c7bdb334694ef2c19212e0c9d2ae3af24999"
)


def upgrade():
    op.execute("""
      CREATE TABLE demo_account_current_observations (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, sequence BIGINT NOT NULL,
        capture_id VARCHAR(32) NOT NULL, source_sequence BIGINT NOT NULL,
        previous_sha256 VARCHAR(64), event_sha256 VARCHAR(64) NOT NULL,
        document_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
        db_recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_demo_account_current_observations PRIMARY KEY(environment,account_id,settlement_currency,sequence),
        CONSTRAINT uq_account_current_observation_capture UNIQUE(capture_id),
        CONSTRAINT fk_account_current_observation_source FOREIGN KEY(capture_id,source_sequence) REFERENCES demo_account_capture_events(capture_id,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_current_observations_scope CHECK(environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'),
        CONSTRAINT ck_demo_account_current_observations_sequence_bound CHECK(sequence BETWEEN 1 AND 9223372036854775807 AND source_sequence BETWEEN 1 AND 8192),
        CONSTRAINT ck_demo_account_current_observations_chain CHECK(event_sha256 ~ '^[a-f0-9]{64}$' AND ((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 ~ '^[a-f0-9]{64}$'))),
        CONSTRAINT ck_demo_account_current_observations_byte_bound CHECK(octet_length(document_json) BETWEEN 1 AND 262144 AND octet_length(receipt_json) BETWEEN 1 AND 8388608)
      )
    """)
    op.execute(f"""
      CREATE FUNCTION account_current_observation_guard() RETURNS trigger AS $$
      DECLARE j JSONB; r JSONB; prior RECORD; src RECORD; first_event RECORD; lock_key BIGINT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        SELECT * INTO prior FROM demo_account_current_observations
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency
          ORDER BY sequence DESC LIMIT 1;
        SELECT * INTO src FROM demo_account_capture_events
          WHERE capture_id=NEW.capture_id AND sequence=NEW.source_sequence;
        SELECT * INTO first_event FROM demo_account_capture_events
          WHERE capture_id=NEW.capture_id AND sequence=1;
        -- Never accept a caller timestamp, including one supplied despite the
        -- column default. This time is a DB receipt, not a host-clock claim.
        NEW.db_recorded_at := clock_timestamp();
        j := NEW.document_json::jsonb; r := NEW.receipt_json::jsonb;
        IF src.sequence IS NULL
          OR first_event.sequence IS NULL
          OR ROW(src.environment,src.account_id,src.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR ROW(first_event.environment,first_event.account_id,first_event.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR first_event.event_json::jsonb->>'kind' IS DISTINCT FROM 'capture_start'
          OR first_event.event_json::jsonb->'data'->'plan'->>'contract_version' IS DISTINCT FROM 'ctcc.demo_current_account_plan.v7'
          OR src.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
          OR src.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
          OR NEW.db_recorded_at < src.db_recorded_at
          OR (prior.sequence IS NOT NULL AND NEW.db_recorded_at < prior.db_recorded_at)
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.demo_current_observation.v1'
          OR j->>'policy_sha256' IS DISTINCT FROM '{_POLICY_SHA256}'
          OR j->>'current_source_policy_sha256' IS DISTINCT FROM '{_SOURCE_POLICY_SHA256}'
          OR r->>'schema_version' IS DISTINCT FROM 'ctcc.current_account_source_observation.v6'
          OR r->>'policy_sha256' IS DISTINCT FROM '{_SOURCE_POLICY_SHA256}'
          OR r->>'effective_policy_sha256' IS DISTINCT FROM '{_SOURCE_POLICY_SHA256}'
          OR j->'source_reference' IS DISTINCT FROM r->'source_reference'
          OR j->'source_reference'->>'capture_id' IS DISTINCT FROM NEW.capture_id
          OR j->'source_reference'->>'head_sha256' IS DISTINCT FROM src.event_sha256
          OR j->'source_reference'->>'plan_sha256' IS DISTINCT FROM first_event.event_json::jsonb->'data'->>'plan_sha256'
          OR j->'source_reference'->>'session_binding_sha256' IS DISTINCT FROM first_event.event_json::jsonb->'data'->>'session_binding_sha256'
          OR j->>'scope_sha256' IS DISTINCT FROM r->>'scope_sha256'
          OR r->'identity'->>'environment' IS DISTINCT FROM NEW.environment
          OR r->'identity'->>'uid' IS DISTINCT FROM NEW.account_id
          OR r->'identity'->>'settlement_currency' IS DISTINCT FROM NEW.settlement_currency
          OR j->>'validated_at' IS DISTINCT FROM r->'observation_interval'->>'validated_at'
          OR r->'algo_type_coverage'->'all_eight_terminal_chains_replayed' IS DISTINCT FROM 'true'::jsonb
          OR jsonb_typeof(r->'algo_type_coverage'->'terminal_receipt_sha256s') IS DISTINCT FROM 'object'
          OR (SELECT count(*) FROM jsonb_object_keys(r->'algo_type_coverage'->'terminal_receipt_sha256s')) IS DISTINCT FROM 8::bigint
          OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR j->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR j->'flat_start_permission' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR r->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR r->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR r->'flat_start_permission' IS DISTINCT FROM 'false'::jsonb
          OR r->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR r->>'admission' IS DISTINCT FROM 'DENY'
          OR jsonb_typeof(j->'sequence') IS DISTINCT FROM 'number'
          OR (j->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
          OR j->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256
          OR j->>'receipt_sha256' IS DISTINCT FROM encode(sha256(convert_to(NEW.receipt_json,'UTF8')),'hex')
          OR encode(sha256(convert_to(NEW.document_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
          OR NEW.sequence <> COALESCE(prior.sequence,0)+1
          OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256 THEN
          RAISE EXCEPTION 'account_current_observation_append_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """)
    op.execute(
        "CREATE TRIGGER account_current_observation_guard BEFORE INSERT ON demo_account_current_observations FOR EACH ROW EXECUTE FUNCTION account_current_observation_guard()"
    )
    op.execute(
        "CREATE TRIGGER account_current_observation_immutable BEFORE UPDATE OR DELETE ON demo_account_current_observations FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable()"
    )
    op.execute(
        "CREATE TRIGGER account_current_observation_no_truncate BEFORE TRUNCATE ON demo_account_current_observations FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable()"
    )


def downgrade():
    op.execute(
        "LOCK TABLE demo_account_capture_events, demo_account_current_observations IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM demo_account_current_observations) THEN
          RAISE EXCEPTION 'account_current_observation_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.drop_table("demo_account_current_observations")
    op.execute("DROP FUNCTION account_current_observation_guard()")
