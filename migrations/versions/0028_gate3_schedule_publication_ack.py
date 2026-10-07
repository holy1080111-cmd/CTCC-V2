"""Durable server observation of an already committed Gate 3 schedule pin.

Revision ID: 0028
Revises: 0027

Only a separate restricted login may append acknowledgements. Its inability to
write a 0026 pin in its own transaction is essential: a row seen by this login
was committed by a different transaction before the server observation time.
No role is provisioned or granted by this migration.
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.gate3_capture_schedule_publication_acks (
        schedule_sha256 VARCHAR(64) NOT NULL,
        seal_sha256 VARCHAR(64) NOT NULL,
        coordinate_plan_sha256 VARCHAR(64) NOT NULL,
        window_key VARCHAR(64) NOT NULL,
        holdout_id VARCHAR(160) NOT NULL,
        window_start TIMESTAMP WITH TIME ZONE NOT NULL,
        window_end TIMESTAMP WITH TIME ZONE NOT NULL,
        schedule_recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
        acknowledged_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_gate3_capture_schedule_publication_acks
          PRIMARY KEY(schedule_sha256),
        CONSTRAINT fk_gate3_schedule_publication_ack_pin
          FOREIGN KEY(schedule_sha256)
          REFERENCES public.gate3_capture_schedule_pins(schedule_sha256),
        CONSTRAINT uq_gate3_capture_schedule_publication_acks_seal_sha256
          UNIQUE(seal_sha256),
        CONSTRAINT uq_gate3_capture_schedule_publication_acks_window_key
          UNIQUE(window_key),
        CONSTRAINT uq_gate3_capture_schedule_publication_acks_holdout_id
          UNIQUE(holdout_id),
        CONSTRAINT ck_gate3_capture_schedule_publication_acks_hashes CHECK (
          schedule_sha256 ~ '^[a-f0-9]{64}$'
          AND seal_sha256 ~ '^[a-f0-9]{64}$'
          AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$'
          AND window_key ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_capture_schedule_publication_acks_time CHECK (
          schedule_recorded_at <= acknowledged_at
          AND acknowledged_at < window_start AND window_start < window_end)
      )
    """)
    op.execute(
        "REVOKE ALL ON TABLE public.gate3_capture_schedule_publication_acks FROM PUBLIC"
    )
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_ack_insert_guard()
      RETURNS trigger AS $$
      DECLARE pinned public.gate3_capture_schedule_pins%ROWTYPE;
              safe_role boolean;
      BEGIN
        -- The session login, rather than the SECURITY DEFINER current_user,
        -- must be unable to have written a pin in this transaction. Reject
        -- switchable role membership as well as inherited privileges.
        SELECT NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                    OR r.rolreplication OR r.rolbypassrls)
          AND NOT pg_catalog.pg_has_role(
            session_user,pg_catalog.pg_get_userbyid(p.relowner),'MEMBER')
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles other_role
            WHERE other_role.oid <> r.oid
              AND pg_catalog.pg_has_role(
                session_user,other_role.oid,'MEMBER'))
          AND NOT (
            pg_catalog.has_table_privilege(session_user,p.oid,
              'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
            OR pg_catalog.has_any_column_privilege(session_user,p.oid,
              'SELECT,INSERT,UPDATE,REFERENCES')
            OR pg_catalog.has_table_privilege(session_user,a.oid,
              'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
            OR pg_catalog.has_any_column_privilege(session_user,a.oid,
              'SELECT,INSERT,UPDATE,REFERENCES')
            OR pg_catalog.has_table_privilege(session_user,w.oid,
              'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
            OR pg_catalog.has_any_column_privilege(session_user,w.oid,
              'SELECT,INSERT,UPDATE,REFERENCES'))
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_append(jsonb)'::pg_catalog.regprocedure,
            'EXECUTE')
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
               pg_catalog.pg_class p,
               pg_catalog.pg_class a,
               pg_catalog.pg_class w
          WHERE r.rolname=session_user
            AND p.oid='public.gate3_capture_schedule_pins'::pg_catalog.regclass
            AND a.oid='public.gate3_capture_schedule_publication_acks'::pg_catalog.regclass
            AND w.oid='public.public_receipt_witness_revisions'::pg_catalog.regclass;
        IF safe_role IS DISTINCT FROM true THEN
          RAISE EXCEPTION 'gate3_schedule_ack_role_denied';
        END IF;

        SELECT * INTO pinned
        FROM public.gate3_capture_schedule_pins
        WHERE schedule_sha256=NEW.schedule_sha256
        FOR KEY SHARE NOWAIT;
        IF NOT FOUND
          OR NEW.seal_sha256 IS DISTINCT FROM pinned.seal_sha256
          OR NEW.coordinate_plan_sha256 IS DISTINCT FROM
             pinned.coordinate_plan_sha256
          OR NEW.window_key IS DISTINCT FROM pinned.window_key
          OR NEW.holdout_id IS DISTINCT FROM pinned.holdout_id
          OR NEW.window_start IS DISTINCT FROM pinned.window_start
          OR NEW.window_end IS DISTINCT FROM pinned.window_end
          OR NEW.schedule_recorded_at IS DISTINCT FROM pinned.recorded_at
          OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               pinned.schedule_json,'UTF8')),'hex')
             IS DISTINCT FROM pinned.schedule_sha256
          OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               pinned.coordinate_plan_json,'UTF8')),'hex')
             IS DISTINCT FROM pinned.coordinate_plan_sha256 THEN
          RAISE EXCEPTION 'gate3_schedule_ack_identity_denied';
        END IF;
        NEW.acknowledged_at := pg_catalog.clock_timestamp();
        IF NEW.acknowledged_at < pinned.recorded_at
          OR NEW.acknowledged_at >= pinned.window_start THEN
          RAISE EXCEPTION 'gate3_schedule_ack_late';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_ack_immutable()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'gate3_schedule_ack_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_capture_schedule_ack_insert_guard
      BEFORE INSERT ON public.gate3_capture_schedule_publication_acks
      FOR EACH ROW EXECUTE FUNCTION
        public.gate3_capture_schedule_ack_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER gate3_capture_schedule_ack_immutable
      BEFORE UPDATE OR DELETE ON public.gate3_capture_schedule_publication_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_capture_schedule_ack_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_capture_schedule_ack_no_truncate
      BEFORE TRUNCATE ON public.gate3_capture_schedule_publication_acks
      FOR EACH STATEMENT EXECUTE FUNCTION
        public.gate3_capture_schedule_ack_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_ack_append(
        p_schedule_sha text,p_seal_sha text,p_coordinate_sha text,
        p_window_key text,p_holdout_id text)
      RETURNS void AS $$
      BEGIN
        IF p_schedule_sha !~ '^[a-f0-9]{64}$' THEN
          RAISE EXCEPTION 'gate3_schedule_ack_identity_denied';
        END IF;
        -- Fail immediately on concurrent DDL, including a downgrade. The row
        -- lock in the trigger likewise never waits for a conflicting writer.
        LOCK TABLE public.gate3_capture_schedule_pins
          IN ROW SHARE MODE NOWAIT;
        LOCK TABLE public.gate3_capture_schedule_publication_acks
          IN ROW EXCLUSIVE MODE NOWAIT;
        IF NOT pg_catalog.pg_try_advisory_xact_lock(
          ('x'||pg_catalog.substr(p_schedule_sha,1,16))::bit(64)::bigint)
        THEN RAISE EXCEPTION 'gate3_schedule_ack_busy';
        END IF;
        INSERT INTO public.gate3_capture_schedule_publication_acks (
          schedule_sha256,seal_sha256,coordinate_plan_sha256,
          window_key,holdout_id,window_start,window_end,schedule_recorded_at
        ) SELECT p.schedule_sha256,p.seal_sha256,p.coordinate_plan_sha256,
                 p.window_key,p.holdout_id,p.window_start,p.window_end,
                 p.recorded_at
          FROM public.gate3_capture_schedule_pins p
          WHERE p.schedule_sha256=p_schedule_sha
            AND p.seal_sha256=p_seal_sha
            AND p.coordinate_plan_sha256=p_coordinate_sha
            AND p.window_key=p_window_key
            AND p.holdout_id=p_holdout_id;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_schedule_ack_identity_denied';
        END IF;
      END; $$ LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_ack_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_publication_acks AS $$
        SELECT * FROM public.gate3_capture_schedule_publication_acks
        WHERE schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    for name in (
        "gate3_capture_schedule_ack_insert_guard()",
        "gate3_capture_schedule_ack_immutable()",
        "gate3_capture_schedule_ack_append(text,text,text,text,text)",
        "gate3_capture_schedule_ack_read(text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC")


def downgrade():
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_publication_acks
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.gate3_capture_schedule_publication_acks)
        THEN RAISE EXCEPTION 'gate3_schedule_ack_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute("""
      DROP FUNCTION public.gate3_capture_schedule_ack_append(
        text,text,text,text,text)
    """)
    op.execute("DROP FUNCTION public.gate3_capture_schedule_ack_read(text)")
    op.drop_table("gate3_capture_schedule_publication_acks")
    op.execute("DROP FUNCTION public.gate3_capture_schedule_ack_insert_guard()")
    op.execute("DROP FUNCTION public.gate3_capture_schedule_ack_immutable()")
