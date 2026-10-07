"""Admit only exact control-bound V3 journal pairs to DB0018 reporting.

Revision ID: 0025
Revises: 0024

The existing V2 predicate and all revision, clock and spool guards remain.
This migration grants no exchange transport or execution authority.
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def _function() -> str:
    # Missing fields compare false and malformed JSON raises before an INSERT.
    # V3 cannot use the historical unbound V2 branch. The Python repository
    # independently replays canonical bytes and both intent digests.
    pair_guard = """
          NOT (
            (t.reason_code IS NOT DISTINCT FROM 'consumed_with_submit_intent'
             AND t.evidence_json::jsonb->>'version' IS NOT DISTINCT FROM 'ctcc-demo-submit-intent-v2'
             AND COALESCE(r.request_json::jsonb->>'contract_version','') IS DISTINCT FROM 'ctcc-reservation-request-v3')
            OR
            (t.reason_code IS NOT DISTINCT FROM 'consumed_with_control_bound_intent_v1'
             AND t.evidence_json::jsonb->>'version' IS NOT DISTINCT FROM 'ctcc-control-bound-submit-intent-v1'
             AND r.request_json::jsonb->>'contract_version' IS NOT DISTINCT FROM 'ctcc-reservation-request-v3'
             AND b.id IS NOT NULL
             AND b.from_state IS NULL
             AND b.to_state IS NOT DISTINCT FROM 'reserved'
             AND b.reason_code IS NOT DISTINCT FROM 'risk_reserved_control_bound_v1'
             AND b.occurred_at IS NOT DISTINCT FROM r.created_at
             AND b.evidence_json IS NOT NULL
             AND b.evidence_json IS NOT DISTINCT FROM t.evidence_json::jsonb->>'reservation_binding_json'
             AND jsonb_typeof(t.evidence_json::jsonb->'intent_json') IS NOT DISTINCT FROM 'string'
             AND jsonb_typeof(t.evidence_json::jsonb->'reservation_binding_json') IS NOT DISTINCT FROM 'string'
             AND COALESCE((t.evidence_json::jsonb->>'intent_sha256') ~ '^[a-f0-9]{64}$',false)
             AND COALESCE((t.evidence_json::jsonb->>'reservation_binding_sha256') ~ '^[a-f0-9]{64}$',false))
          )
        """
    return f"""
        CREATE OR REPLACE FUNCTION qualification_submission_binding() RETURNS trigger AS $$
        DECLARE r RECORD; t RECORD; s RECORD; prior RECORD; b RECORD;
        BEGIN
          SELECT * INTO r FROM qualification_reservations WHERE reservation_id=NEW.reservation_id;
          SELECT * INTO t FROM qualification_reservation_transitions WHERE id=NEW.intent_transition_id;
          SELECT * INTO b FROM qualification_reservation_transitions
            WHERE reservation_id=NEW.reservation_id AND state_revision=1;
          SELECT * INTO s FROM qualification_account_scopes WHERE environment=r.environment
            AND account_id=r.account_id AND settlement_currency=r.settlement_currency FOR UPDATE;
          SELECT * INTO prior FROM qualification_submission_outcomes WHERE reservation_id=NEW.reservation_id
            ORDER BY sequence DESC LIMIT 1;
          IF r.reservation_id IS NULL OR t.reservation_id IS DISTINCT FROM r.reservation_id
             OR t.to_state <> 'consumed' OR {pair_guard}
             OR t.state_revision <> 2
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
    """


def _original_function() -> str:
    # The 0018 function body is restored exactly for pre-V3 databases.
    return """
        CREATE OR REPLACE FUNCTION qualification_submission_binding() RETURNS trigger AS $$
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
    """


def upgrade():
    op.execute(_function())


def downgrade():
    # An accepted V3 outcome must remain readable under its installed schema.
    # Never erase or reinterpret it to make a downgrade appear successful.
    op.execute("""
        LOCK TABLE qualification_account_scopes, qualification_reservations,
          qualification_reservation_transitions, qualification_submission_outcomes
          IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM qualification_submission_outcomes o
            JOIN qualification_reservation_transitions t
              ON t.id=o.intent_transition_id
            WHERE t.reason_code='consumed_with_control_bound_intent_v1'
          ) THEN
            RAISE EXCEPTION 'control_bound_reporting_downgrade_requires_no_outcomes';
          END IF;
        END $$;
    """)
    op.execute(_original_function())
