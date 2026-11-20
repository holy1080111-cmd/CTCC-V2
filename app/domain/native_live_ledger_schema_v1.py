"""Additive Live DATA ledger specification; never an execution permit.

The root migration installs these statements once. No Demo table, privilege,
environment constraint or existing Live denial is changed by this module.
The dedicated Live writer cannot create/arm/clear a Control or remove a hold.
"""

import re

from app.domain.source_primitives import canonical, sha

SCHEMA = "ctcc.native_live.ledger_data.v1"
TABLES = (
    "native_live_controls_v1",
    "native_live_reservations_v1",
    "native_live_intents_v1",
    "native_live_transitions_v1",
)
HEAD = "0058"
# Actual-head verification is a metadata read, not access to any Demo record.
METADATA_READ_TABLES = ("alembic_version",)
EXECUTABLE_FUNCTIONS = (
    "native_live_control_fence_v1",
    "native_live_disarm_v1",
    "native_live_inhibit_v1",
)

UPGRADE_SQL = (
    """CREATE TABLE public.native_live_controls_v1 (
 environment varchar(4) NOT NULL CHECK(environment='live'),
 account_uid varchar(40) NOT NULL CHECK(account_uid ~ '^[1-9][0-9]{0,39}$'),
 main_uid varchar(40) NOT NULL CHECK(main_uid ~ '^[1-9][0-9]{0,39}$'),
 credential_session_sha256 varchar(64) NOT NULL CHECK(credential_session_sha256 ~ '^[a-f0-9]{64}$'),
 live_writer_oid oid NOT NULL CHECK(live_writer_oid>0),
 revision bigint NOT NULL CHECK(revision>0),
 source_revision_sha256 varchar(64) NOT NULL CHECK(source_revision_sha256 ~ '^[a-f0-9]{64}$'),
 policy_sha256 varchar(64) NOT NULL CHECK(policy_sha256 ~ '^[a-f0-9]{64}$'),
 process_sha256 varchar(64) NOT NULL CHECK(process_sha256 ~ '^[a-f0-9]{64}$'),
 emergency_stop boolean NOT NULL DEFAULT true,
 armed boolean NOT NULL DEFAULT false,
 arm_id varchar(64), arm_expires_at timestamptz, lease_until timestamptz NOT NULL,
 per_trade_risk_cap numeric(60,20) NOT NULL CHECK(per_trade_risk_cap>0 AND per_trade_risk_cap::text NOT IN ('NaN','Infinity','-Infinity')),
 portfolio_risk_cap numeric(60,20) NOT NULL CHECK(portfolio_risk_cap>=per_trade_risk_cap AND portfolio_risk_cap::text NOT IN ('NaN','Infinity','-Infinity')),
 margin_cap numeric(60,20) NOT NULL CHECK(margin_cap>0 AND margin_cap::text NOT IN ('NaN','Infinity','-Infinity')),
 micro_notional_cap numeric(60,20) NOT NULL CHECK(micro_notional_cap>0 AND micro_notional_cap::text NOT IN ('NaN','Infinity','-Infinity')),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment,account_uid),
 CHECK((NOT armed AND arm_id IS NULL AND arm_expires_at IS NULL) OR
 (armed AND NOT emergency_stop AND arm_id IS NOT NULL AND arm_expires_at IS NOT NULL AND arm_id ~ '^[a-f0-9]{64}$' AND
 updated_at<arm_expires_at AND arm_expires_at<=lease_until))
)""",
    """CREATE TABLE public.native_live_reservations_v1 (
 reservation_id varchar(64) PRIMARY KEY CHECK(reservation_id ~ '^[a-f0-9]{64}$'),
 environment varchar(4) NOT NULL CHECK(environment='live'),
 account_uid varchar(40) NOT NULL, event_key varchar(64) NOT NULL CHECK(event_key ~ '^[a-f0-9]{64}$'),
 candidate_sha256 varchar(64) NOT NULL CHECK(candidate_sha256 ~ '^[a-f0-9]{64}$'),
 report_sha256 varchar(64) NOT NULL CHECK(report_sha256 ~ '^[a-f0-9]{64}$'),
 source_revision_sha256 varchar(64) NOT NULL CHECK(source_revision_sha256 ~ '^[a-f0-9]{64}$'),
 control_revision bigint NOT NULL CHECK(control_revision>0),
 arm_id varchar(64) NOT NULL CHECK(arm_id ~ '^[a-f0-9]{64}$'),
 risk_amount numeric(60,20) NOT NULL CHECK(risk_amount>0 AND risk_amount::text NOT IN ('NaN','Infinity','-Infinity')),
 margin_amount numeric(60,20) NOT NULL CHECK(margin_amount>0 AND margin_amount::text NOT IN ('NaN','Infinity','-Infinity')),
 notional_amount numeric(60,20) NOT NULL CHECK(notional_amount>0 AND notional_amount::text NOT IN ('NaN','Infinity','-Infinity')),
 deadline timestamptz NOT NULL,
 state varchar(16) NOT NULL CHECK(state IN ('reserved','uncertain','reconciled')),
 resolution_sha256 varchar(64),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(environment,account_uid) REFERENCES public.native_live_controls_v1(environment,account_uid) ON DELETE RESTRICT,
 UNIQUE(environment,account_uid,event_key),
 UNIQUE(environment,account_uid,arm_id),
 CHECK(deadline>created_at),
 CHECK((state='reconciled' AND resolution_sha256 IS NOT NULL AND resolution_sha256 ~ '^[a-f0-9]{64}$') OR
 (state<>'reconciled' AND resolution_sha256 IS NULL))
)""",
    """CREATE TABLE public.native_live_intents_v1 (
 reservation_id varchar(64) PRIMARY KEY REFERENCES public.native_live_reservations_v1(reservation_id) ON DELETE RESTRICT,
 environment varchar(4) NOT NULL CHECK(environment='live'), account_uid varchar(40) NOT NULL,
 client_order_id varchar(32) NOT NULL CHECK(client_order_id ~ '^[A-Za-z0-9]{1,32}$'),
 protection_client_id varchar(32) NOT NULL CHECK(protection_client_id ~ '^[A-Za-z0-9]{1,32}$'),
 intent_json text NOT NULL CHECK(octet_length(intent_json) BETWEEN 1 AND 65536),
 intent_sha256 varchar(64) NOT NULL CHECK(intent_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(environment,account_uid) REFERENCES public.native_live_controls_v1(environment,account_uid) ON DELETE RESTRICT,
 UNIQUE(environment,account_uid,client_order_id), UNIQUE(environment,account_uid,protection_client_id)
)""",
    """CREATE TABLE public.native_live_transitions_v1 (
 reservation_id varchar(64) NOT NULL REFERENCES public.native_live_reservations_v1(reservation_id) ON DELETE RESTRICT,
 sequence bigint NOT NULL CHECK(sequence>0), kind varchar(32) NOT NULL CHECK(kind IN ('intent_committed','uncertain','reconciled')),
 previous_sha256 varchar(64), journal_json text NOT NULL CHECK(octet_length(journal_json) BETWEEN 1 AND 8192),
 journal_sha256 varchar(64) NOT NULL CHECK(journal_sha256 ~ '^[a-f0-9]{64}$'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(reservation_id,sequence),
 CHECK((sequence=1 AND previous_sha256 IS NULL) OR (sequence>1 AND previous_sha256 ~ '^[a-f0-9]{64}$'))
)""",
    """CREATE INDEX native_live_active_holds_v1 ON public.native_live_reservations_v1(environment,account_uid) WHERE state<>'reconciled'""",
    """CREATE FUNCTION public.native_live_control_fence_v1(e text,u text)
 RETURNS SETOF public.native_live_controls_v1 LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE c public.native_live_controls_v1; who oid;
 BEGIN
 SELECT oid INTO who FROM pg_catalog.pg_roles WHERE rolname=session_user;
 IF current_setting('role')<>'none' OR current_setting('transaction_read_only')<>'off'
 OR (SELECT array_agg(version_num::text) FROM public.alembic_version) IS DISTINCT FROM ARRAY['0058']::text[]
 OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_backend_pid() AND ssl)
 THEN RAISE EXCEPTION 'native_live_control_fence_denied'; END IF;
 SELECT * INTO c FROM public.native_live_controls_v1 WHERE environment=e AND account_uid=u FOR UPDATE;
 IF c.account_uid IS NULL OR e<>'live' OR c.live_writer_oid IS DISTINCT FROM who
 OR who=(SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database())
 THEN RAISE EXCEPTION 'native_live_control_fence_denied'; END IF;
 RETURN NEXT c;
 END $body$""",
    """REVOKE ALL ON FUNCTION public.native_live_control_fence_v1(text,text) FROM PUBLIC""",
    """CREATE FUNCTION public.native_live_inhibit_v1(e text,u text) RETURNS bigint
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE c public.native_live_controls_v1; r bigint;
 BEGIN
 SELECT * INTO c FROM public.native_live_control_fence_v1(e,u);
 IF c.account_uid IS NULL THEN RAISE EXCEPTION 'native_live_inhibit_denied'; END IF;
 UPDATE public.native_live_controls_v1 SET emergency_stop=true,armed=false,arm_id=NULL,arm_expires_at=NULL,
 revision=revision+1,updated_at=clock_timestamp() WHERE environment=e AND account_uid=u RETURNING revision INTO r;
 RETURN r;
 END $body$""",
    """REVOKE ALL ON FUNCTION public.native_live_inhibit_v1(text,text) FROM PUBLIC""",
    """CREATE FUNCTION public.native_live_disarm_v1(e text,u text,a text) RETURNS bigint
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE c public.native_live_controls_v1; r bigint;
 BEGIN
 SELECT * INTO c FROM public.native_live_control_fence_v1(e,u);
 IF c.account_uid IS NULL OR a IS NULL OR a !~ '^[a-f0-9]{64}$'
 THEN RAISE EXCEPTION 'native_live_disarm_denied'; END IF;
 IF c.arm_id IS NULL THEN RETURN c.revision; END IF;
 IF c.arm_id IS DISTINCT FROM a THEN RAISE EXCEPTION 'native_live_disarm_arm_changed'; END IF;
 UPDATE public.native_live_controls_v1 SET armed=false,arm_id=NULL,arm_expires_at=NULL,
 revision=revision+1,updated_at=clock_timestamp() WHERE environment=e AND account_uid=u RETURNING revision INTO r;
 RETURN r;
 END $body$""",
    """REVOKE ALL ON FUNCTION public.native_live_disarm_v1(text,text,text) FROM PUBLIC""",
    """CREATE FUNCTION public.native_live_reservation_guard_v1() RETURNS trigger
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE c public.native_live_controls_v1; r numeric; m numeric; n numeric;
 BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native_live_reservation_immutable'; END IF;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('ctcc-live-ledger-v1:live:'||NEW.account_uid,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 SELECT * INTO c FROM public.native_live_control_fence_v1(NEW.environment,NEW.account_uid);
 IF c.account_uid IS NULL OR c.emergency_stop IS DISTINCT FROM false OR c.armed IS DISTINCT FROM true
 OR c.arm_id IS NULL OR c.arm_expires_at IS NULL OR c.arm_expires_at<=clock_timestamp()
 OR NEW.risk_amount<=0 OR NEW.margin_amount<=0 OR NEW.notional_amount<=0
 OR NEW.deadline>c.arm_expires_at OR NEW.deadline<=clock_timestamp()
 OR NEW.control_revision IS DISTINCT FROM c.revision
 OR NEW.arm_id IS DISTINCT FROM c.arm_id
 OR NEW.source_revision_sha256 IS DISTINCT FROM c.source_revision_sha256
 OR NEW.state<>'reserved' OR NEW.resolution_sha256 IS NOT NULL
 THEN RAISE EXCEPTION 'native_live_reservation_control_denied'; END IF;
 SELECT COALESCE(sum(risk_amount),0),COALESCE(sum(margin_amount),0),COALESCE(sum(notional_amount),0)
 INTO r,m,n FROM public.native_live_reservations_v1
 WHERE environment=NEW.environment AND account_uid=NEW.account_uid AND state<>'reconciled';
 IF NEW.risk_amount>c.per_trade_risk_cap OR r+NEW.risk_amount>c.portfolio_risk_cap
 OR m+NEW.margin_amount>c.margin_cap OR n+NEW.notional_amount>c.micro_notional_cap
 THEN RAISE EXCEPTION 'native_live_reservation_caps_denied'; END IF;
 NEW.created_at:=clock_timestamp(); RETURN NEW;
 END $body$""",
    """REVOKE ALL ON FUNCTION public.native_live_reservation_guard_v1() FROM PUBLIC""",
    """CREATE TRIGGER native_live_reservations_v1_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.native_live_reservations_v1 FOR EACH ROW EXECUTE FUNCTION public.native_live_reservation_guard_v1()""",
    """CREATE FUNCTION public.native_live_data_guard_v1() RETURNS trigger
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $body$
 DECLARE j jsonb; p public.native_live_reservations_v1; prior text;
 BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native_live_data_immutable'; END IF;
 IF TG_TABLE_NAME='native_live_intents_v1' THEN
 j:=NEW.intent_json::jsonb;
 SELECT * INTO p FROM public.native_live_reservations_v1 WHERE reservation_id=NEW.reservation_id;
 IF p.reservation_id IS NULL OR p.environment<>NEW.environment OR p.account_uid<>NEW.account_uid
 OR encode(sha256(convert_to(NEW.intent_json,'UTF8')),'hex') IS DISTINCT FROM NEW.intent_sha256
 OR j->>'schema' IS DISTINCT FROM 'ctcc.native_live.ledger_data.v1'
 OR j->>'environment' IS DISTINCT FROM 'live' OR j->>'account_uid' IS DISTINCT FROM p.account_uid
 OR j->>'event_key' IS DISTINCT FROM p.event_key OR j->>'candidate_sha256' IS DISTINCT FROM p.candidate_sha256
 OR j->>'report_sha256' IS DISTINCT FROM p.report_sha256
 OR j->>'source_revision_sha256' IS DISTINCT FROM p.source_revision_sha256
 OR j->>'reservation_id' IS DISTINCT FROM NEW.reservation_id
 OR j->>'client_order_id' IS DISTINCT FROM NEW.client_order_id
 OR j->>'protection_client_id' IS DISTINCT FROM NEW.protection_client_id
 OR (j->>'risk_amount')::numeric IS DISTINCT FROM p.risk_amount
 OR (j->>'margin_amount')::numeric IS DISTINCT FROM p.margin_amount
 OR (j->>'notional_amount')::numeric IS DISTINCT FROM p.notional_amount
 OR (j->>'expected_control_revision')::bigint IS DISTINCT FROM p.control_revision
 OR j->>'arm_id' IS DISTINCT FROM p.arm_id
 OR (j->>'deadline')::timestamptz IS DISTINCT FROM p.deadline
 OR j->'qualification_verified' IS DISTINCT FROM 'false'::jsonb
 OR j->'execution_authority' IS DISTINCT FROM 'false'::jsonb
 OR j->>'admission' IS DISTINCT FROM 'DENY'
 THEN RAISE EXCEPTION 'native_live_intent_binding_denied'; END IF;
 NEW.created_at:=clock_timestamp();
 ELSIF TG_TABLE_NAME='native_live_transitions_v1' THEN
 IF NEW.sequence>1 THEN
 SELECT journal_sha256 INTO prior FROM public.native_live_transitions_v1
 WHERE reservation_id=NEW.reservation_id AND sequence=NEW.sequence-1;
 END IF;
 IF NEW.previous_sha256 IS DISTINCT FROM prior OR
 (NEW.sequence=1 AND NEW.kind<>'intent_committed') OR (NEW.sequence>1 AND NEW.kind='intent_committed') OR
 NOT EXISTS(SELECT 1 FROM public.native_live_intents_v1 WHERE reservation_id=NEW.reservation_id) OR
 NEW.journal_json::jsonb->>'schema' IS DISTINCT FROM 'ctcc.native_live.ledger_data.v1' OR
 NEW.journal_json::jsonb->>'intent_sha256' IS DISTINCT FROM (SELECT intent_sha256 FROM public.native_live_intents_v1 WHERE reservation_id=NEW.reservation_id) OR
 NEW.journal_json::jsonb->>'reservation_id' IS DISTINCT FROM NEW.reservation_id OR
 NEW.journal_json::jsonb->>'kind' IS DISTINCT FROM NEW.kind OR
 NEW.journal_json::jsonb->'execution_authority' IS DISTINCT FROM 'false'::jsonb OR
 NEW.journal_json::jsonb->>'admission' IS DISTINCT FROM 'DENY' OR
 encode(sha256(convert_to(NEW.journal_json,'UTF8')),'hex') IS DISTINCT FROM NEW.journal_sha256
 THEN RAISE EXCEPTION 'native_live_transition_chain_denied'; END IF;
 NEW.recorded_at:=clock_timestamp();
 ELSE RAISE EXCEPTION 'native_live_data_table_denied'; END IF;
 RETURN NEW;
 END $body$""",
    """REVOKE ALL ON FUNCTION public.native_live_data_guard_v1() FROM PUBLIC""",
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.native_live_data_guard_v1()"
        for table in TABLES[2:]
    ),
)


def live_ledger_grant_sql_v1(writer: str) -> tuple[str, ...]:
    if (
        type(writer) is not str
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", writer) is None
    ):
        raise ValueError("native_live_writer_identifier_refused")
    return (
        f'GRANT USAGE ON SCHEMA public TO "{writer}"',
        *(
            f'GRANT SELECT ON TABLE public.{table} TO "{writer}"'
            for table in (*TABLES, *METADATA_READ_TABLES)
        ),
        *(
            f'GRANT INSERT ON TABLE public.{table} TO "{writer}"'
            for table in TABLES[1:]
        ),
        f'GRANT EXECUTE ON FUNCTION public.native_live_control_fence_v1(text,text) TO "{writer}"',
        f'GRANT EXECUTE ON FUNCTION public.native_live_inhibit_v1(text,text) TO "{writer}"',
        f'GRANT EXECUTE ON FUNCTION public.native_live_disarm_v1(text,text,text) TO "{writer}"',
    )


def _named_table_spec(table, sql):
    """Name source-defined constraints identically for SQL and ORM metadata."""
    checks = []
    offset = 0
    while (match := re.search(r"\bCHECK\(", sql[offset:])) is not None:
        start = offset + match.start()
        opening = offset + match.end() - 1
        level = 1
        quoted = False
        index = opening + 1
        while level:
            char = sql[index]
            if char == "'":
                if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                    index += 2
                    continue
                quoted = not quoted
            if not quoted:
                level += (char == "(") - (char == ")")
            index += 1
        expression = sql[opening + 1 : index - 1]
        name = f"ck_{table}_guard_{len(checks)}"
        checks.append((name, expression))
        replacement = f"CONSTRAINT {name} CHECK({expression})"
        sql = sql[:start] + replacement + sql[index:]
        offset = start + len(replacement)
    unique = []

    def unique_name(match):
        name = f"uq_{table}_guard_{len(unique)}"
        fields = tuple(match[1].split(","))
        unique.append((name, fields))
        return f"CONSTRAINT {name} UNIQUE({match[1]})"

    sql = re.sub(r"UNIQUE\(([^)]*)\)", unique_name, sql)
    sql = sql.replace("PRIMARY KEY", f"CONSTRAINT pk_{table} PRIMARY KEY")
    sql = sql.replace(
        "FOREIGN KEY(environment,account_uid)",
        f"CONSTRAINT fk_{table}_scope FOREIGN KEY(environment,account_uid)",
    )
    sql = sql.replace(
        " REFERENCES public.native_live_reservations_v1",
        f" CONSTRAINT fk_{table}_reservation REFERENCES public.native_live_reservations_v1",
    )
    return sql, tuple(checks), tuple(unique)


_NAMED = tuple(
    _named_table_spec(table, sql)
    for table, sql in zip(TABLES, UPGRADE_SQL[:4], strict=True)
)
TABLE_CHECKS = {table: item[1] for table, item in zip(TABLES, _NAMED, strict=True)}
TABLE_UNIQUES = {table: item[2] for table, item in zip(TABLES, _NAMED, strict=True)}
UPGRADE_SQL = tuple(item[0] for item in _NAMED) + UPGRADE_SQL[4:]
DOWNGRADE_SQL = (
    """DO $body$ BEGIN
 IF EXISTS(SELECT 1 FROM public.native_live_controls_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_reservations_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_intents_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_transitions_v1)
 THEN RAISE EXCEPTION 'native_live_downgrade_requires_empty_retained_custody'; END IF;
 END $body$""",
    *(f"DROP TRIGGER {table}_immutable ON public.{table}" for table in TABLES[1:]),
    "DROP FUNCTION public.native_live_data_guard_v1()",
    "DROP FUNCTION public.native_live_reservation_guard_v1()",
    "DROP FUNCTION public.native_live_inhibit_v1(text,text)",
    "DROP FUNCTION public.native_live_disarm_v1(text,text,text)",
    "DROP FUNCTION public.native_live_control_fence_v1(text,text)",
    *(f"DROP TABLE public.{table}" for table in reversed(TABLES)),
)
SPEC_SHA256 = sha(canonical({"schema": SCHEMA, "head": HEAD, "sql": UPGRADE_SQL}))
