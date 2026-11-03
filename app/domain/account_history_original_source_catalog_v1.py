"""Versioned original B1/legacy-B3 source contract; no native seal inferred.

SQL checks the exact source functions, triggers and structural schema under
five SHARE ROW EXCLUSIVE relation locks. Those locks additionally exclude DML
through both read phases, even if an administrator replaces a trigger function.
Check/index deparser identities require a separate genuine native PG17 seal.
These declarations never change DB0050/0051 or their historical baselines.
"""

from app.domain import account_history_partition_catalog_v1 as parent
from app.domain import account_history_partition_v1 as records

SCHEMA = "ctcc.account_original_source_actual_catalog.v1"
PG17_NATIVE_BASELINE_SHA256 = (
    "5a20251836bfbbf0523d050e7eb475f0a7a74b39b1ba5532130b76dcf6e3c780"
)
PG17_NATIVE_BASELINE_STATUS = "SEALED_FROM_REVIEWED_NATIVE_PG17"
B1 = "demo_account_capture_events"
B3 = "demo_account_observation_batches"
MEMBERS = tuple(
    "demo_account_observation_" + name for name in ("facts", "coverage", "findings")
)
TABLES = (B1, B3, *MEMBERS)
LOCK_SQL = (
    "LOCK TABLE "
    + ", ".join("public." + name for name in TABLES)
    + " IN SHARE ROW EXCLUSIVE MODE NOWAIT;"
)
# SHA256 of exact LF-normalized prosrc literals, not a PG deparser observation.
# Batch uses the repaired0037 body. Typed0040 is a separate, unread source.
FUNCTION_SOURCE_SHA256 = {
    "qualification_ledger_immutable": "14fe6a29f274ba10141a2ecdfbc40e4dc6e4014421f338f76a39352670e48649",
    "account_capture_event_guard": "657545ab2fd1ad64dd9a15945b8a97cb57efe8225071f12c39a57d26a254a4b0",
    "account_observation_batch_guard": "0170a152740f5bccf3fcd5054b23a2d39646bde06fa1420210dff6f861a3946e",
    "account_observation_member_guard": "65a72bbfaeff6d0a28cd5d8e2a0c0a692824ba7415628c0d7c09fc3028880959",
    "account_observation_membership_guard": "89e9f285999aa1b0bc9fc21783df3ad8fca01fbc76d275cd334812c4d1838b94",
}
SCOPE = ("environment", "account_id", "settlement_currency")


def _column(name, dtype, nullable=False, default=None):
    return {
        "name": name,
        "type": dtype,
        "nullable": nullable,
        "default": default,
        "generated": "",
        "identity": "",
    }


_SCOPE_COLUMNS = (
    _column("environment", "character varying(8)"),
    _column("account_id", "character varying(32)"),
    _column("settlement_currency", "character varying(16)"),
)
_MEMBER_PREFIX = (
    *_SCOPE_COLUMNS,
    _column("batch_sequence", "bigint"),
    _column("ordinal", "bigint"),
)
COLUMNS = {
    B1: (
        _column("capture_id", "character varying(32)"),
        _column("sequence", "bigint"),
        *_SCOPE_COLUMNS,
        _column("previous_sha256", "character varying(64)", True),
        _column("event_sha256", "character varying(64)"),
        _column("event_json", "text"),
        _column("chain_bytes", "bigint"),
        _column("raw_body", "bytea", True),
        _column("packet_payload", "bytea", True),
        _column(
            "db_recorded_at", "timestamp with time zone", default="clock_timestamp()"
        ),
    ),
    B3: (
        *_SCOPE_COLUMNS,
        _column("sequence", "bigint"),
        _column("capture_id", "character varying(32)"),
        _column("source_sequence", "bigint"),
        _column("previous_sha256", "character varying(64)", True),
        _column("event_sha256", "character varying(64)"),
        _column("document_json", "text"),
        _column("receipt_json", "text"),
        _column(
            "db_recorded_at", "timestamp with time zone", default="clock_timestamp()"
        ),
    ),
    MEMBERS[0]: (
        *_MEMBER_PREFIX,
        _column("family", "character varying(8)"),
        _column("product", "character varying(16)", True),
        _column("identity_sha256", "character varying(64)"),
        _column("row_sha256", "character varying(64)"),
        _column("metadata_sha256", "character varying(64)", True),
        _column("generation_at", "timestamp with time zone"),
        _column("fill_at", "timestamp with time zone", True),
        _column("fact_json", "text"),
    ),
    MEMBERS[1]: (
        *_MEMBER_PREFIX,
        _column("family", "character varying(8)"),
        _column("product", "character varying(16)"),
        _column("started_at", "timestamp with time zone"),
        _column("ended_at", "timestamp with time zone"),
        _column("coverage_json", "text"),
    ),
    MEMBERS[2]: (*_MEMBER_PREFIX, _column("finding_json", "text")),
}


def _constraint(
    table, name, kind, columns=None, target=None, target_columns=None, deferred=False
):
    return {
        "table": table,
        "name": name,
        "kind": kind,
        "columns": None if columns is None else list(columns),
        "target": target,
        "target_namespace": None if target is None else "public",
        "target_columns": None if target_columns is None else list(target_columns),
        "validated": True,
        "deferrable": deferred,
        "deferred": deferred,
        "delete_action": "r" if kind == "f" else " ",
        "update_action": "a" if kind == "f" else " ",
        "match": "s" if kind == "f" else " ",
    }


CONSTRAINTS = [
    _constraint(B1, "pk_" + B1, "p", ("capture_id", "sequence")),
    _constraint(
        B1,
        "fk_account_capture_scope",
        "f",
        SCOPE,
        "qualification_account_scopes",
        SCOPE,
    ),
    *(
        _constraint(B1, "ck_" + B1 + "_" + name, "c")
        for name in (
            "chain_byte_bound",
            "scope",
            "identity",
            "chain",
            "event_bound",
            "raw_bound",
            "packet_bound",
        )
    ),
    _constraint(B3, "pk_" + B3, "p", (*SCOPE, "sequence")),
    _constraint(B3, "uq_account_observation_capture", "u", ("capture_id",)),
    _constraint(
        B3,
        "fk_account_observation_source",
        "f",
        ("capture_id", "source_sequence"),
        B1,
        ("capture_id", "sequence"),
    ),
    *(
        _constraint(B3, "ck_" + B3 + "_" + name, "c")
        for name in ("scope", "sequence_bound", "chain", "byte_bound")
    ),
    _constraint(B3, "account_observation_membership_guard", "t", deferred=True),
]
for _table, _singular in zip(MEMBERS, ("fact", "coverage", "finding"), strict=True):
    CONSTRAINTS.extend(
        (
            _constraint(
                _table, "pk_" + _table, "p", (*SCOPE, "batch_sequence", "ordinal")
            ),
            _constraint(
                _table,
                "fk_observation_" + _singular + "_batch",
                "f",
                (*SCOPE, "batch_sequence"),
                B3,
                (*SCOPE, "sequence"),
            ),
            _constraint(_table, "ck_" + _table + "_bound", "c"),
        )
    )
CONSTRAINTS.append(_constraint(MEMBERS[0], "ck_" + MEMBERS[0] + "_digests", "c"))


def _index(table, name, columns, *, unique=False, primary=False):
    return {
        "table": table,
        "name": name,
        "columns": list(columns),
        "unique": unique,
        "primary": primary,
        "valid": True,
        "ready": True,
        "live": True,
        "method": "btree",
        "predicate": None,
        "expressions": None,
        "include_count": 0,
        "nulls_not_distinct": False,
        "exclusion": False,
        "immediate": True,
    }


INDEXES = [
    _index(B1, "pk_" + B1, ("capture_id", "sequence"), unique=True, primary=True),
    _index(B3, "pk_" + B3, (*SCOPE, "sequence"), unique=True, primary=True),
    _index(B3, "uq_account_observation_capture", ("capture_id",), unique=True),
]
for _table in MEMBERS:
    INDEXES.append(
        _index(
            _table,
            "pk_" + _table,
            (*SCOPE, "batch_sequence", "ordinal"),
            unique=True,
            primary=True,
        )
    )
INDEXES.extend(
    (
        _index(MEMBERS[0], "ix_observation_fact_identity", (*SCOPE, "identity_sha256")),
        _index(MEMBERS[0], "ix_observation_fact_generation", (*SCOPE, "generation_at")),
        _index(
            MEMBERS[1],
            "ix_observation_coverage_domain",
            (*SCOPE, "family", "product", "ended_at"),
        ),
    )
)
TRIGGERS = []
for _table, _prefix, _function in (
    (B1, "account_capture_event", "account_capture_event_guard"),
    (B3, "account_observation_batch", "account_observation_batch_guard"),
    *(
        (table, "observation_member", "account_observation_member_guard")
        for table in MEMBERS
    ),
):
    for _suffix, _kind, _target in (
        ("guard", 7, _function),
        ("immutable", 27, "qualification_ledger_immutable"),
        ("no_truncate", 34, "qualification_ledger_immutable"),
    ):
        TRIGGERS.append(
            {
                "table": _table,
                "name": _prefix + "_" + _suffix,
                "function": _target,
                "function_namespace": "public",
                "enabled": "O",
                "type": _kind,
                "deferrable": False,
                "deferred": False,
                "constraint": False,
                "arguments": "",
                "when": None,
                "old_table": None,
                "new_table": None,
            }
        )
TRIGGERS.append(
    {
        "table": B3,
        "name": "account_observation_membership_guard",
        "function": "account_observation_membership_guard",
        "function_namespace": "public",
        "enabled": "O",
        "type": 5,
        "deferrable": True,
        "deferred": True,
        "constraint": True,
        "arguments": "",
        "when": None,
        "old_table": None,
        "new_table": None,
    }
)
EXPECTED_STRUCTURE = {
    "tables": [
        {
            "name": table,
            "namespace": "public",
            "kind": "r",
            "persistence": "p",
            "rls": False,
            "force_rls": False,
            "partition": False,
            "has_rules": False,
            "inherited": False,
            "has_policies": False,
        }
        for table in sorted(TABLES)
    ],
    "columns": [
        {"table": table, "position": number, **column}
        for table in sorted(COLUMNS)
        for number, column in enumerate(COLUMNS[table], 1)
    ],
    "constraints": sorted(CONSTRAINTS, key=lambda row: (row["table"], row["name"])),
    "indexes": sorted(INDEXES, key=lambda row: (row["table"], row["name"])),
    "functions": [
        {
            "name": name,
            "namespace": "public",
            "arguments": "",
            "returns": "trigger",
            "language": "plpgsql",
            "source_sha256": FUNCTION_SOURCE_SHA256[name],
            "security_definer": False,
            "volatility": "v",
            "config": None,
            "kind": "f",
            "parallel": "u",
            "strict": False,
            "leakproof": False,
        }
        for name in sorted(FUNCTION_SOURCE_SHA256)
    ],
    "triggers": sorted(TRIGGERS, key=lambda row: (row["table"], row["name"])),
    "ri_triggers": sorted(
        (
            {
                "table": spec["table"],
                "constraint": spec["name"],
                "relation": relation,
                "function": function,
                "function_namespace": "pg_catalog",
                "enabled": "O",
                "type": kind,
                "deferrable": False,
                "deferred": False,
                "arguments": "",
                "when": None,
            }
            for spec in CONSTRAINTS
            if spec["kind"] == "f"
            for relation, function, kind in (
                ("child", "RI_FKey_check_ins", 5),
                ("child", "RI_FKey_check_upd", 17),
                ("parent", "RI_FKey_restrict_del", 9),
                ("parent", "RI_FKey_noaction_upd", 17),
            )
        ),
        key=lambda row: (row["table"], row["constraint"], row["function"]),
    ),
}
STRUCTURE_JSON = records.canonical(EXPECTED_STRUCTURE).decode("ascii")


def _names(names):
    return "ARRAY[" + ",".join("'" + name + "'" for name in names) + "]"


_REL = "n.nspname='public' AND c.relname=ANY(" + _names(TABLES) + ")"
_FN = "n.nspname='public' AND p.proname=ANY(" + _names(FUNCTION_SOURCE_SHA256) + ")"
CATALOG_SELECT = r"""
WITH relations AS (
 SELECT c.*,n.nspname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE __REL__
), functions AS (
 SELECT p.*,p.xmin::text AS catalog_xmin,n.nspname,l.lanname FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_catalog.pg_language l ON l.oid=p.prolang WHERE __FN__
), constraints AS (
 SELECT x.*,c.relname FROM pg_catalog.pg_constraint x JOIN relations c ON c.oid=x.conrelid
), indexes AS (
 SELECT i.*,c.relname,ic.relname AS index_name,am.amname FROM pg_catalog.pg_index i JOIN relations c ON c.oid=i.indrelid
 JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid JOIN pg_catalog.pg_am am ON am.oid=ic.relam
), triggers AS (
 SELECT t.*,c.relname,p.proname,fn.nspname AS function_namespace FROM pg_catalog.pg_trigger t JOIN relations c ON c.oid=t.tgrelid
 JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace fn ON fn.oid=p.pronamespace WHERE NOT t.tgisinternal
), ri_triggers AS (
 SELECT t.*,x.relname AS constraint_table,x.conname,x.conrelid,x.confrelid,p.proname,n.nspname AS function_namespace
 FROM pg_catalog.pg_trigger t JOIN constraints x ON x.oid=t.tgconstraint AND x.contype='f'
 JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE t.tgisinternal
)
SELECT jsonb_build_object('schema_version','ctcc.account_original_source_actual_catalog.v1',
 'structure',jsonb_build_object(
  'tables',coalesce((SELECT jsonb_agg(jsonb_build_object('name',c.relname,'namespace',c.nspname,'kind',c.relkind,'persistence',c.relpersistence,
   'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,'partition',c.relispartition,'has_rules',EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite z WHERE z.ev_class=c.oid),'inherited',EXISTS(SELECT 1 FROM pg_catalog.pg_inherits h WHERE h.inhrelid=c.oid OR h.inhparent=c.oid),
   'has_policies',EXISTS(SELECT 1 FROM pg_catalog.pg_policy y WHERE y.polrelid=c.oid)) ORDER BY c.relname COLLATE "C") FROM relations c),'[]'::jsonb),
  'columns',coalesce((SELECT jsonb_agg(jsonb_build_object('table',c.relname,'position',a.attnum,'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
   'nullable',NOT a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid),'generated',a.attgenerated,'identity',a.attidentity) ORDER BY c.relname COLLATE "C",a.attnum)
   FROM relations c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
   WHERE a.attnum>0 AND NOT a.attisdropped),'[]'::jsonb),
  'constraints',coalesce((SELECT jsonb_agg(jsonb_build_object('table',x.relname,'name',x.conname,'kind',x.contype,
   'columns',CASE WHEN x.contype IN ('c','t') THEN NULL ELSE (SELECT jsonb_agg(a.attname ORDER BY z.ord) FROM unnest(x.conkey) WITH ORDINALITY z(attnum,ord) JOIN pg_catalog.pg_attribute a ON a.attrelid=x.conrelid AND a.attnum=z.attnum) END,
   'target',CASE WHEN x.confrelid=0 THEN NULL ELSE (SELECT relname FROM pg_catalog.pg_class WHERE oid=x.confrelid) END,
   'target_namespace',(SELECT n.nspname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=x.confrelid),
   'target_columns',(SELECT jsonb_agg(a.attname ORDER BY z.ord) FROM unnest(x.confkey) WITH ORDINALITY z(attnum,ord) JOIN pg_catalog.pg_attribute a ON a.attrelid=x.confrelid AND a.attnum=z.attnum),
   'validated',x.convalidated,'deferrable',x.condeferrable,'deferred',x.condeferred,'delete_action',x.confdeltype,'update_action',x.confupdtype,'match',x.confmatchtype)
   ORDER BY x.relname COLLATE "C",x.conname COLLATE "C") FROM constraints x),'[]'::jsonb),
  'indexes',coalesce((SELECT jsonb_agg(jsonb_build_object('table',i.relname,'name',i.index_name,
   'columns',(SELECT jsonb_agg(a.attname ORDER BY z.ord) FROM unnest(i.indkey) WITH ORDINALITY z(attnum,ord) JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=z.attnum),
   'unique',i.indisunique,'primary',i.indisprimary,'valid',i.indisvalid,'ready',i.indisready,'live',i.indislive,'method',i.amname,
   'predicate',pg_get_expr(i.indpred,i.indrelid),'expressions',pg_get_expr(i.indexprs,i.indrelid),'include_count',i.indnatts-i.indnkeyatts,
   'nulls_not_distinct',i.indnullsnotdistinct,'exclusion',i.indisexclusion,'immediate',i.indimmediate) ORDER BY i.relname COLLATE "C",i.index_name COLLATE "C") FROM indexes i),'[]'::jsonb),
  'functions',coalesce((SELECT jsonb_agg(jsonb_build_object('name',p.proname,'namespace',p.nspname,'arguments',oidvectortypes(p.proargtypes),'returns',pg_get_function_result(p.oid),
   'language',p.lanname,'source_sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),'security_definer',p.prosecdef,'volatility',p.provolatile,
   'config',p.proconfig,'kind',p.prokind,'parallel',p.proparallel,'strict',p.proisstrict,'leakproof',p.proleakproof) ORDER BY p.proname COLLATE "C",p.oid) FROM functions p),'[]'::jsonb),
  'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('table',t.relname,'name',t.tgname,'function',t.proname,'function_namespace',t.function_namespace,
   'enabled',t.tgenabled,'type',t.tgtype,'deferrable',t.tgdeferrable,'deferred',t.tginitdeferred,'constraint',t.tgconstraint<>0,'arguments',encode(t.tgargs,'hex'),
   'when',pg_get_expr(t.tgqual,t.tgrelid),'old_table',t.tgoldtable,'new_table',t.tgnewtable) ORDER BY t.relname COLLATE "C",t.tgname COLLATE "C") FROM triggers t),'[]'::jsonb),
  'ri_triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('table',t.constraint_table,'constraint',t.conname,
   'relation',CASE WHEN t.tgrelid=t.conrelid THEN 'child' WHEN t.tgrelid=t.confrelid THEN 'parent' ELSE 'foreign' END,
   'function',t.proname,'function_namespace',t.function_namespace,'enabled',t.tgenabled,'type',t.tgtype,'deferrable',t.tgdeferrable,
   'deferred',t.tginitdeferred,'arguments',encode(t.tgargs,'hex'),'when',pg_get_expr(t.tgqual,t.tgrelid))
   ORDER BY t.constraint_table COLLATE "C",t.conname COLLATE "C",t.proname COLLATE "C") FROM ri_triggers t),'[]'::jsonb)),
 'identities',jsonb_build_object(
  'tables',coalesce((SELECT jsonb_agg(jsonb_build_object('name',c.relname,'oid',c.oid::bigint,'namespace_oid',c.relnamespace::bigint,'owner_oid',c.relowner::bigint) ORDER BY c.relname COLLATE "C") FROM relations c),'[]'::jsonb),
  'functions',coalesce((SELECT jsonb_agg(jsonb_build_object('name',p.proname,'oid',p.oid::bigint,'namespace_oid',p.pronamespace::bigint,'owner_oid',p.proowner::bigint,'catalog_xmin',p.catalog_xmin) ORDER BY p.proname COLLATE "C",p.oid) FROM functions p),'[]'::jsonb),
  'constraints',coalesce((SELECT jsonb_agg(jsonb_build_object('table',x.relname,'name',x.conname,'table_oid',x.conrelid::bigint,'target_oid',nullif(x.confrelid,0)::bigint,
   'index_oid',nullif(x.conindid,0)::bigint,'index_expected_oid',CASE WHEN x.contype IN ('p','u') THEN to_regclass('public.'||x.conname)::oid::bigint
    WHEN x.contype='f' THEN (SELECT conindid::bigint FROM pg_catalog.pg_constraint pk WHERE pk.conrelid=x.confrelid AND pk.contype='p') ELSE NULL END)
   ORDER BY x.relname COLLATE "C",x.conname COLLATE "C") FROM constraints x),'[]'::jsonb),
  'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('table',t.relname,'name',t.tgname,'table_oid',t.tgrelid::bigint,'function_oid',t.tgfoid::bigint) ORDER BY t.relname COLLATE "C",t.tgname COLLATE "C") FROM triggers t),'[]'::jsonb),
  'ri_triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('table',t.constraint_table,'constraint',t.conname,'function',t.proname,
   'function_oid',t.tgfoid::bigint,'function_expected_oid',to_regprocedure('pg_catalog.'||quote_ident(t.proname)||'()')::oid::bigint,
   'relation_oid',t.tgrelid::bigint,'other_oid',t.tgconstrrelid::bigint,
   'expected_relation_oid',(CASE WHEN t.proname IN ('RI_FKey_check_ins','RI_FKey_check_upd') THEN t.conrelid ELSE t.confrelid END)::bigint,
   'expected_other_oid',(CASE WHEN t.proname IN ('RI_FKey_check_ins','RI_FKey_check_upd') THEN t.confrelid ELSE t.conrelid END)::bigint)
   ORDER BY t.constraint_table COLLATE "C",t.conname COLLATE "C",t.proname COLLATE "C") FROM ri_triggers t),'[]'::jsonb)),
 'definitions',jsonb_build_object(
  'constraints',coalesce((SELECT jsonb_agg(jsonb_build_object('table',x.relname,'name',x.conname,'definition',pg_get_constraintdef(x.oid,true),'expression',pg_get_expr(x.conbin,x.conrelid)) ORDER BY x.relname COLLATE "C",x.conname COLLATE "C") FROM constraints x),'[]'::jsonb),
  'indexes',coalesce((SELECT jsonb_agg(jsonb_build_object('table',i.relname,'name',i.index_name,'definition',pg_get_indexdef(i.indexrelid)) ORDER BY i.relname COLLATE "C",i.index_name COLLATE "C") FROM indexes i),'[]'::jsonb),
  'functions',coalesce((SELECT jsonb_agg(jsonb_build_object('name',p.proname,'definition',pg_get_functiondef(p.oid)) ORDER BY p.proname COLLATE "C",p.oid) FROM functions p),'[]'::jsonb),
  'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('table',t.relname,'name',t.tgname,'definition',pg_get_triggerdef(t.oid,true)) ORDER BY t.relname COLLATE "C",t.tgname COLLATE "C") FROM triggers t),'[]'::jsonb)))
""".replace("__REL__", _REL).replace("__FN__", _FN)
CATALOG_SQL = CATALOG_SELECT

# Executes INSIDE the frame definer AFTER the UID/parent and five table locks.
# End-of-read repeats it and checks the complete same-session catalog snapshot.
ASSERT_SQL = r"""
  source_catalog:=((__CATALOG__));
  IF source_catalog->'structure' IS DISTINCT FROM '__EXPECTED__'::jsonb THEN
    RAISE EXCEPTION 'original_frame_source_schema_drift';
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(source_catalog->'identities'->'tables') x
    WHERE (x->>'owner_oid')::oid<>(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
      OR (x->>'namespace_oid')::oid<>'public'::regnamespace)
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(source_catalog->'identities'->'functions') x
    WHERE (x->>'owner_oid')::oid<>(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
      OR (x->>'namespace_oid')::oid<>'public'::regnamespace)
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(source_catalog->'identities'->'triggers') x
      JOIN jsonb_array_elements(source_catalog->'structure'->'triggers') y ON x->>'table'=y->>'table' AND x->>'name'=y->>'name'
      WHERE (x->>'function_oid')::oid IS DISTINCT FROM to_regprocedure('public.'||(y->>'function')||'()')
       OR (x->>'table_oid')::oid IS DISTINCT FROM to_regclass('public.'||(x->>'table')))
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(source_catalog->'identities'->'constraints') x
      JOIN jsonb_array_elements(source_catalog->'structure'->'constraints') y ON x->>'table'=y->>'table' AND x->>'name'=y->>'name'
      WHERE (x->>'table_oid')::oid IS DISTINCT FROM to_regclass('public.'||(x->>'table'))
       OR (x->>'target_oid')::oid IS DISTINCT FROM CASE WHEN y->>'target' IS NULL THEN NULL ELSE to_regclass('public.'||(y->>'target')) END
       OR x->'index_oid' IS DISTINCT FROM x->'index_expected_oid')
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(source_catalog->'identities'->'ri_triggers') x
      WHERE x->'function_oid' IS DISTINCT FROM x->'function_expected_oid'
       OR x->'relation_oid' IS DISTINCT FROM x->'expected_relation_oid'
       OR x->'other_oid' IS DISTINCT FROM x->'expected_other_oid') THEN
    RAISE EXCEPTION 'original_frame_source_identity_drift';
  END IF;
""".replace("__CATALOG__", CATALOG_SELECT).replace(
    "__EXPECTED__", STRUCTURE_JSON.replace("'", "''")
)
CONTRACT_SHA256 = records.digest(
    records.canonical(
        {
            "structure": EXPECTED_STRUCTURE,
            "catalog_sql": CATALOG_SELECT,
            "assert_sql": ASSERT_SQL,
            "lock_sql": LOCK_SQL,
        }
    )
)


def _identity_rows(value, key, fields):
    if type(value) is not list:
        parent.deny("original_frame_source_catalog_shape")
    result = {}
    for row in value:
        if type(row) is not dict or set(row) != set(fields):
            parent.deny("original_frame_source_catalog_shape")
        identity = tuple(row[name] for name in key)
        if identity in result:
            parent.deny("original_frame_source_catalog_duplicate")
        result[identity] = row
    return result


def verify_source_catalog(value, previous, *, require_native_baseline=True):
    """Structural diagnostics are not a seal; CHECK expressions remain pending."""
    try:
        if (
            type(require_native_baseline) is not bool
            or type(value) is not dict
            or set(value)
            != {"schema_version", "structure", "identities", "definitions"}
            or value["schema_version"] != SCHEMA
        ):
            parent.deny("original_frame_source_catalog_shape")
        if records.canonical(value["structure"]) != records.canonical(
            EXPECTED_STRUCTURE
        ):
            parent.deny("original_frame_source_schema_drift")
        identities = value["identities"]
        if type(identities) is not dict or set(identities) != {
            "tables",
            "functions",
            "constraints",
            "triggers",
            "ri_triggers",
        }:
            parent.deny("original_frame_source_catalog_shape")
        tables = _identity_rows(
            identities["tables"],
            ("name",),
            ("name", "oid", "namespace_oid", "owner_oid"),
        )
        functions = _identity_rows(
            identities["functions"],
            ("name",),
            ("name", "oid", "namespace_oid", "owner_oid", "catalog_xmin"),
        )
        constraints = _identity_rows(
            identities["constraints"],
            ("table", "name"),
            (
                "table",
                "name",
                "table_oid",
                "target_oid",
                "index_oid",
                "index_expected_oid",
            ),
        )
        triggers = _identity_rows(
            identities["triggers"],
            ("table", "name"),
            ("table", "name", "table_oid", "function_oid"),
        )
        ri_triggers = _identity_rows(
            identities["ri_triggers"],
            ("table", "constraint", "function"),
            (
                "table",
                "constraint",
                "function",
                "function_oid",
                "function_expected_oid",
                "relation_oid",
                "other_oid",
                "expected_relation_oid",
                "expected_other_oid",
            ),
        )
        if (
            set(tables) != {(table,) for table in TABLES}
            or set(functions) != {(name,) for name in FUNCTION_SOURCE_SHA256}
            or set(constraints) != {(row["table"], row["name"]) for row in CONSTRAINTS}
            or set(triggers) != {(row["table"], row["name"]) for row in TRIGGERS}
        ):
            parent.deny("original_frame_source_identity_drift")
        if set(ri_triggers) != {
            (row["table"], row["constraint"], row["function"])
            for row in EXPECTED_STRUCTURE["ri_triggers"]
        }:
            parent.deny("original_frame_source_identity_drift")
        for row in ri_triggers.values():
            for actual_key, expected_key in (
                ("function_oid", "function_expected_oid"),
                ("relation_oid", "expected_relation_oid"),
                ("other_oid", "expected_other_oid"),
            ):
                if (
                    type(row[actual_key]) is not int
                    or not 0 < row[actual_key] < 2**32
                    or type(row[expected_key]) is not int
                    or row[actual_key] != row[expected_key]
                ):
                    parent.deny("original_frame_source_identity_drift")
        owners = {row["owner_oid"] for row in previous["tables"]}
        namespaces = {row["namespace_oid"] for row in previous["tables"]}
        if len(owners) != 1 or len(namespaces) != 1:
            parent.deny("original_frame_source_identity_drift")
        parent_tables = {row["name"]: row for row in previous["parents"]}
        for row in (*tables.values(), *functions.values()):
            if (
                any(
                    type(row[key]) is not int or not 0 < row[key] < 2**32
                    for key in ("oid", "namespace_oid", "owner_oid")
                )
                or row["owner_oid"] not in owners
                or row["namespace_oid"] not in namespaces
            ):
                parent.deny("original_frame_source_identity_drift")
        for row in functions.values():
            if (
                type(row["catalog_xmin"]) is not str
                or not row["catalog_xmin"].isascii()
                or not row["catalog_xmin"].isdigit()
                or not 0 < int(row["catalog_xmin"]) < 2**32
            ):
                parent.deny("original_frame_source_identity_drift")
        for table in (B1, B3):
            if tables[table,]["oid"] != parent_tables[table]["oid"]:
                parent.deny("original_frame_source_identity_drift")
        for spec in CONSTRAINTS:
            row = constraints[spec["table"], spec["name"]]
            target = spec["target"]
            expected_oid = (
                None
                if target is None
                else (
                    tables[target,]["oid"]
                    if target in TABLES
                    else parent_tables[target]["oid"]
                )
            )
            if (
                type(row["table_oid"]) is not int
                or row["table_oid"] != tables[spec["table"],]["oid"]
                or row["target_oid"] != expected_oid
                or (expected_oid is not None and type(row["target_oid"]) is not int)
            ):
                parent.deny("original_frame_source_identity_drift")
            if spec["kind"] in ("p", "u", "f"):
                if (
                    type(row["index_oid"]) is not int
                    or not 0 < row["index_oid"] < 2**32
                    or type(row["index_expected_oid"]) is not int
                    or row["index_oid"] != row["index_expected_oid"]
                ):
                    parent.deny("original_frame_source_identity_drift")
            elif row["index_oid"] is not None or row["index_expected_oid"] is not None:
                parent.deny("original_frame_source_identity_drift")
        for spec in TRIGGERS:
            row = triggers[spec["table"], spec["name"]]
            if (
                type(row["table_oid"]) is not int
                or type(row["function_oid"]) is not int
                or row["table_oid"] != tables[spec["table"],]["oid"]
                or row["function_oid"] != functions[spec["function"],]["oid"]
            ):
                parent.deny("original_frame_source_identity_drift")
        definitions = value["definitions"]
        if type(definitions) is not dict or set(definitions) != {
            "constraints",
            "indexes",
            "functions",
            "triggers",
        }:
            parent.deny("original_frame_source_catalog_shape")
        for key, specs in (
            ("constraints", CONSTRAINTS),
            ("indexes", INDEXES),
            ("functions", [{"name": name} for name in FUNCTION_SOURCE_SHA256]),
            ("triggers", TRIGGERS),
        ):
            fields = (
                ("name", "definition")
                if key == "functions"
                else ("table", "name", "definition", "expression")
                if key == "constraints"
                else ("table", "name", "definition")
            )
            identity = ("name",) if key == "functions" else ("table", "name")
            actual = _identity_rows(definitions[key], identity, fields)
            if set(actual) != {
                tuple(spec[name] for name in identity) for spec in specs
            } or any(
                type(row["definition"]) is not str or not row["definition"]
                for row in actual.values()
            ):
                parent.deny("original_frame_source_catalog_shape")
            if key == "constraints" and any(
                row["expression"] is not None and type(row["expression"]) is not str
                for row in actual.values()
            ):
                parent.deny("original_frame_source_catalog_shape")
        observed = records.digest(records.canonical(definitions))
        if require_native_baseline:
            if (
                PG17_NATIVE_BASELINE_STATUS != "SEALED_FROM_REVIEWED_NATIVE_PG17"
                or PG17_NATIVE_BASELINE_SHA256 is None
            ):
                parent.deny("original_frame_source_native_baseline_pending")
            if observed != PG17_NATIVE_BASELINE_SHA256:
                parent.deny("original_frame_source_deparser_drift")
        return observed
    except parent.AccountHistoryPartitionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        parent.deny("original_frame_source_catalog_shape")
