"""Require a separately committed preregistration before Gate 3 key claims.

Revision ID: 0030
Revises: 0029

No legacy 0029 claim is promoted. Existing raw pins/claims/ACKs remain intact,
but their read functions expose only claims made after a committed 0030 seal ACK.
The seal and ACK functions require separate, newly provisioned direct logins.
Neither the database server clock nor its custody is independently attested.
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    # One PostgreSQL transaction changes the whole boundary. Refuse to wait
    # behind a concurrent append while installing the new claim guard.
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_key_claims,
        public.gate3_capture_schedule_claim_acks
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      CREATE TABLE public.gate3_preregistration_seals (
        seal_sha256 VARCHAR(64) NOT NULL,
        preregistration_id VARCHAR(160) NOT NULL,
        coordinate_plan_sha256 VARCHAR(64) NOT NULL,
        window_key VARCHAR(64) NOT NULL,
        holdout_id VARCHAR(160) NOT NULL,
        window_start TIMESTAMPTZ NOT NULL,
        window_end TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        seal_json TEXT NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_gate3_preregistration_seals PRIMARY KEY(seal_sha256),
        CONSTRAINT ck_gate3_preregistration_seals_hashes CHECK (
          seal_sha256 ~ '^[a-f0-9]{64}$'
          AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$'
          AND window_key ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_preregistration_seals_times CHECK (
          created_at <= recorded_at AND recorded_at < window_start
          AND window_start < window_end),
        CONSTRAINT ck_gate3_preregistration_seals_payload CHECK (
          pg_catalog.octet_length(seal_json) BETWEEN 1 AND 1048576)
      )
    """)
    op.execute("REVOKE ALL ON TABLE public.gate3_preregistration_seals FROM PUBLIC")
    op.execute("""
      CREATE TABLE public.gate3_preregistration_seal_acks (
        seal_sha256 VARCHAR(64) NOT NULL,
        preregistration_id VARCHAR(160) NOT NULL,
        coordinate_plan_sha256 VARCHAR(64) NOT NULL,
        window_key VARCHAR(64) NOT NULL,
        holdout_id VARCHAR(160) NOT NULL,
        window_start TIMESTAMPTZ NOT NULL,
        window_end TIMESTAMPTZ NOT NULL,
        seal_recorded_at TIMESTAMPTZ NOT NULL,
        acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_gate3_preregistration_seal_acks
          PRIMARY KEY(seal_sha256),
        CONSTRAINT fk_gate3_prereg_seal_ack_seal
          FOREIGN KEY(seal_sha256)
          REFERENCES public.gate3_preregistration_seals(seal_sha256),
        CONSTRAINT uq_gate3_preregistration_seal_acks_preregistration_id
          UNIQUE(preregistration_id),
        CONSTRAINT uq_gate3_preregistration_seal_acks_window_key
          UNIQUE(window_key),
        CONSTRAINT uq_gate3_preregistration_seal_acks_holdout_id
          UNIQUE(holdout_id),
        CONSTRAINT ck_gate3_preregistration_seal_acks_hashes CHECK (
          seal_sha256 ~ '^[a-f0-9]{64}$'
          AND coordinate_plan_sha256 ~ '^[a-f0-9]{64}$'
          AND window_key ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_preregistration_seal_acks_times CHECK (
          seal_recorded_at <= acknowledged_at
          AND acknowledged_at < window_start
          AND window_start < window_end)
      )
    """)
    op.execute("REVOKE ALL ON TABLE public.gate3_preregistration_seal_acks FROM PUBLIC")

    # Canonical integer-only, ASCII JSON is a deliberately narrow admitted
    # subset. Python's prospective model must still revalidate the full seal.
    # Unknown encodings or schema expansion fail closed instead of acquiring a
    # key on the strength of JSONB structural equality alone.
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_integer_numbers(p jsonb)
      RETURNS boolean AS $$
      DECLARE kind text; member record;
      BEGIN
        kind := pg_catalog.jsonb_typeof(p);
        IF kind = 'number' THEN
          RETURN p::text ~ '^-?(0|[1-9][0-9]*)$';
        ELSIF kind = 'object' THEN
          FOR member IN SELECT item.value
            FROM pg_catalog.jsonb_each(p) AS item(key,value) LOOP
            IF NOT public.gate3_prereg_integer_numbers(member.value)
            THEN RETURN false; END IF;
          END LOOP;
          RETURN true;
        ELSIF kind = 'array' THEN
          FOR member IN SELECT item.value
            FROM pg_catalog.jsonb_array_elements(p) AS item(value)
          LOOP
            IF NOT public.gate3_prereg_integer_numbers(member.value)
            THEN RETURN false; END IF;
          END LOOP;
          RETURN true;
        END IF;
        RETURN kind IN ('string','boolean','null');
      END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_seal_insert_guard()
      RETURNS trigger AS $$
      DECLARE payload jsonb; holdout jsonb;
              instrument_count bigint; distinct_instruments bigint;
              instruments_valid boolean; instruments_text text;
              instruments_sorted text; first_us bigint; last_us bigint;
              derived_window_key text;
      BEGIN
        IF NOT public.gate3_prereg_direct_role_allowed('seal_publish')
        THEN RAISE EXCEPTION 'gate3_prereg_seal_role_denied'; END IF;
        NEW.recorded_at := pg_catalog.clock_timestamp();
        IF pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
             NEW.seal_json,'UTF8')),'hex') IS DISTINCT FROM NEW.seal_sha256
          OR NEW.recorded_at < NEW.created_at
          OR NEW.recorded_at >= NEW.window_start
        THEN RAISE EXCEPTION 'gate3_prereg_seal_hash_or_time_denied'; END IF;
        payload := NEW.seal_json::jsonb;
        holdout := payload->'prospective_holdout';
        IF NOT public.gate3_schedule_exact_keys(payload, ARRAY[
             'schema_version','preregistration_id','created_at',
             'source_tree_sha256','training_dataset','bar_construction',
             'outcome_label','candidate','features','selection_split',
             'walk_forward_plan_sha256','baselines','cost_model','evaluation',
             'prospective_holdout','holdout_state','candidate_locked',
             'single_holdout_evaluation','human_access_before_evidence_freeze',
             'current_claim','claim_ceiling','authority','runtime_consumers',
             'execution_authority'])
          OR NOT public.gate3_schedule_exact_keys(holdout, ARRAY[
             'holdout_id','source','source_version','instrument_ids',
             'coordinate_plan_sha256','bar_interval_seconds',
             'artifact_interval_seconds','start_at','end_at',
             'publication_lag_seconds','first_permitted_access_at',
             'expected_artifact_count','expected_rows','selection_policy',
             'window_semantics','event_timestamp_semantics',
             'acquisition_policy','reference_only','promotion_eligible',
             'runtime_consumers','execution_authority'])
          OR NOT public.gate3_schedule_known_types(payload,
             ARRAY['schema_version','preregistration_id','created_at',
                   'source_tree_sha256','holdout_state','current_claim',
                   'claim_ceiling','authority','walk_forward_plan_sha256'],
             ARRAY['runtime_consumers'],
             ARRAY['candidate_locked','single_holdout_evaluation',
                   'human_access_before_evidence_freeze','execution_authority'],
             ARRAY['features','baselines'],
             ARRAY['training_dataset','bar_construction','outcome_label',
                   'candidate','selection_split','cost_model','evaluation',
                   'prospective_holdout'])
          OR NOT public.gate3_schedule_known_types(holdout,
             ARRAY['holdout_id','source','source_version',
                   'coordinate_plan_sha256','start_at','end_at',
                   'first_permitted_access_at','selection_policy',
                   'window_semantics','event_timestamp_semantics',
                   'acquisition_policy'],
             ARRAY['bar_interval_seconds','artifact_interval_seconds',
                   'publication_lag_seconds','expected_artifact_count',
                   'expected_rows','runtime_consumers'],
             ARRAY['reference_only','promotion_eligible','execution_authority'],
             ARRAY['instrument_ids'],ARRAY[]::text[])
          OR NOT public.gate3_schedule_all_ascii(payload)
          OR NOT public.gate3_prereg_integer_numbers(payload)
          OR NEW.seal_json IS DISTINCT FROM
             public.gate3_schedule_canonical_jsonb(payload)
          OR payload->>'schema_version' IS DISTINCT FROM
             'ctcc.mie.gate3.prospective_preregistration.v1'
          OR payload->>'preregistration_id' IS DISTINCT FROM
             NEW.preregistration_id
          OR payload->>'created_at' IS DISTINCT FROM
             pg_catalog.to_char(NEW.created_at AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          OR payload->>'holdout_state' IS DISTINCT FROM
             'scheduled_unobserved'
          OR payload->>'current_claim' IS DISTINCT FROM 'computational'
          OR payload->>'claim_ceiling' IS DISTINCT FROM 'predictive_oos'
          OR payload->>'authority' IS DISTINCT FROM 'offline_shadow_only'
          OR payload->'candidate_locked' IS DISTINCT FROM 'true'::jsonb
          OR payload->'single_holdout_evaluation' IS DISTINCT FROM 'true'::jsonb
          OR payload->'human_access_before_evidence_freeze'
             IS DISTINCT FROM 'false'::jsonb
          OR payload->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR (payload->'runtime_consumers')::text IS DISTINCT FROM '0'
          OR holdout->>'source' IS DISTINCT FROM 'okx.public_market_source'
          OR holdout->>'source_version' IS DISTINCT FROM
             'okx.history_candles.nine_strings.v1'
          OR holdout->>'holdout_id' IS DISTINCT FROM NEW.holdout_id
          OR holdout->>'coordinate_plan_sha256' IS DISTINCT FROM
             NEW.coordinate_plan_sha256
          OR holdout->>'start_at' IS DISTINCT FROM
             pg_catalog.to_char(NEW.window_start AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          OR holdout->>'end_at' IS DISTINCT FROM
             pg_catalog.to_char(NEW.window_end AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          OR holdout->>'selection_policy' IS DISTINCT FROM
             'fixed_future_calendar_window'
          OR holdout->>'window_semantics' IS DISTINCT FROM
             'start_inclusive_end_exclusive'
          OR holdout->>'event_timestamp_semantics' IS DISTINCT FROM 'bar_close'
          OR holdout->>'acquisition_policy' IS DISTINCT FROM
             'sealed_automation_no_summary'
          OR holdout->'reference_only' IS DISTINCT FROM 'true'::jsonb
          OR holdout->'promotion_eligible' IS DISTINCT FROM 'false'::jsonb
          OR holdout->'execution_authority' IS DISTINCT FROM 'false'::jsonb
          OR (holdout->'runtime_consumers')::text IS DISTINCT FROM '0'
          OR (holdout->'bar_interval_seconds')::text IS DISTINCT FROM '60'
          OR (holdout->'artifact_interval_seconds')::text IS DISTINCT FROM '60'
          OR (holdout->'expected_artifact_count')::text IS DISTINCT FROM
             (holdout->'expected_rows')::text
          OR pg_catalog.jsonb_typeof(holdout->'instrument_ids')
             IS DISTINCT FROM 'array'
          OR pg_catalog.jsonb_array_length(holdout->'instrument_ids')
             NOT BETWEEN 1 AND 4096
        THEN RAISE EXCEPTION 'gate3_prereg_seal_noncanonical'; END IF;
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
            FROM pg_catalog.jsonb_array_elements(holdout->'instrument_ids')
              WITH ORDINALITY AS t(item,ordinal)
          ) instruments;
        first_us := (EXTRACT(EPOCH FROM NEW.window_start)*1000000)::bigint;
        last_us := (EXTRACT(EPOCH FROM NEW.window_end)*1000000)::bigint;
        derived_window_key := pg_catalog.encode(pg_catalog.sha256(
          pg_catalog.convert_to(pg_catalog.concat_ws(chr(31),
            holdout->>'source',instruments_text,first_us::text,last_us::text),
          'UTF8')),'hex');
        IF instrument_count NOT BETWEEN 1 AND 4096
          OR instrument_count <> distinct_instruments
          OR instruments_valid IS DISTINCT FROM true
          OR instruments_text IS DISTINCT FROM instruments_sorted
          OR first_us % 60000000 <> 0 OR last_us % 60000000 <> 0
          OR last_us <= first_us
          OR (last_us-first_us) % 60000000 <> 0
          OR (last_us-first_us)/60000000*instrument_count NOT BETWEEN 1 AND 4096
          OR (holdout->>'expected_rows')::bigint IS DISTINCT FROM
             (last_us-first_us)/60000000*instrument_count
          OR NEW.window_key IS DISTINCT FROM derived_window_key
          OR NEW.created_at >= NEW.window_start
        THEN RAISE EXCEPTION 'gate3_prereg_seal_coordinates_denied'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_direct_role_allowed(p_kind text)
      RETURNS boolean AS $$
      DECLARE safe_role boolean; forbidden_table boolean;
              seal_append boolean; seal_read boolean;
              seal_ack_append boolean; seal_ack_read boolean;
              claim_append boolean; claim_read boolean;
              claim_ack_append boolean; claim_ack_read boolean;
              legacy_access boolean; witness_access boolean;
      BEGIN
        IF p_kind NOT IN ('seal_publish','seal_ack','capture_publish',
                         'capture_ack') THEN RETURN false; END IF;
        SELECT r.rolcanlogin
          AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                   OR r.rolreplication OR r.rolbypassrls)
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles other_role
            WHERE other_role.oid <> r.oid
              AND pg_catalog.pg_has_role(session_user,other_role.oid,'MEMBER'))
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace n
            WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
              AND pg_catalog.has_schema_privilege(session_user,n.oid,'CREATE'))
          AND NOT pg_catalog.has_database_privilege(
            session_user,pg_catalog.current_database(),'CREATE')
          INTO safe_role
          FROM pg_catalog.pg_roles r WHERE r.rolname=session_user;
        IF safe_role IS DISTINCT FROM true THEN RETURN false; END IF;
        SELECT EXISTS (
          SELECT 1 FROM pg_catalog.pg_class c
          WHERE c.oid IN (
            'public.gate3_preregistration_seals'::pg_catalog.regclass,
            'public.gate3_preregistration_seal_acks'::pg_catalog.regclass,
            'public.gate3_capture_schedule_pins'::pg_catalog.regclass,
            'public.gate3_capture_schedule_key_claims'::pg_catalog.regclass,
            'public.gate3_capture_schedule_claim_acks'::pg_catalog.regclass,
            'public.gate3_capture_schedule_legacy_inventory'::pg_catalog.regclass,
            'public.gate3_capture_schedule_publication_acks'::pg_catalog.regclass,
            'public.public_receipt_witness_revisions'::pg_catalog.regclass)
            AND (pg_catalog.pg_has_role(session_user,
                   pg_catalog.pg_get_userbyid(c.relowner),'MEMBER')
              OR pg_catalog.has_table_privilege(session_user,c.oid,
                   'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
              OR pg_catalog.has_any_column_privilege(session_user,c.oid,
                   'SELECT,INSERT,UPDATE,REFERENCES')))
          INTO forbidden_table;
        IF forbidden_table THEN RETURN false; END IF;
        seal_append := pg_catalog.has_function_privilege(session_user,
          'public.gate3_prereg_seal_append(jsonb)'::pg_catalog.regprocedure,
          'EXECUTE');
        seal_read := pg_catalog.has_function_privilege(session_user,
          'public.gate3_prereg_seal_read(text)'::pg_catalog.regprocedure,
          'EXECUTE');
        seal_ack_append := pg_catalog.has_function_privilege(session_user,
          'public.gate3_prereg_seal_ack_append(text)'::pg_catalog.regprocedure,
          'EXECUTE');
        seal_ack_read := pg_catalog.has_function_privilege(session_user,
          'public.gate3_prereg_seal_ack_read(text)'::pg_catalog.regprocedure,
          'EXECUTE');
        claim_append := pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_claim_append(jsonb)'::pg_catalog.regprocedure,
          'EXECUTE');
        claim_read := pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_claim_read(text)'::pg_catalog.regprocedure,
          'EXECUTE');
        claim_ack_append := pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_claim_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
          'EXECUTE');
        claim_ack_read := pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_claim_ack_read(text)'::pg_catalog.regprocedure,
          'EXECUTE');
        legacy_access := pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_append(jsonb)'::pg_catalog.regprocedure,
          'EXECUTE') OR pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_read(text)'::pg_catalog.regprocedure,
          'EXECUTE') OR pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
          'EXECUTE') OR pg_catalog.has_function_privilege(session_user,
          'public.gate3_capture_schedule_ack_read(text)'::pg_catalog.regprocedure,
          'EXECUTE');
        witness_access := pg_catalog.has_function_privilege(session_user,
          'public.public_receipt_witness_append(jsonb)'::pg_catalog.regprocedure,
          'EXECUTE') OR pg_catalog.has_function_privilege(session_user,
          'public.public_receipt_witness_read(text)'::pg_catalog.regprocedure,
          'EXECUTE');
        IF legacy_access OR witness_access THEN RETURN false; END IF;
        IF p_kind = 'seal_publish' THEN
          RETURN seal_append AND seal_read AND NOT seal_ack_append
            AND NOT seal_ack_read AND NOT claim_append AND NOT claim_read
            AND NOT claim_ack_append AND NOT claim_ack_read;
        ELSIF p_kind = 'seal_ack' THEN
          RETURN seal_read AND seal_ack_append AND seal_ack_read
            AND NOT seal_append AND NOT claim_append AND NOT claim_read
            AND NOT claim_ack_append AND NOT claim_ack_read;
        ELSIF p_kind = 'capture_publish' THEN
          RETURN claim_append AND claim_read AND NOT seal_append
            AND NOT seal_read AND NOT seal_ack_append AND NOT seal_ack_read
            AND NOT claim_ack_append AND NOT claim_ack_read;
        END IF;
        RETURN claim_ack_append AND claim_ack_read AND NOT seal_append
          AND NOT seal_read AND NOT seal_ack_append AND NOT seal_ack_read
          AND NOT claim_append AND NOT claim_read;
      END; $$ LANGUAGE plpgsql STABLE STRICT
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_immutable()
      RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'gate3_prereg_immutable'; END;
      $$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_seal_insert_guard
      BEFORE INSERT ON public.gate3_preregistration_seals
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_seal_insert_guard()
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_seal_immutable
      BEFORE UPDATE OR DELETE ON public.gate3_preregistration_seals
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_seal_no_truncate
      BEFORE TRUNCATE ON public.gate3_preregistration_seals
      FOR EACH STATEMENT EXECUTE FUNCTION public.gate3_prereg_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_seal_ack_immutable
      BEFORE UPDATE OR DELETE ON public.gate3_preregistration_seal_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_immutable()
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_seal_ack_no_truncate
      BEFORE TRUNCATE ON public.gate3_preregistration_seal_acks
      FOR EACH STATEMENT EXECUTE FUNCTION public.gate3_prereg_immutable()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_seal_ack_insert_guard()
      RETURNS trigger AS $$
      DECLARE sealed public.gate3_preregistration_seals%ROWTYPE;
      BEGIN
        IF NOT public.gate3_prereg_direct_role_allowed('seal_ack')
        THEN RAISE EXCEPTION 'gate3_prereg_seal_ack_role_denied'; END IF;
        SELECT * INTO sealed FROM public.gate3_preregistration_seals
          WHERE seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND
          OR NEW.preregistration_id IS DISTINCT FROM sealed.preregistration_id
          OR NEW.coordinate_plan_sha256 IS DISTINCT FROM
             sealed.coordinate_plan_sha256
          OR NEW.window_key IS DISTINCT FROM sealed.window_key
          OR NEW.holdout_id IS DISTINCT FROM sealed.holdout_id
          OR NEW.window_start IS DISTINCT FROM sealed.window_start
          OR NEW.window_end IS DISTINCT FROM sealed.window_end
          OR NEW.seal_recorded_at IS DISTINCT FROM sealed.recorded_at
        THEN RAISE EXCEPTION 'gate3_prereg_seal_ack_identity_denied'; END IF;
        NEW.acknowledged_at := pg_catalog.clock_timestamp();
        IF NEW.acknowledged_at < sealed.recorded_at
          OR NEW.acknowledged_at >= sealed.window_start
        THEN RAISE EXCEPTION 'gate3_prereg_seal_ack_late'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_seal_ack_insert_guard
      BEFORE INSERT ON public.gate3_preregistration_seal_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_seal_ack_insert_guard()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_seal_append(p_record jsonb)
      RETURNS void AS $$
      BEGIN
        IF NOT public.gate3_schedule_exact_keys(p_record, ARRAY[
             'seal_sha256','preregistration_id','coordinate_plan_sha256',
             'window_key','holdout_id','window_start','window_end',
             'created_at','seal_json'])
        THEN RAISE EXCEPTION 'gate3_prereg_seal_record_invalid'; END IF;
        INSERT INTO public.gate3_preregistration_seals (
          seal_sha256,preregistration_id,coordinate_plan_sha256,window_key,
          holdout_id,window_start,window_end,created_at,seal_json
        ) VALUES (
          p_record->>'seal_sha256',p_record->>'preregistration_id',
          p_record->>'coordinate_plan_sha256',p_record->>'window_key',
          p_record->>'holdout_id',
          (p_record->>'window_start')::timestamptz,
          (p_record->>'window_end')::timestamptz,
          (p_record->>'created_at')::timestamptz,
          p_record->>'seal_json'
        );
      END; $$ LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_seal_read(p_sha text)
      RETURNS SETOF public.gate3_preregistration_seals AS $$
        SELECT * FROM public.gate3_preregistration_seals
        WHERE seal_sha256=p_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_seal_ack_append(p_sha text)
      RETURNS void AS $$
      DECLARE v_window_key text;
      BEGIN
        IF p_sha !~ '^[a-f0-9]{64}$' THEN
          RAISE EXCEPTION 'gate3_prereg_seal_ack_identity_denied';
        END IF;
        IF NOT public.gate3_prereg_direct_role_allowed('seal_ack') THEN
          RAISE EXCEPTION 'gate3_prereg_seal_ack_role_denied';
        END IF;
        LOCK TABLE public.gate3_preregistration_seals
          IN ROW SHARE MODE NOWAIT;
        LOCK TABLE public.gate3_preregistration_seal_acks
          IN ROW EXCLUSIVE MODE NOWAIT;
        SELECT window_key INTO v_window_key
          FROM public.gate3_preregistration_seals
          WHERE seal_sha256=p_sha FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_prereg_seal_ack_identity_denied';
        END IF;
        IF NOT pg_catalog.pg_try_advisory_xact_lock(
          ('x'||pg_catalog.substr(v_window_key,1,16))::bit(64)::bigint)
        THEN RAISE EXCEPTION 'gate3_prereg_seal_ack_busy'; END IF;
        INSERT INTO public.gate3_preregistration_seal_acks (
          seal_sha256,preregistration_id,coordinate_plan_sha256,window_key,holdout_id,
          window_start,window_end,seal_recorded_at)
        SELECT seal_sha256,preregistration_id,coordinate_plan_sha256,window_key,holdout_id,
          window_start,window_end,recorded_at
        FROM public.gate3_preregistration_seals WHERE seal_sha256=p_sha;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_prereg_seal_ack_identity_denied';
        END IF;
      END; $$ LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_seal_ack_read(p_sha text)
      RETURNS SETOF public.gate3_preregistration_seal_acks AS $$
        SELECT * FROM public.gate3_preregistration_seal_acks
        WHERE seal_sha256=p_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)

    # These new triggers apply even if a legacy security-definer function was
    # already running when the migration began. They also prevent accidental
    # combined grants from collapsing the four direct-login roles.
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_capture_role_guard()
      RETURNS trigger AS $$
      BEGIN
        IF NOT public.gate3_prereg_direct_role_allowed('capture_publish')
        THEN RAISE EXCEPTION 'gate3_prereg_capture_role_denied'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_capture_role_guard
      BEFORE INSERT ON public.gate3_capture_schedule_pins
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_capture_role_guard()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_capture_ack_role_guard()
      RETURNS trigger AS $$
      BEGIN
        IF NOT public.gate3_prereg_direct_role_allowed('capture_ack')
        THEN RAISE EXCEPTION 'gate3_prereg_capture_ack_role_denied'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_capture_ack_role_guard
      BEFORE INSERT ON public.gate3_capture_schedule_claim_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_capture_ack_role_guard()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_claim_seal_guard()
      RETURNS trigger AS $$
      DECLARE pinned public.gate3_capture_schedule_pins%ROWTYPE;
              sealed public.gate3_preregistration_seals%ROWTYPE;
              acknowledged public.gate3_preregistration_seal_acks%ROWTYPE;
      BEGIN
        SELECT * INTO pinned FROM public.gate3_capture_schedule_pins
          WHERE schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_prereg_claim_pin_missing';
        END IF;
        SELECT * INTO sealed FROM public.gate3_preregistration_seals
          WHERE seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_prereg_claim_seal_missing';
        END IF;
        SELECT * INTO acknowledged FROM public.gate3_preregistration_seal_acks
          WHERE seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_prereg_claim_ack_missing';
        END IF;
        IF NEW.seal_sha256 IS DISTINCT FROM pinned.seal_sha256
          OR acknowledged.preregistration_id IS DISTINCT FROM
             sealed.preregistration_id
          OR sealed.coordinate_plan_sha256 IS DISTINCT FROM
             pinned.coordinate_plan_sha256
          OR sealed.window_key IS DISTINCT FROM pinned.window_key
          OR sealed.holdout_id IS DISTINCT FROM pinned.holdout_id
          OR sealed.window_start IS DISTINCT FROM pinned.window_start
          OR sealed.window_end IS DISTINCT FROM pinned.window_end
          OR acknowledged.coordinate_plan_sha256 IS DISTINCT FROM
             sealed.coordinate_plan_sha256
          OR acknowledged.window_key IS DISTINCT FROM sealed.window_key
          OR acknowledged.holdout_id IS DISTINCT FROM sealed.holdout_id
          OR acknowledged.window_start IS DISTINCT FROM sealed.window_start
          OR acknowledged.window_end IS DISTINCT FROM sealed.window_end
          OR acknowledged.seal_recorded_at IS DISTINCT FROM sealed.recorded_at
          OR NOT sealed.created_at <= sealed.recorded_at
          OR NOT sealed.recorded_at <= acknowledged.acknowledged_at
          OR NOT acknowledged.acknowledged_at < pinned.planned_at
          OR NOT pinned.planned_at <= pinned.recorded_at
          OR pg_catalog.clock_timestamp() >= pinned.window_start
        THEN RAISE EXCEPTION 'gate3_prereg_claim_identity_denied'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_claim_seal_guard
      BEFORE INSERT ON public.gate3_capture_schedule_key_claims
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_claim_seal_guard()
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_prereg_claim_ack_seal_guard()
      RETURNS trigger AS $$
      DECLARE claimed public.gate3_capture_schedule_key_claims%ROWTYPE;
              pinned public.gate3_capture_schedule_pins%ROWTYPE;
              acknowledged public.gate3_preregistration_seal_acks%ROWTYPE;
      BEGIN
        SELECT * INTO claimed FROM public.gate3_capture_schedule_key_claims
          WHERE schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_prereg_capture_ack_unsealed';
        END IF;
        SELECT * INTO pinned FROM public.gate3_capture_schedule_pins
          WHERE schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'gate3_prereg_capture_ack_unsealed';
        END IF;
        SELECT * INTO acknowledged FROM public.gate3_preregistration_seal_acks
          WHERE seal_sha256=claimed.seal_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND OR claimed.seal_sha256 IS DISTINCT FROM pinned.seal_sha256
          OR acknowledged.coordinate_plan_sha256 IS DISTINCT FROM
             pinned.coordinate_plan_sha256
          OR acknowledged.window_key IS DISTINCT FROM pinned.window_key
          OR acknowledged.holdout_id IS DISTINCT FROM pinned.holdout_id
          OR NOT acknowledged.acknowledged_at < pinned.planned_at
        THEN RAISE EXCEPTION 'gate3_prereg_capture_ack_unsealed'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE TRIGGER gate3_prereg_claim_ack_seal_guard
      BEFORE INSERT ON public.gate3_capture_schedule_claim_acks
      FOR EACH ROW EXECUTE FUNCTION public.gate3_prereg_claim_ack_seal_guard()
    """)
    # Existing function OIDs and grants survive, but old 0029 rows cannot be
    # read as seal-qualified merely because they retain a canonical claim.
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_claim_read(
        p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_pins AS $$
        SELECT p.* FROM public.gate3_capture_schedule_pins p
        JOIN public.gate3_capture_schedule_key_claims c
          ON c.schedule_sha256=p.schedule_sha256
        JOIN public.gate3_preregistration_seals s
          ON s.seal_sha256=c.seal_sha256
        JOIN public.gate3_preregistration_seal_acks a
          ON a.seal_sha256=s.seal_sha256
        WHERE p.schedule_sha256=p_schedule_sha
          AND c.seal_sha256=p.seal_sha256
          AND c.coordinate_plan_sha256=p.coordinate_plan_sha256
          AND c.window_key=p.window_key AND c.holdout_id=p.holdout_id
          AND c.window_start=p.window_start AND c.window_end=p.window_end
          AND s.coordinate_plan_sha256=p.coordinate_plan_sha256
          AND s.window_key=p.window_key AND s.holdout_id=p.holdout_id
          AND s.window_start=p.window_start AND s.window_end=p.window_end
          AND a.coordinate_plan_sha256=s.coordinate_plan_sha256
          AND a.preregistration_id=s.preregistration_id
          AND a.window_key=s.window_key AND a.holdout_id=s.holdout_id
          AND a.window_start=s.window_start AND a.window_end=s.window_end
          AND a.seal_recorded_at=s.recorded_at
          AND s.created_at<=s.recorded_at
          AND s.recorded_at<=a.acknowledged_at
          AND a.acknowledged_at<p.planned_at
          AND p.planned_at<=p.recorded_at
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_claim_ack_read(
        p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_claim_acks AS $$
        SELECT ca.* FROM public.gate3_capture_schedule_claim_acks ca
        JOIN public.gate3_capture_schedule_key_claims c
          ON c.schedule_sha256=ca.schedule_sha256
        JOIN public.gate3_capture_schedule_pins p
          ON p.schedule_sha256=c.schedule_sha256
        JOIN public.gate3_preregistration_seals s
          ON s.seal_sha256=c.seal_sha256
        JOIN public.gate3_preregistration_seal_acks a
          ON a.seal_sha256=s.seal_sha256
        WHERE ca.schedule_sha256=p_schedule_sha
          AND ca.seal_sha256=p.seal_sha256
          AND ca.coordinate_plan_sha256=p.coordinate_plan_sha256
          AND ca.window_key=p.window_key AND ca.holdout_id=p.holdout_id
          AND c.seal_sha256=p.seal_sha256
          AND s.coordinate_plan_sha256=p.coordinate_plan_sha256
          AND s.window_key=p.window_key AND s.holdout_id=p.holdout_id
          AND s.window_start=p.window_start AND s.window_end=p.window_end
          AND a.coordinate_plan_sha256=s.coordinate_plan_sha256
          AND a.preregistration_id=s.preregistration_id
          AND a.window_key=s.window_key AND a.holdout_id=s.holdout_id
          AND a.window_start=s.window_start AND a.window_end=s.window_end
          AND a.seal_recorded_at=s.recorded_at
          AND s.recorded_at<=a.acknowledged_at
          AND a.acknowledged_at<p.planned_at
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    for signature in (
        "gate3_prereg_integer_numbers(jsonb)",
        "gate3_prereg_direct_role_allowed(text)",
        "gate3_prereg_seal_insert_guard()",
        "gate3_prereg_seal_ack_insert_guard()",
        "gate3_prereg_immutable()",
        "gate3_prereg_capture_role_guard()",
        "gate3_prereg_capture_ack_role_guard()",
        "gate3_prereg_claim_seal_guard()",
        "gate3_prereg_claim_ack_seal_guard()",
        "gate3_prereg_seal_append(jsonb)",
        "gate3_prereg_seal_read(text)",
        "gate3_prereg_seal_ack_append(text)",
        "gate3_prereg_seal_ack_read(text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")


def downgrade():
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_pins,
        public.gate3_capture_schedule_key_claims,
        public.gate3_capture_schedule_claim_acks,
        public.gate3_preregistration_seals,
        public.gate3_preregistration_seal_acks
      IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.gate3_preregistration_seals)
          OR EXISTS(SELECT 1 FROM public.gate3_preregistration_seal_acks)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_key_claims)
          OR EXISTS(SELECT 1 FROM public.gate3_capture_schedule_claim_acks)
        THEN RAISE EXCEPTION 'gate3_prereg_downgrade_requires_empty'; END IF;
      END $$
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_claim_read(
        p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_pins AS $$
        SELECT p.* FROM public.gate3_capture_schedule_pins p
        JOIN public.gate3_capture_schedule_key_claims c
          ON c.schedule_sha256=p.schedule_sha256
        WHERE p.schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.gate3_capture_schedule_claim_ack_read(
        p_schedule_sha text)
      RETURNS SETOF public.gate3_capture_schedule_claim_acks AS $$
        SELECT * FROM public.gate3_capture_schedule_claim_acks
        WHERE schedule_sha256=p_schedule_sha
      $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """)
    for trigger, table in (
        ("gate3_prereg_claim_ack_seal_guard", "gate3_capture_schedule_claim_acks"),
        ("gate3_prereg_claim_seal_guard", "gate3_capture_schedule_key_claims"),
        ("gate3_prereg_capture_ack_role_guard", "gate3_capture_schedule_claim_acks"),
        ("gate3_prereg_capture_role_guard", "gate3_capture_schedule_pins"),
        ("gate3_prereg_seal_ack_insert_guard", "gate3_preregistration_seal_acks"),
        ("gate3_prereg_seal_ack_no_truncate", "gate3_preregistration_seal_acks"),
        ("gate3_prereg_seal_ack_immutable", "gate3_preregistration_seal_acks"),
        ("gate3_prereg_seal_no_truncate", "gate3_preregistration_seals"),
        ("gate3_prereg_seal_immutable", "gate3_preregistration_seals"),
        ("gate3_prereg_seal_insert_guard", "gate3_preregistration_seals"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON public.{table}")
    for signature in (
        "gate3_prereg_seal_ack_append(text)",
        "gate3_prereg_seal_ack_read(text)",
        "gate3_prereg_seal_append(jsonb)",
        "gate3_prereg_seal_read(text)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.drop_table("gate3_preregistration_seal_acks")
    op.drop_table("gate3_preregistration_seals")
    for signature in (
        "gate3_prereg_claim_ack_seal_guard()",
        "gate3_prereg_claim_seal_guard()",
        "gate3_prereg_capture_ack_role_guard()",
        "gate3_prereg_capture_role_guard()",
        "gate3_prereg_seal_ack_insert_guard()",
        "gate3_prereg_seal_insert_guard()",
        "gate3_prereg_immutable()",
        "gate3_prereg_direct_role_allowed(text)",
        "gate3_prereg_integer_numbers(jsonb)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
