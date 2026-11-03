"""Complete finite physical helper/catalog extension; source DATA, no seal."""

import hashlib

from app.domain import native_filled_flat_closure_schema_v55 as filled

SCHEMA = "ctcc.native_terminal55.actual_catalog.v1"
CATALOG_SQL = """
SELECT jsonb_build_object('schema','ctcc.native_terminal55.actual_catalog.v1',
 'functions',coalesce((SELECT jsonb_agg(jsonb_build_object(
  'oid',p.oid::bigint,'name',p.proname,'namespace',n.nspname,'owner',p.proowner::bigint,
  'arguments',oidvectortypes(p.proargtypes),'returns',pg_get_function_result(p.oid),
  'language',l.lanname,'security_definer',p.prosecdef,'kind',p.prokind::text,
  'volatility',p.provolatile::text,'parallel',p.proparallel::text,'strict',p.proisstrict,
  'leakproof',p.proleakproof,'returns_set',p.proretset,'defaults',p.pronargdefaults,
  'support_zero',p.prosupport=0,'config',p.proconfig,'source',p.prosrc,'definition',pg_get_functiondef(p.oid),
  'acl_valid',CASE WHEN p.proacl IS NULL OR cardinality(p.proacl)=0 THEN true
   WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN false ELSE array_position(p.proacl,NULL) IS NULL END,
  'acl',coalesce((SELECT jsonb_agg(jsonb_build_array(a.grantee::bigint,a.grantor::bigint,a.privilege_type,a.is_grantable)
   ORDER BY a.grantee,a.privilege_type) FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a),'[]'::jsonb)
 ) ORDER BY p.proname,p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='public' AND p.proname IN (
 'qualification_native_plain_v55','qualification_native_canonical_v55','qualification_native_manifest_v55',
 'qualification_native_rows_v55','qualification_native_number_v55','qualification_native_clock_v55','qualification_native_filled_flat_data_v55')),'[]'::jsonb))
"""
FUNCTION_KEYS = {
    "oid",
    "name",
    "namespace",
    "owner",
    "arguments",
    "returns",
    "language",
    "security_definer",
    "kind",
    "volatility",
    "parallel",
    "strict",
    "leakproof",
    "returns_set",
    "defaults",
    "support_zero",
    "config",
    "source",
    "definition",
    "acl_valid",
    "acl",
}


class NativeTerminalCatalogError(ValueError):
    pass


def _deny():
    raise NativeTerminalCatalogError(
        "native_terminal55_actual_catalog_changed"
    ) from None


def observe_catalog(value, *, owner):
    filled.statements()
    if (
        type(owner) is not int
        or not 0 < owner < 2**32
        or type(value) is not dict
        or set(value) != {"schema", "functions"}
        or value["schema"] != SCHEMA
    ):
        _deny()
    rows = value["functions"]
    if type(rows) is not list or len(rows) != len(filled.HELPER_IDENTITIES):
        _deny()
    expected = {
        name: (args, returns, sql.split("$ctcc$")[1])
        for (name, args, returns), sql in zip(
            filled.HELPER_IDENTITIES, filled.HELPERS, strict=True
        )
    }
    result = {}
    seen_oids = set()
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != FUNCTION_KEYS
            or type(row["name"]) is not str
            or row["name"] not in expected
            or row["name"] in result
        ):
            _deny()
        args, returns, body = expected[row["name"]]
        if (
            type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["oid"] in seen_oids
            or row["namespace"] != "public"
            or row["owner"] != owner
            or row["arguments"] != args
            or row["returns"] != returns
            or row["language"] != "plpgsql"
            or row["security_definer"] is not False
            or row["kind"] != "f"
            or row["volatility"] != "v"
            or row["parallel"] != "u"
            or row["strict"] is not False
            or row["leakproof"] is not False
            or row["returns_set"] is not False
            or type(row["defaults"]) is not int
            or row["defaults"] != 0
            or row["support_zero"] is not True
            or row["config"] != ["search_path=pg_catalog, pg_temp"]
            or row["source"] != body
            or type(row["definition"]) is not str
            or not row["definition"]
            or row["acl_valid"] is not True
            or row["acl"]
            != [[0, owner, "EXECUTE", False], [owner, owner, "EXECUTE", False]]
        ):
            _deny()
        seen_oids.add(row["oid"])
        result[row["name"]] = {
            "definition": row["definition"],
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
    return result
