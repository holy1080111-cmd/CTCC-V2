"""Append-only public-minute journal checkpoint witness; no capture grant.

Revision ID: 0024
Revises: 0023

The migration deliberately grants no collector role. Production must provision
and verify a separate restricted database principal before using this table.
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public_receipt_witness_revisions (
        journal_key VARCHAR(64) NOT NULL,
        revision BIGINT NOT NULL,
        journal_id VARCHAR(32) NOT NULL,
        root_device BIGINT NOT NULL,
        root_inode BIGINT NOT NULL,
        transition VARCHAR(24) NOT NULL,
        state VARCHAR(24) NOT NULL,
        operation_id VARCHAR(32),
        plan_sha256 VARCHAR(64),
        attempt_outcome VARCHAR(24),
        previous_record_sha256 VARCHAR(64),
        checkpoint_json TEXT NOT NULL,
        checkpoint_sha256 VARCHAR(64) NOT NULL,
        record_sha256 VARCHAR(64) NOT NULL,
        recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_public_receipt_witness_revisions PRIMARY KEY(journal_key,revision),
        CONSTRAINT ck_public_receipt_witness_revisions_revision_bound CHECK(revision BETWEEN 0 AND 8192),
        CONSTRAINT ck_public_receipt_witness_revisions_identity CHECK(
          journal_key ~ '^[a-f0-9]{64}$' AND journal_id ~ '^[a-f0-9]{32}$'
          AND checkpoint_sha256 ~ '^[a-f0-9]{64}$'
          AND record_sha256 ~ '^[a-f0-9]{64}$'
          AND (previous_record_sha256 IS NULL OR previous_record_sha256 ~ '^[a-f0-9]{64}$')
          AND (plan_sha256 IS NULL OR plan_sha256 ~ '^[a-f0-9]{64}$')
          AND (operation_id IS NULL OR operation_id ~ '^[a-f0-9]{32}$')),
        CONSTRAINT ck_public_receipt_witness_revisions_checkpoint_bound CHECK(
          root_device >= 0 AND root_inode >= 1
          AND octet_length(checkpoint_json) BETWEEN 1 AND 4096),
        CONSTRAINT ck_public_receipt_witness_revisions_state CHECK(
          transition IN ('initialize','open_attempt','append_attempt',
            'append_capture','close_rejected_attempt')
          AND state IN ('idle','open','attempt_anchored')
          AND (attempt_outcome IS NULL OR attempt_outcome IN
            ('completed_collection','rejected','incomplete')))
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX uq_public_receipt_witness_operation_start
      ON public_receipt_witness_revisions(journal_key,operation_id)
      WHERE transition='open_attempt'
    """)
    op.execute("REVOKE ALL ON TABLE public_receipt_witness_revisions FROM PUBLIC")
    op.execute("""
      CREATE FUNCTION public_receipt_witness_insert_guard() RETURNS trigger AS $$
      DECLARE p public.public_receipt_witness_revisions%ROWTYPE;
              j JSONB; old_j JSONB; lock_key BIGINT;
      BEGIN
        lock_key := ('x'||substr(NEW.journal_key,1,16))::bit(64)::bigint;
        PERFORM pg_catalog.pg_advisory_xact_lock(lock_key);
        SELECT * INTO p FROM public.public_receipt_witness_revisions
          WHERE journal_key=NEW.journal_key ORDER BY revision DESC LIMIT 1;
        IF encode(pg_catalog.sha256(pg_catalog.convert_to(NEW.checkpoint_json,'UTF8')),'hex')
             IS DISTINCT FROM NEW.checkpoint_sha256 THEN
          RAISE EXCEPTION 'public_witness_checkpoint_hash_denied';
        END IF;
        j := NEW.checkpoint_json::jsonb;
        IF pg_catalog.jsonb_typeof(j) IS DISTINCT FROM 'object'
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.public.journal_checkpoint.v1'
          OR j->>'genesis_sha256' IS DISTINCT FROM NEW.journal_key
          OR j->>'head_sha256' IS NULL
          OR j->>'head_sha256' !~ '^[a-f0-9]{64}$'
          OR j->>'attempt_head_sha256' IS NULL
          OR j->>'attempt_head_sha256' !~ '^[a-f0-9]{64}$'
          OR j->>'sequence' IS NULL
          OR j->>'attempt_sequence' IS NULL
          OR (j->>'root_device')::bigint IS DISTINCT FROM NEW.root_device
          OR (j->>'root_inode')::bigint IS DISTINCT FROM NEW.root_inode THEN
          RAISE EXCEPTION 'public_witness_checkpoint_identity_denied';
        END IF;
        IF p.revision IS NULL THEN
          IF NEW.revision <> 0 OR NEW.transition <> 'initialize'
            OR NEW.state <> 'idle' OR NEW.operation_id IS NOT NULL
            OR NEW.plan_sha256 IS NOT NULL OR NEW.attempt_outcome IS NOT NULL
            OR NEW.previous_record_sha256 IS NOT NULL
            OR (j->>'sequence')::bigint <> 0
            OR (j->>'attempt_sequence')::bigint <> 0
            OR j->>'head_sha256' IS DISTINCT FROM NEW.journal_key
            OR j->>'attempt_head_sha256' IS DISTINCT FROM NEW.journal_key THEN
            RAISE EXCEPTION 'public_witness_initialize_denied';
          END IF;
        ELSE
          IF NEW.revision <> p.revision+1 OR NEW.revision > 8192
            OR NEW.previous_record_sha256 IS DISTINCT FROM p.record_sha256
            OR NEW.journal_id IS DISTINCT FROM p.journal_id
            OR NEW.root_device IS DISTINCT FROM p.root_device
            OR NEW.root_inode IS DISTINCT FROM p.root_inode
            OR NEW.operation_id IS NULL
            OR NEW.plan_sha256 IS NULL
            OR NEW.plan_sha256 !~ '^[a-f0-9]{64}$' THEN
            RAISE EXCEPTION 'public_witness_revision_denied';
          END IF;
          old_j := p.checkpoint_json::jsonb;
          IF NEW.transition='open_attempt' THEN
            IF p.state <> 'idle' OR NEW.state <> 'open'
              OR NEW.attempt_outcome IS NOT NULL
              OR NEW.checkpoint_json IS DISTINCT FROM p.checkpoint_json THEN
              RAISE EXCEPTION 'public_witness_open_denied';
            END IF;
          ELSIF NEW.transition='append_attempt' THEN
            IF p.state <> 'open' OR NEW.state <> 'attempt_anchored'
              OR NEW.operation_id IS DISTINCT FROM p.operation_id
              OR NEW.plan_sha256 IS DISTINCT FROM p.plan_sha256
              OR NEW.attempt_outcome IS NULL
              OR NEW.attempt_outcome NOT IN
                ('completed_collection','rejected','incomplete')
              OR (j->>'attempt_sequence')::bigint <>
                (old_j->>'attempt_sequence')::bigint+1
              OR j->>'attempt_head_sha256' IS NOT DISTINCT FROM
                old_j->>'attempt_head_sha256'
              OR j->>'sequence' IS DISTINCT FROM old_j->>'sequence'
              OR j->>'head_sha256' IS DISTINCT FROM old_j->>'head_sha256' THEN
              RAISE EXCEPTION 'public_witness_attempt_denied';
            END IF;
          ELSIF NEW.transition='append_capture' THEN
            IF p.state <> 'attempt_anchored'
              OR p.attempt_outcome <> 'completed_collection'
              OR NEW.state <> 'idle'
              OR NEW.operation_id IS DISTINCT FROM p.operation_id
              OR NEW.plan_sha256 IS DISTINCT FROM p.plan_sha256
              OR NEW.attempt_outcome IS DISTINCT FROM p.attempt_outcome
              OR (j->>'sequence')::bigint <> (old_j->>'sequence')::bigint+1
              OR j->>'head_sha256' IS NOT DISTINCT FROM old_j->>'head_sha256'
              OR j->>'attempt_sequence' IS DISTINCT FROM old_j->>'attempt_sequence'
              OR j->>'attempt_head_sha256' IS DISTINCT FROM
                old_j->>'attempt_head_sha256' THEN
              RAISE EXCEPTION 'public_witness_capture_denied';
            END IF;
          ELSIF NEW.transition='close_rejected_attempt' THEN
            IF p.state <> 'attempt_anchored'
              OR p.attempt_outcome NOT IN ('rejected','incomplete')
              OR NEW.state <> 'idle'
              OR NEW.operation_id IS DISTINCT FROM p.operation_id
              OR NEW.plan_sha256 IS DISTINCT FROM p.plan_sha256
              OR NEW.attempt_outcome IS DISTINCT FROM p.attempt_outcome
              OR NEW.checkpoint_json IS DISTINCT FROM p.checkpoint_json THEN
              RAISE EXCEPTION 'public_witness_close_denied';
            END IF;
          ELSE
            RAISE EXCEPTION 'public_witness_transition_denied';
          END IF;
        END IF;
        NEW.record_sha256 := encode(pg_catalog.sha256(pg_catalog.convert_to(
          pg_catalog.concat_ws(chr(31),
            NEW.journal_key,NEW.revision::text,NEW.journal_id,
            NEW.root_device::text,NEW.root_inode::text,NEW.transition,
            NEW.state,coalesce(NEW.operation_id,''),coalesce(NEW.plan_sha256,''),
            coalesce(NEW.attempt_outcome,''),
            coalesce(NEW.previous_record_sha256,''),
            NEW.checkpoint_sha256,NEW.checkpoint_json), 'UTF8')),'hex');
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public_receipt_witness_immutable() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'public_witness_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER public_receipt_witness_insert_guard
      BEFORE INSERT ON public_receipt_witness_revisions
      FOR EACH ROW EXECUTE FUNCTION public_receipt_witness_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER public_receipt_witness_immutable
      BEFORE UPDATE OR DELETE ON public_receipt_witness_revisions
      FOR EACH ROW EXECUTE FUNCTION public_receipt_witness_immutable()
    """)
    op.execute("""
      CREATE TRIGGER public_receipt_witness_no_truncate
      BEFORE TRUNCATE ON public_receipt_witness_revisions
      FOR EACH STATEMENT EXECUTE FUNCTION public_receipt_witness_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public_receipt_witness_append(p_record JSONB)
      RETURNS void AS $$
      BEGIN
        IF pg_catalog.jsonb_typeof(p_record) IS DISTINCT FROM 'object' THEN
          RAISE EXCEPTION 'public_witness_record_invalid';
        END IF;
        INSERT INTO public.public_receipt_witness_revisions (
          journal_key,revision,journal_id,root_device,root_inode,
          transition,state,operation_id,plan_sha256,attempt_outcome,
          previous_record_sha256,checkpoint_json,checkpoint_sha256,record_sha256
        ) VALUES (
          p_record->>'journal_key',
          (p_record->>'revision')::bigint,
          p_record->>'journal_id',
          (p_record->>'root_device')::bigint,
          (p_record->>'root_inode')::bigint,
          p_record->>'transition',p_record->>'state',
          p_record->>'operation_id',p_record->>'plan_sha256',
          p_record->>'attempt_outcome',p_record->>'previous_record_sha256',
          p_record->>'checkpoint_json',p_record->>'checkpoint_sha256',
          repeat('0',64)
        );
      END; $$ LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public_receipt_witness_read(p_journal_key TEXT)
      RETURNS SETOF public.public_receipt_witness_revisions AS $$
        SELECT * FROM public.public_receipt_witness_revisions
        WHERE journal_key=p_journal_key ORDER BY revision LIMIT 8194
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    # PostgreSQL grants EXECUTE on new functions to PUBLIC by default. The
    # triggers stay table-owned; append/read need explicit restricted-role grants.
    op.execute(
        "REVOKE ALL ON FUNCTION public_receipt_witness_insert_guard() FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON FUNCTION public_receipt_witness_immutable() FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION public_receipt_witness_append(jsonb) FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON FUNCTION public_receipt_witness_read(text) FROM PUBLIC")


def downgrade():
    op.execute(
        "LOCK TABLE public_receipt_witness_revisions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public_receipt_witness_revisions) THEN
          RAISE EXCEPTION 'public_witness_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute("DROP FUNCTION public_receipt_witness_append(jsonb)")
    op.execute("DROP FUNCTION public_receipt_witness_read(text)")
    op.drop_table("public_receipt_witness_revisions")
    op.execute("DROP FUNCTION public_receipt_witness_insert_guard()")
    op.execute("DROP FUNCTION public_receipt_witness_immutable()")
