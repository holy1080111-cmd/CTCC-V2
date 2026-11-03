"""Actual current55 graph; no inherited54 native seal or account authority.

Only unchanged table/body subroutines are reused from strict54. The actual55
extra tables, modified Control/issuer bodies and complete60-row RI graph are
independently checked before those structural subroutines are called. Actual
head is55 throughout; no observed head/role/source is rewritten to54.
"""

import hashlib

from app.domain import collector_current_0054_catalog_v1 as old54
from app.domain import controlled_history_current55_schema_v2 as controlled_history
from app.domain import demo_operator_ack_schema_v55 as operator
from app.domain import native_close_intent_catalog_v55 as native_close_index
from app.domain import native_close_intent_schema_v55 as native_close_intent
from app.domain import native_closed_report_catalog_v55 as native_report
from app.domain import native_filled_flat_closure_schema_v55 as native_closure
from app.domain import native_leverage_configuration_catalog_v55 as native_configuration
from app.domain import (
    native_leverage_configuration_enforcement_v55 as configuration_enforcement,
)
from app.domain import native_submission_reporting_schema_v55 as native_submission
from app.domain import native_terminal_catalog_v55 as native_terminal
from app.domain.source_primitives import canonical

SCHEMA = "ctcc.collector_current0055.actual_catalog.v1"
CATALOG_KEYS = frozenset(
    {
        "schema",
        "issued",
        "lineage",
        "lineage_base",
        "role",
        "table_guards",
        "defaults",
        "acl",
        "custody_internal",
        "scope_parent",
        "operator55",
        "controlled_history55",
        "native_submission55",
        "native_closure55",
        "native_terminal55",
        "native_report55",
        "native_close_intent55",
        "native_configuration55",
    }
)
PG17_NATIVE_BASELINE_STATUS = "SEALED_FROM_REVIEWED_NATIVE_PG17"
PG17_NATIVE_BASELINE_SHA256 = (
    "6243338abe9cfcbb4eb613bc3d21cd7adbaf8059644e8b7a1c2df9755ac340c7"
)
FROZEN_OPERATOR_DDL_SHA256 = (
    "9535c233c7398c9f70d407a6c172576f251cc4d6d2cd9ce2a0e716164b69a629"
)
BASE_KEYS, PARENTS, old53, lineage, issued = (
    old54.BASE_KEYS,
    old54.PARENTS,
    old54.old53,
    old54.lineage,
    old54.issued,
)
_RELATIONS = old53.PARENTS | {
    "qualification_reservations",
    lineage.TABLE,
    issued.TABLE,
    operator.CLEAR_TABLE,
    operator.COUNTER_TABLE,
}
_RELATIONS |= set(native_configuration.schema.TABLES) | {
    "qualification_reservation_transitions",
    "qualification_submission_outcomes",
}
_MONITORED = {
    "demo_account_controls",
    "demo_control_journal",
    operator.CLEAR_TABLE,
    operator.COUNTER_TABLE,
}
_NEW_TABLES = {operator.CLEAR_TABLE, operator.COUNTER_TABLE}
_PRIVILEGES = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
}
_ACTOR_PRIVILEGES = {
    "demo_account_controls": {"SELECT", "INSERT", "UPDATE"},
    "demo_control_journal": {"SELECT", "INSERT"},
    operator.CLEAR_TABLE: {"SELECT", "INSERT"},
    operator.COUNTER_TABLE: {"SELECT", "INSERT"},
    "qualification_submission_outcomes": {"SELECT", "INSERT"},
}
_SCOPE = ["environment", "account_id", "settlement_currency"]
_KEYS = {
    operator.CLEAR_TABLE: {
        "demo_control_clear_acknowledgements_v2_pkey": (
            "p",
            ["ack_sha256"],
            None,
            None,
            False,
        ),
        "fk_control_clear_scope": (
            "f",
            _SCOPE,
            "qualification_account_scopes",
            _SCOPE,
            False,
        ),
        "fk_control_clear_stopped": (
            "f",
            ["environment", "account_id", "stopped_control_revision"],
            "demo_control_journal",
            ["environment", "account_id", "control_revision"],
            False,
        ),
        "fk_control_clear_result": (
            "f",
            ["environment", "account_id", "clear_control_revision"],
            "demo_control_journal",
            ["environment", "account_id", "control_revision"],
            True,
        ),
        "fk_control_clear_current": (
            "f",
            ["source_lineage_sha256"],
            lineage.TABLE,
            ["lineage_sha256"],
            False,
        ),
        "uq_control_clear_command": (
            "u",
            ["environment", "account_id", "command_id"],
            None,
            None,
            False,
        ),
        "uq_control_clear_once": (
            "u",
            ["environment", "account_id", "stopped_control_revision"],
            None,
            None,
            False,
        ),
    },
    operator.COUNTER_TABLE: {
        "demo_policy_counter_initializations_v1_pkey": (
            "p",
            ["initialization_sha256"],
            None,
            None,
            False,
        ),
        "fk_policy_counter_scope": (
            "f",
            _SCOPE,
            "qualification_account_scopes",
            _SCOPE,
            False,
        ),
        "fk_policy_counter_control": (
            "f",
            ["environment", "account_id", "control_revision"],
            "demo_control_journal",
            ["environment", "account_id", "control_revision"],
            False,
        ),
        "fk_policy_counter_current": (
            "f",
            ["source_lineage_sha256"],
            lineage.TABLE,
            ["lineage_sha256"],
            False,
        ),
        "uq_policy_counter_once": ("u", _SCOPE, None, None, False),
        "uq_policy_counter_command": (
            "u",
            ["environment", "account_id", "command_id"],
            None,
            None,
            False,
        ),
    },
    "demo_account_controls": {
        "pk_demo_account_controls": (
            "p",
            ["environment", "account_id"],
            None,
            None,
            False,
        ),
    },
    "demo_control_journal": {
        "pk_demo_control_journal": (
            "p",
            ["environment", "account_id", "control_revision"],
            None,
            None,
            False,
        ),
        "fk_demo_control_journal_environment_demo_account_controls": (
            "f",
            ["environment", "account_id"],
            "demo_account_controls",
            ["environment", "account_id"],
            False,
        ),
        "uq_demo_control_journal_environment": (
            "u",
            ["environment", "account_id", "command_id"],
            None,
            None,
            False,
        ),
    },
}
_CHECKS = {
    operator.CLEAR_TABLE: {
        "control_clear_scope",
        "control_clear_revisions",
        "control_clear_claims_pair",
        "control_clear_digests",
        "control_clear_bounds",
        "control_clear_clocks",
    },
    operator.COUNTER_TABLE: {
        "policy_counter_scope",
        "policy_counter_revisions",
        "policy_counter_claims_pair",
        "policy_counter_digests",
        "policy_counter_bounds",
        "policy_counter_clocks",
    },
    "demo_account_controls": {
        "ck_demo_account_controls_identity",
        "ck_demo_account_controls_revisions",
        "ck_demo_account_controls_digests",
        "ck_demo_account_controls_owner_pair",
        "ck_demo_account_controls_clocks",
        "ck_demo_account_controls_state_bound",
        "ck_demo_account_controls_arm_pair",
    },
    "demo_control_journal": {
        "ck_demo_control_journal_revision",
        "ck_demo_control_journal_digests",
        "ck_demo_control_journal_chain",
        "ck_demo_control_journal_action",
        "ck_demo_control_journal_event_bound",
    },
}
_TRIGGERS = {
    "demo_account_controls": {
        "demo_control_state_guard": (31, "demo_control_state_guard", False),
        "demo_control_commit_guard": (21, "demo_control_commit_guard", True),
        "demo_control_no_truncate": (34, "qualification_ledger_immutable", False),
    },
    "demo_control_journal": {
        "demo_control_journal_guard": (7, "demo_control_journal_guard", False),
        "demo_control_journal_immutable": (27, "qualification_ledger_immutable", False),
        "demo_control_journal_no_truncate": (
            34,
            "qualification_ledger_immutable",
            False,
        ),
    },
    operator.CLEAR_TABLE: {
        "control_clear_ack_insert": (7, "control_clear_ack_guard_v2", False),
        "control_clear_ack_commit": (5, "control_clear_ack_commit_guard_v2", True),
        "control_clear_ack_immutable": (
            27,
            "control_operator_ack_immutable_v55",
            False,
        ),
        "control_clear_ack_no_truncate": (
            34,
            "control_operator_ack_immutable_v55",
            False,
        ),
    },
    operator.COUNTER_TABLE: {
        "policy_counter_initialization_insert": (
            7,
            "policy_counter_initialization_guard_v1",
            False,
        ),
        "policy_counter_initialization_commit": (
            5,
            "policy_counter_initialization_commit_guard_v1",
            True,
        ),
        "policy_counter_initialization_immutable": (
            27,
            "control_operator_ack_immutable_v55",
            False,
        ),
        "policy_counter_initialization_no_truncate": (
            34,
            "control_operator_ack_immutable_v55",
            False,
        ),
    },
}


class Current0055CatalogError(ValueError):
    pass


def _deny():
    raise Current0055CatalogError("current0055_actual_catalog_unavailable") from None


def _oid(value):
    if type(value) is not int or not 0 < value < 2**32:
        _deny()
    return value


_TRIGGERS["demo_control_journal"][configuration_enforcement.TRIGGER] = (
    7,
    configuration_enforcement.FUNCTION,
    False,
)


def _body(source, delimiter):
    start, end = "AS " + delimiter, delimiter + " LANGUAGE"
    if type(source) is not str or source.count(start) != 1 or source.count(end) != 1:
        _deny()
    return source.split(start, 1)[1].split(end, 1)[0]


def _sha(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _source_functions():
    if (
        operator.FROZEN_DDL_SHA256 != FROZEN_OPERATOR_DDL_SHA256
        or len(operator.statements()) != 27
    ):
        _deny()
    result = {
        "demo_control_state_guard": (
            _sha(_body(operator.STATE_GUARD_SQL, "$$")),
            False,
            None,
            True,
        ),
        "demo_control_journal_guard": (
            _sha(_body(operator.JOURNAL_GUARD_SQL, "$$")),
            False,
            None,
            True,
        ),
        "demo_control_commit_guard": (
            "873cc68ee3647b2d56f1c73cb18d4a67cfa736ad3cb0713d332a821cf4d0e67e",
            False,
            None,
            True,
        ),
        "qualification_ledger_immutable": (
            "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649",
            False,
            None,
            True,
        ),
    }
    for name, source, security in (
        ("control_clear_ack_guard_v2", operator.ACK_GUARD_SQL, True),
        ("control_clear_ack_commit_guard_v2", operator.ACK_COMMIT_SQL, True),
        ("policy_counter_initialization_guard_v1", operator.COUNTER_GUARD_SQL, True),
        (
            "policy_counter_initialization_commit_guard_v1",
            operator.COUNTER_COMMIT_SQL,
            True,
        ),
        ("control_operator_ack_immutable_v55", operator.IMMUTABLE_SQL, False),
    ):
        result[name] = (
            _sha(_body(source, "$ctcc$")),
            security,
            ["search_path=pg_catalog, pg_temp"],
            False,
        )
    result[configuration_enforcement.FUNCTION] = (
        configuration_enforcement.BODY_SHA256,
        False,
        ["search_path=pg_catalog, pg_temp"],
        False,
    )
    return result


def _context(value, *, role, read_only, parents, schema_name):
    if (
        value["schema_version"] != schema_name
        or value["database_head"] != "0055"
        or value["actor"] != role["oid"]
        or type(value["table_oid"]) is not int
        or not 0 < value["table_oid"] < 2**32
    ):
        _deny()
    server = value["server"]
    if type(server) is not dict or set(server) != {
        "version_num",
        "current_user",
        "session_user",
        "role_setting",
        "database",
        "timezone",
        "search_path",
        "isolation",
        "read_only",
        "synchronous_commit",
        "fsync",
        "full_page_writes",
        "replication_role",
        "recovery",
        "role_oid",
        "superuser",
        "can_login",
    }:
        _deny()
    version = server["version_num"]
    if (
        type(version) is not str
        or not version.isascii()
        or not version.isdecimal()
        or not 170000 <= int(version) < 180000
        or server["role_oid"] != role["oid"]
        or server["current_user"] != role["name"]
        or server["session_user"] != role["name"]
        or server["superuser"] is not role["superuser"]
        or server["can_login"] is not True
        or server["recovery"] is not False
        or type(server["database"]) is not str
        or not server["database"]
    ):
        _deny()
    for name, expected in (
        ("role_setting", "none"),
        ("timezone", "UTC"),
        ("search_path", "pg_catalog, public, pg_temp"),
        ("isolation", "read committed"),
        ("read_only", read_only),
        ("synchronous_commit", "on"),
        ("fsync", "on"),
        ("full_page_writes", "on"),
        ("replication_role", "origin"),
    ):
        if server[name] != expected:
            _deny()
    rows = value["parent_facts"]
    if type(rows) is not list or len(rows) != len(parents):
        _deny()
    identities = {}
    for row in rows:
        if (
            type(row) is not dict
            or set(row)
            != {"name", "oid", "owner", "kind", "persistence", "rls", "force_rls"}
            or row["name"] not in parents
            or row["name"] in identities
            or type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["owner"] != value["owner"]
            or row["kind"] != "r"
            or row["persistence"] != "p"
            or row["rls"] is not False
            or row["force_rls"] is not False
        ):
            _deny()
        identities[row["name"]] = row["oid"]
    return identities


_OPERATOR_CATALOG_SQL = """WITH relations AS (
 SELECT c.*,n.nspname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname IN(
  'qualification_account_scopes','qualification_reservations','demo_account_controls','demo_control_journal',
  'demo_account_capture_events','public_runtime_raw_bundles_v1','demo_collector_current_revision_lineages',
  'demo_collector_issued_account_revisions','demo_control_clear_acknowledgements_v2','demo_policy_counter_initializations_v1',
  'qualification_leverage_configuration_intents_v1','qualification_leverage_configuration_attempts_v1',
  'qualification_leverage_configuration_results_v1','qualification_reservation_transitions','qualification_submission_outcomes')
), monitored AS (
 SELECT * FROM relations WHERE relname IN('demo_account_controls','demo_control_journal',
  'demo_control_clear_acknowledgements_v2','demo_policy_counter_initializations_v1')
), actual_triggers AS (
 SELECT t.* FROM pg_catalog.pg_trigger t JOIN monitored c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal
), actual_functions AS (
 SELECT p.*,n.nspname,l.lanname FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace JOIN pg_catalog.pg_language l ON l.oid=p.prolang
 WHERE p.oid IN(SELECT tgfoid FROM actual_triggers)
), relevant_fks AS (
 SELECT k.* FROM pg_catalog.pg_constraint k WHERE k.contype='f' AND (
  k.conrelid IN('public.demo_collector_current_revision_lineages'::regclass,
   'public.demo_collector_issued_account_revisions'::regclass,'public.demo_control_clear_acknowledgements_v2'::regclass,
   'public.demo_policy_counter_initializations_v1'::regclass,
   'public.qualification_leverage_configuration_intents_v1'::regclass,
   'public.qualification_leverage_configuration_attempts_v1'::regclass,
   'public.qualification_leverage_configuration_results_v1'::regclass)
  OR k.confrelid IN('public.demo_collector_current_revision_lineages'::regclass,
   'public.demo_collector_issued_account_revisions'::regclass,'public.demo_control_clear_acknowledgements_v2'::regclass,
   'public.demo_policy_counter_initializations_v1'::regclass,
   'public.qualification_leverage_configuration_intents_v1'::regclass,
   'public.qualification_leverage_configuration_attempts_v1'::regclass,
   'public.qualification_leverage_configuration_results_v1'::regclass))
)
SELECT jsonb_build_object(
 'head',(SELECT jsonb_agg(version_num ORDER BY version_num COLLATE "C") FROM public.alembic_version),
 'relations',(SELECT jsonb_agg(jsonb_build_object(
  'name',c.relname,'oid',c.oid::bigint,'owner',c.relowner::bigint,'namespace',c.nspname,
  'kind',c.relkind::text,'persistence',c.relpersistence::text,'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
  'partition',c.relispartition,'inherited',EXISTS(SELECT 1 FROM pg_catalog.pg_inherits x WHERE x.inhrelid=c.oid OR x.inhparent=c.oid),
  'rules',EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite x WHERE x.ev_class=c.oid),
  'policies',EXISTS(SELECT 1 FROM pg_catalog.pg_policy x WHERE x.polrelid=c.oid),
  'read_columns',CASE WHEN c.relname='qualification_reservations' THEN (
   SELECT jsonb_agg(jsonb_build_array(a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull)
    ORDER BY a.attnum) FROM pg_catalog.pg_attribute a
    WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) ELSE NULL END)
   ORDER BY c.relname COLLATE "C") FROM relations c),
 'tables',(SELECT jsonb_agg(jsonb_build_object(
  'name',c.relname,'oid',c.oid::bigint,
  'columns',(SELECT jsonb_agg(jsonb_build_array(a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),
   a.attnotnull,pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attgenerated,a.attidentity,
   CASE WHEN a.attcollation=0 THEN NULL ELSE cn.nspname||'.'||co.collname END)
   ORDER BY a.attnum) FROM pg_catalog.pg_attribute a
   LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
   LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
   LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=co.collnamespace
   WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
  'column_acl',EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND a.attacl IS NOT NULL),
  'constraints',(SELECT jsonb_agg(jsonb_build_object(
   'oid',k.oid::bigint,'name',k.conname,'kind',k.contype::text,'validated',k.convalidated,'deferrable',k.condeferrable,'deferred',k.condeferred,
   'columns',(SELECT jsonb_agg(a.attname ORDER BY s.n) FROM unnest(k.conkey) WITH ORDINALITY s(attnum,n)
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=s.attnum),
   'parent',CASE WHEN k.confrelid=0 THEN NULL ELSE k.confrelid::regclass::text END,
   'parent_columns',(SELECT jsonb_agg(a.attname ORDER BY s.n) FROM unnest(k.confkey) WITH ORDINALITY s(attnum,n)
    JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=s.attnum),
   'delete_action',k.confdeltype::text,'update_action',k.confupdtype::text,'match',k.confmatchtype::text,
   'definition',CASE WHEN k.contype='t' THEN NULL ELSE pg_catalog.pg_get_constraintdef(k.oid,true) END) ORDER BY k.conname COLLATE "C")
   FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid),
  'indexes',(SELECT jsonb_agg(jsonb_build_object('name',i.relname,'unique',x.indisunique,'primary',x.indisprimary,
   'valid',x.indisvalid,'ready',x.indisready,'live',x.indislive,'exclusion',x.indisexclusion,
   'definition',pg_catalog.pg_get_indexdef(i.oid)) ORDER BY i.relname COLLATE "C")
   FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid WHERE x.indrelid=c.oid),
  'acl',(SELECT jsonb_agg(jsonb_build_array(x.grantee::bigint,x.grantor::bigint,x.privilege_type,x.is_grantable)
   ORDER BY x.grantee,x.privilege_type COLLATE "C")
   FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x))
   ORDER BY c.relname COLLATE "C") FROM monitored c),
 'triggers',(SELECT jsonb_agg(jsonb_build_object(
  'oid',t.oid::bigint,'name',t.tgname,'relation_oid',t.tgrelid::bigint,'function_oid',t.tgfoid::bigint,
  'enabled',t.tgenabled::text,'type',t.tgtype::int,'internal',t.tgisinternal,'constraint_oid',t.tgconstraint::bigint,
  'parent_oid',t.tgparentid::bigint,'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,
  'args_count',t.tgnargs::int,'args',encode(t.tgargs,'hex'),'attributes',t.tgattr::text,
  'when',pg_catalog.pg_get_expr(t.tgqual,t.tgrelid),'old_table',t.tgoldtable,'new_table',t.tgnewtable,
  'definition',pg_catalog.pg_get_triggerdef(t.oid,true)) ORDER BY t.tgrelid,t.tgname COLLATE "C") FROM actual_triggers t),
 'functions',(SELECT jsonb_agg(jsonb_build_object(
  'oid',p.oid::bigint,'name',p.proname,'namespace',p.nspname,'owner',p.proowner::bigint,'language',p.lanname,
  'arguments',pg_catalog.oidvectortypes(p.proargtypes),'arity',p.pronargs::int,'arg_defaults',p.pronargdefaults::int,
  'returns_trigger',p.prorettype='pg_catalog.trigger'::regtype,'returns_set',p.proretset,'volatility',p.provolatile::text,
  'security_definer',p.prosecdef,'strict',p.proisstrict,'leakproof',p.proleakproof,'parallel',p.proparallel::text,
  'kind',p.prokind::text,'support_zero',p.prosupport=0,'config',p.proconfig,
  'source_sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),
  'acl',(SELECT jsonb_agg(jsonb_build_array(x.grantee::bigint,x.grantor::bigint,x.privilege_type,x.is_grantable)
    ORDER BY x.grantee,x.privilege_type COLLATE "C") FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x),
  'definition',pg_catalog.pg_get_functiondef(p.oid)) ORDER BY p.proname COLLATE "C") FROM actual_functions p),
 'ri_triggers',(SELECT jsonb_agg(jsonb_build_object(
  'constraint',k.conname,'constraint_oid',k.oid::bigint,'child_oid',k.conrelid::bigint,'parent_oid',k.confrelid::bigint,
  'trigger_oid',t.oid::bigint,'name',t.tgname,'relation_oid',t.tgrelid::bigint,'other_oid',t.tgconstrrelid::bigint,
  'function',p.proname,'namespace',n.nspname,'function_oid',p.oid::bigint,
  'expected_function_oid',to_regprocedure('pg_catalog.'||quote_ident(p.proname)||'()')::oid::bigint,
  'type',t.tgtype,'enabled',t.tgenabled::text,'internal',t.tgisinternal,'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,
  'parent_trigger_oid',t.tgparentid::bigint,'args_count',t.tgnargs,'args',encode(t.tgargs,'hex'),
  'attributes',t.tgattr::text,'when',pg_get_expr(t.tgqual,t.tgrelid),'old_table',t.tgoldtable,'new_table',t.tgnewtable,
  'delete_action',k.confdeltype::text,'update_action',k.confupdtype::text,'match',k.confmatchtype::text,
  'validated',k.convalidated,'fk_deferrable',k.condeferrable,'fk_deferred',k.condeferred)
  ORDER BY k.conname COLLATE "C",p.proname COLLATE "C",t.oid)
  FROM pg_trigger t JOIN relevant_fks k ON k.oid=t.tgconstraint
  JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace WHERE t.tgisinternal)
)
"""

_COLUMNS = {
    "demo_account_controls": [
        [
            "environment",
            "character varying(8)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "account_id",
            "character varying(32)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["control_revision", "bigint", True, None, "", "", None],
        [
            "owner_sha256",
            "character varying(64)",
            False,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["owner_epoch", "bigint", True, None, "", "", None],
        ["lease_until", "timestamp with time zone", True, None, "", "", None],
        ["emergency_stop", "boolean", True, None, "", "", None],
        [
            "arm_request_id",
            "character varying(64)",
            False,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["arm_expires_at", "timestamp with time zone", False, None, "", "", None],
        ["state_json", "text", True, None, "", "", "pg_catalog.default"],
        [
            "state_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["created_at", "timestamp with time zone", True, None, "", "", None],
        ["updated_at", "timestamp with time zone", True, None, "", "", None],
    ],
    "demo_control_clear_acknowledgements_v2": [
        [
            "ack_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "environment",
            "character varying(8)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "account_id",
            "character varying(32)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "settlement_currency",
            "character varying(16)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "command_id",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["stopped_control_revision", "bigint", True, None, "", "", None],
        [
            "stopped_event_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "stopped_state_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["clear_control_revision", "bigint", True, None, "", "", None],
        [
            "clear_state_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "clear_event_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "source_lineage_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["account_revision", "bigint", True, None, "", "", None],
        ["ledger_revision", "bigint", True, None, "", "", None],
        [
            "claims_sha256",
            "character varying(64)",
            False,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "owner_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["owner_epoch", "bigint", True, None, "", "", None],
        ["original_lease_until", "timestamp with time zone", True, None, "", "", None],
        ["owner_sampled_at", "timestamp with time zone", True, None, "", "", None],
        ["original_expires_at", "timestamp with time zone", True, None, "", "", None],
        ["ack_json", "text", True, None, "", "", "pg_catalog.default"],
        ["state_json", "text", True, None, "", "", "pg_catalog.default"],
        ["event_json", "text", True, None, "", "", "pg_catalog.default"],
        [
            "db_recorded_at",
            "timestamp with time zone",
            True,
            "clock_timestamp()",
            "",
            "",
            None,
        ],
    ],
    "demo_control_journal": [
        [
            "environment",
            "character varying(8)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "account_id",
            "character varying(32)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["control_revision", "bigint", True, None, "", "", None],
        [
            "command_id",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["action", "character varying(24)", True, None, "", "", "pg_catalog.default"],
        [
            "previous_sha256",
            "character varying(64)",
            False,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "event_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "state_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["event_json", "text", True, None, "", "", "pg_catalog.default"],
        ["occurred_at", "timestamp with time zone", True, None, "", "", None],
    ],
    "demo_policy_counter_initializations_v1": [
        [
            "initialization_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "environment",
            "character varying(8)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "account_id",
            "character varying(32)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "settlement_currency",
            "character varying(16)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "command_id",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["control_revision", "bigint", True, None, "", "", None],
        [
            "control_event_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "control_state_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "source_lineage_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["account_revision", "bigint", True, None, "", "", None],
        ["ledger_revision", "bigint", True, None, "", "", None],
        [
            "claims_sha256",
            "character varying(64)",
            False,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        [
            "configured_policy_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["configured_policy_json", "text", True, None, "", "", "pg_catalog.default"],
        [
            "measured_genesis_sha256",
            "character varying(64)",
            True,
            None,
            "",
            "",
            "pg_catalog.default",
        ],
        ["measured_genesis_json", "text", True, None, "", "", "pg_catalog.default"],
        ["owner_sampled_at", "timestamp with time zone", True, None, "", "", None],
        ["original_expires_at", "timestamp with time zone", True, None, "", "", None],
        ["ack_json", "text", True, None, "", "", "pg_catalog.default"],
        [
            "initialized_at",
            "timestamp with time zone",
            True,
            "clock_timestamp()",
            "",
            "",
            None,
        ],
        [
            "db_recorded_at",
            "timestamp with time zone",
            True,
            "clock_timestamp()",
            "",
            "",
            None,
        ],
    ],
}


_SUBMISSION_CATALOG_SQL = """
SELECT coalesce(jsonb_agg(jsonb_build_object(
 'name',p.proname,'oid',p.oid::bigint,'namespace',n.nspname,
 'owner',p.proowner::bigint,'source',p.prosrc,'definition',pg_get_functiondef(p.oid),
 'arguments',oidvectortypes(p.proargtypes),'returns',pg_get_function_result(p.oid),
 'language',l.lanname,'security_definer',p.prosecdef,'kind',p.prokind,
 'volatility',p.provolatile,'parallel',p.proparallel,'strict',p.proisstrict,
 'leakproof',p.proleakproof,'config',p.proconfig,
 'acl_valid',CASE WHEN p.proacl IS NULL OR cardinality(p.proacl)=0 THEN true
  WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN false
  ELSE array_position(p.proacl,NULL) IS NULL END,
 'acl',coalesce((SELECT jsonb_agg(jsonb_build_array(x.grantee::bigint,x.grantor::bigint,x.privilege_type,x.is_grantable)
   ORDER BY x.grantee,x.privilege_type) FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x),'[]'::jsonb)
 ) ORDER BY p.oid),'[]'::jsonb)
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
JOIN pg_language l ON l.oid=p.prolang
WHERE n.nspname='public' AND p.proname='qualification_submission_binding'
"""
_SUBMISSION_KEYS = {
    "name",
    "oid",
    "namespace",
    "owner",
    "source",
    "definition",
    "arguments",
    "returns",
    "language",
    "security_definer",
    "kind",
    "volatility",
    "parallel",
    "strict",
    "leakproof",
    "config",
    "acl_valid",
    "acl",
}


def _submission55(rows, *, owner):
    if type(rows) is not list or len(rows) != 1:
        _deny()
    row = rows[0]
    if (
        type(row) is not dict
        or set(row) != _SUBMISSION_KEYS
        or row["name"] != native_submission.FUNCTION
        or row["namespace"] != "public"
        or row["owner"] != owner
        or row["arguments"] != ""
        or row["returns"] != "trigger"
        or row["language"] != "plpgsql"
        or row["security_definer"] is not False
        or row["kind"] != "f"
        or row["volatility"] != "v"
        or row["parallel"] != "u"
        or row["strict"] is not False
        or row["leakproof"] is not False
        or row["config"] is not None
        or row["acl_valid"] is not True
        or type(row["source"]) is not str
        or row["source"] != native_submission.CURRENT_SQL.split("$$")[1]
        or hashlib.sha256(row["source"].encode("utf-8")).hexdigest()
        != native_submission.CURRENT_BODY_SHA256
        or type(row["definition"]) is not str
        or not row["definition"]
    ):
        _deny()
    _oid(row["oid"])
    _acl(row["acl"], owner=owner, actor=owner, owner_mode=True, rights=(), public=True)
    return row["definition"]


_CLOSURE_FUNCTION_CATALOG_SQL = _SUBMISSION_CATALOG_SQL.replace(
    "p.proname='qualification_submission_binding'",
    "p.proname='qualification_reservation_update'",
)
_CLOSURE_CATALOG_SQL = (
    """
SELECT jsonb_build_object(
 'functions',("""
    + _CLOSURE_FUNCTION_CATALOG_SQL
    + """),
 'tables',coalesce((SELECT jsonb_agg(jsonb_build_object(
   'oid',c.oid::bigint,'namespace',n.nspname,'name',c.relname,
   'owner',c.relowner::bigint,'kind',c.relkind,'persistence',c.relpersistence,
   'partition',c.relispartition,'has_subclass',c.relhassubclass,
   'rules',c.relhasrules,'row_security',c.relrowsecurity,'force_row_security',c.relforcerowsecurity
  ) ORDER BY c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname='qualification_reservations'),'[]'::jsonb),
 'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object(
   'oid',t.oid::bigint,'table_oid',c.oid::bigint,'name',t.tgname,
   'enabled',t.tgenabled,'kind',t.tgtype::integer,'internal',t.tgisinternal,
   'arguments',t.tgnargs,'argument_bytes',encode(t.tgargs,'hex'),'columns',t.tgattr::text,
   'qual_is_null',t.tgqual IS NULL,'constraint_oid',t.tgconstraint::bigint,
   'old_table',t.tgoldtable,'new_table',t.tgnewtable,'definition',pg_get_triggerdef(t.oid),
   'function_oid',p.oid::bigint,'function_namespace',pn.nspname,'function_name',p.proname,
   'function_owner',p.proowner::bigint,'function_arguments',p.pronargs,
   'function_kind',p.prokind
  ) ORDER BY t.tgname,t.oid)
  FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
  JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
  JOIN pg_namespace pn ON pn.oid=p.pronamespace
  WHERE n.nspname='public' AND c.relname='qualification_reservations'
   AND NOT t.tgisinternal),'[]'::jsonb)
)
"""
)
_CLOSURE_TABLE_KEYS = {
    "oid",
    "namespace",
    "name",
    "owner",
    "kind",
    "persistence",
    "partition",
    "has_subclass",
    "rules",
    "row_security",
    "force_row_security",
}
_CLOSURE_TRIGGER_KEYS = {
    "oid",
    "table_oid",
    "name",
    "enabled",
    "kind",
    "internal",
    "arguments",
    "argument_bytes",
    "columns",
    "qual_is_null",
    "constraint_oid",
    "old_table",
    "new_table",
    "definition",
    "function_oid",
    "function_namespace",
    "function_name",
    "function_owner",
    "function_arguments",
    "function_kind",
}
_CLOSURE_TRIGGERS = {
    "qualification_reservation_no_delete": (11, "qualification_ledger_immutable"),
    "qualification_reservation_no_truncate": (34, "qualification_ledger_immutable"),
    "qualification_reservation_update_guard": (19, native_closure.FUNCTION),
}


_CLOSURE_TRIGGERS[configuration_enforcement.TRIGGER] = (
    23,
    configuration_enforcement.FUNCTION,
)


def _closure55(value, *, owner, configuration):
    """Exact physical metadata only; source/closure authority is never a DATA field."""
    if type(value) is not dict or set(value) != {"functions", "tables", "triggers"}:
        _deny()
    functions, tables, triggers = value["functions"], value["tables"], value["triggers"]
    if type(functions) is not list or len(functions) != 1:
        _deny()
    row = functions[0]
    if (
        type(row) is not dict
        or set(row) != _SUBMISSION_KEYS
        or row["name"] != native_closure.FUNCTION
        or row["namespace"] != "public"
        or row["owner"] != owner
        or row["arguments"] != ""
        or row["returns"] != "trigger"
        or row["language"] != "plpgsql"
        or row["security_definer"] is not False
        or row["kind"] != "f"
        or row["volatility"] != "v"
        or row["parallel"] != "u"
        or row["strict"] is not False
        or row["leakproof"] is not False
        or row["config"] != ["search_path=pg_catalog, pg_temp"]
        or row["acl_valid"] is not True
        or type(row["source"]) is not str
        or row["source"] != native_closure.CURRENT_SQL.split("$$")[1]
        or hashlib.sha256(row["source"].encode("utf-8")).hexdigest()
        != native_closure.CURRENT_BODY_SHA256
        or type(row["definition"]) is not str
        or not row["definition"]
    ):
        _deny()
    function_oid = _oid(row["oid"])
    _acl(row["acl"], owner=owner, actor=owner, owner_mode=True, rights=(), public=True)
    if type(tables) is not list or len(tables) != 1:
        _deny()
    table = tables[0]
    if (
        type(table) is not dict
        or set(table) != _CLOSURE_TABLE_KEYS
        or table["namespace"] != "public"
        or table["name"] != "qualification_reservations"
        or table["owner"] != owner
        or table["kind"] != "r"
        or table["persistence"] != "p"
        or any(
            table[key] is not False
            for key in (
                "partition",
                "has_subclass",
                "rules",
                "row_security",
                "force_row_security",
            )
        )
    ):
        _deny()
    table_oid = _oid(table["oid"])
    if type(triggers) is not list or len(triggers) != len(_CLOSURE_TRIGGERS):
        _deny()
    seen = set()
    definitions = []
    for trigger in triggers:
        if type(trigger) is not dict or set(trigger) != _CLOSURE_TRIGGER_KEYS:
            _deny()
        name = trigger["name"]
        if type(name) is not str or name in seen or name not in _CLOSURE_TRIGGERS:
            _deny()
        kind, function = _CLOSURE_TRIGGERS[name]
        if (
            trigger["table_oid"] != table_oid
            or trigger["enabled"] != "O"
            or type(trigger["kind"]) is not int
            or trigger["kind"] != kind
            or trigger["internal"] is not False
            or type(trigger["arguments"]) is not int
            or trigger["arguments"] != 0
            or trigger["argument_bytes"] != ""
            or trigger["columns"] != ""
            or trigger["qual_is_null"] is not True
            or type(trigger["constraint_oid"]) is not int
            or trigger["constraint_oid"] != 0
            or trigger["old_table"] is not None
            or trigger["new_table"] is not None
            or trigger["function_namespace"] != "public"
            or trigger["function_name"] != function
            or trigger["function_owner"] != owner
            or type(trigger["function_arguments"]) is not int
            or trigger["function_arguments"] != 0
            or trigger["function_kind"] != "f"
            or type(trigger["definition"]) is not str
            or not trigger["definition"]
        ):
            _deny()
        _oid(trigger["oid"])
        _oid(trigger["function_oid"])
        if (
            name == "qualification_reservation_update_guard"
            and trigger["function_oid"] != function_oid
        ):
            _deny()
        if (
            name == configuration_enforcement.TRIGGER
            and trigger["function_oid"]
            != configuration["function_oids"][configuration_enforcement.FUNCTION]
        ):
            _deny()
        seen.add(name)
        definitions.append([name, trigger["definition"]])
    if seen != set(_CLOSURE_TRIGGERS):
        _deny()
    return {"function": row["definition"], "triggers": sorted(definitions)}


_CLOSE_INTENT_CATALOG_SQL = native_close_index.CATALOG_SQL


def _close_intent55(value, *, owner, actor, owner_mode, configuration):
    return native_close_index.observe_catalog(
        value,
        owner=owner,
        actor=actor,
        owner_mode=owner_mode,
        configuration_guard_oid=configuration["function_oids"][
            configuration_enforcement.FUNCTION
        ],
    )


def _wrap(issued_sql, lineage_sql):
    # Reuse only the original fixed query builder, then add an explicit actual55
    # root. Its inner table-schema tags name unchanged table contracts; their
    # actual database_head remains freshly queried55, never rewritten to54.
    base = old54._wrap(issued_sql, lineage_sql)
    base = old54._once(
        base, "'schema','" + old54.SCHEMA + "'", "'schema','" + SCHEMA + "'"
    )
    return old54._once(
        base,
        "'scope_parent',",
        "'operator55',(" + _OPERATOR_CATALOG_SQL + "),\n"
        " 'controlled_history55',(" + controlled_history.CATALOG_SQL + "),\n"
        " 'native_submission55',(" + _SUBMISSION_CATALOG_SQL + "),\n"
        " 'native_closure55',(" + _CLOSURE_CATALOG_SQL + "),\n"
        " 'native_terminal55',(" + native_terminal.CATALOG_SQL + "),\n"
        " 'native_report55',(" + native_report.CATALOG_SQL + "),\n"
        " 'native_close_intent55',("
        + _CLOSE_INTENT_CATALOG_SQL
        + "),\n"
        + " 'native_configuration55',("
        + native_configuration.CATALOG_SQL
        + "),\n 'scope_parent',",
    )


OWNER_CATALOG_SQL = _wrap(old54._ISSUED_OWNER, old54._LINEAGE_OWNER)
ACTOR_CATALOG_SQL = _wrap(old54._ISSUED_ACTOR, old54._LINEAGE_ACTOR)


def _acl(rows, *, owner, actor, owner_mode, rights, public=False):
    if type(rows) is not list:
        _deny()
    expected = {
        (owner, owner, "EXECUTE" if public is not None else right, False)
        for right in ({"EXECUTE"} if public is not None else _PRIVILEGES)
    }
    if public:
        expected.add((0, owner, "EXECUTE", False))
    if public is None and not owner_mode:
        expected |= {(actor, owner, right, False) for right in rights}
    found = set()
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 4
            or type(row[0]) is not int
            or type(row[1]) is not int
            or type(row[2]) is not str
            or type(row[3]) is not bool
            or tuple(row) in found
        ):
            _deny()
        found.add(tuple(row))
    if found != expected:
        _deny()


_RESERVATION_READ_COLUMNS = [
    ["reservation_id", "character varying(64)", True],
    ["environment", "character varying(8)", True],
    ["account_id", "character varying(32)", True],
    ["settlement_currency", "character varying(16)", True],
    ["original_event_key", "character varying(64)", True],
    ["report_id", "character varying(96)", True],
    ["instrument_id", "character varying(64)", True],
    ["direction", "character varying(5)", True],
    ["correlation_group", "character varying(128)", True],
    ["request_json", "text", True],
    ["request_sha256", "character varying(64)", True],
    ["coverage_json", "text", True],
    ["risk_amount", "numeric(60,20)", True],
    ["margin_amount", "numeric(60,20)", True],
    ["notional_amount", "numeric(60,20)", True],
    ["state", "character varying(24)", True],
    ["state_revision", "bigint", True],
    ["deadline", "timestamp with time zone", True],
    ["created_at", "timestamp with time zone", True],
    ["updated_at", "timestamp with time zone", True],
]


def _relations(rows, *, owner):
    if type(rows) is not list or len(rows) != len(_RELATIONS):
        _deny()
    result, seen = {}, set()
    fields = {
        "name",
        "oid",
        "owner",
        "namespace",
        "kind",
        "persistence",
        "rls",
        "force_rls",
        "partition",
        "inherited",
        "rules",
        "policies",
        "read_columns",
    }
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != fields
            or row["name"] not in _RELATIONS
            or row["name"] in result
            or _oid(row["oid"]) in seen
            or type(row["owner"]) is not int
            or row["owner"] != owner
            or row["namespace"] != "public"
            or row["kind"] != "r"
            or row["persistence"] != "p"
            or any(
                row[key] is not False
                for key in (
                    "rls",
                    "force_rls",
                    "partition",
                    "inherited",
                    "rules",
                    "policies",
                )
            )
        ):
            _deny()
        shape = row["read_columns"]
        if shape is not None and (
            type(shape) is not list
            or any(
                type(column) is not list
                or len(column) != 3
                or type(column[0]) is not str
                or type(column[1]) is not str
                or type(column[2]) is not bool
                for column in shape
            )
        ):
            _deny()
        if shape != (
            _RESERVATION_READ_COLUMNS
            if row["name"] == "qualification_reservations"
            else None
        ):
            _deny()
        result[row["name"]] = row["oid"]
        seen.add(row["oid"])
    return result


def _tables(rows, *, identities, owner, actor, owner_mode):
    if type(rows) is not list or len(rows) != len(_MONITORED):
        _deny()
    result, constraints = {}, {}
    for table in rows:
        if (
            type(table) is not dict
            or set(table)
            != {"name", "oid", "columns", "column_acl", "constraints", "indexes", "acl"}
            or type(table["name"]) is not str
            or table["name"] not in _MONITORED
            or table["name"] in result
            or _oid(table["oid"]) != identities[table["name"]]
            or table["column_acl"] is not False
            or table["columns"] != _COLUMNS[table["name"]]
        ):
            _deny()
        if type(table["columns"]) is not list or any(
            type(column) is not list
            or len(column) != 7
            or type(column[0]) is not str
            or type(column[1]) is not str
            or type(column[2]) is not bool
            or (column[3] is not None and type(column[3]) is not str)
            or type(column[4]) is not str
            or type(column[5]) is not str
            or (column[6] is not None and type(column[6]) is not str)
            for column in table["columns"]
        ):
            _deny()
        name = table["name"]
        _acl(
            table["acl"],
            owner=owner,
            actor=actor,
            owner_mode=owner_mode,
            rights=_ACTOR_PRIVILEGES[name],
            public=None,
        )
        keys, checks = _KEYS[name], _CHECKS[name]
        trigger_constraints = {n for n, spec in _TRIGGERS[name].items() if spec[2]}
        expected = set(keys) | checks | trigger_constraints
        records = table["constraints"]
        if type(records) is not list or len(records) != len(expected):
            _deny()
        found = {}
        for row in records:
            if (
                type(row) is not dict
                or set(row)
                != {
                    "oid",
                    "name",
                    "kind",
                    "validated",
                    "deferrable",
                    "deferred",
                    "columns",
                    "parent",
                    "parent_columns",
                    "delete_action",
                    "update_action",
                    "match",
                    "definition",
                }
                or row["name"] not in expected
                or row["name"] in found
                or _oid(row["oid"]) in constraints
                or row["validated"] is not True
                or type(row["deferrable"]) is not bool
                or type(row["deferred"]) is not bool
            ):
                _deny()
            if row["name"] in keys:
                kind, columns, parent, parent_columns, deferred = keys[row["name"]]
                actual_parent = row["parent"]
                if type(actual_parent) is str and actual_parent.startswith("public."):
                    actual_parent = actual_parent[7:]
                if (
                    (row["kind"], row["columns"], actual_parent, row["parent_columns"])
                    != (kind, columns, parent, parent_columns)
                    or row["deferrable"] is not deferred
                    or row["deferred"] is not deferred
                ):
                    _deny()
                if kind == "f":
                    expected_delete = (
                        "a" if row["name"] == "fk_control_clear_result" else "r"
                    )
                    if (row["delete_action"], row["update_action"], row["match"]) != (
                        expected_delete,
                        "a",
                        "s",
                    ):
                        _deny()
            elif row["name"] in checks:
                if (
                    row["kind"] != "c"
                    or row["deferrable"] is not False
                    or row["deferred"] is not False
                ):
                    _deny()
            elif (
                row["kind"] != "t"
                or row["deferrable"] is not True
                or row["deferred"] is not True
                or row["columns"] is not None
                or row["parent"] is not None
                or row["parent_columns"] is not None
            ):
                _deny()
            if row["kind"] == "t":
                if row["definition"] is not None:
                    _deny()
            elif type(row["definition"]) is not str or not row["definition"]:
                _deny()
            found[row["name"]], constraints[row["oid"]] = row, row
        indexes = table["indexes"]
        expected_indexes = {n for n, spec in keys.items() if spec[0] in {"p", "u"}}
        if type(indexes) is not list or len(indexes) != len(expected_indexes):
            _deny()
        seen = set()
        for row in indexes:
            if (
                type(row) is not dict
                or set(row)
                != {
                    "name",
                    "unique",
                    "primary",
                    "valid",
                    "ready",
                    "live",
                    "exclusion",
                    "definition",
                }
                or row["name"] not in expected_indexes
                or row["name"] in seen
                or any(
                    row[key] is not True for key in ("unique", "valid", "ready", "live")
                )
                or row["primary"] is not (keys[row["name"]][0] == "p")
                or row["exclusion"] is not False
                or type(row["definition"]) is not str
                or not row["definition"]
            ):
                _deny()
            seen.add(row["name"])
        result[name] = table
    return result, constraints


def _functions_and_triggers(
    value, *, identities, tables, constraints, owner, actor, owner_mode
):
    functions = value["functions"]
    expected = _source_functions()
    if type(functions) is not list or len(functions) != len(expected):
        _deny()
    by_oid, by_name = {}, {}
    fields = {
        "oid",
        "name",
        "namespace",
        "owner",
        "language",
        "arguments",
        "arity",
        "arg_defaults",
        "returns_trigger",
        "returns_set",
        "volatility",
        "security_definer",
        "strict",
        "leakproof",
        "parallel",
        "kind",
        "support_zero",
        "config",
        "source_sha256",
        "acl",
        "definition",
    }
    for row in functions:
        if (
            type(row) is not dict
            or set(row) != fields
            or type(row["name"]) is not str
            or row["name"] not in expected
            or row["name"] in by_name
            or _oid(row["oid"]) in by_oid
            or type(row["owner"]) is not int
            or row["owner"] != owner
            or row["namespace"] != "public"
            or row["language"] != "plpgsql"
            or row["arguments"] != ""
            or any(
                type(row[key]) is not int or row[key] != 0
                for key in ("arity", "arg_defaults")
            )
            or row["returns_trigger"] is not True
            or row["returns_set"] is not False
            or row["volatility"] != "v"
            or row["parallel"] != "u"
            or row["kind"] != "f"
            or row["strict"] is not False
            or row["leakproof"] is not False
            or row["support_zero"] is not True
            or type(row["definition"]) is not str
            or not row["definition"]
        ):
            _deny()
        source, security, config, public = expected[row["name"]]
        if (
            row["source_sha256"] != source
            or row["security_definer"] is not security
            or row["config"] != config
        ):
            _deny()
        _acl(
            row["acl"],
            owner=owner,
            actor=actor,
            owner_mode=owner_mode,
            rights=set(),
            public=public,
        )
        by_oid[row["oid"]], by_name[row["name"]] = row, row
    rows = value["triggers"]
    expected_count = sum(len(specs) for specs in _TRIGGERS.values())
    if type(rows) is not list or len(rows) != expected_count:
        _deny()
    found, seen, used_constraints = set(), set(), set()
    relation_names = {identities[name]: name for name in _MONITORED}
    fields = {
        "oid",
        "name",
        "relation_oid",
        "function_oid",
        "enabled",
        "type",
        "internal",
        "constraint_oid",
        "parent_oid",
        "deferrable",
        "deferred",
        "args_count",
        "args",
        "attributes",
        "when",
        "old_table",
        "new_table",
        "definition",
    }
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != fields
            or _oid(row["oid"]) in seen
            or type(row["relation_oid"]) is not int
            or row["relation_oid"] not in relation_names
            or type(row["function_oid"]) is not int
            or row["function_oid"] not in by_oid
            or row["enabled"] != "O"
            or row["internal"] is not False
            or any(
                type(row[key]) is not int or row[key] != 0
                for key in ("parent_oid", "args_count")
            )
            or row["args"] != ""
            or row["attributes"] != ""
            or any(row[key] is not None for key in ("when", "old_table", "new_table"))
            or type(row["definition"]) is not str
            or not row["definition"]
        ):
            _deny()
        table = relation_names[row["relation_oid"]]
        if (
            type(row["name"]) is not str
            or row["name"] not in _TRIGGERS[table]
            or (table, row["name"]) in found
        ):
            _deny()
        kind, function, deferred = _TRIGGERS[table][row["name"]]
        if (
            type(row["type"]) is not int
            or row["type"] != kind
            or by_oid[row["function_oid"]]["name"] != function
            or row["deferrable"] is not deferred
            or row["deferred"] is not deferred
        ):
            _deny()
        if deferred:
            constraint = constraints.get(_oid(row["constraint_oid"]))
            if (
                constraint is None
                or constraint["name"] != row["name"]
                or constraint["kind"] != "t"
                or row["constraint_oid"] in used_constraints
            ):
                _deny()
            used_constraints.add(row["constraint_oid"])
        elif type(row["constraint_oid"]) is not int or row["constraint_oid"] != 0:
            _deny()
        found.add((table, row["name"]))
        seen.add(row["oid"])
    if used_constraints != {
        oid for oid, row in constraints.items() if row["kind"] == "t"
    }:
        _deny()
    return {
        "tables": {
            name: {
                "columns": table["columns"],
                # Constraint-trigger details are authenticated by their actual
                # pg_get_triggerdef rows, not a made-up constraint definition.
                "constraints": [
                    [row["name"], row["definition"]] for row in table["constraints"]
                ],
                "indexes": [
                    [row["name"], row["definition"]] for row in table["indexes"]
                ],
            }
            for name, table in sorted(tables.items())
        },
        "functions": [[row["name"], row["definition"]] for row in functions],
        "triggers": [
            [relation_names[row["relation_oid"]], row["name"], row["definition"]]
            for row in sorted(
                rows, key=lambda row: (relation_names[row["relation_oid"]], row["name"])
            )
        ],
    }


def _fk_specs():
    result = {}
    for child, keys in (
        (lineage.TABLE, lineage.KEYS),
        (issued.TABLE, old54._ISSUED_KEYS),
    ):
        for name, spec in keys.items():
            if spec[0] == "f":
                result[name] = (child, spec[2], "r", False)
    for child in _NEW_TABLES:
        for name, spec in _KEYS[child].items():
            if spec[0] == "f":
                if name in result:
                    _deny()
                result[name] = (
                    child,
                    spec[2],
                    "a" if name == "fk_control_clear_result" else "r",
                    spec[4],
                )
    if len(result) != 15:
        _deny()
    return result


def _ri60(rows, *, identities, constraints):
    # This validates the complete actual outgoing/incoming FK closure of all
    # four source/ACK tables, including the deferred NO ACTION clear result FK.
    specs = _fk_specs()
    if type(rows) is not list or len(rows) != 60:
        _deny()
    found, seen, constraint_ids = {}, set(), {}
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != old54._RI_FIELDS
            or row["constraint"] not in specs
        ):
            _deny()
        child, parent, delete, deferred = specs[row["constraint"]]
        routines = {
            "RI_FKey_check_ins": (5, "c", True, deferred),
            "RI_FKey_check_upd": (17, "c", True, deferred),
            "RI_FKey_noaction_upd": (17, "a", False, deferred),
            "RI_FKey_noaction_del" if delete == "a" else "RI_FKey_restrict_del": (
                9,
                "a",
                False,
                deferred if delete == "a" else False,
            ),
        }
        if (
            row["function"] not in routines
            or (row["constraint"], row["function"]) in seen
        ):
            _deny()
        for key in (
            "constraint_oid",
            "child_oid",
            "parent_oid",
            "trigger_oid",
            "relation_oid",
            "other_oid",
            "function_oid",
            "expected_function_oid",
        ):
            _oid(row[key])
        identity = row["constraint_oid"], row["child_oid"], row["parent_oid"]
        if constraint_ids.setdefault(row["constraint"], identity) != identity:
            _deny()
        kind, prefix, on_child, trigger_deferred = routines[row["function"]]
        if (
            row["child_oid"] != identities[child]
            or row["parent_oid"] != identities[parent]
            or row["name"]
            != "RI_ConstraintTrigger_" + prefix + "_" + str(row["trigger_oid"])
            or type(row["type"]) is not int
            or row["type"] != kind
            or row["relation_oid"] != identities[child if on_child else parent]
            or row["other_oid"] != identities[parent if on_child else child]
            or row["namespace"] != "pg_catalog"
            or row["function_oid"] != row["expected_function_oid"]
            or row["enabled"] != "O"
            or row["internal"] is not True
            or row["deferrable"] is not trigger_deferred
            or row["deferred"] is not trigger_deferred
            or type(row["parent_trigger_oid"]) is not int
            or row["parent_trigger_oid"] != 0
            or type(row["args_count"]) is not int
            or row["args_count"] != 0
            or row["args"] != ""
            or row["attributes"] != ""
            or any(row[key] is not None for key in ("when", "old_table", "new_table"))
            or (row["delete_action"], row["update_action"], row["match"])
            != (delete, "a", "s")
            or row["validated"] is not True
            or row["fk_deferrable"] is not deferred
            or row["fk_deferred"] is not deferred
            or row["trigger_oid"] in found
        ):
            _deny()
        if child in _NEW_TABLES:
            actual = constraints.get(row["constraint_oid"])
            if actual is None or actual["name"] != row["constraint"]:
                _deny()
        found[row["trigger_oid"]] = row
        seen.add((row["constraint"], row["function"]))
    if (
        len(constraint_ids) != 15
        or len({item[0] for item in constraint_ids.values()}) != 15
    ):
        _deny()
    return found


def _ri84(rows, *, identities, constraints, configuration):
    # This validates the complete actual outgoing/incoming FK closure of all
    # seven source/ACK/configuration tables, including the deferred NO ACTION clear result FK.
    specs = _fk_specs()
    if set(specs) & set(configuration["fk_specs"]) or set(constraints) & set(
        configuration["constraints_by_oid"]
    ):
        _deny()
    specs = {**specs, **configuration["fk_specs"]}
    constraints = {**constraints, **configuration["constraints_by_oid"]}
    if len(specs) != 21:
        _deny()
    if type(rows) is not list or len(rows) != 84:
        _deny()
    found, seen, constraint_ids = {}, set(), {}
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != old54._RI_FIELDS
            or row["constraint"] not in specs
        ):
            _deny()
        child, parent, delete, deferred = specs[row["constraint"]]
        routines = {
            "RI_FKey_check_ins": (5, "c", True, deferred),
            "RI_FKey_check_upd": (17, "c", True, deferred),
            "RI_FKey_noaction_upd": (17, "a", False, deferred),
            "RI_FKey_noaction_del" if delete == "a" else "RI_FKey_restrict_del": (
                9,
                "a",
                False,
                deferred if delete == "a" else False,
            ),
        }
        if (
            row["function"] not in routines
            or (row["constraint"], row["function"]) in seen
        ):
            _deny()
        for key in (
            "constraint_oid",
            "child_oid",
            "parent_oid",
            "trigger_oid",
            "relation_oid",
            "other_oid",
            "function_oid",
            "expected_function_oid",
        ):
            _oid(row[key])
        identity = row["constraint_oid"], row["child_oid"], row["parent_oid"]
        if constraint_ids.setdefault(row["constraint"], identity) != identity:
            _deny()
        kind, prefix, on_child, trigger_deferred = routines[row["function"]]
        if (
            row["child_oid"] != identities[child]
            or row["parent_oid"] != identities[parent]
            or row["name"]
            != "RI_ConstraintTrigger_" + prefix + "_" + str(row["trigger_oid"])
            or type(row["type"]) is not int
            or row["type"] != kind
            or row["relation_oid"] != identities[child if on_child else parent]
            or row["other_oid"] != identities[parent if on_child else child]
            or row["namespace"] != "pg_catalog"
            or row["function_oid"] != row["expected_function_oid"]
            or row["enabled"] != "O"
            or row["internal"] is not True
            or row["deferrable"] is not trigger_deferred
            or row["deferred"] is not trigger_deferred
            or type(row["parent_trigger_oid"]) is not int
            or row["parent_trigger_oid"] != 0
            or type(row["args_count"]) is not int
            or row["args_count"] != 0
            or row["args"] != ""
            or row["attributes"] != ""
            or any(row[key] is not None for key in ("when", "old_table", "new_table"))
            or (row["delete_action"], row["update_action"], row["match"])
            != (delete, "a", "s")
            or row["validated"] is not True
            or row["fk_deferrable"] is not deferred
            or row["fk_deferred"] is not deferred
            or row["trigger_oid"] in found
        ):
            _deny()
        if child in _NEW_TABLES or child in native_configuration.schema.TABLES:
            actual = constraints.get(row["constraint_oid"])
            if actual is None or actual["name"] != row["constraint"]:
                _deny()
        found[row["trigger_oid"]] = row
        seen.add((row["constraint"], row["function"]))
    if (
        len(constraint_ids) != 21
        or len({item[0] for item in constraint_ids.values()}) != 21
    ):
        _deny()
    return found


def _operator55(value, *, owner, role, owner_mode, configuration):
    if (
        type(value) is not dict
        or set(value)
        != {"head", "relations", "tables", "triggers", "functions", "ri_triggers"}
        or value["head"] != ["0055"]
    ):
        _deny()
    identities = _relations(value["relations"], owner=owner)
    if any(
        identities.get(name) != oid
        for name, oid in {
            **configuration["table_oids"],
            **configuration["shared_table_oids"],
        }.items()
    ):
        _deny()
    tables, constraints = _tables(
        value["tables"],
        identities=identities,
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    definitions = _functions_and_triggers(
        value,
        identities=identities,
        tables=tables,
        constraints=constraints,
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    ri = _ri84(
        value["ri_triggers"],
        identities=identities,
        constraints=constraints,
        configuration=configuration,
    )
    actual_shape = next(
        row["read_columns"]
        for row in value["relations"]
        if row["name"] == "qualification_reservations"
    )
    return identities, ri, {**definitions, "reservation_read_columns": actual_shape}


def _source_bodies55():
    if (
        operator.FROZEN_DDL_SHA256 != FROZEN_OPERATOR_DDL_SHA256
        or len(operator.statements()) != 27
    ):
        _deny()
    # Original54 immutable-body contract is unchanged; no original54 baseline
    # or actual-head approval is consumed by this versioned55 path.
    _, immutable = old54._source_bodies()
    return _body(operator.ISSUER_GUARD_SQL, "$ctcc$"), immutable


def _observe(value, *, owner_mode, read_only):
    if (
        type(value) is not dict
        or set(value) != CATALOG_KEYS
        or value["schema"] != SCHEMA
    ):
        _deny()
    role = value["role"]
    if (
        type(role) is not dict
        or set(role)
        != {
            "oid",
            "name",
            "current_user",
            "session_user",
            "setting",
            "login",
            "superuser",
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "membership",
            "create",
        }
        or type(role["oid"]) is not int
        or not 0 < role["oid"] < 2**32
        or type(role["name"]) is not str
        or not role["name"]
        or role["current_user"] != role["name"]
        or role["session_user"] != role["name"]
        or role["setting"] != "none"
        or role["login"] is not True
        or role["superuser"] is not owner_mode
    ):
        _deny()
    if not owner_mode and any(
        role[key] is not False
        for key in (
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "membership",
            "create",
        )
    ):
        _deny()
    if type(value["issued"]) is not dict:
        _deny()
    owner = _oid(value["issued"]["owner"])
    configuration = native_configuration.observe_catalog(
        value["native_configuration55"],
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    identities, combined, operator_defs = _operator55(
        value["operator55"],
        owner=owner,
        role=role,
        owner_mode=owner_mode,
        configuration=configuration,
    )
    controlled_defs = controlled_history.observe_catalog(
        value["controlled_history55"],
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    submission_def = _submission55(value["native_submission55"], owner=owner)
    closure_defs = _closure55(
        value["native_closure55"], owner=owner, configuration=configuration
    )
    terminal_defs = native_terminal.observe_catalog(
        value["native_terminal55"], owner=owner
    )
    report_defs = native_report.observe_catalog(
        value["native_report55"], owner=owner, actor=role["oid"], owner_mode=owner_mode
    )
    close_intent_defs = _close_intent55(
        value["native_close_intent55"],
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
        configuration=configuration,
    )
    # Only now reuse unchanged54 body/table structural validators. Their old
    # native admission functions and head54 predicates are never called.
    guard_body, immutable_body = _source_bodies55()
    current, previous = value["issued"], value["lineage"]
    old54._structure(
        current,
        owner_mode=owner_mode,
        columns=old54._ISSUED_COLUMNS,
        keys=old54._ISSUED_KEYS,
        checks=old54._ISSUED_CHECKS,
        triggers=old54._ISSUED_TRIGGERS,
        functions=(
            (issued.GUARD, guard_body, True),
            (issued.IMMUTABLE, immutable_body, False),
        ),
    )
    old54._structure(
        previous,
        owner_mode=owner_mode,
        columns=lineage.COLUMNS,
        keys=lineage.KEYS,
        checks=lineage.CHECKS,
        triggers=lineage.TRIGGERS,
        functions=(
            (lineage.GUARD, lineage.GUARD_BODY, True),
            (lineage.IMMUTABLE, lineage.IMMUTABLE_BODY, False),
        ),
    )
    if (
        current["owner"] != previous["owner"]
        or current["table_oid"] == previous["table_oid"]
    ):
        _deny()
    if owner_mode:
        if role["oid"] != current["owner"]:
            _deny()
    else:
        # Unchanged restricted actor verifier, actual writer query only. The
        # complete independently measured lineage_base remains untouched.
        lineage.verify_catalog(value["lineage_base"])
        if any(value["lineage_base"][key] != previous[key] for key in BASE_KEYS):
            _deny()
    current_parents = _context(
        current,
        role=role,
        read_only=read_only,
        parents=PARENTS,
        schema_name="ctcc.collector_current0054.actual_issued_catalog.v1",
    )
    previous_parents = _context(
        previous,
        role=role,
        read_only=read_only,
        parents=old53.PARENTS,
        schema_name="ctcc.collector_current0053.actual_owner_catalog.v1",
    )
    if (
        current_parents[lineage.TABLE] != previous["table_oid"]
        or any(current_parents[key] != previous_parents[key] for key in old53.PARENTS)
        or current["server"]["database"] != previous["server"]["database"]
    ):
        _deny()
    if value["table_guards"] != [
        [lineage.TABLE, True, True, True, True],
        [issued.TABLE, True, True, True, True],
    ]:
        _deny()
    expected_defaults = []
    for table, columns in (
        (lineage.TABLE, lineage.COLUMNS),
        (issued.TABLE, old54._ISSUED_COLUMNS),
    ):
        for number, column in enumerate(columns, 1):
            expected_defaults.append(
                [
                    table,
                    number,
                    column[0],
                    "clock_timestamp()" if column[0] == "db_recorded_at" else None,
                    "",
                    "",
                ]
            )
    if value["defaults"] != expected_defaults:
        _deny()
    old54._acl(
        value["acl"], owner=current["owner"], actor=role["oid"], owner_mode=owner_mode
    )
    current_defs = old54._definitions(
        current,
        keys=old54._ISSUED_KEYS,
        checks=old54._ISSUED_CHECKS,
        functions=(issued.GUARD, issued.IMMUTABLE),
        triggers=old54._ISSUED_TRIGGERS,
    )
    previous_defs = old54._definitions(
        previous,
        keys=lineage.KEYS,
        checks=lineage.CHECKS,
        functions=(lineage.GUARD, lineage.IMMUTABLE),
        triggers=lineage.TRIGGERS,
    )
    scope_defs = old54._scope_parent(
        value["scope_parent"],
        owner=current["owner"],
        table_oid=current_parents["qualification_account_scopes"],
    )
    if any(current_parents[name] != identities[name] for name in PARENTS):
        _deny()
    if (
        current["table_oid"] != identities[issued.TABLE]
        or previous["table_oid"] != identities[lineage.TABLE]
    ):
        _deny()
    # Both independently queried component RI populations must equal the
    # corresponding rows of the already-validated global60 closure. No incoming
    # ACK edge is filtered away or trusted from a caller-provided list.
    for component in (current, previous):
        rows = component["ri_triggers"]
        if type(rows) is not list:
            _deny()
        actual = {}
        for row in rows:
            if (
                type(row) is not dict
                or set(row) != old54._RI_FIELDS
                or _oid(row["trigger_oid"]) in actual
            ):
                _deny()
            actual[row["trigger_oid"]] = row
        expected = {
            oid: row
            for oid, row in combined.items()
            if row["child_oid"] == component["table_oid"]
            or row["relation_oid"] == component["table_oid"]
        }
        if actual != expected:
            _deny()
    old54._incoming_custody(current)
    old54._incoming_custody(previous)
    if current["custody_incoming"] != previous["custody_incoming"]:
        _deny()
    custody_oid = current_parents["public_runtime_raw_bundles_v1"]
    expected_internal = sorted(
        [
            row["trigger_oid"],
            row["name"],
            row["function_oid"],
            row["constraint_oid"],
            row["relation_oid"],
            row["other_oid"],
        ]
        for row in combined.values()
        if row["relation_oid"] == custody_oid
    )
    if len(expected_internal) != 4 or value["custody_internal"] != expected_internal:
        _deny()
    if current_defs["custody_triggers"] != previous_defs["custody_triggers"]:
        _deny()
    native_sha256 = hashlib.sha256(
        canonical(
            {
                "issued": current_defs,
                "lineage": previous_defs,
                "scope_parent": scope_defs,
                "operator55": operator_defs,
                "controlled_history55": controlled_defs["definitions"],
                "native_submission55": submission_def,
                "native_closure55": closure_defs,
                "native_terminal55": terminal_defs,
                "native_report55": report_defs,
                "native_close_intent55": close_intent_defs,
                "native_configuration55": configuration["definitions"],
            }
        )
    ).hexdigest()
    return {
        "schema": "ctcc.collector_current0055.catalog_observation.v1",
        "database_head": "0055",
        "legacy_0054_issuer_ddl_sha256": issued.DDL_SHA256,
        "legacy_0053_lineage_ddl_sha256": lineage.DDL_SHA256,
        "operator_ddl_sha256": FROZEN_OPERATOR_DDL_SHA256,
        "controlled_history_ddl_sha256": controlled_history.DDL_SHA256,
        "native_submission_body_sha256": native_submission.CURRENT_BODY_SHA256,
        "native_closure_body_sha256": native_closure.CURRENT_BODY_SHA256,
        "native_zero_closure_body_sha256": native_closure.ZERO_BODY_SHA256,
        "native_filled_flat_body_sha256": terminal_defs[
            "qualification_native_filled_flat_data_v55"
        ]["body_sha256"],
        "native_report_eligibility_body_sha256": native_report.schema.body_sha(
            native_report.schema.ELIGIBLE_SQL, "$ctcc$"
        ),
        "native_close_intent_index_sha256": native_close_intent.INDEX_SPEC_SHA256,
        "native_configuration_ddl_sha256": native_configuration.DDL_SHA256,
        "native_configuration_positive_body_sha256": configuration_enforcement.BODY_SHA256,
        "native_definitions_sha256": native_sha256,
        "role_oid": role["oid"],
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }


def _observed(value, *, owner_mode, read_only):
    try:
        return _observe(value, owner_mode=owner_mode, read_only=read_only)
    except Current0055CatalogError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        _deny()


def observe_actual_owner_catalog(value):
    return _observed(value, owner_mode=True, read_only="on")


def observe_actual_writer_catalog(value, *, read_only):
    if type(read_only) is not str or read_only not in {"on", "off"}:
        _deny()
    return _observed(value, owner_mode=False, read_only=read_only)


def _required_baseline():
    if (
        PG17_NATIVE_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
        or type(PG17_NATIVE_BASELINE_SHA256) is not str
        or len(PG17_NATIVE_BASELINE_SHA256) != 64
        or any(c not in "0123456789abcdef" for c in PG17_NATIVE_BASELINE_SHA256)
    ):
        raise Current0055CatalogError(
            "current0055_genuine_native_baseline_pending_or_changed"
        ) from None
    return PG17_NATIVE_BASELINE_SHA256


def _admit(observed, schema_name):
    if observed["native_definitions_sha256"] != _required_baseline():
        _deny()
    return {**observed, "schema": schema_name, "native_baseline_admission": True}


def verify_actual_owner_catalog(value):
    return _admit(
        observe_actual_owner_catalog(value),
        "ctcc.collector_current0055.owner_installation.v1",
    )


def verify_actual_writer_catalog(value):
    return _admit(
        _observed(value, owner_mode=False, read_only="off"),
        "ctcc.collector_current0055.actual_writer_catalog.v1",
    )


def verify_actual_readonly_writer_catalog(value):
    return _admit(
        _observed(value, owner_mode=False, read_only="on"),
        "ctcc.collector_current0055.actual_readonly_writer_catalog.v1",
    )
