"""Repair two fail-closed Gate 3 JSON validators on already-upgraded databases.

Revision ID: 0031
Revises: 0030

The deployed 0029 ``gate3_schedule_all_ascii`` body selected an unqualified
``value`` column while declaring a local variable of the same name. PostgreSQL
raises ambiguous_column on nested JSON, so canonical schedule claims cannot
proceed. The 0030 integer validator also used unqualified ``value``; qualify
it defensively for installed consistency. Fresh installs receive the corrected
bodies in 0029/0030; this revision repairs databases that already applied
those revisions. CREATE OR REPLACE retains each function's OID and existing
role grants. No raw evidence, legacy inventory, claims, or ACKs are modified
or retroactively certified.
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    # Both replacements commit atomically. If an append is in progress, fail
    # promptly instead of waiting behind it and making release state uncertain.
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_key_claims,
        public.gate3_capture_schedule_claim_acks,
        public.gate3_preregistration_seals,
        public.gate3_preregistration_seal_acks
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_schedule_all_ascii(p jsonb)
      RETURNS boolean AS $$
      DECLARE kind text; member record; ascii_value text;
      BEGIN
        kind := pg_catalog.jsonb_typeof(p);
        IF kind = 'string' THEN
          ascii_value := p #>> '{}';
          RETURN pg_catalog.octet_length(ascii_value) =
            pg_catalog.length(ascii_value);
        ELSIF kind = 'object' THEN
          FOR member IN SELECT item.key,item.value
            FROM pg_catalog.jsonb_each(p) AS item(key,value) LOOP
            IF pg_catalog.octet_length(member.key) <>
                 pg_catalog.length(member.key)
              OR NOT public.gate3_schedule_all_ascii(member.value)
            THEN RETURN false;
            END IF;
          END LOOP;
          RETURN true;
        ELSIF kind = 'array' THEN
          FOR member IN SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p) AS item(value)
          LOOP
            IF NOT public.gate3_schedule_all_ascii(member.value) THEN
              RETURN false;
            END IF;
          END LOOP;
          RETURN true;
        END IF;
        RETURN kind IN ('number','boolean','null');
      END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_prereg_integer_numbers(p jsonb)
      RETURNS boolean AS $$
      DECLARE kind text; member record;
      BEGIN
        kind := pg_catalog.jsonb_typeof(p);
        IF kind = 'number' THEN
          RETURN p::text ~ '^-?(0|[1-9][0-9]*)$';
        ELSIF kind = 'object' THEN
          FOR member IN SELECT item.value
            FROM pg_catalog.jsonb_each(p) AS item(key,value) LOOP
            IF NOT public.gate3_prereg_integer_numbers(member.value)
            THEN RETURN false; END IF;
          END LOOP;
          RETURN true;
        ELSIF kind = 'array' THEN
          FOR member IN SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p) AS item(value)
          LOOP
            IF NOT public.gate3_prereg_integer_numbers(member.value)
            THEN RETURN false; END IF;
          END LOOP;
          RETURN true;
        END IF;
        RETURN kind IN ('string','boolean','null');
      END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    # Replacement preserves existing ACLs; do not allow an unexpectedly
    # inherited PUBLIC execute privilege on either safety predicate.
    op.execute(
        "REVOKE ALL ON FUNCTION public.gate3_schedule_all_ascii(jsonb) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.gate3_prereg_integer_numbers(jsonb) FROM PUBLIC"
    )


def downgrade():
    # Never restore the ambiguous validator. On a disposable empty database,
    # the corrected definitions can remain until 0030/0029 drop them in the
    # full downgrade chain. With any historical evidence, a 0030-only rollback
    # would misstate the installed admission boundary and is refused.
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
          'gate3_canonical_sql_repair_downgrade_requires_empty'; END IF;
      END $$
    """)
