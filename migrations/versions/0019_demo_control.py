"""Persistent Demo control epochs and immutable journal; no execution permit.

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE demo_account_controls (
            environment VARCHAR(8) NOT NULL,
            account_id VARCHAR(32) NOT NULL,
            control_revision BIGINT NOT NULL,
            owner_sha256 VARCHAR(64),
            owner_epoch BIGINT NOT NULL,
            lease_until TIMESTAMP WITH TIME ZONE NOT NULL,
            emergency_stop BOOLEAN NOT NULL,
            arm_request_id VARCHAR(64),
            arm_expires_at TIMESTAMP WITH TIME ZONE,
            state_json TEXT NOT NULL,
            state_sha256 VARCHAR(64) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_demo_account_controls PRIMARY KEY (environment, account_id),
            CONSTRAINT ck_demo_account_controls_identity CHECK (environment = 'demo' AND account_id ~ '^[0-9]{1,32}$'),
            CONSTRAINT ck_demo_account_controls_revisions CHECK (control_revision > 0 AND owner_epoch >= 0 AND owner_epoch <= control_revision),
            CONSTRAINT ck_demo_account_controls_digests CHECK (state_sha256 ~ '^[a-f0-9]{64}$'),
            CONSTRAINT ck_demo_account_controls_owner_pair CHECK ((owner_epoch=0 AND owner_sha256 IS NULL AND emergency_stop AND arm_request_id IS NULL AND lease_until=updated_at) OR (owner_epoch>0 AND owner_sha256 IS NOT NULL AND owner_sha256 ~ '^[a-f0-9]{64}$')),
            CONSTRAINT ck_demo_account_controls_clocks CHECK (created_at <= updated_at AND updated_at <= lease_until),
            CONSTRAINT ck_demo_account_controls_state_bound CHECK (octet_length(state_json) BETWEEN 1 AND 8192),
            CONSTRAINT ck_demo_account_controls_arm_pair CHECK ((arm_request_id IS NULL AND arm_expires_at IS NULL) OR (arm_request_id IS NOT NULL AND arm_request_id ~ '^[a-f0-9]{64}$' AND arm_expires_at IS NOT NULL AND NOT emergency_stop AND created_at < arm_expires_at AND arm_expires_at <= lease_until))
        )
    """)
    op.execute("""
        CREATE TABLE demo_control_journal (
            environment VARCHAR(8) NOT NULL,
            account_id VARCHAR(32) NOT NULL,
            control_revision BIGINT NOT NULL,
            command_id VARCHAR(64) NOT NULL,
            action VARCHAR(24) NOT NULL,
            previous_sha256 VARCHAR(64),
            event_sha256 VARCHAR(64) NOT NULL,
            state_sha256 VARCHAR(64) NOT NULL,
            event_json TEXT NOT NULL,
            occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_demo_control_journal PRIMARY KEY (environment, account_id, control_revision),
            CONSTRAINT fk_demo_control_journal_environment_demo_account_controls FOREIGN KEY(environment, account_id) REFERENCES demo_account_controls (environment, account_id) ON DELETE RESTRICT,
            CONSTRAINT uq_demo_control_journal_environment UNIQUE (environment, account_id, command_id),
            CONSTRAINT ck_demo_control_journal_revision CHECK (control_revision > 0),
            CONSTRAINT ck_demo_control_journal_digests CHECK (command_id ~ '^[a-f0-9]{64}$' AND event_sha256 ~ '^[a-f0-9]{64}$' AND state_sha256 ~ '^[a-f0-9]{64}$'),
            CONSTRAINT ck_demo_control_journal_chain CHECK ((control_revision = 1 AND previous_sha256 IS NULL) OR (control_revision > 1 AND previous_sha256 IS NOT NULL AND previous_sha256 ~ '^[a-f0-9]{64}$')),
            CONSTRAINT ck_demo_control_journal_action CHECK (action IN ('acquire','renew','arm_requested','disarm','estop','rebind','release')),
            CONSTRAINT ck_demo_control_journal_event_bound CHECK (octet_length(event_json) BETWEEN 1 AND 12288)
        )
    """)

    op.execute("""
        CREATE FUNCTION demo_control_state_guard() RETURNS trigger AS $$
        DECLARE j JSONB;
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'demo_control_delete_denied'; END IF;
          j := NEW.state_json::jsonb;
          IF j->>'version' IS DISTINCT FROM 'ctcc.demo_control.v1'
             OR j->>'environment' IS DISTINCT FROM NEW.environment
             OR j->>'account_id' IS DISTINCT FROM NEW.account_id
             OR (j->>'revision')::bigint IS DISTINCT FROM NEW.control_revision
             OR j->>'owner_sha256' IS DISTINCT FROM NEW.owner_sha256
             OR (j->>'owner_epoch')::bigint IS DISTINCT FROM NEW.owner_epoch
             OR (j->>'lease_until')::timestamptz IS DISTINCT FROM NEW.lease_until
             OR j->'emergency_stop' IS DISTINCT FROM to_jsonb(NEW.emergency_stop)
             OR j->>'arm_request_id' IS DISTINCT FROM NEW.arm_request_id
             OR (j->>'arm_expires_at')::timestamptz IS DISTINCT FROM NEW.arm_expires_at
             OR (j->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
             OR (j->>'updated_at')::timestamptz IS DISTINCT FROM NEW.updated_at
             OR (NEW.owner_epoch=0 AND ROW(j->>'config_sha256',j->>'policy_sha256',j->>'credential_session_sha256') IS DISTINCT FROM ROW(NULL::text,NULL::text,NULL::text))
             OR (NEW.owner_epoch>0 AND (NOT COALESCE(j->>'config_sha256' ~ '^[a-f0-9]{64}$',false)
                OR NOT COALESCE(j->>'policy_sha256' ~ '^[a-f0-9]{64}$',false)
                OR NOT COALESCE(j->>'credential_session_sha256' ~ '^[a-f0-9]{64}$',false)))
             OR encode(sha256(convert_to(NEW.state_json,'UTF8')),'hex') IS DISTINCT FROM NEW.state_sha256
             OR NEW.lease_until > NEW.updated_at + INTERVAL '30 seconds' THEN
            RAISE EXCEPTION 'demo_control_state_binding_denied';
          END IF;
          IF TG_OP = 'INSERT' THEN
            IF NEW.control_revision <> 1 OR NEW.owner_epoch NOT IN (0,1) OR NEW.arm_request_id IS NOT NULL THEN
              RAISE EXCEPTION 'demo_control_initial_state_denied';
            END IF;
          ELSE
            IF ROW(NEW.environment,NEW.account_id,NEW.created_at) IS DISTINCT FROM ROW(OLD.environment,OLD.account_id,OLD.created_at)
               OR NEW.control_revision <> OLD.control_revision+1
               OR NEW.updated_at < OLD.updated_at
               OR (OLD.emergency_stop AND NOT NEW.emergency_stop) THEN
              RAISE EXCEPTION 'demo_control_transition_denied';
            END IF;
            IF NEW.owner_sha256 IS DISTINCT FROM OLD.owner_sha256 THEN
              IF NEW.owner_epoch <> OLD.owner_epoch+1 OR NEW.updated_at < OLD.lease_until OR NEW.arm_request_id IS NOT NULL THEN
                RAISE EXCEPTION 'demo_control_takeover_denied';
              END IF;
            ELSIF NEW.owner_epoch <> OLD.owner_epoch THEN
              RAISE EXCEPTION 'demo_control_epoch_denied';
            END IF;
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER demo_control_state_guard BEFORE INSERT OR UPDATE OR DELETE
          ON demo_account_controls FOR EACH ROW EXECUTE FUNCTION demo_control_state_guard();
    """)
    op.execute("""
        CREATE FUNCTION demo_control_journal_guard() RETURNS trigger AS $$
        DECLARE c RECORD; prior RECORD; old_state JSONB; current_state JSONB;
        BEGIN
          SELECT * INTO c FROM demo_account_controls WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
          SELECT * INTO prior FROM demo_control_journal WHERE environment=NEW.environment AND account_id=NEW.account_id ORDER BY control_revision DESC LIMIT 1;
          current_state := c.state_json::jsonb;
          old_state := prior.event_json::jsonb->'state';
          IF c.control_revision IS DISTINCT FROM NEW.control_revision
             OR c.state_sha256 IS DISTINCT FROM NEW.state_sha256
             OR c.updated_at IS DISTINCT FROM NEW.occurred_at
             OR NEW.control_revision <> COALESCE(prior.control_revision,0)+1
             OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256
             OR encode(sha256(convert_to(NEW.event_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
             OR NEW.event_json::jsonb IS DISTINCT FROM jsonb_build_object(
                'version','ctcc.demo_control_event.v1','command_id',NEW.command_id,
                'action',NEW.action,'previous_sha256',NEW.previous_sha256,'state',current_state) THEN
            RAISE EXCEPTION 'demo_control_journal_binding_denied';
          END IF;
          IF NEW.action='acquire' THEN
            IF c.arm_request_id IS NOT NULL OR (prior.control_revision IS NULL AND c.owner_epoch<>1) OR (prior.control_revision IS NOT NULL AND
               (c.owner_sha256 IS NOT DISTINCT FROM old_state->>'owner_sha256' OR c.owner_epoch <> (old_state->>'owner_epoch')::bigint+1)) THEN
              RAISE EXCEPTION 'demo_control_acquire_denied';
            END IF;
          ELSIF prior.control_revision IS NULL THEN
            IF NEW.action<>'estop' OR c.owner_epoch<>0 OR NOT c.emergency_stop THEN
              RAISE EXCEPTION 'demo_control_stopped_genesis_required';
            END IF;
          ELSIF c.owner_sha256 IS DISTINCT FROM old_state->>'owner_sha256'
             OR c.owner_epoch IS DISTINCT FROM (old_state->>'owner_epoch')::bigint THEN
            RAISE EXCEPTION 'demo_control_owner_binding_denied';
          END IF;
          IF NEW.action IN ('arm_requested','rebind','release','renew') AND NEW.occurred_at >= (old_state->>'lease_until')::timestamptz THEN
            RAISE EXCEPTION 'demo_control_expired_owner_denied';
          END IF;
          IF NEW.action IN ('arm_requested','rebind') AND c.lease_until IS DISTINCT FROM (old_state->>'lease_until')::timestamptz THEN
            RAISE EXCEPTION 'demo_control_lease_change_denied';
          END IF;
          IF NEW.action='renew' AND c.lease_until IS DISTINCT FROM NEW.occurred_at + INTERVAL '30 seconds' THEN
            RAISE EXCEPTION 'demo_control_renew_lease_denied';
          END IF;
          IF NEW.action IN ('disarm','estop') AND c.lease_until IS DISTINCT FROM GREATEST(NEW.occurred_at,(old_state->>'lease_until')::timestamptz) THEN
            RAISE EXCEPTION 'demo_control_tightening_lease_denied';
          END IF;
          IF NEW.action='arm_requested' THEN
            IF c.arm_request_id IS DISTINCT FROM NEW.command_id OR c.arm_expires_at <= NEW.occurred_at
               OR c.arm_expires_at > NEW.occurred_at + INTERVAL '30 seconds'
               OR (old_state->>'arm_expires_at')::timestamptz > NEW.occurred_at THEN
              RAISE EXCEPTION 'demo_control_arm_denied';
            END IF;
          ELSIF NEW.action IN ('acquire','disarm','estop','rebind','release') AND c.arm_request_id IS NOT NULL THEN
            RAISE EXCEPTION 'demo_control_disarm_required';
          ELSIF NEW.action='renew' AND c.arm_request_id IS NOT NULL AND
              (c.arm_request_id IS DISTINCT FROM old_state->>'arm_request_id'
               OR c.arm_expires_at IS DISTINCT FROM (old_state->>'arm_expires_at')::timestamptz) THEN
            RAISE EXCEPTION 'demo_control_renew_cannot_arm';
          END IF;
          IF NEW.action='estop' AND NOT c.emergency_stop THEN
            RAISE EXCEPTION 'demo_control_stop_required';
          END IF;
          IF NEW.action='release' AND c.lease_until IS DISTINCT FROM NEW.occurred_at THEN
            RAISE EXCEPTION 'demo_control_release_required';
          END IF;
          IF NEW.action NOT IN ('acquire','rebind') AND
             ROW(current_state->>'config_sha256',current_state->>'policy_sha256',current_state->>'credential_session_sha256') IS DISTINCT FROM
             ROW(old_state->>'config_sha256',old_state->>'policy_sha256',old_state->>'credential_session_sha256') THEN
            RAISE EXCEPTION 'demo_control_rebind_required';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER demo_control_journal_guard BEFORE INSERT ON demo_control_journal
          FOR EACH ROW EXECUTE FUNCTION demo_control_journal_guard();
    """)
    op.execute("""
        CREATE FUNCTION demo_control_commit_guard() RETURNS trigger AS $$
        DECLARE event RECORD;
        BEGIN
          SELECT * INTO event FROM demo_control_journal WHERE environment=NEW.environment
            AND account_id=NEW.account_id AND control_revision=NEW.control_revision;
          IF event.control_revision IS NULL OR event.state_sha256 IS DISTINCT FROM NEW.state_sha256 THEN
            RAISE EXCEPTION 'demo_control_commit_without_journal';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER demo_control_commit_guard AFTER INSERT OR UPDATE ON demo_account_controls
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION demo_control_commit_guard();
    """)
    op.execute("""
        CREATE TRIGGER demo_control_journal_immutable BEFORE UPDATE OR DELETE ON demo_control_journal
          FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable();
    """)
    op.execute("""
        CREATE TRIGGER demo_control_journal_no_truncate BEFORE TRUNCATE ON demo_control_journal
          FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable();
    """)
    op.execute("""
        CREATE TRIGGER demo_control_no_truncate BEFORE TRUNCATE ON demo_account_controls
          FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable();
    """)


def downgrade():
    # The empty check and DROP must exclude concurrent durable EStop writes.
    # NOWAIT denies an active writer without introducing a lock wait cycle.
    op.execute("""
        LOCK TABLE demo_account_controls, demo_control_journal
          IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM demo_account_controls) OR EXISTS (SELECT 1 FROM demo_control_journal) THEN
            RAISE EXCEPTION 'demo_control_downgrade_requires_empty';
          END IF;
        END $$;
    """)
    op.drop_table("demo_control_journal")
    op.drop_table("demo_account_controls")
    op.execute("DROP FUNCTION demo_control_commit_guard()")
    op.execute("DROP FUNCTION demo_control_journal_guard()")
    op.execute("DROP FUNCTION demo_control_state_guard()")
