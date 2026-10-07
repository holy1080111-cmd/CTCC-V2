"""Independent pre-window pin for exact prospective minute capture schedules.

Revision ID: 0026
Revises: 0025

No collector role or function grant is created by this migration.  A distinct
restricted login must be provisioned and checked before actual acquisition.
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE public.gate3_capture_schedule_pins (
        schedule_sha256 VARCHAR(64) NOT NULL,
        seal_sha256 VARCHAR(64) NOT NULL,
        coordinate_plan_sha256 VARCHAR(64) NOT NULL,
        window_key VARCHAR(64) NOT NULL,
        holdout_id VARCHAR(160) NOT NULL,
        window_start TIMESTAMP WITH TIME ZONE NOT NULL,
        window_end TIMESTAMP WITH TIME ZONE NOT NULL,
        planned_at TIMESTAMP WITH TIME ZONE NOT NULL,
        coordinate_plan_json TEXT NOT NULL,
        schedule_json TEXT NOT NULL,
        recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_gate3_capture_schedule_pins PRIMARY KEY(schedule_sha256),
        CONSTRAINT uq_gate3_capture_schedule_pins_seal_sha256 UNIQUE(seal_sha256),
        CONSTRAINT uq_gate3_capture_schedule_pins_window_key UNIQUE(window_key),
        CONSTRAINT uq_gate3_capture_schedule_pins_holdout_id UNIQUE(holdout_id),
        CONSTRAINT ck_gate3_capture_schedule_pins_hashes CHECK (
          schedule_sha256 ~ '^[a-f0-9]{64}$'
          AND seal_sha256 ~ '^[a-f0-9]{64}$'
          AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$'
          AND window_key ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_capture_schedule_pins_window CHECK (
          window_start < window_end AND planned_at < window_start
          AND recorded_at >= planned_at AND recorded_at < window_start),
        CONSTRAINT ck_gate3_capture_schedule_pins_payload CHECK (
          octet_length(schedule_json) BETWEEN 1 AND 8388608
          AND octet_length(coordinate_plan_json) BETWEEN 1 AND 262144)
      )
    """)
    op.execute("REVOKE ALL ON TABLE public.gate3_capture_schedule_pins FROM PUBLIC")
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_insert_guard()
      RETURNS trigger AS $$
      DECLARE payload jsonb;
              coordinates jsonb;
              instrument_count bigint;
              distinct_instruments bigint;
              instruments_valid boolean;
              instruments_text text;
              instruments_sorted text;
              derived_window_key text;
              first_us bigint;
              last_us bigint;
              planned_us bigint;
              expected_rows bigint;
              plan_count bigint;
              plan_item jsonb;
              plan_ordinal bigint;
              expected_start_ns bigint;
              expected_symbol text;
      BEGIN
        NEW.recorded_at := clock_timestamp();
        IF encode(pg_catalog.sha256(pg_catalog.convert_to(
          NEW.schedule_json,'UTF8')),'hex') IS DISTINCT FROM NEW.schedule_sha256
          OR NEW.recorded_at >= NEW.window_start
          OR NEW.recorded_at < NEW.planned_at THEN
          RAISE EXCEPTION 'gate3_schedule_timing_or_hash_denied';
        END IF;
        payload := NEW.schedule_json::jsonb;
        coordinates := payload->'coordinate_plan';
        IF encode(pg_catalog.sha256(pg_catalog.convert_to(
             NEW.coordinate_plan_json,'UTF8')),'hex') IS DISTINCT FROM
             NEW.coordinate_plan_sha256
          OR NEW.coordinate_plan_json::jsonb IS DISTINCT FROM coordinates
          OR pg_catalog.jsonb_typeof(coordinates->'instrument_ids')
             IS DISTINCT FROM 'array'
          OR pg_catalog.jsonb_array_length(coordinates->'instrument_ids')
             NOT BETWEEN 1 AND 4096 THEN
          RAISE EXCEPTION 'gate3_schedule_coordinates_denied';
        END IF;
        SELECT count(*),count(DISTINCT instrument COLLATE "C"),
               bool_and(kind='string' AND instrument COLLATE "C" ~
                 '^[A-Z0-9]{2,20}-USDT-SWAP$'),
               string_agg(instrument,chr(31) ORDER BY ordinal),
               string_agg(instrument,chr(31) ORDER BY instrument COLLATE "C")
          INTO instrument_count,distinct_instruments,instruments_valid,
               instruments_text,instruments_sorted
          FROM (
            SELECT item #>> '{}' AS instrument,
                   pg_catalog.jsonb_typeof(item) AS kind,ordinal
            FROM pg_catalog.jsonb_array_elements(coordinates->'instrument_ids')
              WITH ORDINALITY AS t(item,ordinal)
          ) instruments;
        IF instrument_count NOT BETWEEN 1 AND 4096
          OR instrument_count <> distinct_instruments
          OR instruments_valid IS DISTINCT FROM true
          OR instruments_text IS DISTINCT FROM instruments_sorted
          OR coordinates->>'source' IS DISTINCT FROM 'okx.public_market_source'
          OR coordinates->>'source_version' IS DISTINCT FROM
             'okx.history_candles.nine_strings.v1'
          OR coordinates->>'schema_version' IS DISTINCT FROM
             'ctcc.mie.gate3.coordinate_plan.v1'
          OR coalesce(coordinates->>'origin','') NOT IN
             ('https://openapi.okx.com','https://us.okx.com',
              'https://eea.okx.com') THEN
          RAISE EXCEPTION 'gate3_schedule_coordinates_denied';
        END IF;
        first_us := (EXTRACT(EPOCH FROM NEW.window_start)*1000000)::bigint;
        last_us := (EXTRACT(EPOCH FROM NEW.window_end)*1000000)::bigint;
        planned_us := (EXTRACT(EPOCH FROM NEW.planned_at)*1000000)::bigint;
        expected_rows := (last_us-first_us)/60000000*instrument_count;
        IF first_us % 60000000 <> 0 OR last_us % 60000000 <> 0
          OR last_us <= first_us
          OR (last_us-first_us) % 60000000 <> 0
          OR expected_rows NOT BETWEEN 1 AND 4096
          OR (coordinates->>'expected_rows')::bigint
             IS DISTINCT FROM expected_rows
          OR (coordinates->>'interval_seconds')::bigint
             IS DISTINCT FROM 60
          OR pg_catalog.jsonb_typeof(payload->'plans') IS DISTINCT FROM 'array'
          OR pg_catalog.jsonb_array_length(payload->'plans') <> expected_rows
          THEN
          RAISE EXCEPTION 'gate3_schedule_coverage_denied';
        END IF;
        FOR plan_item,plan_ordinal IN
          SELECT item,ordinal
          FROM pg_catalog.jsonb_array_elements(payload->'plans')
            WITH ORDINALITY AS t(item,ordinal)
        LOOP
          expected_start_ns := (
            first_us + ((plan_ordinal-1)/instrument_count)*60000000
          )*1000;
          expected_symbol := (coordinates->'instrument_ids')->>
            (((plan_ordinal-1)%instrument_count)::int);
          IF pg_catalog.jsonb_typeof(plan_item) IS DISTINCT FROM 'object'
            OR plan_item->>'schema_version' IS DISTINCT FROM
               'ctcc.public.minute_plan.v1'
            OR plan_item->>'origin' IS DISTINCT FROM coordinates->>'origin'
            OR plan_item->>'instrument_id' IS DISTINCT FROM expected_symbol
            OR plan_item->>'instrument_type' IS DISTINCT FROM 'SWAP'
            OR plan_item->>'environment' IS DISTINCT FROM 'public_production'
            OR plan_item->>'source_parser' IS DISTINCT FROM
               coordinates->>'source_version'
            OR (plan_item->>'interval_seconds')::bigint IS DISTINCT FROM 60
            OR (plan_item->>'expected_rows')::bigint IS DISTINCT FROM 1
            OR (plan_item->>'created_ns')::bigint
               IS DISTINCT FROM planned_us*1000
            OR (plan_item->>'start_ns')::bigint
               IS DISTINCT FROM expected_start_ns
            OR (plan_item->>'end_ns')::bigint
               IS DISTINCT FROM expected_start_ns+60000000000
            THEN
            RAISE EXCEPTION 'gate3_schedule_plan_coordinate_denied';
          END IF;
        END LOOP;
        derived_window_key := encode(pg_catalog.sha256(pg_catalog.convert_to(
          pg_catalog.concat_ws(chr(31),coordinates->>'source',instruments_text,
            first_us::text,last_us::text),
          'UTF8')),'hex');
        IF payload->>'schema_version' IS DISTINCT FROM
             'ctcc.mie.gate3.capture_schedule.v1'
          OR payload->>'preregistration_sha256' IS DISTINCT FROM
             NEW.seal_sha256
          OR payload->>'coordinate_plan_sha256' IS DISTINCT FROM
             NEW.coordinate_plan_sha256
          OR payload->'coordinate_plan'->>'holdout_id' IS DISTINCT FROM
             NEW.holdout_id
          OR NEW.window_key IS DISTINCT FROM derived_window_key
          OR (payload->'coordinate_plan'->>'start_at')::timestamptz
             IS DISTINCT FROM NEW.window_start
          OR (payload->'coordinate_plan'->>'end_at')::timestamptz
             IS DISTINCT FROM NEW.window_end
          OR (payload->>'planned_at')::timestamptz
             IS DISTINCT FROM NEW.planned_at THEN
          RAISE EXCEPTION 'gate3_schedule_identity_denied';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_immutable()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'gate3_schedule_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_capture_schedule_insert_guard
      BEFORE INSERT ON public.gate3_capture_schedule_pins
      FOR EACH ROW EXECUTE FUNCTION public.gate3_capture_schedule_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER gate3_capture_schedule_immutable
      BEFORE UPDATE OR DELETE ON public.gate3_capture_schedule_pins
      FOR EACH ROW EXECUTE FUNCTION public.gate3_capture_schedule_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_capture_schedule_no_truncate
      BEFORE TRUNCATE ON public.gate3_capture_schedule_pins
      FOR EACH STATEMENT EXECUTE FUNCTION public.gate3_capture_schedule_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_capture_schedule_append(p_record jsonb)
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
      CREATE FUNCTION public.gate3_capture_schedule_read(p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_pins AS $$
        SELECT * FROM public.gate3_capture_schedule_pins
        WHERE schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    for name in (
        "gate3_capture_schedule_insert_guard()",
        "gate3_capture_schedule_immutable()",
        "gate3_capture_schedule_append(jsonb)",
        "gate3_capture_schedule_read(text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name} FROM PUBLIC")


def downgrade():
    op.execute(
        "LOCK TABLE public.gate3_capture_schedule_pins IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.gate3_capture_schedule_pins) THEN
          RAISE EXCEPTION 'gate3_schedule_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute("DROP FUNCTION public.gate3_capture_schedule_append(jsonb)")
    op.execute("DROP FUNCTION public.gate3_capture_schedule_read(text)")
    op.drop_table("gate3_capture_schedule_pins")
    op.execute("DROP FUNCTION public.gate3_capture_schedule_insert_guard()")
    op.execute("DROP FUNCTION public.gate3_capture_schedule_immutable()")
