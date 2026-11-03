"""Additive current55 controlled-history RPC declarations and exact catalog.

Original DB0050/52 RPCs keep their stopped-purpose gates. Three NEW names
require actual live Armed Control at original fences and retain original UID,
scope/head, row/genesis/cutoff/source-table guards and unchanged frame DATA.
No grants, Arm, clear, account publication or source authority are installed.
"""

from app.domain import account_history_original_frame_v1 as original_frame
from app.domain import account_history_partition_catalog_v1 as partition
from app.domain.source_primitives import canonical, sha

SCHEMA = "ctcc.controlled_history_current55.catalog.v2"
LOCK = "account_history_partition_current55_v2_lock"
APPEND = "account_history_partition_current55_v2_append"
FRAME = "account_history_original_frame_current55_v2_read"
FUNCTIONS = (LOCK, APPEND, FRAME)
ORIGINALS = (
    "account_history_partition_v1_lock",
    "account_history_partition_v1_append",
    original_frame.FUNCTION,
)


class ControlledHistoryCurrent55Error(ValueError):
    """Fixed codes only; no account, role or SQL contents."""


def _deny():
    raise ControlledHistoryCurrent55Error(
        "current55_controlled_history_catalog_invalid"
    ) from None


def _once(source, before, after):
    if type(source) is not str or source.count(before) != 1:
        _deny()
    return source.replace(before, after, 1)


def _partition_spec(name):
    rows = tuple(value for value in partition.FUNCTION_SPECS if value[0] == name)
    if len(rows) != 1:
        _deny()
    return rows[0]


_OLD_LOCK_SPEC = _partition_spec(ORIGINALS[0])
_OLD_APPEND_SPEC = _partition_spec(ORIGINALS[1])
OLD_LOCK_BODY = partition._body(_OLD_LOCK_SPEC[4])
OLD_APPEND_BODY = partition._body(_OLD_APPEND_SPEC[4])
OLD_FRAME_BODY = original_frame.BODY
_OLD_FENCE = """ IF p_control_revision IS NOT NULL AND (c.control_revision<>p_control_revision OR q.account_revision<>p_account_revision OR q.ledger_revision<>p_ledger_revision
   OR NOT c.emergency_stop OR c.arm_request_id IS NOT NULL OR p_expires IS NULL
   OR p_expires<=clock_timestamp() OR p_expires>c.lease_until) THEN
   RAISE EXCEPTION 'account_history_partition_fence_changed'; END IF;
"""
_ARMED_FENCE = """ IF p_control_revision IS NULL OR p_account_revision IS NULL OR p_ledger_revision IS NULL
   OR p_control_revision<1 OR p_account_revision<0 OR p_ledger_revision<0
   OR c.control_revision<>p_control_revision OR q.account_revision<>p_account_revision OR q.ledger_revision<>p_ledger_revision
   OR c.emergency_stop IS DISTINCT FROM false OR c.arm_request_id IS NULL
   OR c.arm_expires_at IS NULL OR NOT isfinite(c.arm_expires_at)
   OR c.owner_sha256 IS NULL OR c.owner_epoch IS NULL OR c.owner_epoch<1
   OR c.lease_until IS NULL OR NOT isfinite(c.lease_until)
   OR p_expires IS NULL OR NOT isfinite(p_expires)
   OR p_expires<=clock_timestamp() OR p_expires>c.lease_until
   OR p_expires>c.arm_expires_at OR c.arm_expires_at<=clock_timestamp() THEN
   RAISE EXCEPTION 'current55_controlled_history_armed_fence_changed'; END IF;
"""
LOCK_BODY = _once(OLD_LOCK_BODY, _OLD_FENCE, _ARMED_FENCE)
APPEND_BODY = _once(
    OLD_APPEND_BODY, "public." + ORIGINALS[0] + "(", "public." + LOCK + "("
)
FRAME_BODY = _once(
    OLD_FRAME_BODY, "public." + ORIGINALS[0] + "(", "public." + LOCK + "("
)
FRAME_BODY = _once(
    FRAME_BODY,
    "      AND lease_until>=p_expires AND emergency_stop AND arm_request_id IS NULL",
    """      AND isfinite(lease_until) AND lease_until>=p_expires
      AND emergency_stop IS FALSE AND arm_request_id IS NOT NULL
      AND owner_sha256 IS NOT NULL AND owner_epoch>0
      AND arm_expires_at IS NOT NULL AND isfinite(arm_expires_at)
      AND arm_expires_at>=p_expires AND arm_expires_at>clock_timestamp()""",
)
SPECS = (
    (LOCK, _OLD_LOCK_SPEC[1], "void", LOCK_BODY),
    (APPEND, _OLD_APPEND_SPEC[1], "text", APPEND_BODY),
    (FRAME, original_frame.ARGUMENTS, "jsonb", FRAME_BODY),
)
SIGNATURES = {
    name: ", ".join(part.split(" ", 1)[1] for part in arguments.split(","))
    for name, arguments, _, _ in SPECS
}
ORIGINAL_SPECS = (
    (ORIGINALS[0], _OLD_LOCK_SPEC[1], "void", OLD_LOCK_BODY),
    (ORIGINALS[1], _OLD_APPEND_SPEC[1], "text", OLD_APPEND_BODY),
    (ORIGINALS[2], original_frame.ARGUMENTS, "jsonb", OLD_FRAME_BODY),
)
ORIGINAL_SIGNATURES = {
    name: ", ".join(part.split(" ", 1)[1] for part in arguments.split(","))
    for name, arguments, _, _ in ORIGINAL_SPECS
}
CREATE_SQL = tuple(
    f"CREATE FUNCTION public.{name}({arguments}) RETURNS {returns} LANGUAGE plpgsql "
    "VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $controlled55$"
    + body
    + "$controlled55$"
    for name, arguments, returns, body in SPECS
)
REVOKE_SQL = tuple(
    f"REVOKE ALL ON FUNCTION public.{name}({SIGNATURES[name]}) FROM PUBLIC"
    for name in FUNCTIONS
)
STATEMENTS = tuple(
    item for pair in zip(CREATE_SQL, REVOKE_SQL, strict=True) for item in pair
)
DROP_SQL = tuple(
    f"DROP FUNCTION public.{name}({SIGNATURES[name]})" for name in reversed(FUNCTIONS)
)
DDL_SHA256 = sha(canonical(list(STATEMENTS)))
BODY_SHA256 = {name: sha(body.encode("utf-8")) for name, _, _, body in SPECS}
ORIGINAL_BODY_SHA256 = {
    name: sha(body.encode("utf-8")) for name, _, _, body in ORIGINAL_SPECS
}


def statements():
    if len(STATEMENTS) != 6 or set(BODY_SHA256) != set(FUNCTIONS):
        _deny()
    return STATEMENTS


_ALL_NAMES_SQL = ",".join("'" + name + "'" for name in (*FUNCTIONS, *ORIGINALS))
CATALOG_SQL = """
SELECT jsonb_build_object(
 'schema','ctcc.controlled_history_current55.catalog.v2',
 'head',(SELECT version_num FROM public.alembic_version),'database',current_database(),
 'actor',(SELECT oid::bigint FROM pg_roles WHERE rolname=current_user),
 'current_user',current_user,'session_user',session_user,'role_setting',current_setting('role'),
 'functions',coalesce((
  SELECT jsonb_agg(jsonb_build_object(
   'name',p.proname,'oid',p.oid::bigint,'namespace',n.nspname,'namespace_oid',n.oid::bigint,
   'owner_oid',p.proowner::bigint,'arguments',oidvectortypes(p.proargtypes),
   'identity_arguments',pg_get_function_identity_arguments(p.oid),
   'returns',pg_get_function_result(p.oid),'language',l.lanname,'source',p.prosrc,
   'definition',pg_get_functiondef(p.oid),'security_definer',p.prosecdef,
   'volatility',p.provolatile,'config',p.proconfig,'kind',p.prokind,'parallel',p.proparallel,
   'strict',p.proisstrict,'leakproof',p.proleakproof,
   'can_execute',has_function_privilege(current_user,p.oid,'EXECUTE'),
   'acl_valid',CASE WHEN p.proacl IS NULL OR cardinality(p.proacl)=0 THEN true
     WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN false
     ELSE array_position(p.proacl,NULL) IS NULL END,
   'acl',coalesce((SELECT jsonb_agg(jsonb_build_array(a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable)
    ORDER BY a.grantee,a.privilege_type)
    FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a),'[]'::jsonb)
  ) ORDER BY p.proname,p.oid)
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
  WHERE n.nspname='public' AND p.proname IN(__FUNCTION_NAMES__)
 ),'[]'::jsonb),
 'roles',coalesce((
  SELECT jsonb_agg(jsonb_build_object(
   'oid',r.oid::bigint,'name',r.rolname,'login',r.rolcanlogin,'superuser',r.rolsuper,
   'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,'replication',r.rolreplication,
   'bypassrls',r.rolbypassrls,
   'membership',EXISTS(SELECT 1 FROM pg_roles x WHERE x.oid<>r.oid AND pg_has_role(r.oid,x.oid,'MEMBER')),
   'create',has_schema_privilege(r.oid,'public','CREATE') OR has_database_privilege(r.oid,current_database(),'CREATE')
  ) ORDER BY r.oid)
  FROM pg_roles r WHERE r.oid IN(
   SELECT DISTINCT x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
   WHERE n.nspname='public' AND p.proname IN(__FUNCTION_NAMES__)
    AND x.grantee<>p.proowner AND x.grantee<>0
  )
 ),'[]'::jsonb)
)
""".replace("__FUNCTION_NAMES__", _ALL_NAMES_SQL)

_FUNCTION_KEYS = {
    "name",
    "oid",
    "namespace",
    "namespace_oid",
    "owner_oid",
    "arguments",
    "identity_arguments",
    "returns",
    "language",
    "source",
    "definition",
    "security_definer",
    "volatility",
    "config",
    "kind",
    "parallel",
    "strict",
    "leakproof",
    "can_execute",
    "acl_valid",
    "acl",
}
_ROLE_KEYS = {
    "oid",
    "name",
    "login",
    "superuser",
    "createdb",
    "createrole",
    "replication",
    "bypassrls",
    "membership",
    "create",
}


def _oid(value):
    if type(value) is not int or not 0 < value < 2**32:
        _deny()
    return value


def _grantees(row, owner):
    if type(row["acl"]) is not list or row["acl_valid"] is not True:
        _deny()
    actual = set()
    for grant in row["acl"]:
        if (
            type(grant) is not list
            or len(grant) != 4
            or type(grant[0]) is not int
            or type(grant[1]) is not int
            or grant[1] != owner
            or grant[2] != "EXECUTE"
            or grant[3] is not False
            or grant[0] == 0
            or grant[0] in actual
        ):
            _deny()
        actual.add(_oid(grant[0]))
    if owner not in actual:
        _deny()
    return actual


def observe_catalog(value, *, owner, actor, owner_mode, reader_role_oid=None):
    """Actual metadata only; no caller role rewriting or native admission.

    Writer actor is from the independent actual55 current-user profile.
    A reader is compared with its actual captured-factory OID and BOTH frame
    ACLs. Owner installation admits no runtime grants. No grant is installed.
    """
    owner, actor = _oid(owner), _oid(actor)
    if (
        type(owner_mode) is not bool
        or type(value) is not dict
        or set(value)
        != {
            "schema",
            "head",
            "database",
            "actor",
            "current_user",
            "session_user",
            "role_setting",
            "functions",
            "roles",
        }
        or value["schema"] != SCHEMA
        or value["head"] != "0055"
        or type(value["actor"]) is not int
        or value["actor"] != actor
        or type(value["database"]) is not str
        or not value["database"]
        or type(value["current_user"]) is not str
        or not value["current_user"]
        or value["current_user"] != value["session_user"]
        or value["role_setting"] != "none"
        or type(value["functions"]) is not list
        or len(value["functions"]) != 6
        or type(value["roles"]) is not list
    ):
        _deny()
    rows = {}
    for row in value["functions"]:
        if type(row) is not dict or set(row) != _FUNCTION_KEYS or row["name"] in rows:
            _deny()
        rows[row["name"]] = row
    all_specs = (*SPECS, *ORIGINAL_SPECS)
    if set(rows) != {item[0] for item in all_specs}:
        _deny()
    namespace = None
    seen_oids = set()
    for name, arguments, returns, body in all_specs:
        row = rows[name]
        if _oid(row["oid"]) in seen_oids:
            _deny()
        seen_oids.add(row["oid"])
        _oid(row["namespace_oid"])
        _oid(row["owner_oid"])
        if namespace is None:
            namespace = _oid(row["namespace_oid"])
        if (
            row["namespace"] != "public"
            or row["namespace_oid"] != namespace
            or row["owner_oid"] != owner
            or row["arguments"]
            != ", ".join(part.split(" ", 1)[1] for part in arguments.split(","))
            or row["identity_arguments"] != arguments.replace(",", ", ")
            or row["returns"] != returns
            or row["language"] != "plpgsql"
            or row["source"] != body
            or type(row["definition"]) is not str
            or not row["definition"]
            or row["security_definer"] is not True
            or row["volatility"] != "v"
            or row["config"] != ["search_path=pg_catalog, public, pg_temp"]
            or row["kind"] != "f"
            or row["parallel"] != "u"
            or row["strict"] is not False
            or row["leakproof"] is not False
            or type(row["can_execute"]) is not bool
        ):
            _deny()
    grants = {name: _grantees(row, owner) for name, row in rows.items()}
    if grants[LOCK] != {owner} or grants[ORIGINALS[0]] != {owner}:
        _deny()
    old_reader = grants[ORIGINALS[2]] - {owner}
    new_reader = grants[FRAME] - {owner}
    new_writer = grants[APPEND] - {owner}
    if owner_mode:
        if actor != owner or old_reader or new_reader or new_writer:
            _deny()
        writer_oid = raw_oid = None
    else:
        if (
            actor == owner
            or len(old_reader) != 1
            or old_reader != new_reader
            or len(new_writer) != 1
        ):
            _deny()
        raw_oid = next(iter(old_reader))
        writer_oid = next(iter(new_writer))
        if raw_oid == writer_oid or owner in (raw_oid, writer_oid):
            _deny()
        if reader_role_oid is None:
            if actor != writer_oid:
                _deny()
        elif _oid(reader_role_oid) != raw_oid or actor != raw_oid:
            _deny()
    roles = {}
    for role in value["roles"]:
        if (
            type(role) is not dict
            or set(role) != _ROLE_KEYS
            or _oid(role["oid"]) in roles
            or type(role["name"]) is not str
            or not role["name"]
            or role["login"] is not True
            or any(
                role[key] is not False
                for key in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "membership",
                    "create",
                )
            )
        ):
            _deny()
        roles[role["oid"]] = role
    all_nonowners = set().union(*(grants[name] - {owner} for name in grants))
    if set(roles) != all_nonowners:
        _deny()
    # Historical metadata grants on the old stopped append remain separate.
    # They cannot invoke the new writer RPC; all their actual flags are read.
    if owner_mode:
        if roles:
            _deny()
    elif not {raw_oid, writer_oid} <= set(roles):
        _deny()
    if any(rows[name]["can_execute"] is not (actor in grants[name]) for name in rows):
        _deny()
    return {
        "definitions": {name: rows[name]["definition"] for name in FUNCTIONS},
        "writer_role_oid": writer_oid,
        "raw_role_oid": raw_oid,
        "function_oids": {name: rows[name]["oid"] for name in FUNCTIONS},
        "account_complete": False,
        "execution_authority": False,
    }


APPEND_SQL = (
    "SELECT public." + APPEND + "(:environment,:account_id,:currency,:head,:previous,"
    ":registration,:checkpoint,:control_revision,:account_revision,:ledger_revision,:expires)"
)
FRAME_SQL = (
    "SELECT public." + FRAME + "(:environment,:account_id,:currency,:through,:head,"
    ":after,:previous,:control,:account,:ledger,:expires)"
)
