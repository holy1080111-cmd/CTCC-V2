"""Persist a DENY-only, sampled balance-HWM continuation.

Revision ID: 0044
Revises: 0043

This is an index of the existing DB0021 sample population. It neither proves
exchange account genesis nor issues an authenticated account/current-clock
owner. A nonempty index cannot be downgraded or reset to a later genesis.
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

_TABLE = "demo_account_measured_hwm_v2"
_POLICY_SHA256 = "8098bda7733ab7c3e3e814768af7591ad6da8caf605d82b000224f54a2e1c5f0"


def _guard() -> str:
    # The domain and member encoding match account_measured_hwm_continuation.
    # SQL checks the inductive chain and core balance fields against original
    # immutable B1 packet bytes; the repository independently replays the full
    # original B1 semantics before and after commit.
    return f"""
      CREATE FUNCTION public.account_measured_hwm_v2_guard() RETURNS trigger AS $$
      DECLARE b RECORD; source RECORD; packet_source RECORD;
              prior RECORD; genesis RECORD; peak RECORD;
              m JSONB; p JSONB; oldp JSONB; g JSONB; selected JSONB;
              packet JSONB; balance_page JSONB; balance_row JSONB;
              config_page JSONB; config_row JSONB;
              packet_count BIGINT; balance_count BIGINT; balance_index BIGINT;
              config_count BIGINT; finalized_count BIGINT; observed_native BOOLEAN;
              lock_key BIGINT; member TEXT; expected_membership TEXT;
              old_num NUMERIC; old_den NUMERIC; new_num NUMERIC; new_den NUMERIC;
              avail_num NUMERIC; avail_den NUMERIC;
              gcd_left NUMERIC; gcd_right NUMERIC; gcd_remainder NUMERIC;
              expected_peak BIGINT; expected_native BOOLEAN;
              projection_keys TEXT[] := ARRAY[
                'account_complete','account_id','admission',
                'all_sources_recorded_native','durable_membership_verified',
                'environment','execution_authority','genesis_measurement_sha256',
                'genesis_source_reference','head_sha256','last_measured_at',
                'last_measurement_sha256','last_source_reference',
                'membership_sha256','native_clock_verified','peak_equity',
                'peak_measurement_sha256','peak_observed_at','peak_source_reference',
                'policy_sha256','previous_head_sha256',
                'previous_projection_sha256','sample_count','schema_version',
                'settlement_currency','through_sequence',
                'unsampled_intermediate_peak','window_started_at'];
      BEGIN
        lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
        PERFORM pg_advisory_xact_lock(lock_key);
        SELECT * INTO b FROM public.demo_account_observation_batches
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency AND sequence=NEW.sequence
          FOR KEY SHARE;
        SELECT * INTO source FROM public.demo_account_capture_events
          WHERE capture_id=NEW.capture_id AND sequence=NEW.source_sequence FOR KEY SHARE;
        SELECT count(*) INTO packet_count FROM public.demo_account_capture_events
          WHERE capture_id=NEW.capture_id AND packet_payload IS NOT NULL;
        SELECT * INTO packet_source FROM public.demo_account_capture_events
          WHERE capture_id=NEW.capture_id AND packet_payload IS NOT NULL FOR KEY SHARE;
        SELECT * INTO prior FROM public.demo_account_measured_hwm_v2
          WHERE environment=NEW.environment AND account_id=NEW.account_id
            AND settlement_currency=NEW.settlement_currency
          ORDER BY sequence DESC LIMIT 1 FOR KEY SHARE;
        BEGIN
          m := NEW.measurement_json::jsonb;
          p := NEW.projection_json::jsonb;
          oldp := prior.projection_json::jsonb;
          packet := convert_from(packet_source.packet_payload,'UTF8')::jsonb;
          SELECT count(*) INTO balance_count
            FROM jsonb_array_elements(packet->'observations') AS o(value)
            WHERE o.value->'request'->>'stream'='balance';
          SELECT o.value,o.ordinality-1 INTO balance_page,balance_index
            FROM jsonb_array_elements(packet->'observations')
              WITH ORDINALITY AS o(value,ordinality)
            WHERE o.value->'request'->>'stream'='balance' LIMIT 1;
          balance_row := (balance_page->'rows'->0->>'canonical_json')::jsonb;
          SELECT count(*) INTO config_count
            FROM jsonb_array_elements(packet->'observations') AS o(value)
            WHERE o.value->'request'->>'stream'='config_after';
          SELECT o.value INTO config_page
            FROM jsonb_array_elements(packet->'observations') AS o(value)
            WHERE o.value->'request'->>'stream'='config_after' LIMIT 1;
          config_row := (config_page->'rows'->0->>'canonical_json')::jsonb;
          SELECT count(*),bool_and(
            e.event_json::jsonb->'data'->>'tls_provenance'='owned_signed_verified_tls'
            AND COALESCE(e.event_json::jsonb->'data'->>'tls_certificate_sha256' ~ '^[a-f0-9]{{64}}$',false))
            INTO finalized_count,observed_native
            FROM public.demo_account_capture_events AS e
            WHERE e.capture_id=NEW.capture_id
              AND e.event_json::jsonb->>'kind'='raw_finalized';
        EXCEPTION WHEN others THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_document_invalid';
        END;
        IF b.sequence IS NULL OR source.sequence IS NULL
          OR packet_count IS DISTINCT FROM 1::bigint
          OR packet_source.sequence IS NULL
          OR packet_source.event_json::jsonb->>'kind' IS DISTINCT FROM 'packet_recorded'
          OR ROW(packet_source.environment,packet_source.account_id,packet_source.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR encode(sha256(packet_source.packet_payload),'hex')
             IS DISTINCT FROM b.document_json::jsonb->'reference'->>'packet_sha256'
          OR packet->>'plan_sha256' IS DISTINCT FROM b.document_json::jsonb->'reference'->>'plan_sha256'
          OR packet->'plan'->>'expected_uid' IS DISTINCT FROM NEW.account_id
          OR packet->'plan'->>'settlement_currency' IS DISTINCT FROM NEW.settlement_currency
          OR packet->'plan'->>'registration_region' IS DISTINCT FROM 'global'
          OR balance_page->>'environment' IS DISTINCT FROM 'demo'
          OR packet->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR packet->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR balance_count IS DISTINCT FROM 1::bigint
          OR config_count IS DISTINCT FROM 1::bigint
          OR jsonb_typeof(config_page->'rows') IS DISTINCT FROM 'array'
          OR jsonb_array_length(config_page->'rows') IS DISTINCT FROM 1
          OR config_row->>'acctLv' IS DISTINCT FROM '2'
          OR config_row->>'uid' IS DISTINCT FROM NEW.account_id
          OR config_row->>'mainUid' IS DISTINCT FROM m->>'main_uid'
          OR finalized_count IS DISTINCT FROM jsonb_array_length(packet->'observations')::bigint
          OR m->'recorded_native_transport' IS DISTINCT FROM to_jsonb(COALESCE(observed_native,false))
          OR jsonb_typeof(balance_page->'rows') IS DISTINCT FROM 'array'
          OR jsonb_array_length(balance_page->'rows') IS DISTINCT FROM 1
          OR jsonb_typeof(balance_row) IS DISTINCT FROM 'object'
          OR ROW(b.capture_id,b.source_sequence,b.event_sha256,b.previous_sha256)
             IS DISTINCT FROM ROW(NEW.capture_id,NEW.source_sequence,NEW.event_sha256,NEW.previous_event_sha256)
          OR ROW(source.environment,source.account_id,source.settlement_currency)
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR source.event_sha256 IS DISTINCT FROM b.document_json::jsonb->'reference'->>'head_sha256'
          OR source.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
          OR source.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
          OR b.db_recorded_at < source.db_recorded_at
          OR NEW.sequence IS DISTINCT FROM COALESCE(prior.sequence,0)+1
          OR NEW.previous_projection_sha256 IS DISTINCT FROM prior.projection_sha256
          OR NEW.previous_event_sha256 IS DISTINCT FROM prior.event_sha256
          OR NEW.genesis_sequence IS DISTINCT FROM 1::bigint
          OR encode(sha256(convert_to(NEW.measurement_json,'UTF8')),'hex') IS DISTINCT FROM NEW.measurement_sha256
          OR encode(sha256(convert_to(NEW.projection_json,'UTF8')),'hex') IS DISTINCT FROM NEW.projection_sha256
          OR jsonb_typeof(m) IS DISTINCT FROM 'object'
          OR jsonb_typeof(p) IS DISTINCT FROM 'object'
          -- The shared Gate 3 serializer is proved only for ASCII JSON.
          -- B5 is diagnostic: a non-ASCII source fails closed, never gets an
          -- alternate lossy encoding or a newly inferred balance.
          OR public.gate3_schedule_all_ascii(m) IS DISTINCT FROM true
          OR public.gate3_schedule_all_ascii(p) IS DISTINCT FROM true
          OR NEW.measurement_json IS DISTINCT FROM
             public.gate3_schedule_canonical_jsonb(m)
          OR NEW.projection_json IS DISTINCT FROM
             public.gate3_schedule_canonical_jsonb(p)
          OR (p ?& projection_keys) IS DISTINCT FROM true
          OR (p - projection_keys) IS DISTINCT FROM '{{}}'::jsonb
          OR m->>'schema_version' IS DISTINCT FROM 'ctcc.measured_balance.v1'
          OR m->'source_reference' IS DISTINCT FROM b.document_json::jsonb->'reference'
          OR ROW(m->>'environment',m->>'account_id',m->>'settlement_currency')
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR m->'current_source_authority' IS DISTINCT FROM 'false'::jsonb
          OR m IS DISTINCT FROM jsonb_build_object(
               'schema_version','ctcc.measured_balance.v1',
               'source_reference',b.document_json::jsonb->'reference',
               'environment',NEW.environment,'account_id',NEW.account_id,
               'main_uid',config_row->>'mainUid',
               'settlement_currency',NEW.settlement_currency,
               'equity',m->'equity','available_margin',m->'available_margin',
               'request_index',balance_index,'row_ordinal',0,
               'raw_row_sha256',m->>'raw_row_sha256',
               'raw_row_json',balance_page->'rows'->0->>'canonical_json',
               'page_receipt_sha256',balance_page->>'receipt_sha256',
               'body_sha256',balance_page->>'body_sha256',
               'request_started_at',balance_page->>'request_started_at',
               'headers_received_at',balance_page->>'headers_received_at',
               'body_completed_at',balance_page->>'body_completed_at',
               'account_uTime',balance_row->>'uTime',
               'settlement_uTime',balance_row->'details'->0->>'uTime',
               'recorded_native_transport',COALESCE(observed_native,false),
               'current_source_authority',false)
          OR m->'request_index' IS DISTINCT FROM to_jsonb(balance_index)
          OR m->'row_ordinal' IS DISTINCT FROM '0'::jsonb
          OR m->>'raw_row_json' IS DISTINCT FROM balance_page->'rows'->0->>'canonical_json'
          OR m->>'page_receipt_sha256' IS DISTINCT FROM balance_page->>'receipt_sha256'
          OR m->>'body_sha256' IS DISTINCT FROM balance_page->>'body_sha256'
          OR m->>'request_started_at' IS DISTINCT FROM balance_page->>'request_started_at'
          OR m->>'headers_received_at' IS DISTINCT FROM balance_page->>'headers_received_at'
          OR m->>'body_completed_at' IS DISTINCT FROM balance_page->>'body_completed_at'
          OR m->>'account_uTime' IS DISTINCT FROM balance_row->>'uTime'
          OR m->>'settlement_uTime' IS DISTINCT FROM balance_row->'details'->0->>'uTime'
          OR jsonb_typeof(balance_row->'details') IS DISTINCT FROM 'array'
          OR jsonb_array_length(balance_row->'details') IS DISTINCT FROM 1
          OR balance_row->'details'->0->>'ccy' IS DISTINCT FROM NEW.settlement_currency
          OR (balance_row->'details'->0->>'liab')::numeric IS DISTINCT FROM 0::numeric
          OR (balance_row->'details'->0->>'borrowFroz')::numeric IS DISTINCT FROM 0::numeric
          OR jsonb_typeof(m->'raw_row_json') IS DISTINCT FROM 'string'
          OR COALESCE(m->>'raw_row_sha256' ~ '^[a-f0-9]{{64}}$',false) IS NOT TRUE
          OR m->>'raw_row_sha256' IS DISTINCT FROM encode(sha256(convert_to(m->>'raw_row_json','UTF8')),'hex')
          OR jsonb_typeof(m->'recorded_native_transport') IS DISTINCT FROM 'boolean'
          OR jsonb_typeof(m->'equity') IS DISTINCT FROM 'object'
          OR m->'equity' IS DISTINCT FROM jsonb_build_object(
               'numerator',m->'equity'->>'numerator',
               'denominator',m->'equity'->>'denominator')
          OR COALESCE(m->'equity'->>'numerator' ~ '^[1-9][0-9]*$',false) IS NOT TRUE
          OR COALESCE(m->'equity'->>'denominator' ~ '^[1-9][0-9]*$',false) IS NOT TRUE
          OR length(m->'equity'->>'numerator') > 64
          OR length(m->'equity'->>'denominator') > 64
          OR jsonb_typeof(m->'available_margin') IS DISTINCT FROM 'object'
          OR m->'available_margin' IS DISTINCT FROM jsonb_build_object(
               'numerator',m->'available_margin'->>'numerator',
               'denominator',m->'available_margin'->>'denominator')
          OR COALESCE(m->'available_margin'->>'numerator' ~ '^(0|[1-9][0-9]*)$',false) IS NOT TRUE
          OR COALESCE(m->'available_margin'->>'denominator' ~ '^[1-9][0-9]*$',false) IS NOT TRUE
          OR length(m->'available_margin'->>'numerator') > 64
          OR length(m->'available_margin'->>'denominator') > 64
          OR jsonb_typeof(m->'request_started_at') IS DISTINCT FROM 'string'
          OR jsonb_typeof(m->'headers_received_at') IS DISTINCT FROM 'string'
          OR jsonb_typeof(m->'body_completed_at') IS DISTINCT FROM 'string'
          OR p->>'schema_version' IS DISTINCT FROM 'ctcc.measured_balance_hwm_continuation.v2'
          OR p->>'policy_sha256' IS DISTINCT FROM '{_POLICY_SHA256}'
          OR ROW(p->>'environment',p->>'account_id',p->>'settlement_currency')
             IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
          OR p->'sample_count' IS DISTINCT FROM to_jsonb(NEW.sequence)
          OR p->'through_sequence' IS DISTINCT FROM to_jsonb(NEW.sequence)
          OR p->>'head_sha256' IS DISTINCT FROM NEW.event_sha256
          OR p->>'previous_head_sha256' IS DISTINCT FROM NEW.previous_event_sha256
          OR p->>'previous_projection_sha256' IS DISTINCT FROM NEW.previous_projection_sha256
          OR p->>'last_measurement_sha256' IS DISTINCT FROM NEW.measurement_sha256
          OR p->'last_source_reference' IS DISTINCT FROM m->'source_reference'
          OR jsonb_typeof(p->'all_sources_recorded_native') IS DISTINCT FROM 'boolean'
          OR p->'unsampled_intermediate_peak' IS DISTINCT FROM '"unknown"'::jsonb
          OR p->'durable_membership_verified' IS DISTINCT FROM 'false'::jsonb
          OR p->'native_clock_verified' IS DISTINCT FROM 'false'::jsonb
          OR p->'account_complete' IS DISTINCT FROM 'false'::jsonb
          OR p->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR p->>'admission' IS DISTINCT FROM 'DENY' THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_binding_denied';
        END IF;
        new_num := (m->'equity'->>'numerator')::numeric;
        new_den := (m->'equity'->>'denominator')::numeric;
        avail_num := (m->'available_margin'->>'numerator')::numeric;
        avail_den := (m->'available_margin'->>'denominator')::numeric;
        IF new_num <= 0 OR new_den <= 0 OR avail_num < 0 OR avail_den <= 0
          OR new_num IS DISTINCT FROM (balance_row->'details'->0->>'eq')::numeric*new_den
          OR avail_num IS DISTINCT FROM (balance_row->'details'->0->>'availEq')::numeric*avail_den
          OR avail_num*new_den > new_num*avail_den THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_equity_denied';
        END IF;
        gcd_left := new_num; gcd_right := new_den;
        WHILE gcd_right <> 0 LOOP
          gcd_remainder := mod(gcd_left,gcd_right);
          gcd_left := gcd_right; gcd_right := gcd_remainder;
        END LOOP;
        IF gcd_left IS DISTINCT FROM 1::numeric THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_equity_denied';
        END IF;
        gcd_left := avail_num; gcd_right := avail_den;
        WHILE gcd_right <> 0 LOOP
          gcd_remainder := mod(gcd_left,gcd_right);
          gcd_left := gcd_right; gcd_right := gcd_remainder;
        END LOOP;
        IF gcd_left IS DISTINCT FROM 1::numeric THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_available_denied';
        END IF;
        IF prior.sequence IS NULL THEN
          g := m; selected := m;
          expected_peak := NEW.sequence;
          expected_native := (m->>'recorded_native_transport')::boolean;
        ELSE
          SELECT * INTO genesis FROM public.demo_account_measured_hwm_v2
            WHERE environment=NEW.environment AND account_id=NEW.account_id
              AND settlement_currency=NEW.settlement_currency AND sequence=1 FOR KEY SHARE;
          SELECT * INTO peak FROM public.demo_account_measured_hwm_v2
            WHERE environment=NEW.environment AND account_id=NEW.account_id
              AND settlement_currency=NEW.settlement_currency AND sequence=prior.peak_sequence FOR KEY SHARE;
          IF genesis.sequence IS NULL OR peak.sequence IS NULL
            OR oldp->>'membership_sha256' IS NULL
            OR oldp->'genesis_source_reference' IS DISTINCT FROM genesis.measurement_json::jsonb->'source_reference'
            OR oldp->>'genesis_measurement_sha256' IS DISTINCT FROM genesis.measurement_sha256
            OR oldp->'peak_source_reference' IS DISTINCT FROM peak.measurement_json::jsonb->'source_reference'
            OR oldp->>'peak_measurement_sha256' IS DISTINCT FROM peak.measurement_sha256 THEN
            RAISE EXCEPTION 'account_measured_hwm_v2_prior_witness_denied';
          END IF;
          g := genesis.measurement_json::jsonb;
          old_num := (peak.measurement_json::jsonb->'equity'->>'numerator')::numeric;
          old_den := (peak.measurement_json::jsonb->'equity'->>'denominator')::numeric;
          IF old_den <= 0 OR old_num <= 0 THEN
            RAISE EXCEPTION 'account_measured_hwm_v2_equity_denied';
          END IF;
          expected_peak := CASE WHEN new_num*old_den > old_num*new_den
                                THEN NEW.sequence ELSE prior.peak_sequence END;
          selected := CASE WHEN expected_peak=NEW.sequence THEN m
                           ELSE peak.measurement_json::jsonb END;
          expected_native := (oldp->>'all_sources_recorded_native')::boolean
                             AND (m->>'recorded_native_transport')::boolean;
        END IF;
        IF NEW.peak_sequence IS DISTINCT FROM expected_peak
          OR p->>'genesis_measurement_sha256' IS DISTINCT FROM
             CASE WHEN prior.sequence IS NULL THEN NEW.measurement_sha256 ELSE genesis.measurement_sha256 END
          OR p->'genesis_source_reference' IS DISTINCT FROM g->'source_reference'
          OR p->>'window_started_at' IS DISTINCT FROM g->>'body_completed_at'
          OR p->>'peak_measurement_sha256' IS DISTINCT FROM
             CASE WHEN expected_peak=NEW.sequence THEN NEW.measurement_sha256 ELSE peak.measurement_sha256 END
          OR p->'peak_source_reference' IS DISTINCT FROM selected->'source_reference'
          OR p->>'peak_observed_at' IS DISTINCT FROM selected->>'body_completed_at'
          OR p->'peak_equity' IS DISTINCT FROM selected->'equity'
          OR p->>'last_measured_at' IS DISTINCT FROM m->>'body_completed_at'
          OR p->'all_sources_recorded_native' IS DISTINCT FROM to_jsonb(expected_native)
          OR (prior.sequence IS NOT NULL AND
              (m->>'body_completed_at')::timestamptz < (oldp->>'last_measured_at')::timestamptz)
          OR (m->>'request_started_at')::timestamptz > (m->>'headers_received_at')::timestamptz
          OR (m->>'headers_received_at')::timestamptz > (m->>'body_completed_at')::timestamptz THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_selected_peak_denied';
        END IF;
        member := '['||NEW.sequence::text||','||
          COALESCE('"'||NEW.previous_event_sha256||'"','null')||',"'||
          NEW.event_sha256||'","'||NEW.measurement_sha256||'"]';
        expected_membership := encode(sha256(
          convert_to('ctcc.measured_balance_hwm_continuation.v2','UTF8')||decode('00','hex')||
          CASE WHEN prior.sequence IS NULL THEN ''::bytea
               ELSE decode(oldp->>'membership_sha256','hex') END||
          convert_to(member,'UTF8')),'hex');
        IF p->>'membership_sha256' IS DISTINCT FROM expected_membership THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_membership_denied';
        END IF;
        NEW.db_recorded_at := clock_timestamp();
        RETURN NEW;
      END; $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;
    """


def upgrade():
    op.execute("""
      CREATE TABLE public.demo_account_measured_hwm_v2 (
        environment VARCHAR(8) NOT NULL, account_id VARCHAR(32) NOT NULL,
        settlement_currency VARCHAR(16) NOT NULL, sequence BIGINT NOT NULL,
        capture_id VARCHAR(32) NOT NULL, source_sequence BIGINT NOT NULL,
        event_sha256 VARCHAR(64) NOT NULL, previous_event_sha256 VARCHAR(64),
        measurement_sha256 VARCHAR(64) NOT NULL, measurement_json TEXT NOT NULL,
        genesis_sequence BIGINT NOT NULL, peak_sequence BIGINT NOT NULL,
        previous_projection_sha256 VARCHAR(64), projection_sha256 VARCHAR(64) NOT NULL,
        projection_json TEXT NOT NULL,
        account_complete BOOLEAN NOT NULL DEFAULT false,
        execution_authority BOOLEAN NOT NULL DEFAULT false,
        admission VARCHAR(8) NOT NULL DEFAULT 'DENY',
        db_recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_demo_account_measured_hwm_v2 PRIMARY KEY(environment,account_id,settlement_currency,sequence),
        CONSTRAINT fk_hwm_v2_observation FOREIGN KEY(environment,account_id,settlement_currency,sequence)
          REFERENCES public.demo_account_observation_batches(environment,account_id,settlement_currency,sequence) ON DELETE RESTRICT,
        CONSTRAINT fk_hwm_v2_source FOREIGN KEY(capture_id,source_sequence)
          REFERENCES public.demo_account_capture_events(capture_id,sequence) ON DELETE RESTRICT,
        CONSTRAINT ck_demo_account_measured_hwm_v2_scope CHECK(environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'),
        CONSTRAINT ck_demo_account_measured_hwm_v2_sequence CHECK(sequence BETWEEN 1 AND 9223372036854775807 AND source_sequence BETWEEN 1 AND 8192 AND genesis_sequence=1 AND peak_sequence BETWEEN 1 AND sequence),
        CONSTRAINT ck_demo_account_measured_hwm_v2_digests CHECK(event_sha256 ~ '^[a-f0-9]{64}$' AND measurement_sha256 ~ '^[a-f0-9]{64}$' AND projection_sha256 ~ '^[a-f0-9]{64}$' AND ((sequence=1 AND previous_event_sha256 IS NULL AND previous_projection_sha256 IS NULL) OR (sequence>1 AND previous_event_sha256 ~ '^[a-f0-9]{64}$' AND previous_projection_sha256 ~ '^[a-f0-9]{64}$'))),
        CONSTRAINT ck_demo_account_measured_hwm_v2_bytes CHECK(octet_length(measurement_json) BETWEEN 1 AND 1048576 AND octet_length(projection_json) BETWEEN 1 AND 1048576),
        CONSTRAINT ck_demo_account_measured_hwm_v2_no_authority CHECK(account_complete=false AND execution_authority=false AND admission='DENY')
      )
    """)
    op.execute(_guard())
    op.execute(
        "REVOKE ALL ON FUNCTION public.account_measured_hwm_v2_guard() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER account_measured_hwm_v2_guard BEFORE INSERT ON public.demo_account_measured_hwm_v2 FOR EACH ROW EXECUTE FUNCTION public.account_measured_hwm_v2_guard()"
    )
    op.execute(
        "CREATE TRIGGER account_measured_hwm_v2_immutable BEFORE UPDATE OR DELETE ON public.demo_account_measured_hwm_v2 FOR EACH ROW EXECUTE FUNCTION public.qualification_ledger_immutable()"
    )
    op.execute(
        "CREATE TRIGGER account_measured_hwm_v2_no_truncate BEFORE TRUNCATE ON public.demo_account_measured_hwm_v2 FOR EACH STATEMENT EXECUTE FUNCTION public.qualification_ledger_immutable()"
    )


def downgrade():
    op.execute(
        "LOCK TABLE public.demo_account_capture_events, "
        "public.demo_account_observation_batches, "
        "public.demo_account_measured_hwm_v2 IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.demo_account_measured_hwm_v2) THEN
          RAISE EXCEPTION 'account_measured_hwm_v2_downgrade_requires_empty';
        END IF;
      END $$;
    """)
    op.drop_table(_TABLE)
    op.execute("DROP FUNCTION public.account_measured_hwm_v2_guard()")
