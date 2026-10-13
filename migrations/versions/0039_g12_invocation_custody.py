"""Append-only pre-G12 invocation claim and later publication observation.

Revision ID: 0039
Revises: 0038

This diagnostic journal grants no source authenticity or execution permission.
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.g12_invocation_claims (
        invocation_id VARCHAR(32) NOT NULL,
        environment VARCHAR(8) NOT NULL,
        account_uid_sha256 VARCHAR(64) NOT NULL,
        event_key VARCHAR(64) NOT NULL,
        claim_json TEXT NOT NULL,
        claim_sha256 VARCHAR(64) NOT NULL,
        db_claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_g12_invocation_claims PRIMARY KEY(invocation_id),
        CONSTRAINT uq_g12_claim_uid_event UNIQUE(environment,account_uid_sha256,event_key),
        CONSTRAINT ck_g12_invocation_claims_demo_only CHECK(environment='demo'),
        CONSTRAINT ck_g12_invocation_claims_identity CHECK(
          invocation_id ~ '^[a-f0-9]{32}$' AND
          account_uid_sha256 ~ '^[a-f0-9]{64}$' AND
          event_key ~ '^[a-f0-9]{64}$' AND
          claim_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_g12_invocation_claims_bounded CHECK(octet_length(claim_json) BETWEEN 1 AND 8192)
      )
    """)
    op.execute("""
      CREATE TABLE public.g12_invocation_publications (
        invocation_id VARCHAR(32) NOT NULL,
        claim_sha256 VARCHAR(64) NOT NULL,
        publication_json TEXT NOT NULL,
        publication_sha256 VARCHAR(64) NOT NULL,
        db_recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_g12_invocation_publications PRIMARY KEY(invocation_id),
        CONSTRAINT fk_g12_invocation_publications_invocation_id_g12_invocation_claims FOREIGN KEY(invocation_id) REFERENCES public.g12_invocation_claims(invocation_id) ON DELETE RESTRICT,
        CONSTRAINT ck_g12_invocation_publications_hashes CHECK(
          claim_sha256 ~ '^[a-f0-9]{64}$' AND publication_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_g12_invocation_publications_bounded CHECK(octet_length(publication_json) BETWEEN 1 AND 8192)
      )
    """)
    op.execute("""
      CREATE FUNCTION public.g12_invocation_claim_guard() RETURNS trigger AS $$
      DECLARE j JSONB;
      BEGIN
        NEW.db_claimed_at := clock_timestamp();
        j := NEW.claim_json::jsonb;
        IF encode(sha256(convert_to(NEW.claim_json,'UTF8')),'hex') IS DISTINCT FROM NEW.claim_sha256
          OR (SELECT count(*) FROM jsonb_object_keys(j)) IS DISTINCT FROM 21::bigint
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.g12.invocation_claim.v1'
          OR j->>'invocation_id' IS DISTINCT FROM NEW.invocation_id
          OR j->>'environment' IS DISTINCT FROM NEW.environment
          OR j->>'account_uid_sha256' IS DISTINCT FROM NEW.account_uid_sha256
          OR j->>'event_key' IS DISTINCT FROM NEW.event_key
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR j->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR j->>'report_id' IS NULL OR j->>'instrument_id' IS NULL
          OR COALESCE(j->>'direction','') NOT IN ('long','short')
          OR j->>'entry' IS NULL OR j->>'stop_loss' IS NULL OR j->>'take_profit' IS NULL
          OR j->>'deadline' IS NULL
          OR COALESCE(j->>'account_plan_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->>'candidate_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->>'original_run_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->>'original_market_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->>'original_inputs_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->>'evidence_root_sha256','') !~ '^[a-f0-9]{64}$'
          OR (j->>'deadline')::timestamptz <= NEW.db_claimed_at THEN
          RAISE EXCEPTION 'g12_claim_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """)
    op.execute("REVOKE ALL ON FUNCTION public.g12_invocation_claim_guard() FROM PUBLIC")
    op.execute("""
      CREATE FUNCTION public.g12_invocation_publication_guard() RETURNS trigger AS $$
      DECLARE j JSONB; c RECORD; k TEXT;
      BEGIN
        SELECT * INTO c FROM public.g12_invocation_claims WHERE invocation_id=NEW.invocation_id FOR KEY SHARE;
        NEW.db_recorded_at := clock_timestamp();
        j := NEW.publication_json::jsonb;
        IF c.invocation_id IS NULL
          OR (SELECT count(*) FROM jsonb_object_keys(j)) IS DISTINCT FROM 13::bigint
          OR NEW.claim_sha256 IS DISTINCT FROM c.claim_sha256
          OR NEW.db_recorded_at < c.db_claimed_at
          OR encode(sha256(convert_to(NEW.publication_json,'UTF8')),'hex') IS DISTINCT FROM NEW.publication_sha256
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.g12.invocation_publication.v1'
          OR j->>'invocation_id' IS DISTINCT FROM NEW.invocation_id
          OR j->>'claim_sha256' IS DISTINCT FROM c.claim_sha256
          OR j->>'evidence_root_sha256' IS DISTINCT FROM c.claim_json::jsonb->>'evidence_root_sha256'
          OR j->>'candidate_sha256' IS DISTINCT FROM c.claim_json::jsonb->>'candidate_sha256'
          OR j->>'event_key' IS DISTINCT FROM c.event_key
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR j->'publication_provenance_verified' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR COALESCE(j->>'evidence_sha256','') !~ '^[a-f0-9]{64}$'
          OR COALESCE(j->>'report_sha256','') !~ '^[a-f0-9]{64}$'
          OR jsonb_typeof(j->'file_sha256') IS DISTINCT FROM 'object'
          OR (SELECT count(*) FROM jsonb_object_keys(j->'file_sha256')) IS DISTINCT FROM 6::bigint
          OR j->>'publication_completed_at' IS NULL
          OR (j->>'publication_completed_at')::timestamptz <= c.db_claimed_at
          OR (j->>'publication_completed_at')::timestamptz > NEW.db_recorded_at
          OR (j->>'publication_completed_at')::timestamptz >= (c.claim_json::jsonb->>'deadline')::timestamptz THEN
          RAISE EXCEPTION 'g12_publication_denied';
        END IF;
        FOREACH k IN ARRAY ARRAY['4h.png','1h.png','15m.png','5m.png','summary.png','report.json'] LOOP
          IF COALESCE(j->'file_sha256'->>k,'') !~ '^[a-f0-9]{64}$' THEN
            RAISE EXCEPTION 'g12_publication_file_denied';
          END IF;
        END LOOP;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.g12_invocation_publication_guard() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER g12_claim_guard BEFORE INSERT ON public.g12_invocation_claims FOR EACH ROW EXECUTE FUNCTION public.g12_invocation_claim_guard()"
    )
    op.execute(
        "CREATE TRIGGER g12_publication_guard BEFORE INSERT ON public.g12_invocation_publications FOR EACH ROW EXECUTE FUNCTION public.g12_invocation_publication_guard()"
    )
    for table in ("g12_invocation_claims", "g12_invocation_publications"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.qualification_ledger_immutable()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.qualification_ledger_immutable()"
        )


def downgrade():
    op.execute(
        "LOCK TABLE public.g12_invocation_claims, public.g12_invocation_publications IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.g12_invocation_claims)
          OR EXISTS(SELECT 1 FROM public.g12_invocation_publications) THEN
          RAISE EXCEPTION 'g12_custody_downgrade_requires_empty';
        END IF;
      END $$;
    """)
    op.execute("DROP TABLE public.g12_invocation_publications")
    op.execute("DROP TABLE public.g12_invocation_claims")
    op.execute("DROP FUNCTION public.g12_invocation_publication_guard()")
    op.execute("DROP FUNCTION public.g12_invocation_claim_guard()")
