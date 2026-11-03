"""Uninstalled DB0053 CURRENT lineage guards; no complete revision issuer.

The trigger only cross-binds existing private source/custody/control rows. It
returns no raw bundle or source bytes. Installation grants no application role
access. Runtime catalogue checks pin this exact trigger body and row shape.
"""

import hashlib

TABLE = "demo_collector_current_revision_lineages"
GUARD = "collector_current_revision_lineage_guard_v1"
IMMUTABLE = "collector_current_revision_lineage_immutable_v1"

TABLE_DDL = """
CREATE TABLE public.demo_collector_current_revision_lineages (
 lineage_sha256 varchar(64) PRIMARY KEY,
 environment varchar(8) NOT NULL,
 account_id varchar(32) NOT NULL,
 settlement_currency varchar(16) NOT NULL,
 account_revision bigint NOT NULL,
 ledger_revision bigint NOT NULL,
 claims_sha256 varchar(64),
 claims_json text,
 control_revision bigint NOT NULL,
 control_event_sha256 varchar(64) NOT NULL,
 control_state_sha256 varchar(64) NOT NULL,
 current_capture_id varchar(32) NOT NULL,
 current_terminal_sequence bigint NOT NULL,
 current_head_sha256 varchar(64) NOT NULL,
 custody_id varchar(32) NOT NULL,
 lineage_json text NOT NULL,
 db_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT fk_collector_current_scope FOREIGN KEY(environment,account_id,settlement_currency)
   REFERENCES public.qualification_account_scopes(environment,account_id,settlement_currency) ON DELETE RESTRICT,
 CONSTRAINT fk_collector_current_control FOREIGN KEY(environment,account_id,control_revision)
   REFERENCES public.demo_control_journal(environment,account_id,control_revision) ON DELETE RESTRICT,
 CONSTRAINT fk_collector_current_source FOREIGN KEY(current_capture_id,current_terminal_sequence)
   REFERENCES public.demo_account_capture_events(capture_id,sequence) ON DELETE RESTRICT,
 CONSTRAINT fk_collector_current_custody FOREIGN KEY(custody_id)
   REFERENCES public.public_runtime_raw_bundles_v1(bundle_id) ON DELETE RESTRICT,
 CONSTRAINT uq_collector_current_lineage_binding UNIQUE
   (environment,account_id,settlement_currency,account_revision,control_revision,current_capture_id),
 CONSTRAINT collector_current_scope CHECK
   (environment='demo' AND account_id ~ '^[0-9]{1,32}$' AND settlement_currency ~ '^[A-Z0-9]{1,16}$'),
 CONSTRAINT collector_current_revisions CHECK
   (account_revision>=0 AND ledger_revision>=account_revision AND control_revision>0 AND current_terminal_sequence>0),
 CONSTRAINT collector_current_claims_pair CHECK
   ((account_revision=0 AND claims_json IS NULL AND claims_sha256 IS NULL)
    OR (account_revision>0 AND claims_json IS NOT NULL AND claims_sha256 ~ '^[a-f0-9]{64}$')),
 CONSTRAINT collector_current_digests CHECK
   (lineage_sha256 ~ '^[a-f0-9]{64}$' AND current_head_sha256 ~ '^[a-f0-9]{64}$'
    AND current_capture_id ~ '^[a-f0-9]{32}$' AND custody_id ~ '^[a-f0-9]{32}$'
    AND control_event_sha256 ~ '^[a-f0-9]{64}$' AND control_state_sha256 ~ '^[a-f0-9]{64}$'),
 CONSTRAINT collector_current_bounds CHECK
   (octet_length(lineage_json) BETWEEN 1 AND 262144
    AND (claims_json IS NULL OR octet_length(claims_json) BETWEEN 1 AND 8388608))
)
"""

GUARD_BODY = """
DECLARE a RECORD; c RECORD; cj RECORD; b RECORD; f RECORD; t RECORD; e RECORD;
        role_row RECORD; j jsonb; bd jsonb; part jsonb; lock_key bigint;
        captured_count bigint; n bigint := 0; sampled timestamptz; expires timestamptz;
BEGIN
  -- SECURITY DEFINER is limited to cross-checking existing private rows. It
  -- does not expose those rows and cannot be called as a data-returning API.
  IF current_setting('role')<>'none' THEN
    RAISE EXCEPTION 'collector_lineage_role_denied';
  END IF;
  SELECT oid,rolsuper,rolcreaterole,rolbypassrls INTO role_row
    FROM pg_catalog.pg_roles WHERE rolname=session_user;
  IF role_row.oid IS NULL OR role_row.rolsuper OR role_row.rolcreaterole OR role_row.rolbypassrls THEN
    RAISE EXCEPTION 'collector_lineage_role_denied';
  END IF;
  lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')
    ||decode('00','hex')||convert_to(NEW.environment,'UTF8')||decode('00','hex')
    ||convert_to(NEW.account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint;
  PERFORM pg_advisory_xact_lock(lock_key);
  SELECT * INTO c FROM public.demo_account_controls
    WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
  SELECT * INTO a FROM public.qualification_account_scopes
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND settlement_currency=NEW.settlement_currency FOR UPDATE;
  SELECT * INTO cj FROM public.demo_control_journal
    WHERE environment=NEW.environment AND account_id=NEW.account_id
      AND control_revision=NEW.control_revision FOR KEY SHARE;
  SELECT * INTO b FROM public.public_runtime_raw_bundles_v1
    WHERE bundle_id=NEW.custody_id FOR KEY SHARE;
  SELECT * INTO f FROM public.demo_account_capture_events
    WHERE capture_id=NEW.current_capture_id AND sequence=1 FOR KEY SHARE;
  SELECT * INTO t FROM public.demo_account_capture_events
    WHERE capture_id=NEW.current_capture_id AND sequence=NEW.current_terminal_sequence FOR KEY SHARE;
  j := NEW.lineage_json::jsonb;
  bd := convert_from(b.bundle_raw,'UTF8')::jsonb;
  sampled := (j->>'owner_sampled_at')::timestamptz;
  expires := (j->>'original_expires_at')::timestamptz;
  IF NEW.environment<>'demo' OR NEW.account_id !~ '^[0-9]{1,32}$'
    OR NEW.settlement_currency !~ '^[A-Z0-9]{1,16}$'
    OR NEW.account_revision<0 OR NEW.ledger_revision<NEW.account_revision
    OR NEW.control_revision<=0 OR NEW.current_terminal_sequence<=0
    OR octet_length(NEW.lineage_json) NOT BETWEEN 1 AND 262144
    OR a.account_revision IS NULL OR c.control_revision IS NULL OR cj.control_revision IS NULL
    OR b.bundle_id IS NULL OR f.sequence IS NULL OR t.sequence IS NULL
    OR ROW(a.account_revision,a.ledger_revision,a.claims_json,a.claims_sha256)
       IS DISTINCT FROM ROW(NEW.account_revision,NEW.ledger_revision,NEW.claims_json,NEW.claims_sha256)
    OR c.control_revision IS DISTINCT FROM NEW.control_revision
    OR c.state_sha256 IS DISTINCT FROM NEW.control_state_sha256
    OR cj.state_sha256 IS DISTINCT FROM c.state_sha256
    OR cj.event_sha256 IS DISTINCT FROM NEW.control_event_sha256
    OR encode(sha256(convert_to(c.state_json,'UTF8')),'hex') IS DISTINCT FROM c.state_sha256
    OR encode(sha256(convert_to(cj.event_json,'UTF8')),'hex') IS DISTINCT FROM cj.event_sha256
    OR ROW(f.environment,f.account_id,f.settlement_currency)
       IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
    OR ROW(t.environment,t.account_id,t.settlement_currency)
       IS DISTINCT FROM ROW(NEW.environment,NEW.account_id,NEW.settlement_currency)
    OR t.event_sha256 IS DISTINCT FROM NEW.current_head_sha256
    OR t.event_json::jsonb->>'kind' IS DISTINCT FROM 'terminal'
    OR t.event_json::jsonb->>'outcome' IS DISTINCT FROM 'complete_recorded'
    OR f.event_json::jsonb->'data'->'plan'->>'contract_version' IS DISTINCT FROM 'ctcc.demo_current_account_plan.v7'
    OR b.contract_version IS DISTINCT FROM 1
    OR encode(sha256(b.bundle_raw),'hex') IS DISTINCT FROM b.bundle_sha256
    OR encode(sha256(convert_to(NEW.lineage_json,'UTF8')),'hex') IS DISTINCT FROM NEW.lineage_sha256
    OR jsonb_typeof(j) IS DISTINCT FROM 'object'
    OR j->>'schema_version' IS DISTINCT FROM 'ctcc.collector_owned_current_revision_lineage.v1'
    OR j->'scope' IS DISTINCT FROM jsonb_build_array(NEW.environment,NEW.account_id,NEW.settlement_currency)
    OR j->'account_revision' IS DISTINCT FROM to_jsonb(NEW.account_revision)
    OR j->'ledger_revision' IS DISTINCT FROM to_jsonb(NEW.ledger_revision)
    OR j->>'claims_sha256' IS DISTINCT FROM NEW.claims_sha256
    OR j->>'claims_binding_kind' IS DISTINCT FROM 'UNAUTHENTICATED_LEDGER_SNAPSHOT'
    OR j->'control_revision' IS DISTINCT FROM to_jsonb(NEW.control_revision)
    OR j->>'control_event_sha256' IS DISTINCT FROM NEW.control_event_sha256
    OR j->>'control_state_sha256' IS DISTINCT FROM NEW.control_state_sha256
    OR j->>'custody_id' IS DISTINCT FROM b.bundle_id
    OR j->>'source_bundle_sha256' IS DISTINCT FROM b.bundle_sha256
    OR j->>'proof_envelope_sha256' IS DISTINCT FROM b.proof_envelope_sha256
    OR j->>'source_policy_sha256' IS DISTINCT FROM bd->>'policy_sha256'
    OR j->'source_reference' IS DISTINCT FROM bd->'proof_envelope'->'source_reference'
    OR j->'source_reference'->>'capture_id' IS DISTINCT FROM NEW.current_capture_id
    OR j->'source_reference'->>'head_sha256' IS DISTINCT FROM NEW.current_head_sha256
    OR j->'source_reference'->>'plan_sha256' IS DISTINCT FROM f.event_json::jsonb->'data'->>'plan_sha256'
    OR j->'source_reference'->>'session_binding_sha256' IS DISTINCT FROM f.event_json::jsonb->'data'->>'session_binding_sha256'
    OR j->>'native_proof_sha256' IS DISTINCT FROM bd->'proof_envelope'->>'native_proof_sha256'
    OR j->>'native_readback_sha256' IS DISTINCT FROM bd->'proof_envelope'->>'native_readback_sha256'
    OR j->'db_role_oid' IS DISTINCT FROM to_jsonb(role_row.oid::bigint)
    OR j->'db_backend_pid' IS DISTINCT FROM to_jsonb(pg_backend_pid())
    OR j->>'db_transaction_id' IS DISTINCT FROM pg_current_xact_id()::text
    OR j->'account_revision_issued' IS DISTINCT FROM 'false'::jsonb
    OR j->'account_complete' IS DISTINCT FROM 'false'::jsonb
    OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
    OR j->>'admission' IS DISTINCT FROM 'DENY'
    OR j->'unresolved_obligations' IS DISTINCT FROM jsonb_build_array(
      'complete_source_derived_portfolio_snapshot_unavailable',
      'full_history_funding_and_finality_unproven',
      'source_derived_loss_streak_seed_unknown',
      'preserved_native_hwm_and_drawdown_window_unproven',
      'registered_account_region_unverified','complete_account_revision_not_issued')
    OR sampled IS NULL OR expires IS NULL OR sampled>=expires OR clock_timestamp()>=expires
    OR (j->>'packet_completed_at')::timestamptz > (j->>'db_recorded_at')::timestamptz
    OR (j->>'db_recorded_at')::timestamptz > sampled
    OR t.db_recorded_at > (j->>'db_recorded_at')::timestamptz
    OR b.recorded_at > (j->>'db_recorded_at')::timestamptz
    OR (NEW.account_revision=0 AND (NEW.claims_json IS NOT NULL OR NEW.claims_sha256 IS NOT NULL))
    OR (NEW.account_revision>0 AND (NEW.claims_json IS NULL OR NEW.claims_sha256 IS NULL
        OR octet_length(NEW.claims_json) NOT BETWEEN 1 AND 8388608
        OR encode(sha256(convert_to(NEW.claims_json,'UTF8')),'hex') IS DISTINCT FROM NEW.claims_sha256))
    OR jsonb_typeof(bd->'chain') IS DISTINCT FROM 'array'
    OR jsonb_array_length(bd->'chain') IS DISTINCT FROM NEW.current_terminal_sequence THEN
    RAISE EXCEPTION 'collector_lineage_binding_denied';
  END IF;
  SELECT count(*) INTO captured_count FROM public.demo_account_capture_events
    WHERE capture_id=NEW.current_capture_id;
  IF captured_count<>NEW.current_terminal_sequence THEN
    RAISE EXCEPTION 'collector_lineage_binding_denied';
  END IF;
  FOR part IN SELECT value FROM jsonb_array_elements(bd->'chain') LOOP
    n := n+1;
    SELECT * INTO e FROM public.demo_account_capture_events
      WHERE capture_id=NEW.current_capture_id AND sequence=n FOR KEY SHARE;
    IF e.sequence IS NULL
      OR part->>'event_hex' IS DISTINCT FROM encode(convert_to(e.event_json,'UTF8'),'hex')
      OR part->>'raw_hex' IS DISTINCT FROM encode(e.raw_body,'hex')
      OR part->>'packet_hex' IS DISTINCT FROM encode(e.packet_payload,'hex')
      OR (part->>'db_recorded_at')::timestamptz IS DISTINCT FROM e.db_recorded_at THEN
      RAISE EXCEPTION 'collector_lineage_original_bytes_changed';
    END IF;
  END LOOP;
  -- UID serialization also enforces the composite identity if its index has
  -- drifted; runtime catalogue admission independently requires that index.
  IF EXISTS(SELECT 1 FROM public.demo_collector_current_revision_lineages x
      WHERE ROW(x.environment,x.account_id,x.settlement_currency,x.account_revision,
                x.control_revision,x.current_capture_id)
        =ROW(NEW.environment,NEW.account_id,NEW.settlement_currency,NEW.account_revision,
             NEW.control_revision,NEW.current_capture_id)) THEN
    RAISE EXCEPTION 'collector_lineage_duplicate';
  END IF;
  NEW.db_recorded_at := clock_timestamp();
  RETURN NEW;
EXCEPTION WHEN others THEN
  RAISE EXCEPTION 'collector_lineage_write_denied';
END;
"""

IMMUTABLE_BODY = """
BEGIN
  RAISE EXCEPTION 'collector_lineage_immutable';
END;
"""

FUNCTIONS_DDL = (
    "CREATE FUNCTION public."
    + GUARD
    + "() RETURNS trigger AS $ctcc$"
    + GUARD_BODY
    + "$ctcc$ LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp;"
    + "\nCREATE FUNCTION public."
    + IMMUTABLE
    + "() RETURNS trigger AS $ctcc$"
    + IMMUTABLE_BODY
    + "$ctcc$ LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp;"
)

TRIGGERS_DDL = """
CREATE TRIGGER collector_current_lineage_insert BEFORE INSERT
 ON public.demo_collector_current_revision_lineages FOR EACH ROW
 EXECUTE FUNCTION public.collector_current_revision_lineage_guard_v1();
CREATE TRIGGER collector_current_lineage_immutable BEFORE UPDATE OR DELETE
 ON public.demo_collector_current_revision_lineages FOR EACH ROW
 EXECUTE FUNCTION public.collector_current_revision_lineage_immutable_v1();
CREATE TRIGGER collector_current_lineage_no_truncate BEFORE TRUNCATE
 ON public.demo_collector_current_revision_lineages FOR EACH STATEMENT
 EXECUTE FUNCTION public.collector_current_revision_lineage_immutable_v1();
REVOKE ALL ON public.demo_collector_current_revision_lineages FROM PUBLIC;
REVOKE ALL ON FUNCTION public.collector_current_revision_lineage_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.collector_current_revision_lineage_immutable_v1() FROM PUBLIC;
"""

DDL = TABLE_DDL + ";\n" + FUNCTIONS_DDL + "\n" + TRIGGERS_DDL
DDL_SHA256 = hashlib.sha256(DDL.encode("utf-8")).hexdigest()

# This is a source-body/structural admission, not a claim of a separately
# reviewed pg_get_functiondef/native deparser baseline or platform authenticity.
CATALOG_SQL = """
SELECT jsonb_build_object(
 'owner',c.relowner::bigint,'actor',(SELECT oid::bigint FROM pg_roles WHERE rolname=session_user),
 'table_ok',c.relkind='r' AND c.relpersistence='p' AND NOT c.relrowsecurity
   AND NOT c.relforcerowsecurity AND NOT pg_has_role(session_user,c.relowner,'MEMBER'),
 'parent_owners',NOT EXISTS(SELECT 1 FROM pg_class p WHERE p.oid IN(
   'public.qualification_account_scopes'::regclass,'public.demo_account_controls'::regclass,
   'public.demo_control_journal'::regclass,'public.demo_account_capture_events'::regclass,
   'public.public_runtime_raw_bundles_v1'::regclass) AND p.relowner<>c.relowner),
 'columns',(SELECT jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull)
   ORDER BY a.attnum) FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
 'constraints',(SELECT jsonb_agg(jsonb_build_object(
   'name',k.conname,'kind',k.contype::text,'validated',k.convalidated,'deferrable',k.condeferrable,
   'columns',(SELECT jsonb_agg(a.attname ORDER BY z.n) FROM unnest(k.conkey) WITH ORDINALITY z(v,n)
     JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=z.v),
   'parent',CASE WHEN k.confrelid=0 THEN NULL ELSE k.confrelid::regclass::text END,
   'parent_columns',(SELECT jsonb_agg(a.attname ORDER BY z.n) FROM unnest(k.confkey) WITH ORDINALITY z(v,n)
     JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=z.v)) ORDER BY k.conname)
   FROM pg_constraint k WHERE k.conrelid=c.oid),
 'triggers',(SELECT jsonb_agg(jsonb_build_array(t.tgname,t.tgtype,t.tgenabled::text,p.proname)
   ORDER BY t.tgname) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
   WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
 'functions',(SELECT jsonb_agg(jsonb_build_object(
   'name',p.proname,'owner',p.proowner::bigint,'body',p.prosrc,'security_definer',p.prosecdef,
   'language',l.lanname,'args',p.pronargs,'returns',p.prorettype::regtype::text,
   'volatility',p.provolatile::text,'parallel',p.proparallel::text,'config',p.proconfig,
   'other_acl',EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
      WHERE x.grantee<>p.proowner)) ORDER BY p.proname)
   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
   WHERE n.nspname='public' AND p.proname IN
     ('collector_current_revision_lineage_guard_v1','collector_current_revision_lineage_immutable_v1')),
 'bad_table_acl',EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
   WHERE x.grantee<>c.relowner AND (x.grantee<>(SELECT oid FROM pg_roles WHERE rolname=session_user)
      OR x.privilege_type NOT IN ('SELECT','INSERT') OR x.is_grantable)),
 'column_acl',EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL),
 'public_schema_create',EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL
   aclexplode(n.nspacl) x WHERE n.nspname='public' AND x.grantee=0 AND x.privilege_type='CREATE'))
FROM pg_class c WHERE c.oid='public.demo_collector_current_revision_lineages'::regclass
"""

COLUMNS = [
    ["lineage_sha256", "character varying(64)", True],
    ["environment", "character varying(8)", True],
    ["account_id", "character varying(32)", True],
    ["settlement_currency", "character varying(16)", True],
    ["account_revision", "bigint", True],
    ["ledger_revision", "bigint", True],
    ["claims_sha256", "character varying(64)", False],
    ["claims_json", "text", False],
    ["control_revision", "bigint", True],
    ["control_event_sha256", "character varying(64)", True],
    ["control_state_sha256", "character varying(64)", True],
    ["current_capture_id", "character varying(32)", True],
    ["current_terminal_sequence", "bigint", True],
    ["current_head_sha256", "character varying(64)", True],
    ["custody_id", "character varying(32)", True],
    ["lineage_json", "text", True],
    ["db_recorded_at", "timestamp with time zone", True],
]

TRIGGERS = [
    ["collector_current_lineage_immutable", 27, "O", IMMUTABLE],
    ["collector_current_lineage_insert", 7, "O", GUARD],
    ["collector_current_lineage_no_truncate", 34, "O", IMMUTABLE],
]

KEYS = {
    "demo_collector_current_revision_lineages_pkey": (
        "p",
        ["lineage_sha256"],
        None,
        None,
    ),
    "uq_collector_current_lineage_binding": (
        "u",
        [
            "environment",
            "account_id",
            "settlement_currency",
            "account_revision",
            "control_revision",
            "current_capture_id",
        ],
        None,
        None,
    ),
    "fk_collector_current_scope": (
        "f",
        ["environment", "account_id", "settlement_currency"],
        "qualification_account_scopes",
        ["environment", "account_id", "settlement_currency"],
    ),
    "fk_collector_current_control": (
        "f",
        ["environment", "account_id", "control_revision"],
        "demo_control_journal",
        ["environment", "account_id", "control_revision"],
    ),
    "fk_collector_current_source": (
        "f",
        ["current_capture_id", "current_terminal_sequence"],
        "demo_account_capture_events",
        ["capture_id", "sequence"],
    ),
    "fk_collector_current_custody": (
        "f",
        ["custody_id"],
        "public_runtime_raw_bundles_v1",
        ["bundle_id"],
    ),
}
CHECKS = frozenset(
    {
        "collector_current_scope",
        "collector_current_revisions",
        "collector_current_claims_pair",
        "collector_current_digests",
        "collector_current_bounds",
    }
)


def verify_catalog(value):
    from app.domain.account_revision_collector_lineage_v1 import deny

    if (
        type(value) is not dict
        or set(value)
        != {
            "owner",
            "actor",
            "table_ok",
            "parent_owners",
            "columns",
            "constraints",
            "triggers",
            "functions",
            "bad_table_acl",
            "column_acl",
            "public_schema_create",
        }
        or type(value["owner"]) is not int
        or type(value["actor"]) is not int
        or value["owner"] <= 0
        or value["actor"] <= 0
        or value["owner"] == value["actor"]
        or value["table_ok"] is not True
        or value["parent_owners"] is not True
        or value["bad_table_acl"] is not False
        or value["column_acl"] is not False
        or value["public_schema_create"] is not False
        or value["columns"] != COLUMNS
        or value["triggers"] != TRIGGERS
    ):
        deny("collector_lineage_schema_or_role_changed")
    functions = value["functions"]
    if type(functions) is not list or len(functions) != 2:
        deny("collector_lineage_schema_or_role_changed")
    for item, name, body, security in zip(
        functions,
        (GUARD, IMMUTABLE),
        (GUARD_BODY, IMMUTABLE_BODY),
        (True, False),
        strict=True,
    ):
        if (
            type(item) is not dict
            or set(item)
            != {
                "name",
                "owner",
                "body",
                "security_definer",
                "language",
                "args",
                "returns",
                "volatility",
                "parallel",
                "config",
                "other_acl",
            }
            or item
            != {
                "name": name,
                "owner": value["owner"],
                "body": body,
                "security_definer": security,
                "language": "plpgsql",
                "args": 0,
                "returns": "trigger",
                "volatility": "v",
                "parallel": "u",
                "config": ["search_path=pg_catalog, pg_temp"],
                "other_acl": False,
            }
        ):
            deny("collector_lineage_schema_or_role_changed")
    constraints = value["constraints"]
    if type(constraints) is not list or len(constraints) != len(KEYS) + len(CHECKS):
        deny("collector_lineage_schema_or_role_changed")
    seen = set()
    for item in constraints:
        if (
            type(item) is not dict
            or set(item)
            != {
                "name",
                "kind",
                "validated",
                "deferrable",
                "columns",
                "parent",
                "parent_columns",
            }
            or type(item["name"]) is not str
            or item["name"] in seen
            or item["validated"] is not True
            or item["deferrable"] is not False
        ):
            deny("collector_lineage_schema_or_role_changed")
        seen.add(item["name"])
        if item["name"] in KEYS:
            kind, columns, parent, parent_columns = KEYS[item["name"]]
            actual_parent = item["parent"]
            if type(actual_parent) is str and actual_parent.startswith("public."):
                actual_parent = actual_parent[7:]
            if (
                item["kind"],
                item["columns"],
                actual_parent,
                item["parent_columns"],
            ) != (kind, columns, parent, parent_columns):
                deny("collector_lineage_schema_or_role_changed")
        elif item["name"] not in CHECKS or item["kind"] != "c":
            deny("collector_lineage_schema_or_role_changed")
    if seen != set(KEYS) | CHECKS:
        deny("collector_lineage_schema_or_role_changed")
