"""Commit a fitted candidate and independent ACK before prospective use.

Revision ID: 0056
Revises: 0055

No grants or role provisioning occur here. Independent code owns artifact
replay, UTC evidence and the before-window observation of committed ACKs.
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

_BINDINGS = "gate3_fitted_candidate_bindings"
_ACKS = "gate3_fitted_candidate_acks"
_EVALUATIONS = "gate3_fitted_candidate_evaluations"
_TABLES = (_BINDINGS, _ACKS, _EVALUATIONS)
_APIS = (
    ("gate3_fitted_binding_append(text,text,text)", _BINDINGS, 1),
    ("gate3_fitted_binding_read(text)", _BINDINGS, 2),
    ("gate3_fitted_binding_ack_append(text,text)", _ACKS, 4),
    ("gate3_fitted_binding_ack_read(text)", _ACKS, 8),
    ("gate3_fitted_evaluation_append(text,text,text,text,text,text)", _EVALUATIONS, 16),
    ("gate3_fitted_evaluation_read(text)", _EVALUATIONS, 32),
)
_INTERNAL = (
    "gate3_fitted_role(integer)",
    "gate3_fitted_durability(boolean)",
    "gate3_fitted_hash(text)",
    "gate3_fitted_json_hash(jsonb)",
    "gate3_fitted_lock(text)",
    "gate3_fitted_seal(text)",
    "gate3_fitted_holdout_access(jsonb,timestamptz)",
    "gate3_fitted_binding_matches(text,text,text,public.gate3_preregistration_seals)",
    "gate3_fitted_binding_insert_guard()",
    "gate3_fitted_ack_insert_guard()",
    "gate3_fitted_evaluation_insert_guard()",
    "gate3_fitted_immutable()",
    "gate3_fitted_commit_guard()",
    "gate3_fitted_candidate_claim_guard()",
)


def _tables():
    op.execute("""
      CREATE TABLE public.gate3_fitted_candidate_bindings (
        seal_sha256 VARCHAR(64) NOT NULL,
        binding_sha256 VARCHAR(64) NOT NULL,
        binding_json TEXT NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        publisher_oid OID NOT NULL,
        publication_xid TEXT NOT NULL,
        CONSTRAINT pk_gate3_fitted_candidate_bindings PRIMARY KEY(seal_sha256),
        CONSTRAINT fk_gate3_fitted_binding_seal FOREIGN KEY(seal_sha256)
          REFERENCES public.gate3_preregistration_seals(seal_sha256),
        CONSTRAINT uq_gate3_fitted_candidate_bindings_binding_sha256 UNIQUE(binding_sha256),
        CONSTRAINT ck_gate3_fitted_candidate_bindings_hashes CHECK (
          seal_sha256 ~ '^[a-f0-9]{64}$' AND binding_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_fitted_candidate_bindings_payload CHECK (
          pg_catalog.octet_length(binding_json) BETWEEN 1 AND 16384),
        CONSTRAINT ck_gate3_fitted_candidate_bindings_xids CHECK (
          publication_xid ~ '^[1-9][0-9]{0,19}$')
      )
    """)
    op.execute("""
      CREATE TABLE public.gate3_fitted_candidate_acks (
        seal_sha256 VARCHAR(64) NOT NULL,
        binding_sha256 VARCHAR(64) NOT NULL,
        acknowledged_at TIMESTAMPTZ NOT NULL,
        observer_oid OID NOT NULL,
        acknowledgement_xid TEXT NOT NULL,
        CONSTRAINT pk_gate3_fitted_candidate_acks PRIMARY KEY(seal_sha256),
        CONSTRAINT fk_gate3_fitted_ack_binding FOREIGN KEY(seal_sha256)
          REFERENCES public.gate3_fitted_candidate_bindings(seal_sha256),
        CONSTRAINT ck_gate3_fitted_candidate_acks_hashes CHECK (
          seal_sha256 ~ '^[a-f0-9]{64}$' AND binding_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_fitted_candidate_acks_xids CHECK (
          acknowledgement_xid ~ '^[1-9][0-9]{0,19}$')
      )
    """)
    op.execute("""
      CREATE TABLE public.gate3_fitted_candidate_evaluations (
        seal_sha256 VARCHAR(64) NOT NULL,
        binding_sha256 VARCHAR(64) NOT NULL,
        acknowledgement_xid TEXT NOT NULL,
        receipt_sha256 VARCHAR(64) NOT NULL,
        dataset_sha256 VARCHAR(64) NOT NULL,
        attempt_sha256 VARCHAR(64) NOT NULL,
        reserved_at TIMESTAMPTZ NOT NULL,
        evaluator_oid OID NOT NULL,
        reservation_xid TEXT NOT NULL,
        CONSTRAINT pk_gate3_fitted_candidate_evaluations PRIMARY KEY(seal_sha256),
        CONSTRAINT fk_gate3_fitted_evaluation_ack FOREIGN KEY(seal_sha256)
          REFERENCES public.gate3_fitted_candidate_acks(seal_sha256),
        CONSTRAINT ck_gate3_fitted_candidate_evaluations_hashes CHECK (
          seal_sha256 ~ '^[a-f0-9]{64}$' AND binding_sha256 ~ '^[a-f0-9]{64}$'
          AND receipt_sha256 ~ '^[a-f0-9]{64}$' AND dataset_sha256 ~ '^[a-f0-9]{64}$'
          AND attempt_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_gate3_fitted_candidate_evaluations_xids CHECK (
          acknowledgement_xid ~ '^[1-9][0-9]{0,19}$'
          AND reservation_xid ~ '^[1-9][0-9]{0,19}$')
      )
    """)
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")


def _role():
    signatures = ",".join(
        f"'public.{signature}'::pg_catalog.regprocedure::oid"
        for signature, _, _ in _APIS
    )
    op.execute(f"""
      CREATE FUNCTION public.gate3_fitted_role(required_bit integer)
      RETURNS oid LANGUAGE plpgsql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE actor oid := pg_catalog.to_regrole(session_user)::oid;
              boundary oid[] := ARRAY[{signatures}];
              routine oid; bit_value integer := 1; mask integer := 0;
      BEGIN
        IF required_bit IS NULL OR required_bit NOT IN (1,2,4,8,16,32) OR NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid=actor AND r.rolcanlogin
            AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                     OR r.rolreplication OR r.rolbypassrls)
        ) OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid<>actor
            AND pg_catalog.pg_has_role(actor,r.oid,'MEMBER')
        ) OR pg_catalog.has_database_privilege(
          actor,pg_catalog.current_database(),'CREATE'
        ) OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_namespace n
          WHERE n.nspname !~ '^pg_(temp|toast_temp)_[0-9]+$'
            AND pg_catalog.has_schema_privilege(actor,n.oid,'CREATE')
        ) OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_class c
          WHERE c.oid IN (
            'public.gate3_fitted_candidate_bindings'::pg_catalog.regclass,
            'public.gate3_fitted_candidate_acks'::pg_catalog.regclass,
            'public.gate3_fitted_candidate_evaluations'::pg_catalog.regclass,
            'public.gate3_preregistration_seals'::pg_catalog.regclass,
            'public.gate3_preregistration_seal_acks'::pg_catalog.regclass,
            'public.gate3_capture_schedule_pins'::pg_catalog.regclass,
            'public.gate3_capture_schedule_key_claims'::pg_catalog.regclass,
            'public.gate3_capture_schedule_claim_acks'::pg_catalog.regclass)
          AND (c.relowner=actor OR pg_catalog.has_table_privilege(actor,c.oid,
                 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
               OR pg_catalog.has_any_column_privilege(actor,c.oid,
                 'SELECT,INSERT,UPDATE,REFERENCES'))
        ) OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_proc p
          WHERE p.oid IN (
            'public.gate3_prereg_seal_append(jsonb)'::pg_catalog.regprocedure,
            'public.gate3_prereg_seal_ack_append(text)'::pg_catalog.regprocedure,
            'public.gate3_capture_schedule_claim_append(jsonb)'::pg_catalog.regprocedure,
            'public.gate3_capture_schedule_claim_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure,
            'public.gate3_capture_schedule_append(jsonb)'::pg_catalog.regprocedure,
            'public.gate3_capture_schedule_ack_append(text,text,text,text,text)'::pg_catalog.regprocedure)
          AND pg_catalog.has_function_privilege(actor,p.oid,'EXECUTE')
        ) THEN RAISE EXCEPTION 'gate3_fitted_role_denied'; END IF;

        FOREACH routine IN ARRAY boundary LOOP
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc p
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE p.oid=routine AND (p.proowner=actor OR
              (a.privilege_type='EXECUTE' AND
                (a.grantee=0 OR (a.grantee=actor AND a.is_grantable))))
          ) THEN RAISE EXCEPTION 'gate3_fitted_role_denied'; END IF;
          IF pg_catalog.has_function_privilege(actor,routine,'EXECUTE') THEN
            IF NOT EXISTS (
              SELECT 1 FROM pg_catalog.pg_proc p
              CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
              WHERE p.oid=routine AND a.grantee=actor
                AND a.privilege_type='EXECUTE' AND NOT a.is_grantable
            ) THEN RAISE EXCEPTION 'gate3_fitted_role_denied'; END IF;
            mask := mask | bit_value;
          END IF;
          bit_value := bit_value * 2;
        END LOOP;

        IF mask NOT IN (3,14,10,58) OR (mask & required_bit) <> required_bit
        THEN RAISE EXCEPTION 'gate3_fitted_role_denied'; END IF;
        RETURN actor;
      END; $ctcc$
    """)


def _helpers():
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_durability(p_write boolean)
      RETURNS void LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $ctcc$
      BEGIN
        IF pg_catalog.current_setting('server_encoding') <> 'UTF8'
          OR pg_catalog.current_setting('fsync') <> 'on'
          OR pg_catalog.current_setting('full_page_writes') <> 'on'
          OR (p_write AND (
            pg_catalog.current_setting('synchronous_commit') <> 'on'
            OR pg_catalog.current_setting('transaction_read_only') <> 'off'))
        THEN RAISE EXCEPTION 'gate3_fitted_durability_denied'; END IF;
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_hash(value text)
      RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $ctcc$
        SELECT COALESCE(value COLLATE "C" ~ '^[a-f0-9]{64}$',false)
      $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_json_hash(value jsonb)
      RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $ctcc$
        SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
          public.gate3_schedule_canonical_jsonb(value),'UTF8')),'hex')
      $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_lock(p_seal text)
      RETURNS void LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,pg_temp AS $ctcc$
      BEGIN
        IF NOT public.gate3_fitted_hash(p_seal)
        THEN RAISE EXCEPTION 'gate3_fitted_identity_denied'; END IF;
        IF NOT pg_catalog.pg_try_advisory_xact_lock(
          pg_catalog.hashtextextended('ctcc.gate3.fitted:'||p_seal,0))
        THEN RAISE EXCEPTION 'gate3_fitted_busy'; END IF;
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_seal(p_seal text)
      RETURNS public.gate3_preregistration_seals
      LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE sealed public.gate3_preregistration_seals%ROWTYPE;
              ack public.gate3_preregistration_seal_acks%ROWTYPE;
      BEGIN
        LOCK TABLE public.gate3_preregistration_seals,
          public.gate3_preregistration_seal_acks IN ROW SHARE MODE NOWAIT;
        SELECT s.* INTO sealed FROM public.gate3_preregistration_seals s
          WHERE s.seal_sha256=p_seal FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN RAISE EXCEPTION 'gate3_fitted_seal_missing'; END IF;
        SELECT a.* INTO ack FROM public.gate3_preregistration_seal_acks a
          WHERE a.seal_sha256=p_seal FOR KEY SHARE NOWAIT;
        IF NOT FOUND OR
          (ack.preregistration_id,ack.coordinate_plan_sha256,ack.window_key,
           ack.holdout_id,ack.window_start,ack.window_end,ack.seal_recorded_at)
          IS DISTINCT FROM
          (sealed.preregistration_id,sealed.coordinate_plan_sha256,sealed.window_key,
           sealed.holdout_id,sealed.window_start,sealed.window_end,sealed.recorded_at)
          OR NOT sealed.created_at<=sealed.recorded_at
          OR NOT sealed.recorded_at<=ack.acknowledged_at
          OR NOT ack.acknowledged_at<sealed.window_start
          OR NOT ack.acknowledged_at<=pg_catalog.clock_timestamp()
        THEN RAISE EXCEPTION 'gate3_fitted_seal_ack_denied'; END IF;
        RETURN sealed;
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_holdout_access(holdout jsonb, window_end timestamptz)
      RETURNS timestamptz LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE access_text text := holdout->>'first_permitted_access_at';
              lag_text text := holdout->>'publication_lag_seconds'; access_at timestamptz;
      BEGIN
        IF pg_catalog.jsonb_typeof(holdout->'first_permitted_access_at') IS DISTINCT FROM 'string'
          OR pg_catalog.jsonb_typeof(holdout->'publication_lag_seconds') IS DISTINCT FROM 'number'
          OR access_text COLLATE "C" !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
          OR lag_text COLLATE "C" !~ '^(0|[1-9][0-9]{0,19})$'
        THEN RAISE EXCEPTION 'gate3_fitted_access_time_denied'; END IF;
        BEGIN
          access_at := access_text::timestamptz;
          IF pg_catalog.to_char(access_at AT TIME ZONE 'UTC',
              'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') IS DISTINCT FROM access_text
            OR NOT access_at>=window_end
            OR EXTRACT(EPOCH FROM (access_at-window_end)) IS DISTINCT FROM lag_text::numeric
          THEN RAISE EXCEPTION 'gate3_fitted_access_time_denied'; END IF;
        EXCEPTION WHEN data_exception THEN
          RAISE EXCEPTION 'gate3_fitted_access_time_denied';
        END;
        RETURN access_at;
      END; $ctcc$
    """)


def _binding_validation():
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_binding_matches(
        p_seal text,p_binding text,p_json text,sealed public.gate3_preregistration_seals)
      RETURNS void LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE body jsonb; source jsonb; seal jsonb; candidate jsonb;
              cohort jsonb; registry jsonb; holdout jsonb; field text;
              selected_count bigint; selected_pin text; legacy_count bigint; legacy_pin text;
              hash_fields text[] := ARRAY[
                'source_archive_sha256','preregistration_sha256','holdout_coordinate_sha256',
                'training_dataset_sha256','campaign_sha256','campaign_plan_sha256',
                'campaign_completion_sha256','campaign_root_binding_sha256','row_inventory_sha256',
                'batch_sha256','study_sha256','study_plan_sha256','feature_plan_sha256',
                'legacy_score_bundle_sha256','legacy_score_source_sha256','trial_result_sha256',
                'trial_plan_sha256','trial_registry_sha256','selected_trial_configuration_sha256',
                'fitted_model_sha256'];
              flags text[] := ARRAY[
                'independent_publication_verified','legacy_score_origin_verified',
                'evaluator_first_access_verified','predictive_oos_eligible','execution_authority'];
      BEGIN
        IF NOT public.gate3_fitted_hash(p_binding) OR p_json IS NULL
          OR pg_catalog.octet_length(p_json) NOT BETWEEN 1 AND 16384
          OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p_json,'UTF8')),'hex')
             IS DISTINCT FROM p_binding
        THEN RAISE EXCEPTION 'gate3_fitted_binding_bytes_denied'; END IF;
        BEGIN
          body := p_json::jsonb;
          seal := sealed.seal_json::jsonb;
        EXCEPTION WHEN data_exception THEN
          RAISE EXCEPTION 'gate3_fitted_binding_json_denied';
        END;
        source := body->'source_checkpoint';
        candidate := seal->'candidate';
        cohort := seal->'training_dataset';
        holdout := seal->'prospective_holdout';
        registry := seal #> '{evaluation,trials}';
        IF public.gate3_schedule_exact_keys(body,
               hash_fields || flags || ARRAY['schema_version','source_checkpoint']) IS NOT TRUE
          OR body->>'schema_version' IS DISTINCT FROM 'ctcc.mie.devval_fitted_candidate_binding.v1'
          OR public.gate3_schedule_exact_keys(source,ARRAY['commit','tree','manifest_sha256']) IS NOT TRUE
          OR pg_catalog.jsonb_typeof(source->'commit') IS DISTINCT FROM 'string'
          OR pg_catalog.jsonb_typeof(source->'tree') IS DISTINCT FROM 'string'
          OR pg_catalog.jsonb_typeof(source->'manifest_sha256') IS DISTINCT FROM 'string'
          OR (source->>'commit') COLLATE "C" !~ '^[a-f0-9]{40}$'
          OR (source->>'tree') COLLATE "C" !~ '^[a-f0-9]{40}$'
          OR NOT public.gate3_fitted_hash(source->>'manifest_sha256')
        THEN RAISE EXCEPTION 'gate3_fitted_binding_shape_denied'; END IF;
        FOREACH field IN ARRAY hash_fields LOOP
          IF pg_catalog.jsonb_typeof(body->field) IS DISTINCT FROM 'string'
            OR NOT public.gate3_fitted_hash(body->>field)
          THEN RAISE EXCEPTION 'gate3_fitted_binding_shape_denied'; END IF;
        END LOOP;
        FOREACH field IN ARRAY flags LOOP
          IF body->field IS DISTINCT FROM 'false'::jsonb
          THEN RAISE EXCEPTION 'gate3_fitted_binding_shape_denied'; END IF;
        END LOOP;
        IF p_json IS DISTINCT FROM public.gate3_schedule_canonical_jsonb(body)
          OR pg_catalog.jsonb_typeof(registry->'trials') IS DISTINCT FROM 'array'
          OR pg_catalog.jsonb_typeof(seal->'baselines') IS DISTINCT FROM 'array'
        THEN RAISE EXCEPTION 'gate3_fitted_binding_shape_denied'; END IF;
        SELECT count(*),max(item->>'configuration_sha256') INTO selected_count,selected_pin
          FROM pg_catalog.jsonb_array_elements(registry->'trials') AS entries(item)
          WHERE item->>'trial_id'=candidate->>'selected_trial_id';
        SELECT count(*),max(item->>'configuration_sha256') INTO legacy_count,legacy_pin
          FROM pg_catalog.jsonb_array_elements(seal->'baselines') AS entries(item)
          WHERE item->>'kind'='frozen_legacy_score';
        IF body->>'preregistration_sha256' IS DISTINCT FROM p_seal
          OR body->>'holdout_coordinate_sha256' IS DISTINCT FROM sealed.coordinate_plan_sha256
          OR holdout->>'coordinate_plan_sha256' IS DISTINCT FROM sealed.coordinate_plan_sha256
          OR body->>'training_dataset_sha256' IS DISTINCT FROM public.gate3_fitted_json_hash(cohort)
          OR body->>'batch_sha256' IS DISTINCT FROM cohort->>'content_sha256'
          OR body->>'campaign_sha256' IS DISTINCT FROM cohort->>'manifest_sha256'
          OR cohort->'instrument_ids' IS DISTINCT FROM holdout->'instrument_ids'
          OR cohort->'source' IS DISTINCT FROM holdout->'source'
          OR cohort->'source_version' IS DISTINCT FROM holdout->'source_version'
          OR body->>'study_plan_sha256' IS DISTINCT FROM seal->>'walk_forward_plan_sha256'
          OR body->>'trial_registry_sha256' IS DISTINCT FROM public.gate3_fitted_json_hash(registry)
          OR body->>'selected_trial_configuration_sha256' IS DISTINCT FROM candidate->>'configuration_sha256'
          OR candidate->>'model_version' IS DISTINCT FROM 'empirical-probability-bins.v1'
          OR public.gate3_fitted_json_hash(source) IS DISTINCT FROM seal->>'source_tree_sha256'
          OR public.gate3_fitted_json_hash(source) IS DISTINCT FROM candidate->>'source_sha256'
          OR selected_count<>1 OR selected_pin IS DISTINCT FROM candidate->>'configuration_sha256'
          OR legacy_count<>1 OR legacy_pin IS DISTINCT FROM body->>'legacy_score_source_sha256'
        THEN RAISE EXCEPTION 'gate3_fitted_binding_seal_mismatch'; END IF;
        PERFORM public.gate3_fitted_holdout_access(holdout,sealed.window_end);
      END; $ctcc$
    """)


def _insert_guards():
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_binding_insert_guard()
      RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE sealed public.gate3_preregistration_seals%ROWTYPE;
      BEGIN
        NEW.publisher_oid := public.gate3_fitted_role(1);
        PERFORM public.gate3_fitted_durability(true);
        PERFORM public.gate3_fitted_lock(NEW.seal_sha256);
        sealed := public.gate3_fitted_seal(NEW.seal_sha256);
        PERFORM public.gate3_fitted_binding_matches(
          NEW.seal_sha256,NEW.binding_sha256,NEW.binding_json,sealed);
        NEW.recorded_at := pg_catalog.clock_timestamp();
        IF NOT (NEW.recorded_at<sealed.window_start AND EXISTS (
          SELECT 1 FROM public.gate3_preregistration_seal_acks a
          WHERE a.seal_sha256=NEW.seal_sha256 AND a.acknowledged_at<=NEW.recorded_at
        ))
        THEN RAISE EXCEPTION 'gate3_fitted_binding_late'; END IF;
        NEW.publication_xid := pg_catalog.pg_current_xact_id()::text;
        RETURN NEW;
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_ack_insert_guard()
      RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE sealed public.gate3_preregistration_seals%ROWTYPE;
              bound public.gate3_fitted_candidate_bindings%ROWTYPE;
      BEGIN
        NEW.observer_oid := public.gate3_fitted_role(4);
        PERFORM public.gate3_fitted_durability(true);
        PERFORM public.gate3_fitted_lock(NEW.seal_sha256);
        sealed := public.gate3_fitted_seal(NEW.seal_sha256);
        LOCK TABLE public.gate3_fitted_candidate_bindings IN ROW SHARE MODE NOWAIT;
        SELECT b.* INTO bound FROM public.gate3_fitted_candidate_bindings b
          WHERE b.seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        NEW.acknowledged_at := pg_catalog.clock_timestamp();
        NEW.acknowledgement_xid := pg_catalog.pg_current_xact_id()::text;
        IF NOT FOUND OR bound.binding_sha256 IS DISTINCT FROM NEW.binding_sha256
          OR bound.publisher_oid=NEW.observer_oid OR bound.publication_xid=NEW.acknowledgement_xid
          OR NOT bound.recorded_at<=NEW.acknowledged_at
          OR NOT NEW.acknowledged_at<sealed.window_start
        THEN RAISE EXCEPTION 'gate3_fitted_ack_denied'; END IF;
        RETURN NEW;
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_evaluation_insert_guard()
      RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE sealed public.gate3_preregistration_seals%ROWTYPE;
              bound public.gate3_fitted_candidate_bindings%ROWTYPE;
              ack public.gate3_fitted_candidate_acks%ROWTYPE;
              first_access timestamptz;
      BEGIN
        NEW.evaluator_oid := public.gate3_fitted_role(16);
        PERFORM public.gate3_fitted_durability(true);
        PERFORM public.gate3_fitted_lock(NEW.seal_sha256);
        sealed := public.gate3_fitted_seal(NEW.seal_sha256);
        first_access := public.gate3_fitted_holdout_access(
          sealed.seal_json::jsonb->'prospective_holdout',sealed.window_end);
        LOCK TABLE public.gate3_fitted_candidate_bindings,
          public.gate3_fitted_candidate_acks IN ROW SHARE MODE NOWAIT;
        SELECT b.* INTO bound FROM public.gate3_fitted_candidate_bindings b
          WHERE b.seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN RAISE EXCEPTION 'gate3_fitted_evaluation_denied'; END IF;
        SELECT a.* INTO ack FROM public.gate3_fitted_candidate_acks a
          WHERE a.seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        NEW.reserved_at := pg_catalog.clock_timestamp();
        NEW.reservation_xid := pg_catalog.pg_current_xact_id()::text;
        IF NOT FOUND OR bound.binding_sha256 IS DISTINCT FROM NEW.binding_sha256
          OR ack.binding_sha256 IS DISTINCT FROM NEW.binding_sha256
          OR ack.acknowledgement_xid IS DISTINCT FROM NEW.acknowledgement_xid
          OR NEW.evaluator_oid IN (bound.publisher_oid,ack.observer_oid)
          OR NEW.reservation_xid IN (bound.publication_xid,ack.acknowledgement_xid)
          OR NOT bound.recorded_at<=ack.acknowledged_at
          OR NOT ack.acknowledged_at<sealed.window_start
          OR NOT NEW.reserved_at>=first_access
        THEN RAISE EXCEPTION 'gate3_fitted_evaluation_denied'; END IF;
        RETURN NEW;
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_immutable()
      RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $ctcc$
      BEGIN RAISE EXCEPTION 'gate3_fitted_immutable'; END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_commit_guard()
      RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE sealed public.gate3_preregistration_seals%ROWTYPE;
      BEGIN
        PERFORM public.gate3_fitted_durability(true);
        sealed := public.gate3_fitted_seal(NEW.seal_sha256);
        IF TG_TABLE_NAME<>'gate3_fitted_candidate_evaluations'
          AND NOT pg_catalog.clock_timestamp()<sealed.window_start
        THEN RAISE EXCEPTION 'gate3_fitted_commit_late'; END IF;
        RETURN NULL;
      END; $ctcc$
    """)
    for table, kind in zip(_TABLES, ("binding", "ack", "evaluation"), strict=True):
        op.execute(f"""
          CREATE TRIGGER gate3_fitted_{kind}_insert_guard BEFORE INSERT ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.gate3_fitted_{kind}_insert_guard()
        """)
        op.execute(f"""
          CREATE TRIGGER gate3_fitted_{kind}_immutable BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.gate3_fitted_immutable()
        """)
        op.execute(f"""
          CREATE TRIGGER gate3_fitted_{kind}_no_truncate BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.gate3_fitted_immutable()
        """)
        op.execute(f"""
          CREATE CONSTRAINT TRIGGER gate3_fitted_{kind}_commit_guard AFTER INSERT ON public.{table}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.gate3_fitted_commit_guard()
        """)
        for suffix in ("insert_guard", "immutable", "no_truncate", "commit_guard"):
            op.execute(
                f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER gate3_fitted_{kind}_{suffix}"
            )


def _api():
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_binding_append(p_seal text,p_binding text,p_json text)
      RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      BEGIN
        PERFORM public.gate3_fitted_role(1);
        IF NOT public.gate3_fitted_hash(p_seal) OR NOT public.gate3_fitted_hash(p_binding)
        THEN RAISE EXCEPTION 'gate3_fitted_identity_denied'; END IF;
        LOCK TABLE public.gate3_fitted_candidate_bindings IN ROW EXCLUSIVE MODE NOWAIT;
        INSERT INTO public.gate3_fitted_candidate_bindings(seal_sha256,binding_sha256,binding_json)
          VALUES (p_seal,p_binding,p_json);
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_binding_ack_append(p_seal text,p_binding text)
      RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      BEGIN
        PERFORM public.gate3_fitted_role(4);
        IF NOT public.gate3_fitted_hash(p_seal) OR NOT public.gate3_fitted_hash(p_binding)
        THEN RAISE EXCEPTION 'gate3_fitted_identity_denied'; END IF;
        LOCK TABLE public.gate3_fitted_candidate_acks IN ROW EXCLUSIVE MODE NOWAIT;
        INSERT INTO public.gate3_fitted_candidate_acks(seal_sha256,binding_sha256)
          VALUES (p_seal,p_binding);
      END; $ctcc$
    """)
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_evaluation_append(
        p_seal text,p_binding text,p_ack_xid text,p_receipt text,p_dataset text,p_attempt text)
      RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      BEGIN
        PERFORM public.gate3_fitted_role(16);
        IF NOT (public.gate3_fitted_hash(p_seal) AND public.gate3_fitted_hash(p_binding)
          AND public.gate3_fitted_hash(p_receipt) AND public.gate3_fitted_hash(p_dataset)
          AND public.gate3_fitted_hash(p_attempt))
          OR p_ack_xid IS NULL OR p_ack_xid COLLATE "C" !~ '^[1-9][0-9]{0,19}$'
        THEN RAISE EXCEPTION 'gate3_fitted_identity_denied'; END IF;
        LOCK TABLE public.gate3_fitted_candidate_evaluations IN ROW EXCLUSIVE MODE NOWAIT;
        INSERT INTO public.gate3_fitted_candidate_evaluations(
          seal_sha256,binding_sha256,acknowledgement_xid,receipt_sha256,dataset_sha256,attempt_sha256)
          VALUES (p_seal,p_binding,p_ack_xid,p_receipt,p_dataset,p_attempt);
      END; $ctcc$
    """)
    for signature, table, role in _APIS:
        if role not in (2, 8, 32):
            continue
        name = signature.removesuffix("(text)")
        op.execute(f"""
          CREATE FUNCTION public.{name}(p_seal text)
          RETURNS SETOF public.{table} LANGUAGE plpgsql STABLE SECURITY DEFINER
          SET search_path=pg_catalog,pg_temp AS $ctcc$
          BEGIN
            PERFORM public.gate3_fitted_role({role});
            PERFORM public.gate3_fitted_durability(false);
            IF NOT public.gate3_fitted_hash(p_seal)
            THEN RAISE EXCEPTION 'gate3_fitted_identity_denied'; END IF;
            RETURN QUERY SELECT entry.* FROM public.{table} entry WHERE entry.seal_sha256=p_seal;
          END; $ctcc$
        """)


def _claim_guard():
    op.execute("""
      CREATE FUNCTION public.gate3_fitted_candidate_claim_guard()
      RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $ctcc$
      DECLARE sealed public.gate3_preregistration_seals%ROWTYPE;
              pinned public.gate3_capture_schedule_pins%ROWTYPE;
              ack public.gate3_fitted_candidate_acks%ROWTYPE;
      BEGIN
        LOCK TABLE public.gate3_preregistration_seals IN ROW SHARE MODE NOWAIT;
        SELECT s.* INTO sealed FROM public.gate3_preregistration_seals s
          WHERE s.seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND OR sealed.seal_json::jsonb #>> '{candidate,model_version}'
            IS DISTINCT FROM 'empirical-probability-bins.v1'
        THEN RETURN NEW; END IF;
        PERFORM public.gate3_fitted_lock(NEW.seal_sha256);
        LOCK TABLE public.gate3_capture_schedule_pins,
          public.gate3_fitted_candidate_acks IN ROW SHARE MODE NOWAIT;
        SELECT p.* INTO pinned FROM public.gate3_capture_schedule_pins p
          WHERE p.schedule_sha256=NEW.schedule_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN RAISE EXCEPTION 'gate3_fitted_claim_ack_required'; END IF;
        SELECT a.* INTO ack FROM public.gate3_fitted_candidate_acks a
          WHERE a.seal_sha256=NEW.seal_sha256 FOR KEY SHARE NOWAIT;
        IF NOT FOUND OR pinned.seal_sha256 IS DISTINCT FROM NEW.seal_sha256
          OR pinned.window_start IS DISTINCT FROM sealed.window_start
          OR ack.acknowledgement_xid=pg_catalog.pg_current_xact_id()::text
          OR NOT ack.acknowledged_at<pinned.planned_at
          OR NOT ack.acknowledged_at<sealed.window_start
          OR NOT pg_catalog.clock_timestamp()<sealed.window_start
        THEN RAISE EXCEPTION 'gate3_fitted_claim_ack_required'; END IF;
        RETURN NEW;
      END; $ctcc$
    """)
    op.execute("""
      CREATE TRIGGER gate3_fitted_candidate_claim_guard
        BEFORE INSERT ON public.gate3_capture_schedule_key_claims
        FOR EACH ROW EXECUTE FUNCTION public.gate3_fitted_candidate_claim_guard()
    """)
    op.execute("""
      ALTER TABLE public.gate3_capture_schedule_key_claims
        ENABLE ALWAYS TRIGGER gate3_fitted_candidate_claim_guard
    """)


def upgrade():
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_key_claims,
        public.gate3_preregistration_seals,public.gate3_preregistration_seal_acks
        IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $ctcc$ BEGIN
        IF EXISTS (
          SELECT 1 FROM public.gate3_capture_schedule_key_claims c
          JOIN public.gate3_preregistration_seals s ON s.seal_sha256=c.seal_sha256
          WHERE s.seal_json::jsonb #>> '{candidate,model_version}'
            ='empirical-probability-bins.v1')
        THEN RAISE EXCEPTION 'gate3_fitted_upgrade_existing_claim'; END IF;
      END; $ctcc$
    """)
    _tables()
    _role()
    _helpers()
    _binding_validation()
    _insert_guards()
    _api()
    _claim_guard()
    for signature in (*_INTERNAL, *(api[0] for api in _APIS)):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")


def downgrade():
    op.execute("""
      LOCK TABLE public.gate3_capture_schedule_key_claims,
        public.gate3_preregistration_seals,public.gate3_preregistration_seal_acks,
        public.gate3_fitted_candidate_bindings,public.gate3_fitted_candidate_acks,
        public.gate3_fitted_candidate_evaluations IN ACCESS EXCLUSIVE MODE NOWAIT
    """)
    op.execute("""
      DO $ctcc$ BEGIN
        IF EXISTS(SELECT 1 FROM public.gate3_fitted_candidate_bindings)
          OR EXISTS(SELECT 1 FROM public.gate3_fitted_candidate_acks)
          OR EXISTS(SELECT 1 FROM public.gate3_fitted_candidate_evaluations)
        THEN RAISE EXCEPTION 'gate3_fitted_downgrade_requires_empty'; END IF;
      END; $ctcc$
    """)
    op.execute("""
      DROP TRIGGER gate3_fitted_candidate_claim_guard
        ON public.gate3_capture_schedule_key_claims
    """)
    for signature, _, _ in _APIS:
        op.execute(f"DROP FUNCTION public.{signature}")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE public.{table}")
    for signature in reversed(_INTERNAL):
        op.execute(f"DROP FUNCTION public.{signature}")
