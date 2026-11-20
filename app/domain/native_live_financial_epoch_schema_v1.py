"""Additive main0058 Live financial epoch SPEC, no execution grants.

The collector role is separate from the Live intent writer. There is exactly
one prospective controlled epoch per Live UID; no truncate/update/reset API.
Actual historical seven-day cash losses remain present in each observation.
"""

import re

from app.domain import native_live_legacy_safety_v1 as legacy
from app.domain.source_primitives import canonical, sha

HEAD = "0058"
SCHEMA = "ctcc.native_live.financial_epoch_data.v1"
TABLES = ("native_live_financial_epochs_v1", "native_live_financial_samples_v1")
READ_TABLES = (
    "alembic_version",
    "native_live_controls_v1",
    "native_live_reservations_v1",
    "native_live_intents_v1",
    "native_live_transitions_v1",
    *legacy.TABLES,
    *TABLES,
)
POLICY_BYTES = canonical(
    {
        "schema": SCHEMA,
        "environment": "live",
        "currency": "USDT",
        "epoch": "one_prospective_controlled_epoch_per_exact_live_uid_no_reset",
        "genesis": "native_flat_source_plus_stopped_disarmed_control_and_no_prior_local_live_intents_or_reservations",
        "streak": "prospective_controlled_closed_lifecycles_since_measured_genesis",
        "initial_streak": "known_empty_prospective_population_only_not_prior_account_history",
        "peak": "maximum_of_every_durable_original_native_equity_measurement_in_epoch_not_all_time",
        "daily": "UTC_day_signed_fill_cashflow_plus_actual_booked_funding_payment",
        "weekly": "rolling_7d_signed_fill_cashflow_plus_actual_booked_funding_payment",
        "prior_losses": "included_before_genesis_never_reset_or_discarded",
        "funding_accrual": "not_inferred_from_bill_generation",
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)

_EPOCH_TABLE = """CREATE TABLE public.native_live_financial_epochs_v1 (
 epoch_id varchar(64) PRIMARY KEY CHECK(epoch_id ~ '^[a-f0-9]{64}$'),
 environment varchar(4) NOT NULL CHECK(environment='live'),
 account_uid varchar(40) NOT NULL CHECK(account_uid ~ '^[1-9][0-9]{0,39}$'),
 main_uid varchar(40) NOT NULL CHECK(main_uid ~ '^[1-9][0-9]{0,39}$'),
 started_at timestamptz NOT NULL, policy_sha256 varchar(64) NOT NULL,
 epoch_json text NOT NULL CHECK(octet_length(epoch_json) BETWEEN 1 AND 65536),
 epoch_sha256 varchar(64) NOT NULL CHECK(epoch_sha256 ~ '^[a-f0-9]{64}$'),
 UNIQUE(environment,account_uid), CHECK(policy_sha256 ~ '^[a-f0-9]{64}$')
)"""
_SAMPLE_TABLE = """CREATE TABLE public.native_live_financial_samples_v1 (
 epoch_id varchar(64) NOT NULL REFERENCES public.native_live_financial_epochs_v1(epoch_id) ON DELETE RESTRICT,
 sequence bigint NOT NULL CHECK(sequence>0), previous_sha256 varchar(64),
 observed_at timestamptz NOT NULL, source_revision_sha256 varchar(64) NOT NULL,
 equity numeric(60,20) NOT NULL CHECK(equity>0 AND equity::text NOT IN ('NaN','Infinity','-Infinity')),
 sample_json text NOT NULL CHECK(octet_length(sample_json) BETWEEN 1 AND 2097152),
 sample_sha256 varchar(64) NOT NULL CHECK(sample_sha256 ~ '^[a-f0-9]{64}$'),
 PRIMARY KEY(epoch_id,sequence), UNIQUE(epoch_id,source_revision_sha256),
 CHECK(source_revision_sha256 ~ '^[a-f0-9]{64}$'),
 CHECK((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 IS NOT NULL AND previous_sha256 ~ '^[a-f0-9]{64}$'))
)"""
GUARD_SQL = f"""CREATE FUNCTION public.native_live_financial_guard_v1() RETURNS trigger
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE j jsonb; e public.native_live_financial_epochs_v1; p public.native_live_financial_samples_v1;
 BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native_live_financial_append_only'; END IF;
 IF TG_TABLE_NAME='native_live_financial_epochs_v1' THEN
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('ctcc-live-ledger-v1:live:'||NEW.account_uid,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 PERFORM pg_advisory_xact_lock(1680010);
 j:=NEW.epoch_json::jsonb;
 IF NOT ({legacy.CLEAR_PREDICATE_SQL}) OR NEW.environment IS DISTINCT FROM 'live' OR
 NOT EXISTS(SELECT 1 FROM public.native_live_controls_v1 c WHERE c.environment='live' AND c.account_uid=NEW.account_uid AND c.main_uid=NEW.main_uid AND NOT c.armed AND c.emergency_stop AND c.arm_id IS NULL AND c.arm_expires_at IS NULL) OR
 EXISTS(SELECT 1 FROM public.native_live_reservations_v1 WHERE environment='live' AND account_uid=NEW.account_uid) OR
 j->>'schema' IS DISTINCT FROM 'ctcc.native_live.financial_epoch_data.v1' OR
 j->>'kind' IS DISTINCT FROM 'prospective_controlled_genesis' OR
 j->>'environment' IS DISTINCT FROM 'live' OR j->>'account_uid' IS DISTINCT FROM NEW.account_uid OR
 j->>'main_uid' IS DISTINCT FROM NEW.main_uid OR j->>'epoch_id' IS DISTINCT FROM NEW.epoch_id OR
 j->>'policy_sha256' IS DISTINCT FROM NEW.policy_sha256 OR (j->>'started_at')::timestamptz IS DISTINCT FROM NEW.started_at OR
 j->'prospective_seed' IS DISTINCT FROM '0'::jsonb OR
 j->'account_complete' IS DISTINCT FROM 'false'::jsonb OR j->'qualification_verified' IS DISTINCT FROM 'false'::jsonb OR
 j->'native_authentication'->>'schema' IS DISTINCT FROM 'ctcc.native_live.financial_prefix_auth.v1' OR
 NOT coalesce((j->'native_authentication'->>'tag') ~ '^[a-f0-9]{{64}}$',false) OR
 NOT coalesce((j->'native_authentication'->>'key_id') ~ '^[a-f0-9]{{64}}$',false) OR
 j->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR j->>'admission' IS DISTINCT FROM 'DENY' OR
 encode(sha256(convert_to(NEW.epoch_json,'UTF8')),'hex') IS DISTINCT FROM NEW.epoch_sha256
 THEN RAISE EXCEPTION 'native_live_financial_genesis_denied'; END IF;
 ELSIF TG_TABLE_NAME='native_live_financial_samples_v1' THEN
 SELECT * INTO STRICT e FROM public.native_live_financial_epochs_v1 WHERE epoch_id=NEW.epoch_id;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('ctcc-live-ledger-v1:live:'||e.account_uid,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 SELECT * INTO p FROM public.native_live_financial_samples_v1 WHERE epoch_id=NEW.epoch_id ORDER BY sequence DESC LIMIT 1;
 j:=NEW.sample_json::jsonb;
 IF (p.sequence IS NULL AND (NEW.sequence<>1 OR NEW.previous_sha256 IS NOT NULL OR NEW.observed_at<>e.started_at)) OR
 (p.sequence IS NOT NULL AND (NEW.sequence<>p.sequence+1 OR NEW.previous_sha256 IS DISTINCT FROM p.sample_sha256 OR NEW.observed_at<=p.observed_at)) OR
 j->>'schema' IS DISTINCT FROM 'ctcc.native_live.financial_epoch_data.v1' OR
 j->>'epoch_id' IS DISTINCT FROM NEW.epoch_id OR (j->>'sequence')::bigint IS DISTINCT FROM NEW.sequence OR
 j->>'previous_sha256' IS DISTINCT FROM NEW.previous_sha256 OR
 (j->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at OR
 j->>'source_revision_sha256' IS DISTINCT FROM NEW.source_revision_sha256 OR
 (j->>'equity')::numeric IS DISTINCT FROM NEW.equity OR
 j->>'epoch_sha256' IS DISTINCT FROM e.epoch_sha256 OR
 j->'account_complete' IS DISTINCT FROM 'false'::jsonb OR j->'qualification_verified' IS DISTINCT FROM 'false'::jsonb OR
 j->'native_authentication'->>'schema' IS DISTINCT FROM 'ctcc.native_live.financial_prefix_auth.v1' OR
 NOT coalesce((j->'native_authentication'->>'tag') ~ '^[a-f0-9]{{64}}$',false) OR
 NOT coalesce((j->'native_authentication'->>'key_id') ~ '^[a-f0-9]{{64}}$',false) OR
 j->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR j->>'admission' IS DISTINCT FROM 'DENY' OR
 encode(sha256(convert_to(NEW.sample_json,'UTF8')),'hex') IS DISTINCT FROM NEW.sample_sha256
 THEN RAISE EXCEPTION 'native_live_financial_prefix_denied'; END IF;
 ELSE RAISE EXCEPTION 'native_live_financial_table_denied'; END IF;
 RETURN NEW;
 END $body$"""
UPGRADE_SQL = (
    _EPOCH_TABLE,
    _SAMPLE_TABLE,
    GUARD_SQL,
    "REVOKE ALL ON FUNCTION public.native_live_financial_guard_v1() FROM PUBLIC",
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.native_live_financial_guard_v1()"
        for table in TABLES
    ),
)
DOWNGRADE_SQL = (
    """DO $body$ BEGIN IF EXISTS(SELECT 1 FROM public.native_live_financial_epochs_v1) OR EXISTS(SELECT 1 FROM public.native_live_financial_samples_v1) THEN RAISE EXCEPTION 'native_live_financial_retained_prefix_downgrade_denied'; END IF; END $body$""",
    *(
        f"DROP TRIGGER {table}_immutable ON public.{table}"
        for table in reversed(TABLES)
    ),
    "DROP FUNCTION public.native_live_financial_guard_v1()",
    *(f"DROP TABLE public.{table}" for table in reversed(TABLES)),
)


def live_financial_collector_grant_sql_v1(name):
    if (
        type(name) is not str
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is None
        or name == "public"
        or name.startswith("pg_")
    ):
        raise ValueError("native_live_financial_role_refused")
    return (
        f'GRANT USAGE ON SCHEMA public TO "{name}"',
        *(f'GRANT SELECT ON TABLE public.{table} TO "{name}"' for table in READ_TABLES),
        *(f'GRANT INSERT ON TABLE public.{table} TO "{name}"' for table in TABLES),
    )


def live_financial_epoch_spec_sha256_v1():
    return sha(
        canonical(
            {
                "upgrade": UPGRADE_SQL,
                "downgrade": DOWNGRADE_SQL,
                "policy_sha256": POLICY_SHA256,
            }
        )
    )


# Reuse only the deterministic SQL naming parser; no existing ledger grant or
# migration is changed by preparing this independent additive SPEC.
from app.domain.native_live_ledger_schema_v1 import _named_table_spec

_NAMED = tuple(
    _named_table_spec(table, statement)
    for table, statement in zip(TABLES, UPGRADE_SQL[:2], strict=True)
)
TABLE_CHECKS = {table: item[1] for table, item in zip(TABLES, _NAMED, strict=True)}
TABLE_UNIQUES = {table: item[2] for table, item in zip(TABLES, _NAMED, strict=True)}
UPGRADE_SQL = (
    tuple(
        item[0].replace(
            " REFERENCES public.native_live_financial_epochs_v1",
            " CONSTRAINT fk_native_live_financial_samples_v1_epoch REFERENCES public.native_live_financial_epochs_v1",
        )
        for item in _NAMED
    )
    + UPGRADE_SQL[2:]
)
