"""DB0051 extension of DB0048/49 coverage custody; native seal starts pending.

No original event, reservation, intent, transition or historical coverage row is
rewritten. The single immutable marker references an existing active epoch.
"""

from app.domain import prospective_event_cutover_v2 as records

PG17_DEPARSED_BASELINE = None
PG17_DEPARSED_BASELINE_STATUS = "UNSEALED_REQUIRES_NATIVE_PG17_CAPTURE"
TABLE = "demo_controlled_writer_coverage_cutovers"
PARENTS = (
    "demo_account_controls",
    "qualification_account_scopes",
    "qualification_reservations",
    "qualification_reservation_transitions",
    "demo_controlled_writer_coverage_scopes",
    "demo_controlled_writer_coverage_journal",
)
COLUMNS = (
    ("environment", "character varying(8)"),
    ("account_id", "character varying(32)"),
    ("settlement_currency", "character varying(16)"),
    ("epoch_id", "character varying(32)"),
    ("active_journal_sequence", "bigint"),
    ("active_event_sha256", "character varying(64)"),
    ("cutover_id", "character varying(64)"),
    ("record_sha256", "character varying(64)"),
    ("record_json", "text"),
    ("coverage_start_at", "timestamp with time zone"),
    ("recorded_at", "timestamp with time zone"),
)
CONSTRAINTS = (
    "pk_cwc_cutover",
    "uq_cwc_cutover_id",
    "fk_cwc_cutover_active",
    "fk_cwc_cutover_scope",
    "ck_cwc_cutover_identity",
    "ck_cwc_cutover_hashes",
    "ck_cwc_cutover_bytes",
    "ck_cwc_cutover_clock",
)
TABLE_SQL = """
CREATE TABLE public.demo_controlled_writer_coverage_cutovers (
 environment varchar(8) NOT NULL, account_id varchar(32) NOT NULL,
 settlement_currency varchar(16) NOT NULL, epoch_id varchar(32) NOT NULL,
 active_journal_sequence bigint NOT NULL, active_event_sha256 varchar(64) NOT NULL,
 cutover_id varchar(64) NOT NULL, record_sha256 varchar(64) NOT NULL,
 record_json text NOT NULL, coverage_start_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT pk_cwc_cutover PRIMARY KEY(environment,account_id,epoch_id),
 CONSTRAINT uq_cwc_cutover_id UNIQUE(cutover_id),
 CONSTRAINT fk_cwc_cutover_active FOREIGN KEY(environment,account_id,active_journal_sequence)
  REFERENCES public.demo_controlled_writer_coverage_journal(environment,account_id,journal_sequence) ON DELETE RESTRICT,
 CONSTRAINT fk_cwc_cutover_scope FOREIGN KEY(environment,account_id,settlement_currency)
  REFERENCES public.qualification_account_scopes(environment,account_id,settlement_currency) ON DELETE RESTRICT,
 CONSTRAINT ck_cwc_cutover_identity CHECK(environment='demo' AND account_id ~ '^[0-9]{1,32}$'
  AND settlement_currency='USDT' AND epoch_id ~ '^[a-f0-9]{32}$' AND active_journal_sequence>1),
 CONSTRAINT ck_cwc_cutover_hashes CHECK(active_event_sha256 ~ '^[a-f0-9]{64}$'
  AND cutover_id ~ '^[a-f0-9]{64}$' AND record_sha256 ~ '^[a-f0-9]{64}$'
  AND encode(sha256(convert_to(record_json,'UTF8')),'hex')=record_sha256),
 CONSTRAINT ck_cwc_cutover_bytes CHECK(octet_length(record_json) BETWEEN 1 AND 16384),
 CONSTRAINT ck_cwc_cutover_clock CHECK(coverage_start_at<=recorded_at)
)
""".strip()

UID_SQL = "('x'||substr(encode(pg_catalog.sha256(pg_catalog.convert_to('ctcc-qualification-account-v1','UTF8')||decode('00','hex')||pg_catalog.convert_to(p_environment,'UTF8')||decode('00','hex')||pg_catalog.convert_to(p_account_id,'UTF8')),'hex'),1,16))::bit(64)::bigint"

_GUARD_BODY = r"""
DECLARE d jsonb; s jsonb; scan jsonb; h public.demo_controlled_writer_coverage_scopes%ROWTYPE;
 j public.demo_controlled_writer_coverage_journal%ROWTYPE;
 c public.demo_account_controls%ROWTYPE; q public.qualification_account_scopes%ROWTYPE;
 k text; before_ns numeric; start_ns numeric; before_mono numeric; start_mono numeric;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'prospective_cutover_immutable'; END IF;
 d:=public.account_history_partition_v1_json(NEW.record_json);
 IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(d) key)
   IS DISTINCT FROM ARRAY[__FIELDS__]::text[] THEN RAISE EXCEPTION 'prospective_cutover_shape'; END IF;
 IF d->>'schema_version' IS DISTINCT FROM 'ctcc.prospective_ctcc_event_cutover.v2'
   OR d->>'environment' IS DISTINCT FROM NEW.environment
   OR d->>'account_id' IS DISTINCT FROM NEW.account_id
   OR d->>'settlement_currency' IS DISTINCT FROM NEW.settlement_currency
   OR d->>'epoch_id' IS DISTINCT FROM NEW.epoch_id
   OR d->'active_journal_sequence' IS DISTINCT FROM to_jsonb(NEW.active_journal_sequence)
   OR d->>'active_event_sha256' IS DISTINCT FROM NEW.active_event_sha256
   OR d->>'cutover_id' IS DISTINCT FROM NEW.cutover_id
   OR d->>'namespace_policy' IS DISTINCT FROM 'ctcc-prospective-reservation-namespace-v1'
   OR d->>'scope_rule' IS DISTINCT FROM 'original_trigger_candle_open_strictly_after_start'
   OR d->>'record_kind' IS DISTINCT FROM 'prospective_cutover_not_g6_or_order_authority' THEN
   RAISE EXCEPTION 'prospective_cutover_binding'; END IF;
 FOREACH k IN ARRAY ARRAY[__FALSE_FIELDS__] LOOP
   IF d->k IS DISTINCT FROM 'false'::jsonb THEN RAISE EXCEPTION 'prospective_cutover_authority_claim'; END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['generation','active_journal_sequence','control_owner_epoch','control_revision','account_revision','ledger_revision'] LOOP
   IF jsonb_typeof(d->k) IS DISTINCT FROM 'number' OR d->>k !~ '^(0|[1-9][0-9]{0,18})$'
    OR (d->>k)::numeric>9223372036854775807
    OR (k NOT IN ('account_revision','ledger_revision') AND (d->>k)::numeric<1) THEN
     RAISE EXCEPTION 'prospective_cutover_revision'; END IF;
 END LOOP;
 IF (d->>'ledger_revision')::bigint<(d->>'account_revision')::bigint
  OR jsonb_typeof(d->'journal_scan_json') IS DISTINCT FROM 'string'
  OR octet_length(d->>'journal_scan_json') NOT BETWEEN 1 AND 4096
  OR encode(sha256(convert_to(d->>'journal_scan_json','UTF8')),'hex') IS DISTINCT FROM d->>'journal_scan_sha256' THEN
   RAISE EXCEPTION 'prospective_cutover_scan_binding'; END IF;
 scan:=public.account_history_partition_v1_json(d->>'journal_scan_json');
 IF scan->>'schema_version' IS DISTINCT FROM 'ctcc.consumed_event_journal_inspection.v1'
  OR scan->'db_journal_row_chain_verified' IS DISTINCT FROM 'true'::jsonb
  OR scan->>'admission' IS DISTINCT FROM 'DENY' THEN RAISE EXCEPTION 'prospective_cutover_scan_binding'; END IF;
 FOREACH k IN ARRAY ARRAY['db_journal_complete','external_event_history_complete','source_authenticity_verified','execution_authority'] LOOP
   IF scan->k IS DISTINCT FROM 'false'::jsonb THEN RAISE EXCEPTION 'prospective_cutover_scan_binding'; END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['account_revision','ledger_revision','event_keys_sha256','rows_sha256','transitions_sha256'] LOOP
   IF scan->k IS DISTINCT FROM d->k THEN RAISE EXCEPTION 'prospective_cutover_scan_binding'; END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY[__DIGEST_FIELDS__] LOOP
   IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' OR d->>k !~ '^[a-f0-9]{64}$' THEN
     RAISE EXCEPTION 'prospective_cutover_digest'; END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['post_active_readback','coverage_start'] LOOP
   IF jsonb_typeof(d->k) IS DISTINCT FROM 'object'
     OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(d->k) key)
      IS DISTINCT FROM ARRAY['monotonic_ns','utc_ns']::text[]
     OR jsonb_typeof(d->k->'utc_ns') IS DISTINCT FROM 'number'
     OR jsonb_typeof(d->k->'monotonic_ns') IS DISTINCT FROM 'number'
     OR d->k->>'utc_ns' !~ '^(0|[1-9][0-9]{0,19})$'
     OR d->k->>'monotonic_ns' !~ '^(0|[1-9][0-9]{0,19})$'
     OR (d->k->>'utc_ns')::numeric>32503680000000000000
     OR (d->k->>'monotonic_ns')::numeric>32503680000000000000 THEN
     RAISE EXCEPTION 'prospective_cutover_stamp'; END IF;
 END LOOP;
 before_ns:=(d->'post_active_readback'->>'utc_ns')::numeric;
 start_ns:=(d->'coverage_start'->>'utc_ns')::numeric;
 before_mono:=(d->'post_active_readback'->>'monotonic_ns')::numeric;
 start_mono:=(d->'coverage_start'->>'monotonic_ns')::numeric;
 IF start_ns<=before_ns OR start_mono<=before_mono THEN
   RAISE EXCEPTION 'prospective_cutover_start_not_after_readback'; END IF;
 NEW.coverage_start_at:=TIMESTAMPTZ 'epoch'+(ceil(start_ns/1000)::text||' microseconds')::interval;
 NEW.recorded_at:=clock_timestamp();
 IF NEW.coverage_start_at>NEW.recorded_at THEN RAISE EXCEPTION 'prospective_cutover_future_start'; END IF;
 IF NEW.cutover_id<>encode(sha256(convert_to(public.account_history_partition_v1_encode(
   jsonb_build_array(NEW.environment,NEW.account_id,NEW.epoch_id,NEW.active_event_sha256)),'UTF8')),'hex') THEN
   RAISE EXCEPTION 'prospective_cutover_identity'; END IF;
 SELECT * INTO c FROM public.demo_account_controls WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
 SELECT * INTO h FROM public.demo_controlled_writer_coverage_scopes WHERE environment=NEW.environment AND account_id=NEW.account_id FOR UPDATE;
 SELECT * INTO q FROM public.qualification_account_scopes WHERE environment=NEW.environment AND account_id=NEW.account_id AND settlement_currency=NEW.settlement_currency FOR UPDATE;
 SELECT * INTO j FROM public.demo_controlled_writer_coverage_journal WHERE environment=NEW.environment AND account_id=NEW.account_id AND journal_sequence=NEW.active_journal_sequence;
 s:=j.state_json::jsonb;
 IF c.account_id IS NULL OR h.account_id IS NULL OR q.account_id IS NULL OR j.account_id IS NULL
   OR h.state<>'active' OR j.action<>'active' OR h.epoch_id<>NEW.epoch_id
   OR h.journal_sequence<>NEW.active_journal_sequence OR h.journal_sha256<>NEW.active_event_sha256
   OR j.event_sha256<>NEW.active_event_sha256 OR h.state_json<>j.state_json
   OR j.recorded_at> TIMESTAMPTZ 'epoch'+(ceil(before_ns/1000)::text||' microseconds')::interval
   OR (s->'transition_stamp'->>'monotonic_ns')::numeric>before_mono
   OR d->'generation' IS DISTINCT FROM to_jsonb(j.generation)
   OR d->>'active_state_sha256' IS DISTINCT FROM j.state_sha256
   OR d->>'audit_sha256' IS DISTINCT FROM j.audit_sha256 THEN
   RAISE EXCEPTION 'prospective_cutover_active_changed'; END IF;
 FOREACH k IN ARRAY ARRAY['credential_session_sha256','writer_inventory_sha256','source_bundle_sha256',
   'source_current_receipt_sha256','source_native_proof_sha256','source_native_readback_sha256',
   'control_owner_epoch','control_revision','control_event_sha256','control_state_sha256'] LOOP
   IF d->k IS DISTINCT FROM s->k THEN RAISE EXCEPTION 'prospective_cutover_source_changed'; END IF;
 END LOOP;
 IF d->>'manifest_sha256' IS DISTINCT FROM s->>'deployed_manifest_sha256'
   OR NOT c.emergency_stop OR c.arm_request_id IS NOT NULL
   OR c.owner_epoch IS DISTINCT FROM (d->>'control_owner_epoch')::bigint
   OR c.control_revision IS DISTINCT FROM (d->>'control_revision')::bigint
   OR c.state_sha256 IS DISTINCT FROM d->>'control_state_sha256'
   OR c.lease_until<=clock_timestamp()
   OR (s->>'source_current_expires_at')::timestamptz<=clock_timestamp()
   OR q.account_revision IS DISTINCT FROM (d->>'account_revision')::bigint
   OR q.ledger_revision IS DISTINCT FROM (d->>'ledger_revision')::bigint
   OR NOT EXISTS(SELECT 1 FROM public.demo_control_journal x WHERE x.environment=c.environment
    AND x.account_id=c.account_id AND x.control_revision=c.control_revision
    AND x.event_sha256=d->>'control_event_sha256')
   OR EXISTS(SELECT 1 FROM public.qualification_reservations r WHERE r.environment=NEW.environment
    AND r.account_id=NEW.account_id AND r.state<>'reconciled_flat') THEN
   RAISE EXCEPTION 'prospective_cutover_control_or_holds_changed'; END IF;
 RETURN NEW;
END
"""

_APPEND_BODY = r"""
DECLARE d jsonb;
BEGIN
 IF p_environment IS DISTINCT FROM 'demo' OR p_account_id !~ '^[0-9]{1,32}$'
   OR p_epoch_id !~ '^[a-f0-9]{32}$' OR octet_length(p_raw) NOT BETWEEN 1 AND 16384 THEN
   RAISE EXCEPTION 'prospective_cutover_input'; END IF;
 IF current_user=session_user OR current_setting('role')<>'none'
   OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=session_user AND
    (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
   OR EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname<>session_user AND pg_has_role(session_user,r.oid,'MEMBER'))
   OR has_schema_privilege(session_user,'public','CREATE')
   OR has_database_privilege(session_user,current_database(),'CREATE')
   OR has_table_privilege(session_user,'public.demo_controlled_writer_coverage_cutovers','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN') THEN
   RAISE EXCEPTION 'prospective_cutover_role_denied'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' OR current_setting('TimeZone')<>'UTC'
   OR current_setting('synchronous_commit')<>'on' OR current_setting('session_replication_role')<>'origin'
   OR pg_is_in_recovery() OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
   RAISE EXCEPTION 'prospective_cutover_session_denied'; END IF;
 PERFORM pg_advisory_xact_lock(__UID_SQL__);
 PERFORM 1 FROM public.demo_account_controls WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE;
 PERFORM 1 FROM public.demo_controlled_writer_coverage_scopes WHERE environment=p_environment AND account_id=p_account_id FOR UPDATE;
 PERFORM 1 FROM public.qualification_account_scopes WHERE environment=p_environment AND account_id=p_account_id ORDER BY settlement_currency FOR UPDATE;
 LOCK TABLE public.qualification_account_scopes, public.qualification_reservations,
  public.qualification_reservation_transitions IN SHARE MODE NOWAIT;
 LOCK TABLE public.demo_controlled_writer_coverage_scopes, public.demo_controlled_writer_coverage_journal,
  public.demo_controlled_writer_coverage_cutovers IN ROW EXCLUSIVE MODE NOWAIT;
 d:=public.account_history_partition_v1_json(convert_from(p_raw,'UTF8'));
 IF d->>'environment' IS DISTINCT FROM p_environment OR d->>'account_id' IS DISTINCT FROM p_account_id
   OR d->>'epoch_id' IS DISTINCT FROM p_epoch_id THEN RAISE EXCEPTION 'prospective_cutover_binding'; END IF;
 INSERT INTO public.demo_controlled_writer_coverage_cutovers VALUES(
  p_environment,p_account_id,d->>'settlement_currency',p_epoch_id,(d->>'active_journal_sequence')::bigint,
  d->>'active_event_sha256',d->>'cutover_id',encode(sha256(p_raw),'hex'),convert_from(p_raw,'UTF8'),
  clock_timestamp(),clock_timestamp());
 RETURN encode(sha256(p_raw),'hex');
END
"""
_READ_BODY = r"""
DECLARE r public.demo_controlled_writer_coverage_cutovers%ROWTYPE;
BEGIN
 IF p_environment IS DISTINCT FROM 'demo' OR p_account_id !~ '^[0-9]{1,32}$'
   OR p_epoch_id !~ '^[a-f0-9]{32}$' OR p_expected !~ '^[a-f0-9]{64}$' THEN
   RAISE EXCEPTION 'prospective_cutover_read_input'; END IF;
 PERFORM pg_advisory_xact_lock(__UID_SQL__);
 LOCK TABLE public.demo_controlled_writer_coverage_cutovers IN SHARE MODE NOWAIT;
 SELECT * INTO r FROM public.demo_controlled_writer_coverage_cutovers WHERE environment=p_environment
  AND account_id=p_account_id AND epoch_id=p_epoch_id;
 IF r.account_id IS NULL OR r.record_sha256 IS DISTINCT FROM p_expected THEN
   RAISE EXCEPTION 'prospective_cutover_readback'; END IF;
 RETURN jsonb_build_object('record_json',r.record_json,'record_sha256',r.record_sha256,
  'recorded_at',r.recorded_at,'coverage_start_at',r.coverage_start_at);
END
"""
_IMMUTABLE_BODY = "BEGIN RAISE EXCEPTION 'prospective_cutover_immutable'; END"


def _array(values):
    return ",".join("'" + value.replace("'", "''") + "'" for value in values)


_GUARD_BODY = (
    _GUARD_BODY.replace("__FIELDS__", _array(sorted(records.FIELDS)))
    .replace("__FALSE_FIELDS__", _array(records.FALSE_FIELDS))
    .replace(
        "__DIGEST_FIELDS__",
        _array(
            sorted(
                key
                for key in records.FIELDS
                if key.endswith("_sha256") or key == "cutover_id"
            )
        ),
    )
)
_APPEND_BODY = _APPEND_BODY.replace("__UID_SQL__", UID_SQL)
_READ_BODY = _READ_BODY.replace("__UID_SQL__", UID_SQL)
FUNCTION_SPECS = (
    ("controlled_writer_coverage_v2_cutover_guard", "", "trigger", _GUARD_BODY),
    ("controlled_writer_coverage_v2_cutover_immutable", "", "trigger", _IMMUTABLE_BODY),
    (
        "controlled_writer_coverage_v2_cutover_append",
        "p_environment text,p_account_id text,p_epoch_id text,p_raw bytea",
        "text",
        _APPEND_BODY,
    ),
    (
        "controlled_writer_coverage_v2_cutover_read",
        "p_environment text,p_account_id text,p_epoch_id text,p_expected text",
        "jsonb",
        _READ_BODY,
    ),
)
FUNCTION_SIGNATURES = {
    name: ",".join(argument.split(" ", 1)[1] for argument in args.split(","))
    if args
    else ""
    for name, args, _, _ in FUNCTION_SPECS
}
FUNCTION_SQL = {
    name: f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $cutover${body}$cutover$"
    for name, args, result, body in FUNCTION_SPECS
}
TRIGGERS = (
    ("cwc_cutover_insert_guard", 7, "controlled_writer_coverage_v2_cutover_guard"),
    ("cwc_cutover_immutable", 27, "controlled_writer_coverage_v2_cutover_immutable"),
    ("cwc_cutover_no_truncate", 34, "controlled_writer_coverage_v2_cutover_immutable"),
)
TRIGGER_SQL = (
    f"CREATE TRIGGER cwc_cutover_insert_guard BEFORE INSERT ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.controlled_writer_coverage_v2_cutover_guard()",
    f"CREATE TRIGGER cwc_cutover_immutable BEFORE UPDATE OR DELETE ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.controlled_writer_coverage_v2_cutover_immutable()",
    f"CREATE TRIGGER cwc_cutover_no_truncate BEFORE TRUNCATE ON public.{TABLE} FOR EACH STATEMENT EXECUTE FUNCTION public.controlled_writer_coverage_v2_cutover_immutable()",
    *(
        f"ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER {name}"
        for name, _, _ in TRIGGERS
    ),
)

CATALOG_SQL = r"""
SELECT jsonb_build_object(
 'schema_version','ctcc.prospective_cutover_actual_catalog.v2',
 'server',jsonb_build_object('version',current_setting('server_version_num'),'current_user',current_user,
  'session_user',session_user,'role',current_setting('role'),'timezone',current_setting('TimeZone'),
  'isolation',current_setting('transaction_isolation'),'search_path',current_setting('search_path'),
  'replication_role',current_setting('session_replication_role'),'synchronous_commit',current_setting('synchronous_commit'),
  'fsync',current_setting('fsync'),'full_page_writes',current_setting('full_page_writes'),'in_recovery',pg_is_in_recovery()),
 'role',(SELECT jsonb_build_object('oid',oid::bigint,'name',rolname,'login',rolcanlogin,'superuser',rolsuper,
  'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication,'bypassrls',rolbypassrls,
  'other_membership',EXISTS(SELECT 1 FROM pg_roles x WHERE x.oid<>r.oid AND pg_has_role(r.oid,x.oid,'MEMBER')),
  'can_create',has_schema_privilege(r.oid,'public','CREATE') OR has_database_privilege(r.oid,current_database(),'CREATE')) FROM pg_roles r WHERE rolname=session_user),
 'parents',(SELECT jsonb_agg(jsonb_build_object('name',relname,'oid',c.oid::bigint,'owner_oid',relowner::bigint,
  'kind',relkind) ORDER BY relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(ARRAY[__PARENTS__])),
 'tables',(SELECT jsonb_agg(jsonb_build_object('name',relname,'oid',c.oid::bigint,'owner_oid',relowner::bigint,
  'owner_superuser',(SELECT rolsuper FROM pg_roles WHERE oid=relowner),'kind',relkind,'persistence',relpersistence,
  'rls',relrowsecurity,'force_rls',relforcerowsecurity,
  'table_access',has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'),
  'column_acl_empty',NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL)) ORDER BY relname)
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='__TABLE__'),
 'columns',coalesce((SELECT jsonb_agg(jsonb_build_object('name',attname,'position',attnum,'type',format_type(atttypid,atttypmod),
  'notnull',attnotnull,'default',pg_get_expr(d.adbin,d.adrelid)) ORDER BY attnum) FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE a.attrelid='public.__TABLE__'::regclass AND a.attnum>0 AND NOT a.attisdropped),'[]'::jsonb),
 'constraints',coalesce((SELECT jsonb_agg(jsonb_build_object('name',conname,'kind',contype,'validated',convalidated,
  'deferrable',condeferrable,'deferred',condeferred,'definition',pg_get_constraintdef(oid,true)) ORDER BY conname)
  FROM pg_constraint WHERE conrelid='public.__TABLE__'::regclass),'[]'::jsonb),
 'indexes',coalesce((SELECT jsonb_agg(jsonb_build_object('name',c.relname,'valid',i.indisvalid,'ready',i.indisready,
  'unique',i.indisunique,'definition',pg_get_indexdef(c.oid)) ORDER BY c.relname) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
  WHERE i.indrelid='public.__TABLE__'::regclass),'[]'::jsonb),
 'functions',coalesce((SELECT jsonb_agg(jsonb_build_object('name',proname,'oid',p.oid::bigint,'owner_oid',proowner::bigint,
  'args',pg_get_function_identity_arguments(p.oid),'result',pg_get_function_result(p.oid),'body',prosrc,
  'language',l.lanname,'volatility',provolatile,'definer',prosecdef,'config',proconfig,
  'can_execute',has_function_privilege(current_user,p.oid,'EXECUTE')) ORDER BY proname)
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
  WHERE n.nspname='public' AND p.proname LIKE 'controlled_writer_coverage_v2_cutover_%'),'[]'::jsonb),
 'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('name',tgname,'type',tgtype,'enabled',tgenabled,'function',p.proname,
  'function_oid',t.tgfoid::bigint,'internal',tgisinternal,'definition',pg_get_triggerdef(t.oid,true)) ORDER BY tgname)
  FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid WHERE tgrelid='public.__TABLE__'::regclass AND NOT tgisinternal),'[]'::jsonb),
 'acl',coalesce((SELECT jsonb_agg(jsonb_build_object('kind',kind,'name',name,'owner_oid',owner_oid,
  'grantee',grantee,'privilege',privilege,'grantable',grantable) ORDER BY kind,name,grantee,privilege) FROM (
   SELECT 'table' kind,c.relname name,c.relowner::bigint owner_oid,x.grantee::bigint grantee,x.privilege_type privilege,x.is_grantable grantable
    FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x WHERE c.oid='public.__TABLE__'::regclass
   UNION ALL SELECT 'function',p.proname,p.proowner::bigint,x.grantee::bigint,x.privilege_type,x.is_grantable
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
    WHERE n.nspname='public' AND p.proname LIKE 'controlled_writer_coverage_v2_cutover_%') a),'[]'::jsonb))
""".replace("__TABLE__", TABLE).replace("__PARENTS__", _array(PARENTS))


def ddl_identity():
    return records.digest(
        records.canonical([TABLE_SQL, list(FUNCTION_SQL.values()), list(TRIGGER_SQL)])
    )


def deparser_sha256(catalog):
    """Seal genuine PG output; dynamic OIDs/roles checked independently."""
    return records.digest(
        records.canonical(
            {
                "columns": catalog["columns"],
                "constraints": catalog["constraints"],
                "indexes": catalog["indexes"],
                "functions": [
                    {
                        key: row[key]
                        for key in (
                            "name",
                            "args",
                            "result",
                            "body",
                            "language",
                            "volatility",
                            "definer",
                            "config",
                        )
                    }
                    for row in catalog["functions"]
                ],
                "triggers": [
                    {
                        key: row[key]
                        for key in (
                            "name",
                            "type",
                            "enabled",
                            "function",
                            "internal",
                            "definition",
                        )
                    }
                    for row in catalog["triggers"]
                ],
            }
        )
    )


def verify_catalog(catalog, *, require_native_baseline=True):
    """Structural migration diagnostics cannot be used for runtime admission."""
    try:
        if (
            type(require_native_baseline) is not bool
            or type(catalog) is not dict
            or set(catalog)
            != {
                "schema_version",
                "server",
                "role",
                "parents",
                "tables",
                "columns",
                "constraints",
                "indexes",
                "functions",
                "triggers",
                "acl",
            }
            or catalog["schema_version"] != "ctcc.prospective_cutover_actual_catalog.v2"
            or not 170000 <= int(catalog["server"]["version"]) < 180000
        ):
            records.deny("prospective_cutover_catalog_shape")

        def rows(name):
            result = catalog[name]
            if type(result) is not list or len({row["name"] for row in result}) != len(
                result
            ):
                records.deny("prospective_cutover_catalog_duplicates")
            return {row["name"]: row for row in result}

        parents, tables, functions = rows("parents"), rows("tables"), rows("functions")
        if (
            set(parents) != set(PARENTS)
            or set(tables) != {TABLE}
            or set(functions) != set(FUNCTION_SQL)
        ):
            records.deny("prospective_cutover_catalog_objects")
        owner = tables[TABLE]["owner_oid"]
        if (
            type(owner) is not int
            or owner < 1
            or tables[TABLE]["owner_superuser"] is not True
            or any(
                row["owner_oid"] != owner or row["kind"] != "r"
                for row in [*parents.values(), *tables.values()]
            )
            or tables[TABLE]["persistence"] != "p"
            or tables[TABLE]["rls"] is not False
            or tables[TABLE]["force_rls"] is not False
            or tables[TABLE]["column_acl_empty"] is not True
        ):
            records.deny("prospective_cutover_catalog_owner")
        columns = rows("columns")
        if set(columns) != {name for name, _ in COLUMNS}:
            records.deny("prospective_cutover_catalog_columns")
        for position, (name, dtype) in enumerate(COLUMNS, 1):
            row = columns[name]
            if (
                row["position"] != position
                or row["type"] != dtype
                or row["notnull"] is not True
                or row["default"]
                != ("clock_timestamp()" if name == "recorded_at" else None)
            ):
                records.deny("prospective_cutover_catalog_columns")
        constraints, indexes, triggers = (
            rows("constraints"),
            rows("indexes"),
            rows("triggers"),
        )
        if (
            set(constraints) != set(CONSTRAINTS)
            or set(indexes) != {"pk_cwc_cutover", "uq_cwc_cutover_id"}
            or any(
                row["validated"] is not True
                or row["deferrable"] is not False
                or row["deferred"] is not False
                for row in constraints.values()
            )
            or any(
                row["valid"] is not True
                or row["ready"] is not True
                or row["unique"] is not True
                for row in indexes.values()
            )
            or set(triggers) != {name for name, _, _ in TRIGGERS}
        ):
            records.deny("prospective_cutover_catalog_constraints")
        for name, kind, function in TRIGGERS:
            row = triggers[name]
            if (
                row["type"] != kind
                or row["enabled"] != "A"
                or row["function"] != function
                or row["internal"] is not False
                or row["function_oid"] != functions[function]["oid"]
            ):
                records.deny("prospective_cutover_catalog_trigger")
        for name, _, result, body in FUNCTION_SPECS:
            row = functions[name]
            if (
                row["owner_oid"] != owner
                or row["result"] != result
                or row["body"] != body
                or row["language"] != "plpgsql"
                or row["volatility"] != "v"
                or row["definer"] is not True
                or row["config"] != ["search_path=pg_catalog, public, pg_temp"]
            ):
                records.deny("prospective_cutover_catalog_function")
        if type(catalog["acl"]) is not list:
            records.deny("prospective_cutover_catalog_acl")
        for row in catalog["acl"]:
            if (
                row["owner_oid"] != owner
                or row["grantee"] == 0
                or (
                    row["grantee"] != owner
                    and (
                        row["kind"] != "function"
                        or row["name"]
                        not in {FUNCTION_SPECS[2][0], FUNCTION_SPECS[3][0]}
                        or row["privilege"] != "EXECUTE"
                        or row["grantable"] is not False
                    )
                )
            ):
                records.deny("prospective_cutover_catalog_acl")
        if require_native_baseline:
            if (
                PG17_DEPARSED_BASELINE is None
                or PG17_DEPARSED_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
            ):
                records.deny("prospective_cutover_native_baseline_pending")
            if deparser_sha256(catalog) != PG17_DEPARSED_BASELINE:
                records.deny("prospective_cutover_native_baseline_drift")
    except records.ProspectiveCutoverError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        records.deny("prospective_cutover_catalog_shape")


def verify_runtime_role(catalog):
    try:
        role, server = catalog["role"], catalog["server"]
        if (
            server["current_user"] != server["session_user"]
            or role["name"] != server["session_user"]
            or server["role"] != "none"
            or role["login"] is not True
            or any(
                role[name] is not False
                for name in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "other_membership",
                    "can_create",
                )
            )
            or server["timezone"] != "UTC"
            or server["isolation"] != "read committed"
            or server["search_path"] != "pg_catalog, public, pg_temp"
            or server["replication_role"] != "origin"
            or server["in_recovery"] is not False
            or any(
                server[name] != "on"
                for name in ("synchronous_commit", "fsync", "full_page_writes")
            )
            or any(
                row["owner_oid"] == role["oid"] or row["table_access"] is not False
                for row in catalog["tables"]
            )
            or any(
                row["owner_oid"] == role["oid"]
                or row["can_execute"]
                is not (row["name"] in {FUNCTION_SPECS[2][0], FUNCTION_SPECS[3][0]})
                for row in catalog["functions"]
            )
        ):
            records.deny("prospective_cutover_runtime_role")
    except records.ProspectiveCutoverError:
        raise
    except (KeyError, TypeError, AttributeError):
        records.deny("prospective_cutover_runtime_role")


def expected_schema():
    """Declaration summary is not a native catalog seal or runtime owner."""
    return {
        "schema_version": "ctcc.prospective_cutover_schema.v2",
        "revision": "0051",
        "table": TABLE,
        "ddl_sha256": ddl_identity(),
        "native_baseline_status": PG17_DEPARSED_BASELINE_STATUS,
        "native_deparser_baseline": PG17_DEPARSED_BASELINE,
        "native_acceptance": False,
        "g6_qualified": False,
        "execution_authority": False,
    }
