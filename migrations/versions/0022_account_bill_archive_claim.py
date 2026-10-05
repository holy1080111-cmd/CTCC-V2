"""One-attempt quarterly Demo archive diagnostic tombstone; no request authority.

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE demo_account_bill_archive_claims (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        year INTEGER NOT NULL, quarter INTEGER NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, bill_types VARCHAR(3) NOT NULL,
        expected_main_uid VARCHAR(32) NOT NULL,
        session_binding_id VARCHAR(96) NOT NULL,
        registration_region VARCHAR(16) NOT NULL, origin VARCHAR(128) NOT NULL,
        registration_evidence_sha256 VARCHAR(64) NOT NULL,
        plan_sha256 VARCHAR(64) NOT NULL, apply_scope_sha256 VARCHAR(64) NOT NULL,
        claim_sha256 VARCHAR(64) NOT NULL, claim_json TEXT NOT NULL,
        db_recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_demo_account_bill_archive_claims PRIMARY KEY(environment,account_id,year,quarter),
        CONSTRAINT fk_bill_archive_claim_scope FOREIGN KEY(environment,account_id,settlement_currency)
          REFERENCES qualification_account_scopes(environment,account_id,settlement_currency) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_bill_archive_claims_scope CHECK(environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$' AND expected_main_uid ~ '^[0-9]{1,32}$'),
        CONSTRAINT ck_demo_account_bill_archive_claims_quarter_and_all_types CHECK(year BETWEEN 2021 AND 9998 AND quarter BETWEEN 1 AND 4 AND bill_types='all'),
        CONSTRAINT ck_demo_account_bill_archive_claims_registration_and_session CHECK(registration_region IN ('global','us_au','eea','tr') AND session_binding_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$'),
        CONSTRAINT ck_demo_account_bill_archive_claims_digests CHECK(registration_evidence_sha256 ~ '^[a-f0-9]{64}$' AND plan_sha256 ~ '^[a-f0-9]{64}$' AND apply_scope_sha256 ~ '^[a-f0-9]{64}$' AND claim_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_demo_account_bill_archive_claims_claim_bound CHECK(octet_length(claim_json) BETWEEN 1 AND 32768)
      )
    """)
    op.execute("""
      CREATE FUNCTION account_bill_archive_claim_guard() RETURNS trigger AS $$
      DECLARE j JSONB; p JSONB; e JSONB; d JSONB; expected_origin TEXT; lock_key BIGINT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        j := NEW.claim_json::jsonb;
        p := j->'plan'; e := j->'events'->0; d := e->'data';
        expected_origin := CASE NEW.registration_region
          WHEN 'global' THEN 'https://openapi.okx.com'
          WHEN 'us_au' THEN 'https://us.okx.com'
          WHEN 'eea' THEN 'https://eea.okx.com'
          WHEN 'tr' THEN 'https://tr.okx.com'
          ELSE NULL END;
        IF expected_origin IS NULL OR NEW.origin IS DISTINCT FROM expected_origin
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.demo_bill_archive_diagnostic_journal.v1'
          OR j->>'plan_sha256' IS DISTINCT FROM NEW.plan_sha256
          OR j->>'apply_scope_sha256' IS DISTINCT FROM NEW.apply_scope_sha256
          OR j->>'state' IS DISTINCT FROM 'apply_uncertain'
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR jsonb_typeof(j->'events') IS DISTINCT FROM 'array'
          OR jsonb_array_length(j->'events') IS DISTINCT FROM 1
          OR p->>'environment' IS DISTINCT FROM NEW.environment
          OR p->>'expected_uid' IS DISTINCT FROM NEW.account_id
          OR p->>'expected_main_uid' IS DISTINCT FROM NEW.expected_main_uid
          OR p->>'session_binding_id' IS DISTINCT FROM NEW.session_binding_id
          OR p->>'registration_region' IS DISTINCT FROM NEW.registration_region
          OR p->>'origin' IS DISTINCT FROM NEW.origin
          OR p->>'registration_evidence_sha256' IS DISTINCT FROM NEW.registration_evidence_sha256
          OR (p->>'year')::integer IS DISTINCT FROM NEW.year
          OR (p->>'quarter')::integer IS DISTINCT FROM NEW.quarter
          OR p->>'bill_types' IS DISTINCT FROM NEW.bill_types
          OR e->>'kind' IS DISTINCT FROM 'apply_claimed'
          OR (e->>'index')::integer IS DISTINCT FROM 0
          OR e->>'previous_sha256' IS DISTINCT FROM NEW.plan_sha256
          OR d->>'method' IS DISTINCT FROM 'POST'
          OR d->>'path' IS DISTINCT FROM '/api/v5/account/bills-history-archive'
          OR d->'remote_send_performed' IS DISTINCT FROM 'false'::jsonb
          OR encode(sha256(convert_to(NEW.claim_json,'UTF8')),'hex') IS DISTINCT FROM NEW.claim_sha256
          THEN RAISE EXCEPTION 'account_bill_archive_claim_binding_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """)
    op.execute(
        "CREATE TRIGGER account_bill_archive_claim_guard BEFORE INSERT ON demo_account_bill_archive_claims FOR EACH ROW EXECUTE FUNCTION account_bill_archive_claim_guard()"
    )
    op.execute(
        "CREATE TRIGGER account_bill_archive_claim_immutable BEFORE UPDATE OR DELETE ON demo_account_bill_archive_claims FOR EACH ROW EXECUTE FUNCTION qualification_ledger_immutable()"
    )
    op.execute(
        "CREATE TRIGGER account_bill_archive_claim_no_truncate BEFORE TRUNCATE ON demo_account_bill_archive_claims FOR EACH STATEMENT EXECUTE FUNCTION qualification_ledger_immutable()"
    )


def downgrade():
    op.execute(
        "LOCK TABLE qualification_account_scopes, demo_account_bill_archive_claims IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM demo_account_bill_archive_claims) THEN RAISE EXCEPTION 'account_bill_archive_claim_downgrade_requires_empty'; END IF; END $$"
    )
    op.drop_table("demo_account_bill_archive_claims")
    op.execute("DROP FUNCTION account_bill_archive_claim_guard()")
