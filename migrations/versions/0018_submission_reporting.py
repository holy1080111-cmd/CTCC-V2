"""Immutable submission outcomes and reporting spool; no execution authority.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE qualification_submission_outcomes (
            outcome_id VARCHAR(64) NOT NULL,
            reservation_id VARCHAR(64) NOT NULL,
            intent_transition_id BIGINT NOT NULL,
            sequence BIGINT NOT NULL,
            previous_sha256 VARCHAR(64),
            observation_kind VARCHAR(24) NOT NULL,
            status VARCHAR(24) NOT NULL,
            intent_sha256 VARCHAR(64) NOT NULL,
            exchange_request_sha256 VARCHAR(64) NOT NULL,
            capture_json TEXT NOT NULL,
            capture_sha256 VARCHAR(64) NOT NULL,
            outcome_json TEXT NOT NULL,
            ledger_revision BIGINT NOT NULL,
            observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_qualification_submission_outcomes PRIMARY KEY (outcome_id),
            CONSTRAINT uq_qualification_submission_outcomes_reservation_id UNIQUE (reservation_id, sequence),
            CONSTRAINT ck_qualification_submission_outcomes_revisions CHECK (sequence > 0 AND ledger_revision > 0),
            CONSTRAINT ck_qualification_submission_outcomes_status CHECK (status IN ('acknowledged','rejected','uncertain')),
            CONSTRAINT ck_qualification_submission_outcomes_kind CHECK (observation_kind IN ('initial','reconciliation')),
            CONSTRAINT ck_qualification_submission_outcomes_chain CHECK ((sequence = 1 AND previous_sha256 IS NULL) OR (sequence > 1 AND previous_sha256 ~ '^[a-f0-9]{64}$')),
            CONSTRAINT ck_qualification_submission_outcomes_digests CHECK (outcome_id ~ '^[a-f0-9]{64}$' AND intent_sha256 ~ '^[a-f0-9]{64}$' AND exchange_request_sha256 ~ '^[a-f0-9]{64}$' AND capture_sha256 ~ '^[a-f0-9]{64}$'),
            CONSTRAINT ck_qualification_submission_outcomes_bounds CHECK (octet_length(capture_json) BETWEEN 1 AND 140000 AND octet_length(outcome_json) BETWEEN 1 AND 16384),
            CONSTRAINT ck_qualification_submission_outcomes_clocks CHECK (recorded_at >= observed_at),
            CONSTRAINT fk_qualification_submission_outcomes_reservation_id_qua_fb0b FOREIGN KEY(reservation_id) REFERENCES qualification_reservations (reservation_id) ON DELETE RESTRICT,
            CONSTRAINT fk_qualification_submission_outcomes_intent_transition__ecb4 FOREIGN KEY(intent_transition_id) REFERENCES qualification_reservation_transitions (id) ON DELETE RESTRICT
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_submission_initial ON qualification_submission_outcomes (reservation_id) WHERE observation_kind = 'initial'
    """)
    op.execute("""
        CREATE TABLE qualification_report_spool (
            spool_id VARCHAR(64) NOT NULL,
            outcome_id VARCHAR(64) NOT NULL,
            reservation_id VARCHAR(64) NOT NULL,
            report_id VARCHAR(96) NOT NULL,
            report_bytes BYTEA NOT NULL,
            report_sha256 VARCHAR(64) NOT NULL,
            policy_json TEXT NOT NULL,
            policy_sha256 VARCHAR(64) NOT NULL,
            queue_namespace VARCHAR(64) NOT NULL,
            eligible BOOLEAN NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_qualification_report_spool PRIMARY KEY (spool_id),
            CONSTRAINT uq_qualification_report_spool_outcome_id UNIQUE (outcome_id),
            CONSTRAINT ck_qualification_report_spool_digests CHECK (spool_id ~ '^[a-f0-9]{64}$' AND report_sha256 ~ '^[a-f0-9]{64}$' AND policy_sha256 ~ '^[a-f0-9]{64}$'),
            CONSTRAINT ck_qualification_report_spool_names CHECK (queue_namespace ~ '^[a-z][a-z0-9_-]{0,63}$' AND report_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$'),
            CONSTRAINT ck_qualification_report_spool_bounds CHECK (octet_length(report_bytes) BETWEEN 1 AND 32768 AND octet_length(policy_json) BETWEEN 1 AND 4096),
            CONSTRAINT fk_qualification_report_spool_outcome_id_qualification__df7a FOREIGN KEY(outcome_id) REFERENCES qualification_submission_outcomes (outcome_id) ON DELETE RESTRICT,
            CONSTRAINT fk_qualification_report_spool_reservation_id_qualificat_4ccb FOREIGN KEY(reservation_id) REFERENCES qualification_reservations (reservation_id) ON DELETE RESTRICT
        )
    """)
    op.execute("""
        CREATE INDEX ix_submission_pending_spool ON qualification_report_spool (queue_namespace, created_at, spool_id) WHERE eligible
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_submission_eligible_report ON qualification_report_spool (queue_namespace, report_id) WHERE eligible
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_submission_eligible_reservation ON qualification_report_spool (reservation_id) WHERE eligible
    """)
    op.execute("""
        CREATE TABLE qualification_report_projection_receipts (
            spool_id VARCHAR(64) NOT NULL,
            report_sha256 VARCHAR(64) NOT NULL,
            policy_sha256 VARCHAR(64) NOT NULL,
            envelope_sha256 VARCHAR(64) NOT NULL,
            queue_namespace VARCHAR(64) NOT NULL,
            verified_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_qualification_report_projection_receipts PRIMARY KEY (spool_id),
            CONSTRAINT ck_qualification_report_projection_receipts_digests CHECK (report_sha256 ~ '^[a-f0-9]{64}$' AND policy_sha256 ~ '^[a-f0-9]{64}$' AND envelope_sha256 ~ '^[a-f0-9]{64}$'),
            CONSTRAINT fk_qualification_report_projection_receipts_spool_id_qu_b1e6 FOREIGN KEY(spool_id) REFERENCES qualification_report_spool (spool_id) ON DELETE RESTRICT
        )
    """)
    op.execute("""
        CREATE TRIGGER qualification_submission_outcomes_immutable BEFORE UPDATE OR DELETE
        ON qualification_submission_outcomes FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable();
    """)
    op.execute("""
        CREATE TRIGGER qualification_report_spool_immutable BEFORE UPDATE OR DELETE
        ON qualification_report_spool FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable();
    """)
    op.execute("""
        CREATE TRIGGER qualification_report_projection_receipts_immutable BEFORE UPDATE OR DELETE
        ON qualification_report_projection_receipts FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable();
    """)

    op.execute("""
        CREATE FUNCTION qualification_submission_binding() RETURNS trigger AS $$
        DECLARE r RECORD; t RECORD; s RECORD; prior RECORD;
        BEGIN
          SELECT * INTO r FROM qualification_reservations WHERE reservation_id=NEW.reservation_id;
          SELECT * INTO t FROM qualification_reservation_transitions WHERE id=NEW.intent_transition_id;
          SELECT * INTO s FROM qualification_account_scopes WHERE environment=r.environment
            AND account_id=r.account_id AND settlement_currency=r.settlement_currency FOR UPDATE;
          SELECT * INTO prior FROM qualification_submission_outcomes WHERE reservation_id=NEW.reservation_id
            ORDER BY sequence DESC LIMIT 1;
          IF r.reservation_id IS NULL OR t.reservation_id IS DISTINCT FROM r.reservation_id
             OR t.to_state <> 'consumed' OR t.reason_code <> 'consumed_with_submit_intent'
             OR t.state_revision <> 2 OR t.evidence_json::jsonb->>'version' IS DISTINCT FROM 'ctcc-demo-submit-intent-v2'
             OR s.ledger_revision IS DISTINCT FROM NEW.ledger_revision
             OR s.updated_at IS DISTINCT FROM NEW.recorded_at
             OR NEW.observed_at < t.occurred_at
             OR (NEW.status='uncertain' AND r.state <> 'uncertain')
             OR NEW.sequence <> COALESCE(prior.sequence,0)+1
             OR NEW.previous_sha256 IS DISTINCT FROM prior.outcome_id
             OR (NEW.observation_kind='initial' AND NEW.sequence <> 1) THEN
            RAISE EXCEPTION 'submission_outcome_binding_denied';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER qualification_submission_binding_guard BEFORE INSERT
          ON qualification_submission_outcomes FOR EACH ROW EXECUTE FUNCTION qualification_submission_binding();
    """)
    op.execute("""
        CREATE FUNCTION qualification_spool_binding() RETURNS trigger AS $$
        DECLARE o RECORD; r RECORD;
        BEGIN
          SELECT * INTO o FROM qualification_submission_outcomes WHERE outcome_id=NEW.outcome_id;
          SELECT * INTO r FROM qualification_reservations WHERE reservation_id=NEW.reservation_id;
          IF o.reservation_id IS DISTINCT FROM NEW.reservation_id OR r.report_id IS DISTINCT FROM NEW.report_id
             OR NEW.eligible IS DISTINCT FROM (o.status='acknowledged')
             OR NEW.created_at IS DISTINCT FROM o.recorded_at THEN
            RAISE EXCEPTION 'submission_spool_binding_denied';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER qualification_spool_binding_guard BEFORE INSERT
          ON qualification_report_spool FOR EACH ROW EXECUTE FUNCTION qualification_spool_binding();
    """)
    op.execute("""
        CREATE FUNCTION qualification_projection_binding() RETURNS trigger AS $$
        DECLARE s RECORD;
        BEGIN
          SELECT * INTO s FROM qualification_report_spool WHERE spool_id=NEW.spool_id;
          IF s.spool_id IS NULL OR NOT s.eligible
             OR ROW(NEW.report_sha256,NEW.policy_sha256,NEW.queue_namespace) IS DISTINCT FROM
                ROW(s.report_sha256,s.policy_sha256,s.queue_namespace)
             OR NEW.verified_at < s.created_at THEN
            RAISE EXCEPTION 'submission_projection_binding_denied';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER qualification_projection_binding_guard BEFORE INSERT
          ON qualification_report_projection_receipts FOR EACH ROW EXECUTE FUNCTION qualification_projection_binding();
    """)


def downgrade():
    # Schema rollback destroys retained outcomes; only permitted when empty.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM qualification_submission_outcomes) THEN
            RAISE EXCEPTION 'submission_reporting_downgrade_requires_empty';
          END IF;
        END $$;
    """)
    op.drop_table("qualification_report_projection_receipts")
    op.drop_table("qualification_report_spool")
    op.drop_table("qualification_submission_outcomes")
    op.execute("DROP FUNCTION qualification_projection_binding()")
    op.execute("DROP FUNCTION qualification_spool_binding()")
    op.execute("DROP FUNCTION qualification_submission_binding()")
