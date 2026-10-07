"""Repair an ``OLD`` trigger-record collision in canonical claim admission.

Revision ID: 0032
Revises: 0031

The 0029 claim INSERT guard used ``old`` as the legacy-inventory SQL alias.
Inside a PL/pgSQL trigger, ``OLD`` is already a trigger record. Resolving
``old.classification`` against that record raises SQLSTATE 42703 instead of
checking whether historical keys are reserved. Fresh installs receive the
corrected 0029 body; this forward revision repairs databases that already
applied 0029. Replacing the function retains its OID, grants, trigger, and
all previously persisted evidence. No rejected claim becomes qualified.
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    # Acquire the same Gate 3 boundary before replacing an active trigger.
    # A concurrent writer makes migration fail promptly, with no partial DDL.
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_publication_acks,
        public.gate3_capture_schedule_legacy_inventory,
        public.gate3_capture_schedule_key_claims,
        public.gate3_capture_schedule_claim_acks,
        public.gate3_preregistration_seals,
        public.gate3_preregistration_seal_acks
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_schedule_claim_insert_guard()
      RETURNS trigger AS $$
      DECLARE pinned public.gate3_capture_schedule_pins%ROWTYPE;
      BEGIN
        SELECT * INTO pinned FROM public.gate3_capture_schedule_pins
          WHERE schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND OR NOT public.gate3_schedule_pin_is_canonical(pinned)
          OR NEW.seal_sha256 IS DISTINCT FROM pinned.seal_sha256
          OR NEW.coordinate_plan_sha256 IS DISTINCT FROM
             pinned.coordinate_plan_sha256
          OR NEW.window_key IS DISTINCT FROM pinned.window_key
          OR NEW.holdout_id IS DISTINCT FROM pinned.holdout_id
          OR NEW.window_start IS DISTINCT FROM pinned.window_start
          OR NEW.window_end IS DISTINCT FROM pinned.window_end
          OR NEW.schedule_recorded_at IS DISTINCT FROM pinned.recorded_at
          OR EXISTS (
            SELECT 1 FROM public.gate3_capture_schedule_legacy_inventory legacy_row
            WHERE legacy_row.classification <> 'noncanonical'
              AND (legacy_row.seal_sha256=NEW.seal_sha256
                OR legacy_row.window_key=NEW.window_key
                OR legacy_row.holdout_id=NEW.holdout_id))
        THEN RAISE EXCEPTION 'gate3_schedule_claim_identity_denied';
        END IF;
        NEW.claimed_at := pg_catalog.clock_timestamp();
        IF NEW.claimed_at < pinned.recorded_at
          OR NEW.claimed_at >= pinned.window_start
        THEN RAISE EXCEPTION 'gate3_schedule_claim_late';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    # CREATE OR REPLACE retains existing ACLs; PUBLIC must never gain EXECUTE.
    op.execute(
        "REVOKE ALL ON FUNCTION public.gate3_schedule_claim_insert_guard() FROM PUBLIC"
    )


def downgrade():
    # Restoring a known-broken trigger is unsafe. The repaired body can remain
    # through a 0031-only rollback on an empty disposable database; the full
    # chain's 0029 downgrade drops the function. Evidence blocks this rollback.
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_publication_acks,
        public.gate3_capture_schedule_legacy_inventory,
        public.gate3_capture_schedule_key_claims,
        public.gate3_capture_schedule_claim_acks,
        public.gate3_preregistration_seals,
        public.gate3_preregistration_seal_acks
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.gate3_capture_schedule_pins)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_publication_acks)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_legacy_inventory)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_key_claims)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_claim_acks)
          OR EXISTS(SELECT 1 FROM public.gate3_preregistration_seals)
          OR EXISTS(SELECT 1 FROM public.gate3_preregistration_seal_acks)
        THEN RAISE EXCEPTION
          'gate3_claim_trigger_alias_repair_downgrade_requires_empty'; END IF;
      END $$
    """)
