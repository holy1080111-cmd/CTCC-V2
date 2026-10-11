"""Admit only the reviewed B3 V3 policy and V2 receipt for new observations.

Revision ID: 0036
Revises: 0035

The DB0021 append-only rows and their original V1 receipts are preserved.
Existing V2-policy prefixes cannot be extended by V3; a later explicit epoch
migration is required. Downgrade is refused if a V2 receipt has been stored.
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_POLICY_V3_SHA256 = "bb953fa3ab251c2298eda0643853afcd1d57ae4454e0aab0860f0531f54a6bba"


def _guard(receipt_schema: str, *, policy_pin: bool, require_same_epoch: bool) -> str:
    """Fixed historical SQL; never import mutable application policy at upgrade."""
    policy_check = (
        f"OR j->>'policy_sha256' IS DISTINCT FROM '{_POLICY_V3_SHA256}'"
        if policy_pin
        else ""
    )
    epoch_check = (
        "OR (prior.sequence IS NOT NULL AND "
        "prior.document_json::jsonb->>'policy_sha256' IS DISTINCT FROM "
        "j->>'policy_sha256')"
        if require_same_epoch
        else ""
    )
    return f"""
      CREATE OR REPLACE FUNCTION public.account_observation_batch_guard() RETURNS trigger AS $$
      DECLARE j JSONB; r JSONB; prior RECORD; src RECORD; lock_key BIGINT; prefix TEXT; maximum BIGINT;
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        SELECT * INTO prior FROM public.demo_account_observation_batches WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency ORDER BY sequence DESC LIMIT 1;
        SELECT * INTO src FROM public.demo_account_capture_events WHERE capture_id=NEW.capture_id AND sequence=NEW.source_sequence;
        j := NEW.document_json::jsonb; r := NEW.receipt_json::jsonb;
        IF src.sequence IS NULL
          OR ROW(src.environment,src.account_id,src.settlement_currency) IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR src.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
          OR src.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.account_observation_batch.v1'
          OR j->'reference'->>'capture_id' IS DISTINCT FROM NEW.capture_id
          OR j->'reference'->>'head_sha256' IS DISTINCT FROM src.event_sha256
          OR jsonb_typeof(j->'sequence') IS DISTINCT FROM 'number'
          OR (j->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
          OR j->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256
          OR j->>'receipt_sha256' IS DISTINCT FROM encode(sha256(convert_to(NEW.receipt_json,'UTF8')),'hex')
          OR encode(sha256(convert_to(NEW.document_json,'UTF8')),'hex') IS DISTINCT FROM NEW.event_sha256
          OR r->>'schema_version' IS DISTINCT FROM '{receipt_schema}'
          OR j->>'policy_sha256' IS DISTINCT FROM r->>'policy_sha256'
          {policy_check}
          {epoch_check}
          OR j->'window' IS DISTINCT FROM r->'window'
          OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR j->>'admission' IS DISTINCT FROM 'DENY'
          OR r->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR r->'source_authenticity_verified' IS DISTINCT FROM 'false'::jsonb
          OR r->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR r->>'admission' IS DISTINCT FROM 'DENY'
          OR jsonb_typeof(j->'anchor_json') IS DISTINCT FROM 'string'
          OR jsonb_typeof((j->>'anchor_json')::jsonb) IS DISTINCT FROM 'object'
          OR (j->>'anchor_json')::jsonb IS DISTINCT FROM r->'continuation_anchor'
          OR j->>'anchor_sha256' IS DISTINCT FROM encode(sha256(convert_to(j->>'anchor_json','UTF8')),'hex')
          OR NEW.sequence <> COALESCE(prior.sequence,0)+1
          OR NEW.previous_sha256 IS DISTINCT FROM prior.event_sha256 THEN
            RAISE EXCEPTION 'account_observation_append_denied';
        END IF;
        FOR prefix, maximum IN SELECT * FROM (VALUES ('source',16384::bigint),('coverage',128::bigint),('finding',16384::bigint)) AS limits(prefix,maximum) LOOP
          IF jsonb_typeof(j->(prefix||'_membership_count')) IS DISTINCT FROM 'number'
            OR NOT COALESCE(j->>(prefix||'_membership_count') ~ '^(0|[1-9][0-9]*)$',false)
            OR (j->>(prefix||'_membership_count'))::bigint NOT BETWEEN 0 AND maximum
            OR NOT COALESCE(j->>(prefix||'_membership_sha256') ~ '^[a-f0-9]{64}$',false) THEN
            RAISE EXCEPTION 'account_observation_membership_shape_denied';
          END IF;
        END LOOP;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
    """


def upgrade():
    op.execute(
        "LOCK TABLE public.demo_account_capture_events, public.demo_account_observation_batches "
        "IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(
        _guard(
            "ctcc.observed_account_execution_window.v2",
            policy_pin=True,
            require_same_epoch=True,
        )
    )


def downgrade():
    op.execute(
        "LOCK TABLE public.demo_account_capture_events, public.demo_account_observation_batches "
        "IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (
          SELECT 1 FROM public.demo_account_observation_batches
          WHERE receipt_json::jsonb->>'schema_version'
                = 'ctcc.observed_account_execution_window.v2'
        ) THEN
          RAISE EXCEPTION 'account_observation_policy_downgrade_requires_no_v2_rows';
        END IF;
      END $$
    """)
    op.execute(
        _guard(
            "ctcc.observed_account_execution_window.v1",
            policy_pin=False,
            require_same_epoch=False,
        )
    )
