"""Durable, DENY-only Demo event-history epoch observations.

Revision ID: 0041
Revises: 0040

An anchor here records a local claim that a source/credential/policy combination
was observed. It never proves an exchange-history beginning, absence of legacy
consumption, account completeness, or permission to reserve/submit an order.
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.demo_event_history_epoch_events (
        epoch_id VARCHAR(64) NOT NULL,
        sequence BIGINT NOT NULL,
        environment VARCHAR(8) NOT NULL,
        account_id VARCHAR(32) NOT NULL,
        credential_session_binding_sha256 VARCHAR(64) NOT NULL,
        claimed_source_sha256 VARCHAR(64) NOT NULL,
        policy_sha256 VARCHAR(64) NOT NULL,
        kind VARCHAR(24) NOT NULL,
        reason_code VARCHAR(64) NOT NULL,
        evidence_sha256 VARCHAR(64) NOT NULL,
        previous_sha256 VARCHAR(64),
        event_sha256 VARCHAR(64) NOT NULL,
        event_json TEXT NOT NULL,
        db_recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_demo_event_history_epoch_events PRIMARY KEY(epoch_id,sequence),
        CONSTRAINT uq_demo_event_history_epoch_uid_binding_sequence UNIQUE(
          environment,account_id,credential_session_binding_sha256,
          claimed_source_sha256,policy_sha256,sequence),
        CONSTRAINT ck_demo_event_history_epoch_events_scope CHECK(
          environment='demo' AND account_id ~ '^[0-9]{1,32}$'),
        CONSTRAINT ck_demo_event_history_epoch_events_hashes CHECK(
          epoch_id ~ '^[a-f0-9]{64}$' AND
          credential_session_binding_sha256 ~ '^[a-f0-9]{64}$' AND
          claimed_source_sha256 ~ '^[a-f0-9]{64}$' AND
          policy_sha256 ~ '^[a-f0-9]{64}$' AND
          evidence_sha256 ~ '^[a-f0-9]{64}$' AND
          event_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_demo_event_history_epoch_events_chain CHECK(
          (sequence=1 AND kind='anchor_unverified' AND
           reason_code='source_history_unverified' AND previous_sha256 IS NULL) OR
          (sequence BETWEEN 2 AND 1024 AND kind='invalidated' AND
           reason_code ~ '^[a-z][a-z0-9_]{1,63}$' AND
           previous_sha256 ~ '^[a-f0-9]{64}$')),
        CONSTRAINT ck_demo_event_history_epoch_events_bounded CHECK(
          octet_length(event_json) BETWEEN 1 AND 4096)
      )
    """)
    op.execute("""
      CREATE INDEX ix_demo_event_history_epoch_uid
      ON public.demo_event_history_epoch_events(environment,account_id,epoch_id,sequence)
    """)
    op.execute("""
      CREATE FUNCTION public.demo_event_history_epoch_insert_guard() RETURNS trigger AS $$
      DECLARE j JSONB; prior RECORD; lock_key BIGINT; expected_id TEXT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(
          convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||
          convert_to(NEW.environment,'UTF8')||decode('00','hex')||
          convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        SELECT sequence,event_sha256,db_recorded_at INTO prior
          FROM public.demo_event_history_epoch_events WHERE epoch_id=NEW.epoch_id
          ORDER BY sequence DESC LIMIT 1;
        NEW.db_recorded_at := clock_timestamp();
        expected_id := encode(sha256(
          convert_to('ctcc-demo-event-history-epoch-v1','UTF8')||decode('00','hex')||
          convert_to(NEW.environment,'UTF8')||decode('00','hex')||
          convert_to(NEW.account_id,'UTF8')||decode('00','hex')||
          convert_to(NEW.credential_session_binding_sha256,'UTF8')||decode('00','hex')||
          convert_to(NEW.claimed_source_sha256,'UTF8')||decode('00','hex')||
          convert_to(NEW.policy_sha256,'UTF8')),'hex');
        j := NEW.event_json::jsonb;
        IF NEW.epoch_id IS DISTINCT FROM expected_id
          OR NEW.sequence IS DISTINCT FROM COALESCE(prior.sequence,0)+1
          OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256
          OR (prior.sequence IS NOT NULL AND NEW.db_recorded_at < prior.db_recorded_at)
          OR jsonb_typeof(j) IS DISTINCT FROM 'object'
          OR (SELECT count(*) FROM jsonb_object_keys(j)) IS DISTINCT FROM 16::bigint
          OR encode(sha256(convert_to(NEW.event_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.demo_event_history_epoch_event.v1'
          OR j->>'epoch_id' IS DISTINCT FROM NEW.epoch_id
          OR jsonb_typeof(j->'sequence') IS DISTINCT FROM 'number'
          OR (j->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
          OR j->>'environment' IS DISTINCT FROM NEW.environment
          OR j->>'account_id' IS DISTINCT FROM NEW.account_id
          OR j->>'credential_session_binding_sha256' IS DISTINCT FROM NEW.credential_session_binding_sha256
          OR j->>'claimed_source_sha256' IS DISTINCT FROM NEW.claimed_source_sha256
          OR j->>'policy_sha256' IS DISTINCT FROM NEW.policy_sha256
          OR j->>'kind' IS DISTINCT FROM NEW.kind
          OR j->>'reason_code' IS DISTINCT FROM NEW.reason_code
          OR j->>'evidence_sha256' IS DISTINCT FROM NEW.evidence_sha256
          OR j->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR j->'history_complete' IS DISTINCT FROM 'false'::jsonb
          OR j->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb THEN
          RAISE EXCEPTION 'demo_event_history_epoch_append_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.demo_event_history_epoch_insert_guard() FROM PUBLIC"
    )
    op.execute("""
      CREATE TRIGGER demo_event_history_epoch_insert_guard
      BEFORE INSERT ON public.demo_event_history_epoch_events FOR EACH ROW
      EXECUTE FUNCTION public.demo_event_history_epoch_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER demo_event_history_epoch_immutable
      BEFORE UPDATE OR DELETE ON public.demo_event_history_epoch_events FOR EACH ROW
      EXECUTE FUNCTION public.qualification_ledger_immutable()
    """)
    op.execute("""
      CREATE TRIGGER demo_event_history_epoch_no_truncate
      BEFORE TRUNCATE ON public.demo_event_history_epoch_events FOR EACH STATEMENT
      EXECUTE FUNCTION public.qualification_ledger_immutable()
    """)


def downgrade():
    op.execute(
        "LOCK TABLE public.demo_event_history_epoch_events IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_event_history_epoch_events) THEN
          RAISE EXCEPTION 'demo_event_history_epoch_downgrade_requires_empty';
        END IF;
      END $$;
    """)
    op.execute("DROP TABLE public.demo_event_history_epoch_events")
    op.execute("DROP FUNCTION public.demo_event_history_epoch_insert_guard()")
