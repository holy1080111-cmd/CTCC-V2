"""Claim only canonical Gate 3 schedule keys and acknowledge committed claims.

Revision ID: 0029
Revises: 0028

The 0026 and 0028 tables remain an immutable raw audit. Legacy rows acquire no
claim implicitly: their bytes and commit history cannot be certified by this
migration. Legacy canonical/unknown keys are reserved, while a provably
noncanonical raw row cannot block a canonical retry.
New function grants must be provisioned to separate restricted logins.
The old publisher function OID remains granted but is closed; the new claim
append entrypoint and raw AFTER INSERT trigger both check the new login.
The claim proves schedule byte canonicality under the 0026 insert guard; the
database has no independent preregistration seal ledger and cannot attest that
the referenced seal hash is genuine. Python readback still checks that join.
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    # Do not wait behind a concurrent raw append or ACK while changing the
    # uniqueness boundary. Alembic runs the whole PostgreSQL migration in one
    # transaction, so readers never see a partially installed boundary.
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_publication_acks
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      CREATE TABLE public.gate3_capture_schedule_key_claims (
        schedule_sha256 VARCHAR(64) NOT NULL,
        seal_sha256 VARCHAR(64) NOT NULL,
        coordinate_plan_sha256 VARCHAR(64) NOT NULL,
        window_key VARCHAR(64) NOT NULL,
        holdout_id VARCHAR(160) NOT NULL,
        window_start TIMESTAMPTZ NOT NULL,
        window_end TIMESTAMPTZ NOT NULL,
        schedule_recorded_at TIMESTAMPTZ NOT NULL,
        claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_gate3_capture_schedule_key_claims
          PRIMARY KEY(schedule_sha256),
        CONSTRAINT fk_gate3_schedule_claim_pin FOREIGN KEY(schedule_sha256)
          REFERENCES public.gate3_capture_schedule_pins(schedule_sha256),
        CONSTRAINT uq_gate3_capture_schedule_key_claims_seal_sha256
          UNIQUE(seal_sha256),
        CONSTRAINT uq_gate3_capture_schedule_key_claims_window_key
          UNIQUE(window_key),
        CONSTRAINT uq_gate3_capture_schedule_key_claims_holdout_id
          UNIQUE(holdout_id),
        CONSTRAINT ck_gate3_capture_schedule_key_claims_hashes CHECK (
          schedule_sha256 ~ '^[a-f0-9]{64}$'
          AND seal_sha256 ~ '^[a-f0-9]{64}$'
          AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$'
          AND window_key ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_capture_schedule_key_claims_times CHECK (
          schedule_recorded_at <= claimed_at
          AND claimed_at < window_start AND window_start < window_end)
      )
    """)
    op.execute(
        "REVOKE ALL ON TABLE public.gate3_capture_schedule_key_claims FROM PUBLIC"
    )
    op.execute("""
      CREATE TABLE public.gate3_capture_schedule_claim_acks (
        schedule_sha256 VARCHAR(64) NOT NULL,
        seal_sha256 VARCHAR(64) NOT NULL,
        coordinate_plan_sha256 VARCHAR(64) NOT NULL,
        window_key VARCHAR(64) NOT NULL,
        holdout_id VARCHAR(160) NOT NULL,
        window_start TIMESTAMPTZ NOT NULL,
        window_end TIMESTAMPTZ NOT NULL,
        schedule_recorded_at TIMESTAMPTZ NOT NULL,
        claim_recorded_at TIMESTAMPTZ NOT NULL,
        acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_gate3_capture_schedule_claim_acks
          PRIMARY KEY(schedule_sha256),
        CONSTRAINT fk_gate3_schedule_claim_ack_claim
          FOREIGN KEY(schedule_sha256)
          REFERENCES public.gate3_capture_schedule_key_claims(schedule_sha256),
        CONSTRAINT uq_gate3_capture_schedule_claim_acks_seal_sha256
          UNIQUE(seal_sha256),
        CONSTRAINT uq_gate3_capture_schedule_claim_acks_window_key
          UNIQUE(window_key),
        CONSTRAINT uq_gate3_capture_schedule_claim_acks_holdout_id
          UNIQUE(holdout_id),
        CONSTRAINT ck_gate3_capture_schedule_claim_acks_hashes CHECK (
          schedule_sha256 ~ '^[a-f0-9]{64}$'
          AND seal_sha256 ~ '^[a-f0-9]{64}$'
          AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$'
          AND window_key ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_capture_schedule_claim_acks_times CHECK (
          schedule_recorded_at <= claim_recorded_at
          AND claim_recorded_at <= acknowledged_at
          AND acknowledged_at < window_start AND window_start < window_end)
      )
    """)
    op.execute(
        "REVOKE ALL ON TABLE public.gate3_capture_schedule_claim_acks FROM PUBLIC"
    )
    op.execute("""
      CREATE TABLE public.gate3_capture_schedule_legacy_inventory (
        schedule_sha256 VARCHAR(64) NOT NULL,
        seal_sha256 VARCHAR(64) NOT NULL,
        window_key VARCHAR(64) NOT NULL,
        holdout_id VARCHAR(160) NOT NULL,
        classification VARCHAR(16) NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_gate3_capture_schedule_legacy_inventory
          PRIMARY KEY(schedule_sha256),
        CONSTRAINT fk_gate3_schedule_legacy_inventory_pin
          FOREIGN KEY(schedule_sha256)
          REFERENCES public.gate3_capture_schedule_pins(schedule_sha256),
        CONSTRAINT ck_gate3_capture_schedule_legacy_inventory_classification
          CHECK (classification IN ('canonical','noncanonical','unknown'))
      )
    """)
    op.execute(
        "REVOKE ALL ON TABLE public.gate3_capture_schedule_legacy_inventory FROM PUBLIC"
    )
    for name, column in (
        ("seal", "seal_sha256"),
        ("window", "window_key"),
        ("holdout", "holdout_id"),
    ):
        op.execute(f"""
          CREATE UNIQUE INDEX uq_gate3_schedule_legacy_reserved_{name}
          ON public.gate3_capture_schedule_legacy_inventory({column})
          WHERE classification <> 'noncanonical'
        """)

    op.execute("""
      CREATE FUNCTION public.gate3_schedule_exact_keys(p jsonb, keys text[])
      RETURNS boolean AS $$
      BEGIN
        RETURN pg_catalog.jsonb_typeof(p) = 'object'
          AND p ?& keys AND (p - keys) = '{}'::jsonb;
      END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    # A deliberately small JSON serializer: every accepted string below is
    # restricted to ASCII. This matches both Python canonical encoders for the
    # admitted schedule/coordinate/plan schemas, including sorted keys and no
    # spaces. Unknown fields and non-integer JSON numerics are rejected first.
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_canonical_jsonb(p jsonb)
      RETURNS text AS $$
      DECLARE kind text; result text;
      BEGIN
        kind := pg_catalog.jsonb_typeof(p);
        IF kind = 'object' THEN
          SELECT '{' || coalesce(pg_catalog.string_agg(
            pg_catalog.to_json(item.key)::text || ':' ||
              public.gate3_schedule_canonical_jsonb(item.value),
            ',' ORDER BY item.key COLLATE "C"), '') || '}'
          INTO result FROM pg_catalog.jsonb_each(p) AS item;
          RETURN result;
        ELSIF kind = 'array' THEN
          SELECT '[' || coalesce(pg_catalog.string_agg(
            public.gate3_schedule_canonical_jsonb(item.value),
            ',' ORDER BY item.ordinal), '') || ']'
          INTO result FROM pg_catalog.jsonb_array_elements(p)
            WITH ORDINALITY AS item(value,ordinal);
          RETURN result;
        ELSIF kind = 'string' THEN
          RETURN pg_catalog.to_json(p #>> '{}')::text;
        ELSIF kind IN ('number','boolean','null') THEN
          RETURN p::text;
        END IF;
        RAISE EXCEPTION 'gate3_schedule_json_kind_denied';
      END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_pin_is_canonical(
        pinned public.gate3_capture_schedule_pins)
      RETURNS boolean AS $$
      DECLARE schedule jsonb; coordinate jsonb; plan jsonb;
              page_size integer; max_pages integer; response_bytes integer;
      BEGIN
        schedule := pinned.schedule_json::jsonb;
        coordinate := pinned.coordinate_plan_json::jsonb;
        IF NOT public.gate3_schedule_exact_keys(schedule, ARRAY[
            'schema_version','preregistration_sha256','coordinate_plan',
            'coordinate_plan_sha256','planned_at','plans','current_claim',
            'predictive_oos_eligible','reference_only','runtime_consumers',
            'execution_authority'])
          OR NOT public.gate3_schedule_exact_keys(coordinate, ARRAY[
            'schema_version','holdout_id','source','source_version','origin',
            'instrument_ids','start_at','end_at','interval_seconds',
            'expected_rows','current_claim','predictive_oos_eligible',
            'runtime_consumers','execution_authority'])
          OR schedule->'coordinate_plan' IS DISTINCT FROM coordinate
          OR NOT public.gate3_schedule_known_types(schedule,
             ARRAY['schema_version','preregistration_sha256',
               'coordinate_plan_sha256','planned_at','current_claim'],
             ARRAY['runtime_consumers'],
             ARRAY['predictive_oos_eligible','reference_only',
               'execution_authority'],ARRAY['plans'],ARRAY['coordinate_plan'])
          OR NOT public.gate3_schedule_known_types(coordinate,
             ARRAY['schema_version','holdout_id','source','source_version',
               'origin','start_at','end_at','current_claim'],
             ARRAY['interval_seconds','expected_rows','runtime_consumers'],
             ARRAY['predictive_oos_eligible','execution_authority'],
             ARRAY['instrument_ids'],ARRAY[]::text[])
          OR NOT public.gate3_schedule_all_ascii(schedule)
          OR NOT public.gate3_schedule_all_ascii(coordinate)
          OR schedule->>'schema_version' IS DISTINCT FROM
             'ctcc.mie.gate3.capture_schedule.v1'
          OR coordinate->>'schema_version' IS DISTINCT FROM
             'ctcc.mie.gate3.coordinate_plan.v1'
          OR schedule->>'preregistration_sha256' IS DISTINCT FROM
             pinned.seal_sha256
          OR schedule->>'coordinate_plan_sha256' IS DISTINCT FROM
             pinned.coordinate_plan_sha256
          OR coordinate->>'holdout_id' IS DISTINCT FROM pinned.holdout_id
          OR coordinate->>'holdout_id' !~
             '^[a-z0-9]+([._:-][a-z0-9]+)*$'
          OR pg_catalog.length(pinned.holdout_id) NOT BETWEEN 3 AND 160
          OR coordinate->>'source' IS DISTINCT FROM
             'okx.public_market_source'
          OR coordinate->>'source_version' IS DISTINCT FROM
             'okx.history_candles.nine_strings.v1'
          OR coordinate->>'origin' NOT IN
             ('https://openapi.okx.com','https://us.okx.com',
              'https://eea.okx.com')
          OR schedule->>'current_claim' IS DISTINCT FROM 'computational'
          OR coordinate->>'current_claim' IS DISTINCT FROM 'computational'
          OR schedule->'predictive_oos_eligible' IS DISTINCT FROM 'false'::jsonb
          OR coordinate->'predictive_oos_eligible' IS DISTINCT FROM 'false'::jsonb
          OR schedule->'reference_only' IS DISTINCT FROM 'true'::jsonb
          OR schedule->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR coordinate->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR (schedule->'runtime_consumers')::text IS DISTINCT FROM '0'
          OR (coordinate->'runtime_consumers')::text IS DISTINCT FROM '0'
          OR (coordinate->'interval_seconds')::text IS DISTINCT FROM '60'
          OR (coordinate->'expected_rows')::text !~ '^[1-9][0-9]{0,3}$'
          OR coordinate->>'start_at' IS DISTINCT FROM
             pg_catalog.to_char(pinned.window_start AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          OR coordinate->>'end_at' IS DISTINCT FROM
             pg_catalog.to_char(pinned.window_end AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          OR schedule->>'planned_at' IS DISTINCT FROM
             pg_catalog.to_char(pinned.planned_at AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          OR pg_catalog.jsonb_typeof(coordinate->'instrument_ids')
             IS DISTINCT FROM 'array'
          OR pg_catalog.jsonb_typeof(schedule->'plans')
             IS DISTINCT FROM 'array'
          OR pg_catalog.jsonb_array_length(schedule->'plans')
             IS DISTINCT FROM (coordinate->>'expected_rows')::integer
          OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               pinned.schedule_json,'UTF8')),'hex')
             IS DISTINCT FROM pinned.schedule_sha256
          OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               pinned.coordinate_plan_json,'UTF8')),'hex')
             IS DISTINCT FROM pinned.coordinate_plan_sha256
        THEN RETURN false;
        END IF;
        FOR plan IN SELECT value FROM
          pg_catalog.jsonb_array_elements(schedule->'plans') AS item(value)
        LOOP
          IF NOT public.gate3_schedule_exact_keys(plan, ARRAY[
              'schema_version','origin','instrument_id','instrument_type',
              'environment','interval_seconds','start_ns','end_ns',
              'expected_rows','created_ns','page_size','max_pages',
              'max_response_bytes','clock_policy','source_parser',
              'volume_column','volume_unit','revision_policy',
              'current_claim','predictive_oos_eligible','runtime_consumers',
              'execution_authority'])
            OR plan->>'schema_version' IS DISTINCT FROM
               'ctcc.public.minute_plan.v1'
            OR NOT public.gate3_schedule_known_types(plan,
               ARRAY['schema_version','origin','instrument_id',
                 'instrument_type','environment','clock_policy',
                 'source_parser','volume_column','volume_unit',
                 'revision_policy','current_claim'],
               ARRAY['interval_seconds','start_ns','end_ns','expected_rows',
                 'created_ns','page_size','max_pages','max_response_bytes',
                 'runtime_consumers'],
               ARRAY['predictive_oos_eligible','execution_authority'],
               ARRAY[]::text[],ARRAY[]::text[])
            OR plan->>'origin' IS DISTINCT FROM coordinate->>'origin'
            OR plan->>'instrument_id' !~ '^[A-Z0-9]{2,20}-USDT-SWAP$'
            OR plan->>'instrument_type' IS DISTINCT FROM 'SWAP'
            OR plan->>'environment' IS DISTINCT FROM 'public_production'
            OR (plan->'interval_seconds')::text IS DISTINCT FROM '60'
            OR (plan->'expected_rows')::text IS DISTINCT FROM '1'
            OR (plan->'start_ns')::text !~ '^(0|[1-9][0-9]*)$'
            OR (plan->'end_ns')::text !~ '^(0|[1-9][0-9]*)$'
            OR (plan->'created_ns')::text !~ '^(0|[1-9][0-9]*)$'
            OR (plan->'page_size')::text !~ '^[1-9][0-9]{0,2}$'
            OR (plan->'max_pages')::text !~ '^[1-9][0-9]{0,1}$'
            OR (plan->'max_response_bytes')::text !~ '^[1-9][0-9]{3,6}$'
            OR plan->>'clock_policy' IS DISTINCT FROM
               'ctcc.windows_w32time_causal.v1'
            OR plan->>'source_parser' IS DISTINCT FROM
               'okx.history_candles.nine_strings.v1'
            OR plan->>'volume_column' IS DISTINCT FROM 'vol'
            OR plan->>'volume_unit' IS DISTINCT FROM 'contracts'
            OR plan->>'revision_policy' IS DISTINCT FROM
               'provider_correctable'
            OR plan->>'current_claim' IS DISTINCT FROM 'computational'
            OR plan->'predictive_oos_eligible' IS DISTINCT FROM 'false'::jsonb
            OR (plan->'runtime_consumers')::text IS DISTINCT FROM '0'
            OR plan->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          THEN RETURN false;
          END IF;
          page_size := (plan->>'page_size')::integer;
          max_pages := (plan->>'max_pages')::integer;
          response_bytes := (plan->>'max_response_bytes')::integer;
          IF page_size NOT BETWEEN 1 AND 100
            OR max_pages NOT BETWEEN 1 AND 30
            OR response_bytes NOT BETWEEN 1024 AND 1048576
            OR page_size * max_pages < 1
          THEN RETURN false;
          END IF;
        END LOOP;
        RETURN pinned.schedule_json =
            public.gate3_schedule_canonical_jsonb(schedule)
          AND pinned.coordinate_plan_json =
            public.gate3_schedule_canonical_jsonb(coordinate);
      EXCEPTION WHEN others THEN
        -- Unknown legacy shape or malformed raw data never acquires a claim.
        RETURN false;
      END; $$ LANGUAGE plpgsql STABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_all_ascii(p jsonb)
      RETURNS boolean AS $$
      DECLARE kind text; member record; value text;
      BEGIN
        kind := pg_catalog.jsonb_typeof(p);
        IF kind = 'string' THEN
          value := p #>> '{}';
          RETURN pg_catalog.octet_length(value) = pg_catalog.length(value);
        ELSIF kind = 'object' THEN
          FOR member IN SELECT key,value FROM pg_catalog.jsonb_each(p) LOOP
            IF pg_catalog.octet_length(member.key) <>
                 pg_catalog.length(member.key)
              OR NOT public.gate3_schedule_all_ascii(member.value)
            THEN RETURN false;
            END IF;
          END LOOP;
          RETURN true;
        ELSIF kind = 'array' THEN
          FOR member IN SELECT value FROM pg_catalog.jsonb_array_elements(p)
          LOOP
            IF NOT public.gate3_schedule_all_ascii(member.value) THEN
              RETURN false;
            END IF;
          END LOOP;
          RETURN true;
        END IF;
        RETURN kind IN ('number','boolean','null');
      END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_known_types(
        p jsonb, strings text[], numbers text[], booleans text[],
        arrays text[], objects text[])
      RETURNS boolean AS $$
      DECLARE key text;
      BEGIN
        FOREACH key IN ARRAY strings LOOP
          IF pg_catalog.jsonb_typeof(p->key) IS DISTINCT FROM 'string'
          THEN RETURN false; END IF;
        END LOOP;
        FOREACH key IN ARRAY numbers LOOP
          IF pg_catalog.jsonb_typeof(p->key) IS DISTINCT FROM 'number'
          THEN RETURN false; END IF;
        END LOOP;
        FOREACH key IN ARRAY booleans LOOP
          IF pg_catalog.jsonb_typeof(p->key) IS DISTINCT FROM 'boolean'
          THEN RETURN false; END IF;
        END LOOP;
        FOREACH key IN ARRAY arrays LOOP
          IF pg_catalog.jsonb_typeof(p->key) IS DISTINCT FROM 'array'
          THEN RETURN false; END IF;
        END LOOP;
        FOREACH key IN ARRAY objects LOOP
          IF pg_catalog.jsonb_typeof(p->key) IS DISTINCT FROM 'object'
          THEN RETURN false; END IF;
        END LOOP;
        RETURN true;
      END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_legacy_classify(
        pinned public.gate3_capture_schedule_pins)
      RETURNS text AS $$
      DECLARE schedule jsonb; coordinate jsonb; plan jsonb; instrument jsonb;
      BEGIN
        IF public.gate3_schedule_pin_is_canonical(pinned) THEN
          RETURN 'canonical';
        END IF;
        schedule := pinned.schedule_json::jsonb;
        coordinate := pinned.coordinate_plan_json::jsonb;
        IF NOT public.gate3_schedule_exact_keys(schedule, ARRAY[
            'schema_version','preregistration_sha256','coordinate_plan',
            'coordinate_plan_sha256','planned_at','plans','current_claim',
            'predictive_oos_eligible','reference_only','runtime_consumers',
            'execution_authority'])
          OR NOT public.gate3_schedule_exact_keys(coordinate, ARRAY[
            'schema_version','holdout_id','source','source_version','origin',
            'instrument_ids','start_at','end_at','interval_seconds',
            'expected_rows','current_claim','predictive_oos_eligible',
            'runtime_consumers','execution_authority'])
          OR schedule->>'schema_version' IS DISTINCT FROM
             'ctcc.mie.gate3.capture_schedule.v1'
          OR coordinate->>'schema_version' IS DISTINCT FROM
             'ctcc.mie.gate3.coordinate_plan.v1'
          OR schedule->'coordinate_plan' IS DISTINCT FROM coordinate
          OR NOT public.gate3_schedule_known_types(schedule,
             ARRAY['schema_version','preregistration_sha256',
               'coordinate_plan_sha256','planned_at','current_claim'],
             ARRAY['runtime_consumers'],
             ARRAY['predictive_oos_eligible','reference_only',
               'execution_authority'],ARRAY['plans'],ARRAY['coordinate_plan'])
          OR NOT public.gate3_schedule_known_types(coordinate,
             ARRAY['schema_version','holdout_id','source','source_version',
               'origin','start_at','end_at','current_claim'],
             ARRAY['interval_seconds','expected_rows','runtime_consumers'],
             ARRAY['predictive_oos_eligible','execution_authority'],
             ARRAY['instrument_ids'],ARRAY[]::text[])
          OR NOT public.gate3_schedule_all_ascii(schedule)
          OR NOT public.gate3_schedule_all_ascii(coordinate)
          OR pg_catalog.jsonb_array_length(schedule->'plans')
             NOT BETWEEN 1 AND 4096
          OR pg_catalog.jsonb_array_length(coordinate->'instrument_ids')
             NOT BETWEEN 1 AND 4096
        THEN RETURN 'unknown';
        END IF;
        FOR instrument IN SELECT value FROM
          pg_catalog.jsonb_array_elements(coordinate->'instrument_ids')
            AS item(value)
        LOOP
          IF pg_catalog.jsonb_typeof(instrument) IS DISTINCT FROM 'string'
          THEN RETURN 'unknown'; END IF;
        END LOOP;
        FOR plan IN SELECT value FROM
          pg_catalog.jsonb_array_elements(schedule->'plans') AS item(value)
        LOOP
          IF NOT public.gate3_schedule_exact_keys(plan, ARRAY[
              'schema_version','origin','instrument_id','instrument_type',
              'environment','interval_seconds','start_ns','end_ns',
              'expected_rows','created_ns','page_size','max_pages',
              'max_response_bytes','clock_policy','source_parser',
              'volume_column','volume_unit','revision_policy',
              'current_claim','predictive_oos_eligible','runtime_consumers',
              'execution_authority'])
            OR plan->>'schema_version' IS DISTINCT FROM
               'ctcc.public.minute_plan.v1'
            OR NOT public.gate3_schedule_known_types(plan,
               ARRAY['schema_version','origin','instrument_id',
                 'instrument_type','environment','clock_policy',
                 'source_parser','volume_column','volume_unit',
                 'revision_policy','current_claim'],
               ARRAY['interval_seconds','start_ns','end_ns','expected_rows',
                 'created_ns','page_size','max_pages','max_response_bytes',
                 'runtime_consumers'],
               ARRAY['predictive_oos_eligible','execution_authority'],
               ARRAY[]::text[],ARRAY[]::text[])
          THEN RETURN 'unknown';
          END IF;
        END LOOP;
        -- Only a byte-level mismatch with the exact JSON serializer proves
        -- noncanonicality within this known ASCII V1 shape. Anything outside
        -- it, including serializer uncertainty, keeps its historical keys.
        IF pinned.schedule_json IS DISTINCT FROM
             public.gate3_schedule_canonical_jsonb(schedule)
          OR pinned.coordinate_plan_json IS DISTINCT FROM
             public.gate3_schedule_canonical_jsonb(coordinate)
        THEN RETURN 'noncanonical';
        END IF;
        RETURN 'unknown';
      EXCEPTION WHEN others THEN
        RETURN 'unknown';
      END; $$ LANGUAGE plpgsql STABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      INSERT INTO public.gate3_capture_schedule_legacy_inventory (
        schedule_sha256,seal_sha256,window_key,holdout_id,classification)
      SELECT p.schedule_sha256,p.seal_sha256,p.window_key,p.holdout_id,
        public.gate3_schedule_legacy_classify(p)
      FROM public.gate3_capture_schedule_pins p
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_legacy_inventory_immutable()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'gate3_schedule_legacy_inventory_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_legacy_inventory_immutable
      BEFORE INSERT OR UPDATE OR DELETE
      ON public.gate3_capture_schedule_legacy_inventory
      FOR EACH ROW EXECUTE FUNCTION
        public.gate3_schedule_legacy_inventory_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_legacy_inventory_no_truncate
      BEFORE TRUNCATE ON public.gate3_capture_schedule_legacy_inventory
      FOR EACH STATEMENT EXECUTE FUNCTION
        public.gate3_schedule_legacy_inventory_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_claim_insert_guard()
      RETURNS trigger AS $$
      DECLARE pinned public.gate3_capture_schedule_pins%ROWTYPE;
      BEGIN
        SELECT * INTO pinned FROM public.gate3_capture_schedule_pins
          WHERE schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND OR NOT public.gate3_schedule_pin_is_canonical(pinned)
          OR NEW.seal_sha256 IS DISTINCT FROM pinned.seal_sha256
          OR NEW.coordinate_plan_sha256 IS DISTINCT FROM
             pinned.coordinate_plan_sha256
          OR NEW.window_key IS DISTINCT FROM pinned.window_key
          OR NEW.holdout_id IS DISTINCT FROM pinned.holdout_id
          OR NEW.window_start IS DISTINCT FROM pinned.window_start
          OR NEW.window_end IS DISTINCT FROM pinned.window_end
          OR NEW.schedule_recorded_at IS DISTINCT FROM pinned.recorded_at
          OR EXISTS (
            SELECT 1 FROM public.gate3_capture_schedule_legacy_inventory old
            WHERE old.classification <> 'noncanonical'
              AND (old.seal_sha256=NEW.seal_sha256
                OR old.window_key=NEW.window_key
                OR old.holdout_id=NEW.holdout_id))
        THEN RAISE EXCEPTION 'gate3_schedule_claim_identity_denied';
        END IF;
        NEW.claimed_at := pg_catalog.clock_timestamp();
        IF NEW.claimed_at < pinned.recorded_at
          OR NEW.claimed_at >= pinned.window_start
        THEN RAISE EXCEPTION 'gate3_schedule_claim_late';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_claim_immutable()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'gate3_schedule_claim_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_claim_insert_guard
      BEFORE INSERT ON public.gate3_capture_schedule_key_claims
      FOR EACH ROW EXECUTE FUNCTION public.gate3_schedule_claim_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_claim_immutable
      BEFORE UPDATE OR DELETE ON public.gate3_capture_schedule_key_claims
      FOR EACH ROW EXECUTE FUNCTION public.gate3_schedule_claim_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_claim_no_truncate
      BEFORE TRUNCATE ON public.gate3_capture_schedule_key_claims
      FOR EACH STATEMENT EXECUTE FUNCTION public.gate3_schedule_claim_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_pin_claim_after_insert()
      RETURNS trigger AS $$
      DECLARE safe_role boolean;
      BEGIN
        SELECT NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                    OR r.rolreplication OR r.rolbypassrls)
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class t
            WHERE t.oid IN (p.oid,c.oid,a.oid,i.oid,old_a.oid,w.oid)
              AND pg_catalog.pg_has_role(session_user,
                pg_catalog.pg_get_userbyid(t.relowner),'MEMBER'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles other_role
            WHERE other_role.oid <> r.oid
              AND pg_catalog.pg_has_role(session_user,other_role.oid,'MEMBER'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class t
            WHERE t.oid IN (p.oid,c.oid,a.oid,i.oid,old_a.oid,w.oid)
              AND (pg_catalog.has_table_privilege(session_user,t.oid,
                'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                OR pg_catalog.has_any_column_privilege(session_user,t.oid,
                  'SELECT,INSERT,UPDATE,REFERENCES')))
          AND pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_claim_append(jsonb)'::pg_catalog.regprocedure,
            'EXECUTE')
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_append(jsonb)'::pg_catalog.regprocedure,
            'EXECUTE')
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
            'EXECUTE')
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_claim_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
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
              AND pg_catalog.has_schema_privilege(session_user,n.oid,'CREATE'))
          AND NOT pg_catalog.has_database_privilege(
            session_user,pg_catalog.current_database(),'CREATE')
          INTO safe_role
          FROM pg_catalog.pg_roles r, pg_catalog.pg_class p,
               pg_catalog.pg_class c, pg_catalog.pg_class a,
               pg_catalog.pg_class i,
               pg_catalog.pg_class old_a, pg_catalog.pg_class w
          WHERE r.rolname=session_user
            AND p.oid='public.gate3_capture_schedule_pins'::pg_catalog.regclass
            AND c.oid='public.gate3_capture_schedule_key_claims'::pg_catalog.regclass
            AND a.oid='public.gate3_capture_schedule_claim_acks'::pg_catalog.regclass
            AND i.oid='public.gate3_capture_schedule_legacy_inventory'::pg_catalog.regclass
            AND old_a.oid='public.gate3_capture_schedule_publication_acks'::pg_catalog.regclass
            AND w.oid='public.public_receipt_witness_revisions'::pg_catalog.regclass;
        IF safe_role IS DISTINCT FROM true THEN
          RAISE EXCEPTION 'gate3_schedule_claim_role_denied';
        END IF;
        IF NOT public.gate3_schedule_pin_is_canonical(NEW) THEN
          RAISE EXCEPTION 'gate3_schedule_claim_noncanonical';
        END IF;
        INSERT INTO public.gate3_capture_schedule_key_claims (
          schedule_sha256,seal_sha256,coordinate_plan_sha256,window_key,
          holdout_id,window_start,window_end,schedule_recorded_at)
        VALUES (NEW.schedule_sha256,NEW.seal_sha256,
          NEW.coordinate_plan_sha256,NEW.window_key,NEW.holdout_id,
          NEW.window_start,NEW.window_end,NEW.recorded_at);
        RETURN NULL;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_pin_claim_after_insert
      AFTER INSERT ON public.gate3_capture_schedule_pins
      FOR EACH ROW EXECUTE FUNCTION public.gate3_schedule_pin_claim_after_insert()
    """)
    # The immutable raw rows keep their hashes/PKs. These three raw UNIQUEs
    # caused the poison: their replacement is the canonical claim table above.
    for name in (
        "uq_gate3_capture_schedule_pins_seal_sha256",
        "uq_gate3_capture_schedule_pins_window_key",
        "uq_gate3_capture_schedule_pins_holdout_id",
    ):
        op.execute(
            "ALTER TABLE public.gate3_capture_schedule_pins DROP CONSTRAINT " + name
        )
    # Existing EXECUTE grants follow the old function OID. Close that OID and
    # require a newly provisioned publisher login for the canonical entrypoint.
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_append(p_record jsonb)
      RETURNS void AS $$
      BEGIN RAISE EXCEPTION 'gate3_schedule_legacy_append_closed'; END;
      $$ LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_claim_append(p_record jsonb)
      RETURNS void AS $$
      BEGIN
        IF pg_catalog.jsonb_typeof(p_record) IS DISTINCT FROM 'object' THEN
          RAISE EXCEPTION 'gate3_schedule_record_invalid';
        END IF;
        INSERT INTO public.gate3_capture_schedule_pins (
          schedule_sha256,seal_sha256,coordinate_plan_sha256,window_key,
          holdout_id,window_start,window_end,planned_at,coordinate_plan_json,
          schedule_json
        ) VALUES (
          p_record->>'schedule_sha256',p_record->>'seal_sha256',
          p_record->>'coordinate_plan_sha256',p_record->>'window_key',
          p_record->>'holdout_id',(p_record->>'window_start')::timestamptz,
          (p_record->>'window_end')::timestamptz,
          (p_record->>'planned_at')::timestamptz,
          p_record->>'coordinate_plan_json',p_record->>'schedule_json'
        );
      END; $$ LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_claim_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_pins AS $$
        SELECT p.* FROM public.gate3_capture_schedule_pins p
        JOIN public.gate3_capture_schedule_key_claims c
          ON c.schedule_sha256=p.schedule_sha256
        WHERE p.schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    # Grants on legacy function OIDs survive a migration. Make their readers
    # return no evidence even when an older process still holds those grants.
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_pins AS $$
        SELECT * FROM public.gate3_capture_schedule_pins WHERE false
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_ack_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_publication_acks AS $$
        SELECT * FROM public.gate3_capture_schedule_publication_acks WHERE false
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)

    op.execute("""
      CREATE FUNCTION public.gate3_schedule_claim_ack_insert_guard()
      RETURNS trigger AS $$
      DECLARE claimed public.gate3_capture_schedule_key_claims%ROWTYPE;
              pinned public.gate3_capture_schedule_pins%ROWTYPE;
              safe_role boolean;
      BEGIN
        -- Session login cannot have inserted a pin/claim in this transaction.
        SELECT NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                    OR r.rolreplication OR r.rolbypassrls)
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class t
            WHERE t.oid IN (p.oid,c.oid,a.oid,i.oid,old_a.oid,w.oid)
              AND pg_catalog.pg_has_role(session_user,
                pg_catalog.pg_get_userbyid(t.relowner),'MEMBER'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles other_role
            WHERE other_role.oid <> r.oid
              AND pg_catalog.pg_has_role(session_user,other_role.oid,'MEMBER'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class t
            WHERE t.oid IN (p.oid,c.oid,a.oid,i.oid,old_a.oid,w.oid)
              AND (pg_catalog.has_table_privilege(session_user,t.oid,
                'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                OR pg_catalog.has_any_column_privilege(session_user,t.oid,
                  'SELECT,INSERT,UPDATE,REFERENCES')))
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_append(jsonb)'::pg_catalog.regprocedure,
            'EXECUTE')
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_claim_append(jsonb)'::pg_catalog.regprocedure,
            'EXECUTE')
          AND NOT pg_catalog.has_function_privilege(session_user,
            'public.gate3_capture_schedule_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
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
              AND pg_catalog.has_schema_privilege(session_user,n.oid,'CREATE'))
          AND NOT pg_catalog.has_database_privilege(
            session_user,pg_catalog.current_database(),'CREATE')
          INTO safe_role
          FROM pg_catalog.pg_roles r, pg_catalog.pg_class p,
               pg_catalog.pg_class c, pg_catalog.pg_class a,
               pg_catalog.pg_class i,
               pg_catalog.pg_class old_a, pg_catalog.pg_class w
          WHERE r.rolname=session_user
            AND p.oid='public.gate3_capture_schedule_pins'::pg_catalog.regclass
            AND c.oid='public.gate3_capture_schedule_key_claims'::pg_catalog.regclass
            AND a.oid='public.gate3_capture_schedule_claim_acks'::pg_catalog.regclass
            AND i.oid='public.gate3_capture_schedule_legacy_inventory'::pg_catalog.regclass
            AND old_a.oid='public.gate3_capture_schedule_publication_acks'::pg_catalog.regclass
            AND w.oid='public.public_receipt_witness_revisions'::pg_catalog.regclass;
        IF safe_role IS DISTINCT FROM true THEN
          RAISE EXCEPTION 'gate3_schedule_claim_ack_role_denied';
        END IF;
        SELECT c.* INTO claimed FROM public.gate3_capture_schedule_key_claims c
          WHERE c.schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        SELECT p.* INTO pinned FROM public.gate3_capture_schedule_pins p
          WHERE p.schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        IF claimed.schedule_sha256 IS NULL OR pinned.schedule_sha256 IS NULL
          OR NEW.seal_sha256 IS DISTINCT FROM claimed.seal_sha256
          OR NEW.coordinate_plan_sha256 IS DISTINCT FROM
             claimed.coordinate_plan_sha256
          OR NEW.window_key IS DISTINCT FROM claimed.window_key
          OR NEW.holdout_id IS DISTINCT FROM claimed.holdout_id
          OR NEW.window_start IS DISTINCT FROM claimed.window_start
          OR NEW.window_end IS DISTINCT FROM claimed.window_end
          OR NEW.schedule_recorded_at IS DISTINCT FROM
             claimed.schedule_recorded_at
          OR NEW.claim_recorded_at IS DISTINCT FROM claimed.claimed_at
          OR claimed.schedule_sha256 IS DISTINCT FROM pinned.schedule_sha256
        THEN RAISE EXCEPTION 'gate3_schedule_claim_ack_identity_denied';
        END IF;
        NEW.acknowledged_at := pg_catalog.clock_timestamp();
        IF NEW.acknowledged_at < claimed.claimed_at
          OR NEW.acknowledged_at >= claimed.window_start
        THEN RAISE EXCEPTION 'gate3_schedule_claim_ack_late';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_claim_ack_immutable()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'gate3_schedule_claim_ack_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_claim_ack_insert_guard
      BEFORE INSERT ON public.gate3_capture_schedule_claim_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_schedule_claim_ack_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_claim_ack_immutable
      BEFORE UPDATE OR DELETE ON public.gate3_capture_schedule_claim_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_schedule_claim_ack_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_claim_ack_no_truncate
      BEFORE TRUNCATE ON public.gate3_capture_schedule_claim_acks
      FOR EACH STATEMENT EXECUTE FUNCTION
        public.gate3_schedule_claim_ack_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_claim_ack_append(
        p_schedule_sha text,p_seal_sha text,p_coordinate_sha text,
        p_window_key text,p_holdout_id text)
      RETURNS void AS $$
      BEGIN
        IF p_schedule_sha !~ '^[a-f0-9]{64}$' THEN
          RAISE EXCEPTION 'gate3_schedule_claim_ack_identity_denied';
        END IF;
        LOCK TABLE public.gate3_capture_schedule_pins
          IN ROW SHARE MODE NOWAIT;
        LOCK TABLE public.gate3_capture_schedule_key_claims
          IN ROW SHARE MODE NOWAIT;
        LOCK TABLE public.gate3_capture_schedule_claim_acks
          IN ROW EXCLUSIVE MODE NOWAIT;
        IF NOT pg_catalog.pg_try_advisory_xact_lock(
          ('x'||pg_catalog.substr(p_schedule_sha,1,16))::bit(64)::bigint)
        THEN RAISE EXCEPTION 'gate3_schedule_claim_ack_busy';
        END IF;
        INSERT INTO public.gate3_capture_schedule_claim_acks (
          schedule_sha256,seal_sha256,coordinate_plan_sha256,window_key,
          holdout_id,window_start,window_end,schedule_recorded_at,
          claim_recorded_at)
        SELECT c.schedule_sha256,c.seal_sha256,c.coordinate_plan_sha256,
               c.window_key,c.holdout_id,c.window_start,c.window_end,
               c.schedule_recorded_at,c.claimed_at
          FROM public.gate3_capture_schedule_key_claims c
          WHERE c.schedule_sha256=p_schedule_sha
            AND c.seal_sha256=p_seal_sha
            AND c.coordinate_plan_sha256=p_coordinate_sha
            AND c.window_key=p_window_key
            AND c.holdout_id=p_holdout_id;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_schedule_claim_ack_identity_denied';
        END IF;
      END; $$ LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_claim_ack_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_claim_acks AS $$
        SELECT * FROM public.gate3_capture_schedule_claim_acks
        WHERE schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    # Existing role-specific 0028 grants remain, so close that insert path
    # explicitly. Its durable historical rows and original functions survive.
    op.execute("""
      CREATE FUNCTION public.gate3_schedule_legacy_ack_closed()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'gate3_schedule_legacy_ack_closed'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_schedule_legacy_ack_closed
      BEFORE INSERT ON public.gate3_capture_schedule_publication_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_schedule_legacy_ack_closed()
    """)
    for name in (
        "gate3_schedule_exact_keys(jsonb,text[])",
        "gate3_schedule_canonical_jsonb(jsonb)",
        "gate3_schedule_pin_is_canonical(public.gate3_capture_schedule_pins)",
        "gate3_schedule_all_ascii(jsonb)",
        "gate3_schedule_known_types(jsonb,text[],text[],text[],text[],text[])",
        "gate3_schedule_legacy_classify(public.gate3_capture_schedule_pins)",
        "gate3_schedule_legacy_inventory_immutable()",
        "gate3_schedule_claim_insert_guard()",
        "gate3_schedule_claim_immutable()",
        "gate3_schedule_pin_claim_after_insert()",
        "gate3_schedule_claim_ack_insert_guard()",
        "gate3_schedule_claim_ack_immutable()",
        "gate3_schedule_legacy_ack_closed()",
        "gate3_capture_schedule_claim_append(jsonb)",
        "gate3_capture_schedule_claim_read(text)",
        "gate3_capture_schedule_claim_ack_append(text,text,text,text,text)",
        "gate3_capture_schedule_claim_ack_read(text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC")


def downgrade():
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_publication_acks,
        public.gate3_capture_schedule_key_claims,
        public.gate3_capture_schedule_claim_acks,
        public.gate3_capture_schedule_legacy_inventory
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.gate3_capture_schedule_pins)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_publication_acks)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_key_claims)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_claim_acks)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_legacy_inventory)
        THEN RAISE EXCEPTION 'gate3_schedule_claim_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute(
        "DROP FUNCTION public.gate3_capture_schedule_claim_ack_append(text,text,text,text,text)"
    )
    op.execute("DROP FUNCTION public.gate3_capture_schedule_claim_append(jsonb)")
    op.execute("DROP FUNCTION public.gate3_capture_schedule_claim_ack_read(text)")
    op.execute("DROP FUNCTION public.gate3_capture_schedule_claim_read(text)")
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_append(p_record jsonb)
      RETURNS void AS $$
      BEGIN
        IF pg_catalog.jsonb_typeof(p_record) IS DISTINCT FROM 'object' THEN
          RAISE EXCEPTION 'gate3_schedule_record_invalid';
        END IF;
        INSERT INTO public.gate3_capture_schedule_pins (
          schedule_sha256,seal_sha256,coordinate_plan_sha256,window_key,
          holdout_id,window_start,window_end,planned_at,coordinate_plan_json,
          schedule_json
        ) VALUES (
          p_record->>'schedule_sha256',p_record->>'seal_sha256',
          p_record->>'coordinate_plan_sha256',p_record->>'window_key',
          p_record->>'holdout_id',(p_record->>'window_start')::timestamptz,
          (p_record->>'window_end')::timestamptz,
          (p_record->>'planned_at')::timestamptz,
          p_record->>'coordinate_plan_json',p_record->>'schedule_json'
        );
      END; $$ LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_pins AS $$
        SELECT * FROM public.gate3_capture_schedule_pins
        WHERE schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_ack_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_publication_acks AS $$
        SELECT * FROM public.gate3_capture_schedule_publication_acks
        WHERE schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute(
        "DROP TRIGGER gate3_schedule_legacy_ack_closed ON public.gate3_capture_schedule_publication_acks"
    )
    op.execute("DROP FUNCTION public.gate3_schedule_legacy_ack_closed()")
    op.execute(
        "DROP TRIGGER gate3_schedule_pin_claim_after_insert ON public.gate3_capture_schedule_pins"
    )
    op.execute("DROP FUNCTION public.gate3_schedule_pin_claim_after_insert()")
    op.drop_table("gate3_capture_schedule_claim_acks")
    op.drop_table("gate3_capture_schedule_key_claims")
    op.drop_table("gate3_capture_schedule_legacy_inventory")
    op.execute("DROP FUNCTION public.gate3_schedule_claim_ack_insert_guard()")
    op.execute("DROP FUNCTION public.gate3_schedule_claim_ack_immutable()")
    op.execute("DROP FUNCTION public.gate3_schedule_claim_insert_guard()")
    op.execute("DROP FUNCTION public.gate3_schedule_claim_immutable()")
    op.execute(
        "DROP FUNCTION public.gate3_schedule_legacy_classify(public.gate3_capture_schedule_pins)"
    )
    op.execute("DROP FUNCTION public.gate3_schedule_legacy_inventory_immutable()")
    op.execute(
        "DROP FUNCTION public.gate3_schedule_known_types(jsonb,text[],text[],text[],text[],text[])"
    )
    op.execute("DROP FUNCTION public.gate3_schedule_all_ascii(jsonb)")
    op.execute(
        "DROP FUNCTION public.gate3_schedule_pin_is_canonical(public.gate3_capture_schedule_pins)"
    )
    op.execute("DROP FUNCTION public.gate3_schedule_canonical_jsonb(jsonb)")
    op.execute("DROP FUNCTION public.gate3_schedule_exact_keys(jsonb,text[])")
    for name, field in (
        ("seal_sha256", "seal_sha256"),
        ("window_key", "window_key"),
        ("holdout_id", "holdout_id"),
    ):
        op.execute(
            "ALTER TABLE public.gate3_capture_schedule_pins ADD CONSTRAINT "
            f"uq_gate3_capture_schedule_pins_{name} UNIQUE({field})"
        )
