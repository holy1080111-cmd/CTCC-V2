"""Permit append-only G12 publication without granting claim UPDATE.

Revision ID: 0057
Revises: 0056

Only the immutable claim read changes. No runtime role receives a grant here.
The successor role profile must be reviewed and independently read back.
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

PREVIOUS_BODY_SHA256 = (
    "419b28c83f950f911a45c5627bc18547869a0ec0c730d964e4ec32426a2f94a1"
)
PUBLICATION_BODY_SHA256 = (
    "ecf3775f2c78f6fee3a70a06ce0036d037a3b29a46f96c51ddd3263ea5aeab97"
)
PUBLICATION_BODY = """
      DECLARE j JSONB; c RECORD; k TEXT;
      BEGIN
        SELECT * INTO c FROM public.g12_invocation_claims WHERE invocation_id=NEW.invocation_id;
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
      END; """
_CLAIM_READ = "SELECT * INTO c FROM public.g12_invocation_claims WHERE invocation_id=NEW.invocation_id;"
_LOCK = (
    "LOCK TABLE public.g12_invocation_claims, "
    "public.g12_invocation_publications IN ACCESS EXCLUSIVE MODE NOWAIT"
)


def _require_definition(body_sha256):
    # Do not replace an altered function or retain an unexpected invoker ACL.
    op.execute(f"""
      DO $check$ BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_language l ON l.oid=p.prolang
          JOIN pg_catalog.pg_class c ON c.oid='public.g12_invocation_claims'::regclass
          JOIN pg_catalog.pg_class d ON d.oid='public.g12_invocation_publications'::regclass
          WHERE p.oid=pg_catalog.to_regprocedure('public.g12_invocation_publication_guard()')
            AND p.proowner=c.relowner AND p.proowner=d.relowner
            AND l.lanname='plpgsql' AND p.prorettype='pg_catalog.trigger'::regtype
            AND p.pronargs=0 AND p.pronargdefaults=0 AND NOT p.proretset
            AND NOT p.prosecdef AND NOT p.proisstrict AND NOT p.proleakproof
            AND p.prokind='f' AND p.provolatile='v' AND p.proparallel='u'
            AND p.prosupport=0
            AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
            AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')='{body_sha256}'
            AND NOT EXISTS (
              SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
              WHERE a.grantee<>p.proowner OR a.grantor<>p.proowner OR a.is_grantable
            )
        ) THEN RAISE EXCEPTION 'g12_successor_definition_changed'; END IF;
      END $check$;
    """)


def _replace(body):
    op.execute(
        "CREATE OR REPLACE FUNCTION public.g12_invocation_publication_guard() "
        "RETURNS trigger AS $body$" + body + "$body$ LANGUAGE plpgsql "
        "SECURITY INVOKER SET search_path=pg_catalog,pg_temp"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.g12_invocation_publication_guard() FROM PUBLIC"
    )


def upgrade():
    op.execute(_LOCK)
    _require_definition(PREVIOUS_BODY_SHA256)
    _replace(PUBLICATION_BODY)
    _require_definition(PUBLICATION_BODY_SHA256)


def downgrade():
    op.execute(_LOCK)
    _require_definition(PUBLICATION_BODY_SHA256)
    # Retained custody is never deleted to permit rollback. Runtime grants must
    # be explicitly revoked before returning to a profile that needs UPDATE.
    op.execute("""
      DO $check$ BEGIN
        IF EXISTS(SELECT 1 FROM public.g12_invocation_claims)
          OR EXISTS(SELECT 1 FROM public.g12_invocation_publications)
          OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
            WHERE c.oid IN ('public.g12_invocation_claims'::regclass,
                            'public.g12_invocation_publications'::regclass)
              AND a.grantee<>c.relowner
          ) THEN RAISE EXCEPTION 'g12_successor_downgrade_requires_empty_ungranted'; END IF;
      END $check$;
    """)
    if PUBLICATION_BODY.count(_CLAIM_READ) != 1:
        raise ValueError("g12_successor_source_changed")
    previous = PUBLICATION_BODY.replace(
        _CLAIM_READ, _CLAIM_READ[:-1] + " FOR KEY SHARE;"
    )
    _replace(previous)
    _require_definition(PREVIOUS_BODY_SHA256)
