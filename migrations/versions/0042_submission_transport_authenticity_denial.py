"""Deny reporting projection until a real transport-authenticity issuer exists.

Revision ID: 0042
Revises: 0041

DB0018 treated an acknowledged status as enough to make a spool eligible.
Current outcomes retain private response evidence but explicitly record
transport_authenticity_verified=false. No existing outcome issuer can set that
field to true with independently verified exchange provenance. This migration
therefore accepts only ineligible new spools and refuses all new projections.
Legacy eligible rows/receipts cause an atomic upgrade refusal; they are never
deleted, rewritten, or delivered as a side effect of this migration.
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def _spool_guard() -> str:
    return """
      CREATE OR REPLACE FUNCTION public.qualification_spool_binding()
      RETURNS trigger AS $$
      DECLARE o RECORD; r RECORD; j JSONB;
      BEGIN
        SELECT * INTO o FROM public.qualification_submission_outcomes
          WHERE outcome_id=NEW.outcome_id;
        SELECT * INTO r FROM public.qualification_reservations
          WHERE reservation_id=NEW.reservation_id;
        IF o.outcome_id IS NULL OR r.reservation_id IS NULL
          OR o.reservation_id IS DISTINCT FROM NEW.reservation_id
          OR r.report_id IS DISTINCT FROM NEW.report_id
          OR NEW.created_at IS DISTINCT FROM o.recorded_at THEN
          RAISE EXCEPTION 'submission_spool_binding_denied';
        END IF;
        BEGIN
          j := o.outcome_json::jsonb;
        EXCEPTION WHEN others THEN
          RAISE EXCEPTION 'submission_spool_transport_evidence_invalid';
        END;
        IF jsonb_typeof(j) IS DISTINCT FROM 'object'
          OR j->>'status' IS DISTINCT FROM o.status
          OR j->'transport_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR NEW.eligible IS DISTINCT FROM false THEN
          RAISE EXCEPTION 'submission_spool_transport_unverified';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """


def _projection_guard() -> str:
    return """
      CREATE OR REPLACE FUNCTION public.qualification_projection_binding()
      RETURNS trigger AS $$
      BEGIN
        RAISE EXCEPTION 'submission_projection_transport_unverified';
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """


def upgrade():
    # NOWAIT plus one migration transaction prevent concurrent writes while the
    # legacy inventory is checked and both trigger functions are replaced.
    op.execute("""
      LOCK TABLE public.qualification_submission_outcomes,
        public.qualification_report_spool,
        public.qualification_report_projection_receipts
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.qualification_report_spool WHERE eligible)
          OR EXISTS(SELECT 1 FROM public.qualification_report_projection_receipts)
        THEN
          RAISE EXCEPTION 'submission_transport_legacy_rows_require_forensic_resolution';
        END IF;
      END $$;
    """)
    # Independent hard constraints remain effective if an INSERT trigger is
    # disabled. Both tables were checked under the same exclusive lock.
    op.execute("""
      ALTER TABLE public.qualification_report_spool
      ADD CONSTRAINT ck_qualification_report_spool_transport_unverified
      CHECK (eligible=false)
    """)
    op.execute("""
      ALTER TABLE public.qualification_report_projection_receipts
      ADD CONSTRAINT ck_qualification_report_projection_receipts_transport_unverified
      CHECK (false)
    """)
    op.execute(_spool_guard())
    op.execute(_projection_guard())
    op.execute(
        "REVOKE ALL ON FUNCTION public.qualification_spool_binding() FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.qualification_projection_binding() FROM PUBLIC"
    )


def downgrade():
    # Restoring DB0018 would again turn a status-only ACK into delivery
    # eligibility. A future reviewed migration can supersede this denial, but
    # an automatic downgrade may not reinstate the unsafe predicate.
    op.execute("""
      DO $$ BEGIN
        RAISE EXCEPTION 'submission_transport_authenticity_downgrade_denied';
      END $$;
    """)
