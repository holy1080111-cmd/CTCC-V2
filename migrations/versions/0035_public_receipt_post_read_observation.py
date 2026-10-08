"""Persist a later server-clock observation of one committed public witness.

Revision ID: 0035
Revises: 0034

The restricted observer has neither witness-write nor direct table access.
The trigger reads the exact committed witness row before taking the database
clock sample. The row is immutable and must be read back in another session.
This proves neither clock custody nor first evaluator access or Gate 3.
Requires PostgreSQL 17 or newer (`MAINTAIN` table privilege inspection),
matching the repository's pinned postgres:17-alpine deployment image.
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.public_receipt_post_read_observations (
        journal_key VARCHAR(64) NOT NULL,
        witness_revision BIGINT NOT NULL,
        witness_record_sha256 VARCHAR(64) NOT NULL,
        checkpoint_sha256 VARCHAR(64) NOT NULL,
        capture_sequence BIGINT NOT NULL,
        capture_head_sha256 VARCHAR(64) NOT NULL,
        capture_id VARCHAR(32) NOT NULL,
        plan_sha256 VARCHAR(64) NOT NULL,
        receipt_sha256 VARCHAR(64) NOT NULL,
        source_rows_sha256 VARCHAR(64) NOT NULL,
        v2_capture_sha256 VARCHAR(64) NOT NULL,
        witness_recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
        observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_public_receipt_post_read_observations
          PRIMARY KEY(journal_key,witness_revision),
        CONSTRAINT fk_public_receipt_post_read_witness
          FOREIGN KEY(journal_key,witness_revision)
          REFERENCES public.public_receipt_witness_revisions(journal_key,revision),
        CONSTRAINT uq_public_receipt_post_read_capture
          UNIQUE(journal_key,capture_sequence),
        CONSTRAINT ck_public_receipt_post_read_hashes CHECK (
          journal_key ~ '^[a-f0-9]{64}$'
          AND witness_record_sha256 ~ '^[a-f0-9]{64}$'
          AND checkpoint_sha256 ~ '^[a-f0-9]{64}$'
          AND capture_head_sha256 ~ '^[a-f0-9]{64}$'
          AND capture_id ~ '^[a-f0-9]{32}$'
          AND plan_sha256 ~ '^[a-f0-9]{64}$'
          AND receipt_sha256 ~ '^[a-f0-9]{64}$'
          AND source_rows_sha256 ~ '^[a-f0-9]{64}$'
          AND v2_capture_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_public_receipt_post_read_bounds CHECK (
          witness_revision BETWEEN 3 AND 8192
          AND capture_sequence BETWEEN 1 AND 1024),
        CONSTRAINT ck_public_receipt_post_read_time CHECK (
          witness_recorded_at <= observed_at)
      )
    """)
    op.execute(
        "REVOKE ALL ON TABLE public.public_receipt_post_read_observations FROM PUBLIC"
    )
    op.execute("""
      CREATE FUNCTION public.public_receipt_post_read_insert_guard()
      RETURNS trigger AS $$
      DECLARE pinned public.public_receipt_witness_revisions%ROWTYPE;
              safe_role boolean;
      BEGIN
        -- SECURITY DEFINER must inspect the original login, not current_user.
        SELECT NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                    OR r.rolreplication OR r.rolbypassrls)
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles other_role
            WHERE other_role.oid <> r.oid
              AND pg_catalog.pg_has_role(
                session_user,other_role.oid,'MEMBER'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class target
            WHERE target.oid IN (w.oid,o.oid)
              AND (pg_catalog.pg_has_role(session_user,
                     pg_catalog.pg_get_userbyid(target.relowner),'MEMBER')
                OR pg_catalog.has_table_privilege(session_user,target.oid,
                     'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                OR pg_catalog.has_any_column_privilege(session_user,target.oid,
                     'SELECT,INSERT,UPDATE,REFERENCES')))
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.public_receipt_witness_append(jsonb)'::pg_catalog.regprocedure,
            'EXECUTE')
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.public_receipt_witness_read(text)'::pg_catalog.regprocedure,
            'EXECUTE')
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace n
            WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
              AND pg_catalog.has_schema_privilege(
                session_user,n.oid,'CREATE'))
          AND NOT pg_catalog.has_database_privilege(
            session_user,pg_catalog.current_database(),'CREATE')
          INTO safe_role
          FROM pg_catalog.pg_roles r,
               pg_catalog.pg_class w,
               pg_catalog.pg_class o
          WHERE r.rolname=session_user
            AND w.oid='public.public_receipt_witness_revisions'::pg_catalog.regclass
            AND o.oid='public.public_receipt_post_read_observations'::pg_catalog.regclass;
        IF safe_role IS DISTINCT FROM true THEN
          RAISE EXCEPTION 'public_receipt_post_read_role_denied';
        END IF;

        -- Another transaction's uncommitted witness is invisible here.
        SELECT * INTO pinned FROM public.public_receipt_witness_revisions
          WHERE journal_key=NEW.journal_key
            AND revision=NEW.witness_revision
          FOR KEY SHARE NOWAIT;
        IF NOT FOUND OR pinned.transition IS DISTINCT FROM 'append_capture'
          OR pinned.state IS DISTINCT FROM 'idle'
          OR pinned.attempt_outcome IS DISTINCT FROM 'completed_collection'
          OR NEW.witness_record_sha256 IS DISTINCT FROM pinned.record_sha256
          OR NEW.checkpoint_sha256 IS DISTINCT FROM pinned.checkpoint_sha256
          OR NEW.plan_sha256 IS DISTINCT FROM pinned.plan_sha256
          OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               pinned.checkpoint_json,'UTF8')),'hex')
             IS DISTINCT FROM pinned.checkpoint_sha256
        THEN RAISE EXCEPTION 'public_receipt_post_read_witness_denied';
        END IF;
        NEW.capture_sequence :=
          (pinned.checkpoint_json::jsonb->>'sequence')::bigint;
        NEW.capture_head_sha256 :=
          pinned.checkpoint_json::jsonb->>'head_sha256';
        NEW.witness_recorded_at := pinned.recorded_at;
        -- Deliberately sampled after the exact committed witness row read.
        NEW.observed_at := pg_catalog.clock_timestamp();
        IF NEW.capture_sequence NOT BETWEEN 1 AND 1024
          OR NEW.capture_head_sha256 !~ '^[a-f0-9]{64}$'
          OR NEW.observed_at < pinned.recorded_at
        THEN RAISE EXCEPTION 'public_receipt_post_read_order_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.public_receipt_post_read_immutable()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'public_receipt_post_read_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER public_receipt_post_read_insert_guard
      BEFORE INSERT ON public.public_receipt_post_read_observations
      FOR EACH ROW EXECUTE FUNCTION public.public_receipt_post_read_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER public_receipt_post_read_immutable
      BEFORE UPDATE OR DELETE ON public.public_receipt_post_read_observations
      FOR EACH ROW EXECUTE FUNCTION public.public_receipt_post_read_immutable()
    """)
    op.execute("""
      CREATE TRIGGER public_receipt_post_read_no_truncate
      BEFORE TRUNCATE ON public.public_receipt_post_read_observations
      FOR EACH STATEMENT EXECUTE FUNCTION public.public_receipt_post_read_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public.public_receipt_post_read_append(
        p_journal_key text,p_revision bigint,p_record_sha text,
        p_checkpoint_sha text,p_capture_id text,p_plan_sha text,
        p_receipt_sha text,p_rows_sha text,p_v2_sha text)
      RETURNS void AS $$
      BEGIN
        IF p_journal_key !~ '^[a-f0-9]{64}$'
          OR p_revision NOT BETWEEN 3 AND 8192
          OR p_record_sha !~ '^[a-f0-9]{64}$'
          OR p_checkpoint_sha !~ '^[a-f0-9]{64}$'
          OR p_capture_id !~ '^[a-f0-9]{32}$'
          OR p_plan_sha !~ '^[a-f0-9]{64}$'
          OR p_receipt_sha !~ '^[a-f0-9]{64}$'
          OR p_rows_sha !~ '^[a-f0-9]{64}$'
          OR p_v2_sha !~ '^[a-f0-9]{64}$'
        THEN RAISE EXCEPTION 'public_receipt_post_read_identity_denied';
        END IF;
        LOCK TABLE public.public_receipt_witness_revisions
          IN ROW SHARE MODE NOWAIT;
        LOCK TABLE public.public_receipt_post_read_observations
          IN ROW EXCLUSIVE MODE NOWAIT;
        IF NOT pg_catalog.pg_try_advisory_xact_lock(
          ('x'||pg_catalog.substr(p_journal_key,1,16))::bit(64)::bigint)
        THEN RAISE EXCEPTION 'public_receipt_post_read_busy';
        END IF;
        INSERT INTO public.public_receipt_post_read_observations (
          journal_key,witness_revision,witness_record_sha256,
          checkpoint_sha256,capture_id,plan_sha256,receipt_sha256,
          source_rows_sha256,v2_capture_sha256)
        VALUES (p_journal_key,p_revision,p_record_sha,p_checkpoint_sha,
          p_capture_id,p_plan_sha,p_receipt_sha,p_rows_sha,p_v2_sha);
      END; $$ LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.public_receipt_post_read_read(
        p_journal_key text,p_revision bigint)
      RETURNS SETOF public.public_receipt_post_read_observations AS $$
        SELECT * FROM public.public_receipt_post_read_observations
        WHERE journal_key=p_journal_key AND witness_revision=p_revision
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    for name in (
        "public_receipt_post_read_insert_guard()",
        "public_receipt_post_read_immutable()",
        "public_receipt_post_read_append(text,bigint,text,text,text,text,text,text,text)",
        "public_receipt_post_read_read(text,bigint)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC")


def downgrade():
    op.execute("""
      LOCK TABLE public.public_receipt_witness_revisions,
        public.public_receipt_post_read_observations
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.public_receipt_post_read_observations)
        THEN RAISE EXCEPTION 'public_receipt_post_read_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute("""
      DROP FUNCTION public.public_receipt_post_read_append(
        text,bigint,text,text,text,text,text,text,text)
    """)
    op.execute("DROP FUNCTION public.public_receipt_post_read_read(text,bigint)")
    op.drop_table("public_receipt_post_read_observations")
    op.execute("DROP FUNCTION public.public_receipt_post_read_insert_guard()")
    op.execute("DROP FUNCTION public.public_receipt_post_read_immutable()")
