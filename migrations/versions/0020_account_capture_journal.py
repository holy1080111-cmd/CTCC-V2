"""Private immutable account ingestion observations; no account revision issuer.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE demo_account_capture_events (
        capture_id VARCHAR(32) NOT NULL, sequence BIGINT NOT NULL,
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, previous_sha256 VARCHAR(64),
        event_sha256 VARCHAR(64) NOT NULL, event_json TEXT NOT NULL, chain_bytes BIGINT NOT NULL,
        raw_body BYTEA, packet_payload BYTEA,
        db_recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_demo_account_capture_events PRIMARY KEY (capture_id,sequence),
        CONSTRAINT fk_account_capture_scope FOREIGN KEY(environment,account_id,settlement_currency) REFERENCES qualification_account_scopes(environment,account_id,settlement_currency) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_capture_events_chain_byte_bound CHECK(chain_bytes BETWEEN 1 AND 67108864),
        CONSTRAINT ck_demo_account_capture_events_scope CHECK(environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'),
        CONSTRAINT ck_demo_account_capture_events_identity CHECK(capture_id ~ '^[a-f0-9]{32}$' AND sequence BETWEEN 1 AND 8192),
        CONSTRAINT ck_demo_account_capture_events_chain CHECK(event_sha256 ~ '^[a-f0-9]{64}$' AND ((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 ~ '^[a-f0-9]{64}$'))),
        CONSTRAINT ck_demo_account_capture_events_event_bound CHECK(octet_length(event_json) BETWEEN 1 AND 262144),
        CONSTRAINT ck_demo_account_capture_events_raw_bound CHECK(raw_body IS NULL OR octet_length(raw_body) BETWEEN 1 AND 262144),
        CONSTRAINT ck_demo_account_capture_events_packet_bound CHECK(packet_payload IS NULL OR octet_length(packet_payload) BETWEEN 1 AND 16777216)
      )
    """)
    op.execute("""
      CREATE FUNCTION account_capture_event_guard() RETURNS trigger AS $$
      DECLARE j JSONB; prior RECORD; lock_key BIGINT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        j := NEW.event_json::jsonb;
        SELECT * INTO prior FROM demo_account_capture_events WHERE capture_id=NEW.capture_id ORDER BY sequence DESC LIMIT 1;
        IF j->>'version' IS DISTINCT FROM 'ctcc.demo_account_ingestion_event.v1'
          OR j->>'capture_id' IS DISTINCT FROM NEW.capture_id
          OR (j->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
          OR j->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256
          OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR jsonb_typeof(j->'data') IS DISTINCT FROM 'object'
          OR NOT COALESCE(j->>'kind' IN ('capture_start','request_start','headers_received','body_progress','body_complete','page_validated','raw_finalized','acquisition_closed','packet_recorded','terminal','recovery'),false)
          OR NOT COALESCE(j->>'outcome' IN ('in_progress','complete_recorded','failed','cancelled','interrupted_process_loss','interrupted_owner_unknown'),false)
          OR ((j->>'kind' IN ('terminal','recovery')) IS DISTINCT FROM (j->>'outcome'<>'in_progress'))
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR encode(sha256(convert_to(NEW.event_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
          OR j->>'raw_sha256' IS DISTINCT FROM encode(sha256(NEW.raw_body),'hex')
          OR j->>'packet_sha256' IS DISTINCT FROM encode(sha256(NEW.packet_payload),'hex')
          OR NEW.chain_bytes IS DISTINCT FROM COALESCE(prior.chain_bytes,0)+octet_length(NEW.event_json)+COALESCE(octet_length(NEW.raw_body),0)+COALESCE(octet_length(NEW.packet_payload),0)
          OR NEW.sequence <> COALESCE(prior.sequence,0)+1
          OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256 THEN
            RAISE EXCEPTION 'account_capture_event_binding_denied';
        END IF;
        IF prior.sequence IS NULL THEN
          IF j->>'kind' IS DISTINCT FROM 'capture_start' OR j->>'outcome' IS DISTINCT FROM 'in_progress'
             OR ROW(j->'data'->>'environment',j->'data'->>'account_id',j->'data'->>'settlement_currency') IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency) THEN
            RAISE EXCEPTION 'account_capture_start_denied';
          END IF;
        ELSIF prior.event_json::jsonb->>'outcome' IS DISTINCT FROM 'in_progress'
          OR ROW(prior.environment,prior.account_id,prior.settlement_currency) IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency) THEN
            RAISE EXCEPTION 'account_capture_append_denied';
        END IF;
        IF NEW.raw_body IS NOT NULL AND (j->>'kind' IS DISTINCT FROM 'raw_finalized' OR j->'data'->>'raw_retention' IS DISTINCT FROM 'durable_secret_checked') THEN
          RAISE EXCEPTION 'account_capture_raw_binding_denied';
        END IF;
        IF NEW.packet_payload IS NOT NULL AND j->>'kind' IS DISTINCT FROM 'packet_recorded' THEN
          RAISE EXCEPTION 'account_capture_packet_binding_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """)
    op.execute(
        "CREATE TRIGGER account_capture_event_guard BEFORE INSERT ON demo_account_capture_events FOR EACH ROW EXECUTE FUNCTION account_capture_event_guard()"
    )
    op.execute(
        "CREATE TRIGGER account_capture_event_immutable BEFORE UPDATE OR DELETE ON demo_account_capture_events FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable()"
    )
    op.execute(
        "CREATE TRIGGER account_capture_event_no_truncate BEFORE TRUNCATE ON demo_account_capture_events FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable()"
    )


def downgrade():
    # DROP removes FK triggers on the parent; acquire both locks without waiting.
    op.execute(
        "LOCK TABLE qualification_account_scopes, demo_account_capture_events IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT 1 FROM demo_account_capture_events) THEN RAISE EXCEPTION 'account_capture_downgrade_requires_empty'; END IF; END $$"""
    )
    op.drop_table("demo_account_capture_events")
    op.execute("DROP FUNCTION account_capture_event_guard()")
