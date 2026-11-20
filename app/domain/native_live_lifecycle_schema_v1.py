"""UNBOUND owner-reviewed Live lifecycle SQL; no migration or role is installed.

Only the root migration may compose this SPEC. The entry writer receives no
new privilege. The reconciler can neither read the HMAC key nor issue/Arm an
order. A source-owned canonical proof is authenticated before any hold release.
"""

import re
from types import MappingProxyType

from app.domain.native_live_legacy_safety_v1 import CLEAR_PREDICATE_SQL
from app.domain.source_primitives import canonical, sha

SCHEMA = "ctcc.native_live.lifecycle_proof.v1"
HEAD = "0058"
TABLES = ("native_live_lifecycle_keys_v1", "native_live_lifecycle_proofs_v1")
DOMAIN = b"ctcc-native-live-lifecycle-proof-v1\x00"
FUNCTIONS = (
    "native_live_lifecycle_key_identity_v1",
    "native_live_lifecycle_control_fence_v1",
    "native_live_lifecycle_inhibit_v1",
    "native_live_lifecycle_append_v1",
    "native_live_lifecycle_immutable_v1",
    "native_live_lifecycle_catalog_v1",
)
EXECUTABLE_FUNCTIONS = (*FUNCTIONS[:4], FUNCTIONS[5])

_KEYS = """CREATE TABLE public.native_live_lifecycle_keys_v1 (
 environment varchar(4) NOT NULL CHECK(environment='live'),
 account_uid varchar(40) NOT NULL CHECK(account_uid ~ '^[1-9][0-9]{0,39}$'),
 main_uid varchar(40) NOT NULL CHECK(main_uid ~ '^[1-9][0-9]{0,39}$'),
 database_oid oid NOT NULL CHECK(database_oid>0), owner_oid oid NOT NULL CHECK(owner_oid>0),
 reconciler_oid oid NOT NULL CHECK(reconciler_oid>0 AND reconciler_oid<>owner_oid),
 origin varchar(64) NOT NULL CHECK(origin IN ('https://openapi.okx.com','https://eea.okx.com')),
 key_id varchar(64) NOT NULL CHECK(key_id ~ '^[a-f0-9]{64}$'),
 key_material bytea NOT NULL CHECK(octet_length(key_material)=32),
 key_material_sha256 varchar(64) NOT NULL CHECK(key_material_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment,account_uid), UNIQUE(environment,account_uid,key_id),
 FOREIGN KEY(environment,account_uid) REFERENCES public.native_live_controls_v1(environment,account_uid) ON DELETE RESTRICT
)"""
_PROOFS = """CREATE TABLE public.native_live_lifecycle_proofs_v1 (
 reservation_id varchar(64) NOT NULL REFERENCES public.native_live_reservations_v1(reservation_id) ON DELETE RESTRICT,
 sequence bigint NOT NULL CHECK(sequence>0),
 environment varchar(4) NOT NULL CHECK(environment='live'), account_uid varchar(40) NOT NULL,
 key_id varchar(64) NOT NULL CHECK(key_id ~ '^[a-f0-9]{64}$'),
 previous_sha256 varchar(64), proof_json text NOT NULL CHECK(octet_length(proof_json) BETWEEN 1 AND 65536),
 proof_sha256 varchar(64) NOT NULL CHECK(proof_sha256 ~ '^[a-f0-9]{64}$'),
 signature varchar(64) NOT NULL CHECK(signature ~ '^[a-f0-9]{64}$'),
 outcome varchar(32) NOT NULL CHECK(outcome IN ('UNCERTAIN','PROTECTED_HOLD','ZERO_FILL_FLAT','RECONCILED_FLAT')),
 observed_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(reservation_id,sequence), UNIQUE(reservation_id,proof_sha256),
 FOREIGN KEY(environment,account_uid,key_id) REFERENCES public.native_live_lifecycle_keys_v1(environment,account_uid,key_id) ON DELETE RESTRICT,
 CHECK((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 IS NOT NULL AND previous_sha256 ~ '^[a-f0-9]{64}$'))
)"""

# Every exposed function verifies the actual session actor against this UID's
# immutable owner-installed key row. SET ROLE and inherited memberships cannot
# turn an unrelated principal into the configured reconciler.
_ACTOR = """
 IF current_setting('role')<>'none' OR
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r
 WHERE r.oid=k.reconciler_oid AND r.rolname=session_user AND r.rolcanlogin
 AND NOT(r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid))
 OR k.database_oid IS DISTINCT FROM (SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
 OR k.owner_oid IS DISTINCT FROM (SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database())
 OR current_user::text IS DISTINCT FROM (SELECT rolname::text FROM pg_catalog.pg_roles WHERE oid=k.owner_oid)
 THEN RAISE EXCEPTION 'native_live_lifecycle_actor_denied'; END IF;
"""

KEY_IDENTITY_SQL = f"""CREATE FUNCTION public.native_live_lifecycle_key_identity_v1(u text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE k public.native_live_lifecycle_keys_v1;
 BEGIN
 SELECT * INTO k FROM public.native_live_lifecycle_keys_v1 WHERE environment='live' AND account_uid=u;
 IF k.account_uid IS NULL THEN RAISE EXCEPTION 'native_live_lifecycle_actor_denied'; END IF;
 {_ACTOR}
 RETURN jsonb_build_object('environment','live','account_uid',k.account_uid,'main_uid',k.main_uid,
 'database_oid',k.database_oid::bigint,'owner_oid',k.owner_oid::bigint,'reconciler_oid',k.reconciler_oid::bigint,
 'origin',k.origin,'key_id',k.key_id,'key_material_sha256',k.key_material_sha256);
 END $body$"""

CONTROL_FENCE_SQL = f"""CREATE FUNCTION public.native_live_lifecycle_control_fence_v1(u text)
 RETURNS SETOF public.native_live_controls_v1 LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE k public.native_live_lifecycle_keys_v1; c public.native_live_controls_v1;
 BEGIN
 SELECT * INTO k FROM public.native_live_lifecycle_keys_v1 WHERE environment='live' AND account_uid=u;
 IF k.account_uid IS NULL THEN RAISE EXCEPTION 'native_live_lifecycle_actor_denied'; END IF;
 {_ACTOR}
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('ctcc-live-ledger-v1:live:'||u,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 PERFORM pg_advisory_xact_lock(1680010);
 SELECT * INTO c FROM public.native_live_controls_v1 WHERE environment='live' AND account_uid=u FOR UPDATE;
 IF c.account_uid IS NULL OR c.main_uid IS DISTINCT FROM k.main_uid THEN RAISE EXCEPTION 'native_live_lifecycle_control_changed'; END IF;
 RETURN NEXT c;
 END $body$"""

INHIBIT_SQL = """CREATE FUNCTION public.native_live_lifecycle_inhibit_v1(u text)
 RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE c public.native_live_controls_v1; new_revision bigint;
 BEGIN
 SELECT * INTO c FROM public.native_live_lifecycle_control_fence_v1(u);
 IF c.account_uid IS NULL THEN RAISE EXCEPTION 'native_live_lifecycle_control_changed'; END IF;
 UPDATE public.native_live_controls_v1 AS original SET armed=false,emergency_stop=true,arm_id=NULL,arm_expires_at=NULL,
 revision=original.revision+1,updated_at=clock_timestamp() WHERE environment='live' AND account_uid=u RETURNING original.revision INTO new_revision;
 RETURN new_revision;
 END $body$"""

IMMUTABLE_SQL = """CREATE FUNCTION public.native_live_lifecycle_immutable_v1() RETURNS trigger
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE own oid; who oid;
 BEGIN
 SELECT datdba INTO own FROM pg_catalog.pg_database WHERE datname=current_database();
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=current_user;
 IF TG_OP<>'INSERT' OR who IS DISTINCT FROM own OR
 (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID) IS DISTINCT FROM own
 THEN RAISE EXCEPTION 'native_live_lifecycle_immutable'; END IF;
 IF TG_TABLE_NAME='native_live_lifecycle_keys_v1' THEN
 IF NEW.database_oid IS DISTINCT FROM (SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
 OR NEW.owner_oid IS DISTINCT FROM own
 OR NEW.key_material_sha256 IS DISTINCT FROM encode(sha256(NEW.key_material),'hex')
 OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid=NEW.reconciler_oid AND r.rolcanlogin
 AND r.rolname<>'public' AND left(r.rolname,3)<>'pg_'
 AND NOT(r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid))
 OR NOT EXISTS(SELECT 1 FROM public.native_live_controls_v1 c WHERE c.environment='live'
 AND c.account_uid=NEW.account_uid AND c.main_uid=NEW.main_uid AND NOT c.armed AND c.emergency_stop
 AND c.arm_id IS NULL AND c.arm_expires_at IS NULL)
 THEN RAISE EXCEPTION 'native_live_lifecycle_key_denied'; END IF;
 NEW.created_at:=clock_timestamp();
 ELSIF TG_TABLE_NAME='native_live_lifecycle_proofs_v1' THEN
 NEW.recorded_at:=clock_timestamp();
 ELSE RAISE EXCEPTION 'native_live_lifecycle_immutable'; END IF;
 RETURN NEW;
 END $body$"""

# Proof INSERT is done only by the fixed authenticated function, as owner.
# Its table trigger does not interpret a caller-provided passed flag.
APPEND_SQL = f"""CREATE FUNCTION public.native_live_lifecycle_append_v1(p text,s text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE j jsonb; k public.native_live_lifecycle_keys_v1; r public.native_live_reservations_v1;
 i public.native_live_intents_v1; c public.native_live_controls_v1; prior text; seq bigint;
 pad bytea; inner_pad bytea; outer_pad bytea; actual bytea; supplied bytea; difference integer:=0; n integer;
 digest text; observed timestamptz; terminal boolean;
 BEGIN
 IF octet_length(p) NOT BETWEEN 1 AND 65536 OR octet_length(p)<>char_length(p)
 OR NOT coalesce(s ~ '^[a-f0-9]{{64}}$',false) THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 j:=p::jsonb;
 IF jsonb_typeof(j)<>'object' OR j->>'schema' IS DISTINCT FROM '{SCHEMA}' OR j->>'environment' IS DISTINCT FROM 'live'
 OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 SELECT * INTO k FROM public.native_live_lifecycle_keys_v1 WHERE environment='live' AND account_uid=j->>'account_uid';
 IF k.account_uid IS NULL THEN RAISE EXCEPTION 'native_live_lifecycle_actor_denied'; END IF;
 {_ACTOR}
 IF j->>'key_id' IS DISTINCT FROM k.key_id OR j->>'main_uid' IS DISTINCT FROM k.main_uid
 OR j->>'origin' IS DISTINCT FROM k.origin OR (j->>'database_oid')::oid IS DISTINCT FROM k.database_oid
 OR (j->>'owner_oid')::oid IS DISTINCT FROM k.owner_oid OR (j->>'reconciler_oid')::oid IS DISTINCT FROM k.reconciler_oid
 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 pad:=k.key_material||decode(repeat('00',32),'hex'); inner_pad:=pad; outer_pad:=pad;
 FOR n IN 0..63 LOOP
 inner_pad:=set_byte(inner_pad,n,get_byte(pad,n)#54);
 outer_pad:=set_byte(outer_pad,n,get_byte(pad,n)#92);
 END LOOP;
 actual:=sha256(outer_pad||sha256(inner_pad||convert_to('ctcc-native-live-lifecycle-proof-v1','UTF8')||decode('00','hex')||convert_to(p,'UTF8')));
 supplied:=decode(s,'hex');
 FOR n IN 0..31 LOOP difference:=difference | (get_byte(actual,n)#get_byte(supplied,n)); END LOOP;
 IF difference<>0 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 SELECT * INTO c FROM public.native_live_lifecycle_control_fence_v1(k.account_uid);
 SELECT * INTO r FROM public.native_live_reservations_v1 WHERE reservation_id=j->>'reservation_id' FOR UPDATE;
 SELECT * INTO i FROM public.native_live_intents_v1 WHERE reservation_id=r.reservation_id;
 IF r.reservation_id IS NULL OR i.reservation_id IS NULL OR r.environment<>'live' OR r.account_uid<>k.account_uid
 OR j->>'intent_sha256' IS DISTINCT FROM i.intent_sha256 OR j->>'event_key' IS DISTINCT FROM r.event_key
 OR j->>'candidate_sha256' IS DISTINCT FROM r.candidate_sha256 OR j->>'report_sha256' IS DISTINCT FROM r.report_sha256
 OR j->>'policy_sha256' IS DISTINCT FROM i.intent_json::jsonb->>'policy_sha256'
 OR j->>'original_credential_session_sha256' IS DISTINCT FROM i.intent_json::jsonb->>'credential_session_sha256'
 OR j->>'request_sha256' IS DISTINCT FROM i.intent_json::jsonb->>'request_sha256'
 OR NOT coalesce((j->>'credential_session_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce(j->>'outcome' IN ('UNCERTAIN','PROTECTED_HOLD','ZERO_FILL_FLAT','RECONCILED_FLAT'),false)
 OR NOT coalesce(j->>'credential_continuity' IN ('same_original_lease','fresh_recovery_lease_uid_reverified'),false)
 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 observed:=(j->>'observed_at')::timestamptz;
 IF observed IS NULL OR observed>clock_timestamp() OR observed<clock_timestamp()-interval '5 seconds'
 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 digest:=encode(sha256(convert_to(p,'UTF8')),'hex');
 IF EXISTS(SELECT 1 FROM public.native_live_lifecycle_proofs_v1 WHERE reservation_id=r.reservation_id AND proof_sha256=digest)
 THEN RETURN digest; END IF;
 IF r.state='reconciled' THEN RAISE EXCEPTION 'native_live_lifecycle_terminal_immutable'; END IF;
 SELECT sequence,proof_sha256 INTO seq,prior FROM public.native_live_lifecycle_proofs_v1
 WHERE reservation_id=r.reservation_id ORDER BY sequence DESC LIMIT 1;
 seq:=coalesce(seq,0)+1;
 IF (j->>'sequence')::bigint IS DISTINCT FROM seq OR j->>'previous_sha256' IS DISTINCT FROM prior
 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 terminal:=j->>'outcome' IN ('ZERO_FILL_FLAT','RECONCILED_FLAT');
 IF terminal AND NOT ({CLEAR_PREDICATE_SQL})
 THEN RAISE EXCEPTION 'native_live_lifecycle_legacy_exposure'; END IF;
 IF terminal AND (c.armed IS DISTINCT FROM false OR c.arm_id IS NOT NULL OR c.arm_expires_at IS NOT NULL
 OR j->'full_account_verified' IS DISTINCT FROM 'true'::jsonb
 OR j->'fills_complete' IS DISTINCT FROM 'true'::jsonb OR j->'flat_verified' IS DISTINCT FROM 'true'::jsonb
 OR j->'unknown_exposure' IS DISTINCT FROM 'false'::jsonb
 OR NOT coalesce((j->>'raw_bank_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'submission_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'account_config_before_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'account_config_after_sha256') ~ '^[a-f0-9]{{64}}$',false)
 OR NOT coalesce((j->>'entry_order_id') ~ '^[1-9][0-9]{{0,39}}$',false)
 OR (j->>'position_count')::bigint IS DISTINCT FROM 0
 OR (j->>'pending_order_count')::bigint IS DISTINCT FROM 0
 OR (j->>'pending_algo_count')::bigint IS DISTINCT FROM 0
 OR j->'submission_attempted' IS DISTINCT FROM 'true'::jsonb)
 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 IF j->>'outcome'='ZERO_FILL_FLAT' AND ((j->>'entry_filled_contracts')::numeric IS DISTINCT FROM 0
 OR j->>'entry_order_state' IS DISTINCT FROM 'canceled')
 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 IF j->>'outcome'='RECONCILED_FLAT' AND (
 (j->>'entry_filled_contracts')::numeric IS DISTINCT FROM (i.intent_json::jsonb->>'contracts')::numeric
 OR (j->>'exit_filled_contracts')::numeric IS DISTINCT FROM (i.intent_json::jsonb->>'contracts')::numeric
 OR j->'exit_lineage_verified' IS DISTINCT FROM 'true'::jsonb
 OR j->>'exit_kind' IS DISTINCT FROM 'original_oco'
 OR j->>'protection_client_id' IS DISTINCT FROM i.protection_client_id
 OR NOT coalesce((j->>'exit_source_sha256') ~ '^[a-f0-9]{{64}}$',false))
 THEN RAISE EXCEPTION 'native_live_lifecycle_proof_rejected'; END IF;
 INSERT INTO public.native_live_lifecycle_proofs_v1
 (reservation_id,sequence,environment,account_uid,key_id,previous_sha256,proof_json,proof_sha256,signature,outcome,observed_at)
 VALUES(r.reservation_id,seq,'live',k.account_uid,k.key_id,prior,p,digest,s,j->>'outcome',observed);
 IF terminal THEN
 UPDATE public.native_live_reservations_v1 SET state='reconciled',resolution_sha256=digest WHERE reservation_id=r.reservation_id;
 ELSIF j->>'outcome'='UNCERTAIN' THEN
 PERFORM public.native_live_lifecycle_inhibit_v1(k.account_uid);
 END IF;
 RETURN digest;
 END $body$"""


# Root composition must replace the original Live guard by this exact source
# statement. Until it does so, the unchanged base58 inspector rejects the delta.
def live_lifecycle_function_overrides_v1():
    return MappingProxyType({"native_live_reservation_guard_v1": RESERVATION_GUARD_SQL})


def _reservation_guard():
    from app.domain import native_live_ledger_schema_v1 as base

    original = next(
        sql
        for sql in base.UPGRADE_SQL
        if sql.startswith("CREATE FUNCTION public.native_live_reservation_guard_v1()")
    )
    previous = " IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native_live_reservation_immutable'; END IF;"
    replacement = """ IF TG_OP='UPDATE' THEN
 IF current_user::text IS DISTINCT FROM (SELECT rolname::text FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database()))
 OR NEW.state IS DISTINCT FROM 'reconciled' OR OLD.state NOT IN ('reserved','uncertain')
 OR (to_jsonb(NEW)-ARRAY['state','resolution_sha256']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','resolution_sha256'])
 OR NOT EXISTS(SELECT 1 FROM public.native_live_lifecycle_proofs_v1 p
 WHERE p.reservation_id=OLD.reservation_id AND p.proof_sha256=NEW.resolution_sha256
 AND p.environment=OLD.environment AND p.account_uid=OLD.account_uid
 AND p.outcome IN ('ZERO_FILL_FLAT','RECONCILED_FLAT'))
 THEN RAISE EXCEPTION 'native_live_reservation_immutable'; END IF;
 RETURN NEW;
 END IF;
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native_live_reservation_immutable'; END IF;"""
    if original.count(previous) != 1:
        raise ValueError("native_live_original_reservation_guard_changed")
    return original.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
        previous, replacement, 1
    )


RESERVATION_GUARD_SQL = _reservation_guard()


def live_lifecycle_ancestor_reservation_guard_v1():
    """Exact ancestor restoration for an empty, explicitly permitted downgrade."""
    from app.domain import native_live_ledger_schema_v1 as base

    original = next(
        sql
        for sql in base.UPGRADE_SQL
        if sql.startswith("CREATE FUNCTION public.native_live_reservation_guard_v1()")
    )
    return original.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


UPGRADE_SQL = (
    _KEYS,
    _PROOFS,
    KEY_IDENTITY_SQL,
    CONTROL_FENCE_SQL,
    INHIBIT_SQL,
    IMMUTABLE_SQL,
    APPEND_SQL,
    *(
        f"REVOKE ALL ON FUNCTION public.{name}{signature} FROM PUBLIC"
        for name, signature in (
            (FUNCTIONS[0], "(text)"),
            (FUNCTIONS[1], "(text)"),
            (FUNCTIONS[2], "(text)"),
            (FUNCTIONS[3], "(text,text)"),
            (FUNCTIONS[4], "()"),
        )
    ),
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.native_live_lifecycle_immutable_v1()"
        for table in TABLES
    ),
)


def live_lifecycle_reconciler_grant_sql_v1(name):
    if (
        type(name) is not str
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is None
        or name == "public"
        or name.startswith("pg_")
    ):
        raise ValueError("native_live_lifecycle_role_identifier_refused")
    from app.domain.native_live_legacy_safety_v1 import TABLES as legacy

    read = (
        "alembic_version",
        "native_live_controls_v1",
        "native_live_reservations_v1",
        "native_live_intents_v1",
        "native_live_transitions_v1",
        *legacy,
        TABLES[1],
    )
    return (
        f'GRANT USAGE ON SCHEMA public TO "{name}"',
        *(f'GRANT SELECT ON TABLE public.{table} TO "{name}"' for table in read),
        *(
            f'GRANT EXECUTE ON FUNCTION public.{function}{signature} TO "{name}"'
            for function, signature in (
                (FUNCTIONS[0], "(text)"),
                (FUNCTIONS[1], "(text)"),
                (FUNCTIONS[2], "(text)"),
                (FUNCTIONS[3], "(text,text)"),
                (FUNCTIONS[5], "()"),
            )
        ),
    )


from app.domain.native_live_ledger_schema_v1 import _named_table_spec

_NAMED = tuple(
    _named_table_spec(table, statement)
    for table, statement in zip(TABLES, UPGRADE_SQL[:2], strict=True)
)
TABLE_CHECKS = {table: item[1] for table, item in zip(TABLES, _NAMED, strict=True)}
TABLE_UNIQUES = {table: item[2] for table, item in zip(TABLES, _NAMED, strict=True)}
UPGRADE_SQL = (
    _NAMED[0][0],
    _NAMED[1][0].replace(
        "FOREIGN KEY(environment,account_uid,key_id)",
        "CONSTRAINT fk_native_live_lifecycle_proofs_v1_key FOREIGN KEY(environment,account_uid,key_id)",
    ),
    *UPGRADE_SQL[2:],
)
SOURCE_PRIMARY_KEYS = {
    TABLES[0]: ("environment", "account_uid"),
    TABLES[1]: ("reservation_id", "sequence"),
}
SOURCE_FOREIGN_KEYS = {
    (TABLES[0], "fk_" + TABLES[0] + "_scope"): (
        ("environment", "account_uid"),
        "native_live_controls_v1",
        ("environment", "account_uid"),
    ),
    (TABLES[1], "fk_" + TABLES[1] + "_reservation"): (
        ("reservation_id",),
        "native_live_reservations_v1",
        ("reservation_id",),
    ),
    (TABLES[1], "fk_native_live_lifecycle_proofs_v1_key"): (
        ("environment", "account_uid", "key_id"),
        TABLES[0],
        ("environment", "account_uid", "key_id"),
    ),
}
DOWNGRADE_SQL = (
    """DO $body$ BEGIN IF EXISTS(SELECT 1 FROM public.native_live_lifecycle_keys_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_lifecycle_proofs_v1)
 THEN RAISE EXCEPTION 'native_live_lifecycle_preserve_keys_and_proofs'; END IF; END $body$""",
    live_lifecycle_ancestor_reservation_guard_v1(),
    *(
        f"DROP TRIGGER {table}_immutable ON public.{table}"
        for table in reversed(TABLES)
    ),
    "DROP FUNCTION public.native_live_lifecycle_append_v1(text,text)",
    "DROP FUNCTION public.native_live_lifecycle_inhibit_v1(text)",
    "DROP FUNCTION public.native_live_lifecycle_control_fence_v1(text)",
    "DROP FUNCTION public.native_live_lifecycle_key_identity_v1(text)",
    "DROP FUNCTION public.native_live_lifecycle_immutable_v1()",
    *(f"DROP TABLE public.{table}" for table in reversed(TABLES)),
)
_CHECK_SOURCE = canonical(
    [
        {"table": table, "name": name, "expression": expression}
        for table in TABLES
        for name, expression in TABLE_CHECKS[table]
    ]
).decode("ascii")
if "$checks$" in _CHECK_SOURCE:
    raise ValueError("native_live_lifecycle_check_source_invalid")
CATALOG_SQL = f"""CREATE FUNCTION public.native_live_lifecycle_catalog_v1()
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE k public.native_live_lifecycle_keys_v1; item jsonb; expression text; constraint_oid oid;
 expected jsonb; actual jsonb; result jsonb:='[]'::jsonb;
 BEGIN
 IF NOT (current_setting('role')='none' AND session_user::text=(SELECT r.rolname::text
 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.oid=d.datdba WHERE d.datname=current_database())) THEN
 SELECT * INTO k FROM public.native_live_lifecycle_keys_v1
 WHERE reconciler_oid=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=session_user);
 IF k.account_uid IS NULL THEN RAISE EXCEPTION 'native_live_lifecycle_actor_denied'; END IF;
 {_ACTOR}
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements($checks${_CHECK_SOURCE}$checks$::jsonb) LOOP
 SELECT pg_get_expr(q.conbin,q.conrelid),q.oid INTO expression,constraint_oid
 FROM pg_catalog.pg_constraint q JOIN pg_catalog.pg_class c ON c.oid=q.conrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname=item->>'table' AND q.conname=item->>'name' AND q.contype='c'
 AND q.convalidated AND NOT q.condeferrable AND NOT q.condeferred;
 IF expression IS NULL OR octet_length(expression)>8192 OR EXISTS(
 SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_proc p ON p.oid=d.refobjid
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE d.classid='pg_catalog.pg_constraint'::regclass AND d.objid=constraint_oid
 AND d.refclassid='pg_catalog.pg_proc'::regclass AND (n.nspname<>'pg_catalog' OR p.oid>=16384 OR p.provolatile<>'i'))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_operator o ON o.oid=d.refobjid
 JOIN pg_catalog.pg_proc p ON p.oid=o.oprcode JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE d.classid='pg_catalog.pg_constraint'::regclass AND d.objid=constraint_oid
 AND d.refclassid='pg_catalog.pg_operator'::regclass AND (n.nspname<>'pg_catalog' OR p.oid>=16384 OR p.provolatile<>'i'))
 THEN RAISE EXCEPTION 'native_live_lifecycle_catalog_drift'; END IF;
 EXECUTE format('EXPLAIN (FORMAT JSON,VERBOSE,COSTS OFF) SELECT 1 FROM public.%I WHERE (%s) IS NOT FALSE',item->>'table',item->>'expression') INTO expected;
 EXECUTE format('EXPLAIN (FORMAT JSON,VERBOSE,COSTS OFF) SELECT 1 FROM public.%I WHERE (%s) IS NOT FALSE',item->>'table',expression) INTO actual;
 result:=result||jsonb_build_array(jsonb_build_object('table',item->>'table','name',item->>'name','source_plan',expected,'actual_plan',actual));
 END LOOP;
 RETURN result;
 END $body$"""
UPGRADE_SQL = (
    *UPGRADE_SQL,
    CATALOG_SQL,
    "REVOKE ALL ON FUNCTION public.native_live_lifecycle_catalog_v1() FROM PUBLIC",
    RESERVATION_GUARD_SQL,
)
DOWNGRADE_SQL = (
    DOWNGRADE_SQL[0],
    "DROP FUNCTION public.native_live_lifecycle_catalog_v1()",
    *DOWNGRADE_SQL[1:],
)
SPEC_SHA256 = sha(
    canonical(
        {
            "schema": SCHEMA,
            "upgrade": UPGRADE_SQL,
            "guard": RESERVATION_GUARD_SQL,
            "downgrade": DOWNGRADE_SQL,
        }
    )
)
