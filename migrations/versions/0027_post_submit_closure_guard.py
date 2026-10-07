"""Retain consumed/uncertain Demo holds without post-submit closure proof.

Revision ID: 0027
Revises: 0026

The application has no authenticated, complete post-submit closure witness.
Changing a caller's current account claims to empty must not retire an intent.
This replaces the existing reservation UPDATE trigger function, including its
prior immutable-column and revision checks, without rewriting ledger records.
An existing terminal row may conceal a prior submission if its journal is
incomplete. Upgrade therefore refuses every pre-existing reconciled_flat row;
manual forensic disposition is required before this migration can apply.
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_LOCK = (
    "LOCK TABLE public.qualification_reservations, "
    "public.qualification_reservation_transitions "
    "IN ACCESS EXCLUSIVE MODE NOWAIT"
)


def _update_function(*, allow_post_submit_flat: bool) -> str:
    allowed = (
        "OR (OLD.state = 'consumed' AND NEW.state IN ('uncertain','reconciled_flat'))\n"
        "                     OR (OLD.state = 'uncertain' AND NEW.state = 'reconciled_flat')"
        if allow_post_submit_flat
        else "OR (OLD.state = 'consumed' AND NEW.state = 'uncertain')"
    )
    # Downgrade restores the exact DB0017 function definition. The guarded
    # version uses only built-ins and OLD/NEW, so an arbitrary caller path must
    # not influence function resolution while the trigger executes.
    path = "" if allow_post_submit_flat else " SET search_path = pg_catalog, pg_temp"
    return f"""
        CREATE OR REPLACE FUNCTION public.qualification_reservation_update() RETURNS trigger AS $$
        BEGIN
          IF (to_jsonb(NEW) - ARRAY['state','state_revision','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['state','state_revision','updated_at'])
             OR NEW.state_revision <> OLD.state_revision + 1
             OR NEW.updated_at < OLD.updated_at
             OR NOT ((OLD.state = 'reserved' AND NEW.state IN ('consumed','uncertain','reconciled_flat'))
                     {allowed}) THEN
            RAISE EXCEPTION 'qualification_reservation_update_denied';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql{path};
    """


def upgrade():
    # Lock both the state and immutable journal before auditing. A concurrent
    # writer causes a bounded migration failure instead of racing the audit or
    # trigger swap. A missing predecessor transition is not proof that a legacy
    # terminal row was never submitted. Even reserved-only cancellations need
    # independent review before the new schema can be accepted.
    op.execute(_LOCK)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM public.qualification_reservations
            WHERE state = 'reconciled_flat'
          ) THEN
            RAISE EXCEPTION 'post_submit_closure_legacy_terminal_unresolved';
          END IF;
        END $$;
    """)
    op.execute(_update_function(allow_post_submit_flat=False))


def downgrade():
    # Relaxing this protection with any retained hold/intent is unsafe. The
    # prior function may be restored only in a disposable, empty ledger.
    op.execute(_LOCK)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.qualification_reservations) THEN
            RAISE EXCEPTION 'post_submit_closure_guard_downgrade_requires_empty';
          END IF;
        END $$;
    """)
    op.execute(_update_function(allow_post_submit_flat=True))
    # PostgreSQL may retain function attributes across CREATE OR REPLACE.
    # Restore the DB0017 metadata exactly on an empty-ledger downgrade.
    op.execute(
        "ALTER FUNCTION public.qualification_reservation_update() RESET search_path"
    )
