"""Bind one existing Demo ledger revision to recorded B1/B3/V7 heads, DENY-only.

Revision ID: 0043
Revises: 0042

This append-only evidence is deliberately not a collector-owned account
revision issuer. Historical caller claims remain unauthenticated. The source
contracts cannot yet prove region, complete economic history, HWM, protection
or a complete PortfolioRiskSnapshot; neither the row nor its presence changes
R6/R7 reservation or transport authority.
"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_TABLE = "demo_account_revision_source_witnesses"


def _guard() -> str:
    return """
      CREATE FUNCTION public.account_revision_source_witness_guard()
      RETURNS trigger AS $$
      DECLARE a RECORD; h RECORD; c RECORD; hf RECORD; cf RECORD;
              b RECORD; v RECORD; j JSONB; q JSONB; lock_key BIGINT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        SELECT * INTO a FROM public.qualification_account_scopes
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency FOR UPDATE;
        SELECT * INTO h FROM public.demo_account_capture_events
          WHERE capture_id=NEW.history_capture_id
            AND sequence=NEW.history_terminal_sequence FOR KEY SHARE;
        SELECT * INTO c FROM public.demo_account_capture_events
          WHERE capture_id=NEW.current_capture_id
            AND sequence=NEW.current_terminal_sequence FOR KEY SHARE;
        SELECT * INTO hf FROM public.demo_account_capture_events
          WHERE capture_id=NEW.history_capture_id AND sequence=1 FOR KEY SHARE;
        SELECT * INTO cf FROM public.demo_account_capture_events
          WHERE capture_id=NEW.current_capture_id AND sequence=1 FOR KEY SHARE;
        SELECT * INTO b FROM public.demo_account_observation_typed_batches
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency
            AND sequence=NEW.typed_sequence FOR KEY SHARE;
        SELECT * INTO v FROM public.demo_account_current_observations
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency
            AND sequence=NEW.current_sequence FOR KEY SHARE;
        BEGIN
          j := NEW.witness_json::jsonb;
          q := NEW.claims_json::jsonb;
        EXCEPTION WHEN others THEN
          RAISE EXCEPTION 'account_revision_witness_document_invalid';
        END;
        IF a.account_revision IS NULL
          OR a.account_revision IS DISTINCT FROM NEW.account_revision
          OR a.ledger_revision IS DISTINCT FROM NEW.ledger_revision
          OR a.claims_sha256 IS DISTINCT FROM NEW.claims_sha256
          OR a.claims_json IS DISTINCT FROM NEW.claims_json
          OR encode(sha256(convert_to(a.claims_json,'UTF8')),'hex')
             IS DISTINCT FROM NEW.claims_sha256
          OR q->'scope'->>'environment' IS DISTINCT FROM NEW.environment
          OR q->'scope'->>'account_id' IS DISTINCT FROM NEW.account_id
          OR q->'scope'->>'settlement_currency' IS DISTINCT FROM NEW.settlement_currency
          OR q->'account'->>'account_id' IS DISTINCT FROM NEW.account_id
          OR q->'account'->>'settlement_currency' IS DISTINCT FROM NEW.settlement_currency
          OR q->'authority'->>'environment' IS DISTINCT FROM NEW.environment
          OR q->'account'->'balance_stamp'->>'account_id' IS DISTINCT FROM NEW.account_id
          OR q->'account'->'positions_stamp'->>'account_id' IS DISTINCT FROM NEW.account_id
          OR q->'account'->'history_stamp'->>'account_id' IS DISTINCT FROM NEW.account_id
          OR q->'account'->'reservations_stamp'->>'account_id' IS DISTINCT FROM NEW.account_id
          OR q->'authority'->'stamp'->>'account_id' IS DISTINCT FROM NEW.account_id
          OR h.sequence IS NULL OR c.sequence IS NULL
          OR hf.sequence IS NULL OR cf.sequence IS NULL
          OR b.sequence IS NULL OR v.sequence IS NULL
          OR ROW(h.environment,h.account_id,h.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR ROW(c.environment,c.account_id,c.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR ROW(hf.environment,hf.account_id,hf.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR ROW(cf.environment,cf.account_id,cf.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR h.event_sha256 IS DISTINCT FROM NEW.history_head_sha256
          OR c.event_sha256 IS DISTINCT FROM NEW.current_head_sha256
          OR b.event_sha256 IS DISTINCT FROM NEW.typed_head_sha256
          OR v.event_sha256 IS DISTINCT FROM NEW.current_index_sha256
          OR b.capture_id IS DISTINCT FROM h.capture_id
          OR b.source_sequence IS DISTINCT FROM h.sequence
          OR v.capture_id IS DISTINCT FROM c.capture_id
          OR v.source_sequence IS DISTINCT FROM c.sequence
          OR h.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
          OR c.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
          OR h.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
          OR c.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
          OR hf.event_json::jsonb->'data'->'plan'->>'contract_version'
             IS DISTINCT FROM 'ctcc.demo_account_plan.v5'
          OR cf.event_json::jsonb->'data'->'plan'->>'contract_version'
             IS DISTINCT FROM 'ctcc.demo_current_account_plan.v7'
          OR hf.event_json::jsonb->'data'->'plan'->>'environment'
             IS DISTINCT FROM NEW.environment
          OR cf.event_json::jsonb->'data'->'plan'->>'environment'
             IS DISTINCT FROM NEW.environment
          OR hf.event_json::jsonb->'data'->'plan'->>'expected_uid'
             IS DISTINCT FROM NEW.account_id
          OR cf.event_json::jsonb->'data'->'plan'->>'expected_uid'
             IS DISTINCT FROM NEW.account_id
          OR hf.event_json::jsonb->'data'->'plan'->>'settlement_currency'
             IS DISTINCT FROM NEW.settlement_currency
          OR cf.event_json::jsonb->'data'->'plan'->>'settlement_currency'
             IS DISTINCT FROM NEW.settlement_currency
          OR hf.event_json::jsonb->'data'->'plan'->>'expected_main_uid'
             IS DISTINCT FROM cf.event_json::jsonb->'data'->'plan'->>'expected_main_uid'
          OR hf.event_json::jsonb->'data'->'plan'->>'session_binding_id'
             IS DISTINCT FROM cf.event_json::jsonb->'data'->'plan'->>'session_binding_id'
          OR hf.event_json::jsonb->'data'->>'session_binding_sha256'
             IS DISTINCT FROM cf.event_json::jsonb->'data'->>'session_binding_sha256'
          OR hf.event_json::jsonb->'data'->'plan'->>'registration_region'
             IS DISTINCT FROM cf.event_json::jsonb->'data'->'plan'->>'registration_region'
          OR COALESCE(hf.event_json::jsonb->'data'->'plan'->>'registration_region','')
             NOT IN ('global','us_au','eea','tr')
          OR hf.event_json::jsonb->'data'->'plan'->>'origin'
             IS DISTINCT FROM (CASE hf.event_json::jsonb->'data'->'plan'->>'registration_region'
               WHEN 'global' THEN 'https://openapi.okx.com'
               WHEN 'us_au' THEN 'https://us.okx.com'
               WHEN 'eea' THEN 'https://eea.okx.com'
               WHEN 'tr' THEN 'https://tr.okx.com'
             END)
          OR cf.event_json::jsonb->'data'->'plan'->>'registration_region'
             IS DISTINCT FROM hf.event_json::jsonb->'data'->'plan'->>'registration_region'
          OR hf.event_json::jsonb->'data'->'plan'->>'registration_evidence_sha256'
             IS DISTINCT FROM cf.event_json::jsonb->'data'->'plan'->>'registration_evidence_sha256'
          OR hf.event_json::jsonb->'data'->'plan'->>'origin'
             IS DISTINCT FROM cf.event_json::jsonb->'data'->'plan'->>'origin'
          OR h.db_recorded_at >= cf.db_recorded_at
          OR b.db_recorded_at < h.db_recorded_at
          OR v.db_recorded_at < c.db_recorded_at
          OR j IS NULL OR jsonb_typeof(j) IS DISTINCT FROM 'object'
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.demo_account_revision_source_witness.v1'
          OR j->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR j->'account_revision' IS DISTINCT FROM to_jsonb(NEW.account_revision)
          OR j->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
          OR j->>'claims_sha256' IS DISTINCT FROM NEW.claims_sha256
          OR j->'history'->>'capture_id' IS DISTINCT FROM NEW.history_capture_id
          OR j->'history'->'terminal_sequence' IS DISTINCT FROM to_jsonb(NEW.history_terminal_sequence)
          OR j->'history'->>'head_sha256' IS DISTINCT FROM NEW.history_head_sha256
          OR j->'history'->'typed_sequence' IS DISTINCT FROM to_jsonb(NEW.typed_sequence)
          OR j->'history'->>'typed_head_sha256' IS DISTINCT FROM NEW.typed_head_sha256
          OR j->'current'->>'capture_id' IS DISTINCT FROM NEW.current_capture_id
          OR j->'current'->'terminal_sequence' IS DISTINCT FROM to_jsonb(NEW.current_terminal_sequence)
          OR j->'current'->>'head_sha256' IS DISTINCT FROM NEW.current_head_sha256
          OR j->'current'->'index_sequence' IS DISTINCT FROM to_jsonb(NEW.current_sequence)
          OR j->'current'->>'index_sha256' IS DISTINCT FROM NEW.current_index_sha256
          OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR j->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR j->'blocking_reasons' IS DISTINCT FROM jsonb_build_array(
               'account_revision_is_caller_claim_not_collector_issued',
               'complete_portfolio_snapshot_unavailable',
               'funding_accrual_and_complete_history_unproven',
               'native_clock_and_credential_session_unverified',
               'registered_region_unverified',
               'risk_reservation_and_execution_recheck_unperformed'
             )
          OR encode(sha256(convert_to(NEW.witness_json,'UTF8')),'hex')
             IS DISTINCT FROM NEW.witness_sha256 THEN
          RAISE EXCEPTION 'account_revision_witness_binding_denied';
        END IF;
        NEW.db_recorded_at := clock_timestamp();
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """


def upgrade():
    op.create_table(
        _TABLE,
        sa.Column("witness_sha256", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(8), nullable=False),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("settlement_currency", sa.String(16), nullable=False),
        sa.Column("account_revision", sa.BigInteger, nullable=False),
        sa.Column("ledger_revision", sa.BigInteger, nullable=False),
        sa.Column("claims_sha256", sa.String(64), nullable=False),
        sa.Column("claims_json", sa.Text, nullable=False),
        sa.Column("history_capture_id", sa.String(32), nullable=False),
        sa.Column("history_terminal_sequence", sa.BigInteger, nullable=False),
        sa.Column("history_head_sha256", sa.String(64), nullable=False),
        sa.Column("typed_sequence", sa.BigInteger, nullable=False),
        sa.Column("typed_head_sha256", sa.String(64), nullable=False),
        sa.Column("current_capture_id", sa.String(32), nullable=False),
        sa.Column("current_terminal_sequence", sa.BigInteger, nullable=False),
        sa.Column("current_head_sha256", sa.String(64), nullable=False),
        sa.Column("current_sequence", sa.BigInteger, nullable=False),
        sa.Column("current_index_sha256", sa.String(64), nullable=False),
        sa.Column("witness_json", sa.Text, nullable=False),
        sa.Column(
            "account_complete", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "source_authenticity_verified",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "execution_authority", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("admission", sa.String(8), nullable=False, server_default="DENY"),
        sa.Column(
            "db_recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency"],
            [
                "qualification_account_scopes.environment",
                "qualification_account_scopes.account_id",
                "qualification_account_scopes.settlement_currency",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_scope",
        ),
        sa.ForeignKeyConstraint(
            ["history_capture_id", "history_terminal_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_history_terminal",
        ),
        sa.ForeignKeyConstraint(
            ["current_capture_id", "current_terminal_sequence"],
            [
                "demo_account_capture_events.capture_id",
                "demo_account_capture_events.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_current_terminal",
        ),
        sa.ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "typed_sequence"],
            [
                "demo_account_observation_typed_batches.environment",
                "demo_account_observation_typed_batches.account_id",
                "demo_account_observation_typed_batches.settlement_currency",
                "demo_account_observation_typed_batches.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_typed",
        ),
        sa.ForeignKeyConstraint(
            ["environment", "account_id", "settlement_currency", "current_sequence"],
            [
                "demo_account_current_observations.environment",
                "demo_account_current_observations.account_id",
                "demo_account_current_observations.settlement_currency",
                "demo_account_current_observations.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_account_revision_source_current",
        ),
        sa.UniqueConstraint(
            "environment",
            "account_id",
            "settlement_currency",
            "account_revision",
            name="uq_account_revision_source_exact_revision",
        ),
        sa.CheckConstraint(
            "environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'",
            name=op.f("ck_demo_account_revision_source_witnesses_scope"),
        ),
        sa.CheckConstraint(
            "account_revision >= 1 AND ledger_revision >= account_revision AND typed_sequence >= 1 AND current_sequence >= 1 AND history_terminal_sequence >= 1 AND current_terminal_sequence >= 1",
            name=op.f("ck_demo_account_revision_source_witnesses_revisions"),
        ),
        sa.CheckConstraint(
            "claims_sha256 ~ '^[a-f0-9]{64}$' AND history_head_sha256 ~ '^[a-f0-9]{64}$' AND current_head_sha256 ~ '^[a-f0-9]{64}$' AND typed_head_sha256 ~ '^[a-f0-9]{64}$' AND current_index_sha256 ~ '^[a-f0-9]{64}$' AND witness_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_demo_account_revision_source_witnesses_digests"),
        ),
        sa.CheckConstraint(
            "history_capture_id ~ '^[a-f0-9]{32}$' AND current_capture_id ~ '^[a-f0-9]{32}$' AND history_capture_id <> current_capture_id",
            name=op.f("ck_demo_account_revision_source_witnesses_capture_ids"),
        ),
        sa.CheckConstraint(
            "account_complete=false AND source_authenticity_verified=false AND execution_authority=false AND admission='DENY'",
            name=op.f("ck_demo_account_revision_source_witnesses_no_authority"),
        ),
        sa.CheckConstraint(
            "octet_length(witness_json) BETWEEN 1 AND 262144",
            name=op.f("ck_demo_account_revision_source_witnesses_witness_bound"),
        ),
        sa.CheckConstraint(
            "octet_length(claims_json) BETWEEN 1 AND 8388608",
            name=op.f("ck_demo_account_revision_source_witnesses_claims_bound"),
        ),
    )
    op.execute(_guard())
    op.execute(
        "REVOKE ALL ON FUNCTION public.account_revision_source_witness_guard() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER account_revision_source_witness_guard BEFORE INSERT ON public.demo_account_revision_source_witnesses FOR EACH ROW EXECUTE FUNCTION public.account_revision_source_witness_guard()"
    )
    op.execute(
        "CREATE TRIGGER account_revision_source_witness_immutable BEFORE UPDATE OR DELETE ON public.demo_account_revision_source_witnesses FOR EACH ROW EXECUTE FUNCTION public.qualification_ledger_immutable()"
    )
    op.execute(
        "CREATE TRIGGER account_revision_source_witness_no_truncate BEFORE TRUNCATE ON public.demo_account_revision_source_witnesses FOR EACH STATEMENT EXECUTE FUNCTION public.qualification_ledger_immutable()"
    )


def downgrade():
    op.execute(
        "LOCK TABLE public.demo_account_revision_source_witnesses IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_account_revision_source_witnesses) THEN
          RAISE EXCEPTION 'account_revision_source_witness_downgrade_requires_empty';
        END IF;
      END $$;
    """)
    op.drop_table(_TABLE)
    op.execute("DROP FUNCTION public.account_revision_source_witness_guard()")
