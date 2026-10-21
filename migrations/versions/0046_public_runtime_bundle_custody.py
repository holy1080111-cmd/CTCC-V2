"""Restricted append-only custody of complete current public runtime bytes.

Revision ID: 0046
Revises: 0045

No login or grant is created. This is retention against a restricted application
role, not WORM, protection from a hostile DBA, or historical availability proof.
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

_INSERT_BODY = """
BEGIN
  IF NEW.bundle_id !~ '^[a-f0-9]{32}$'
    OR NEW.bundle_sha256 !~ '^[a-f0-9]{64}$'
    OR NEW.proof_envelope_sha256 !~ '^[a-f0-9]{64}$'
    OR NEW.contract_version <> 1
    OR pg_catalog.octet_length(NEW.bundle_raw) NOT BETWEEN 1 AND 67108864
    OR pg_catalog.encode(pg_catalog.sha256(NEW.bundle_raw),'hex') <> NEW.bundle_sha256
  THEN RAISE EXCEPTION 'runtime_bundle_input_denied'; END IF;
  NEW.recorded_at := pg_catalog.clock_timestamp();
  RETURN NEW;
END;
"""
_IMMUTABLE_BODY = """
BEGIN RAISE EXCEPTION 'runtime_bundle_immutable'; END;
"""
_APPEND_BODY = """
BEGIN
  IF pg_catalog.current_setting('synchronous_commit') <> 'on'
    OR pg_catalog.current_setting('fsync') <> 'on'
    OR pg_catalog.current_setting('full_page_writes') <> 'on'
    OR pg_catalog.pg_is_in_recovery()
  THEN RAISE EXCEPTION 'runtime_bundle_durability_denied'; END IF;
  INSERT INTO public.public_runtime_raw_bundles_v1
    (bundle_id,bundle_sha256,proof_envelope_sha256,bundle_raw,contract_version)
  VALUES (p_bundle_id,p_bundle_sha256,p_proof_envelope_sha256,p_bundle_raw,1);
END;
"""
_READ_BODY = """
  SELECT * FROM public.public_runtime_raw_bundles_v1
  WHERE bundle_id=p_bundle_id AND p_bundle_id ~ '^[a-f0-9]{32}$';
"""


def upgrade():
    op.execute("""
      CREATE TABLE public.public_runtime_raw_bundles_v1 (
        bundle_id VARCHAR(32) NOT NULL,
        bundle_sha256 VARCHAR(64) NOT NULL,
        proof_envelope_sha256 VARCHAR(64) NOT NULL,
        bundle_raw BYTEA NOT NULL,
        contract_version INTEGER NOT NULL DEFAULT 1,
        recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_public_runtime_raw_bundles_v1 PRIMARY KEY(bundle_id),
        CONSTRAINT uq_public_runtime_raw_bundles_v1_bundle_sha256 UNIQUE(bundle_sha256),
        CONSTRAINT ck_public_runtime_raw_bundles_v1_identity CHECK(
          bundle_id ~ '^[a-f0-9]{32}$' AND bundle_sha256 ~ '^[a-f0-9]{64}$'
          AND proof_envelope_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_public_runtime_raw_bundles_v1_content CHECK(
          contract_version=1 AND octet_length(bundle_raw) BETWEEN 1 AND 67108864
          AND encode(sha256(bundle_raw),'hex')=bundle_sha256)
      )
    """)
    op.execute("REVOKE ALL ON TABLE public.public_runtime_raw_bundles_v1 FROM PUBLIC")
    for name, args, returns, language, security, body in (
        ("guard", "", "trigger", "plpgsql", "", _INSERT_BODY),
        ("immutable", "", "trigger", "plpgsql", "", _IMMUTABLE_BODY),
        (
            "append",
            "p_bundle_id TEXT,p_bundle_sha256 TEXT,p_proof_envelope_sha256 TEXT,p_bundle_raw BYTEA",
            "void",
            "plpgsql",
            "SECURITY DEFINER",
            _APPEND_BODY,
        ),
        (
            "read",
            "p_bundle_id TEXT",
            "SETOF public.public_runtime_raw_bundles_v1",
            "sql",
            "STABLE SECURITY DEFINER",
            _READ_BODY,
        ),
    ):
        op.execute(
            f"CREATE FUNCTION public.public_runtime_bundle_v1_{name}({args}) "
            f"RETURNS {returns} LANGUAGE {language} {security} "
            f"SET search_path=pg_catalog,public AS $ctcc${body}$ctcc$"
        )
    for name, events, scope, function in (
        ("insert_guard", "INSERT", "ROW", "guard"),
        ("immutable", "UPDATE OR DELETE", "ROW", "immutable"),
        ("no_truncate", "TRUNCATE", "STATEMENT", "immutable"),
    ):
        op.execute(
            f"CREATE TRIGGER public_runtime_bundle_v1_{name} BEFORE {events} "
            f"ON public.public_runtime_raw_bundles_v1 FOR EACH {scope} "
            f"EXECUTE FUNCTION public.public_runtime_bundle_v1_{function}()"
        )
        op.execute(
            "ALTER TABLE public.public_runtime_raw_bundles_v1 ENABLE ALWAYS TRIGGER "
            f"public_runtime_bundle_v1_{name}"
        )
    for signature in (
        "guard()",
        "immutable()",
        "append(text,text,text,bytea)",
        "read(text)",
    ):
        op.execute(
            "REVOKE ALL ON FUNCTION public.public_runtime_bundle_v1_"
            f"{signature} FROM PUBLIC"
        )


def downgrade():
    op.execute(
        "LOCK TABLE public.public_runtime_raw_bundles_v1 IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.public_runtime_raw_bundles_v1) THEN
          RAISE EXCEPTION 'runtime_bundle_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute("DROP FUNCTION public.public_runtime_bundle_v1_read(text)")
    op.execute(
        "DROP FUNCTION public.public_runtime_bundle_v1_append(text,text,text,bytea)"
    )
    op.execute("DROP TABLE public.public_runtime_raw_bundles_v1")
    op.execute("DROP FUNCTION public.public_runtime_bundle_v1_guard()")
    op.execute("DROP FUNCTION public.public_runtime_bundle_v1_immutable()")
