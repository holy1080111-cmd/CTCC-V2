"""Closed PG17 catalog JSON typing; no observed-value or Python coercion.

PostgreSQL serializes oid/regproc and oidvector elements as JSON strings.
The source-declared catalog fields below are widened in SQL before capture.
Every catalog array's dimensions remain explicit, including nullable arrays.
No application row, unknown catalog field or caller-selected type is cast.
"""

from __future__ import annotations

import re

ARRAY_DIMS_FIELD = "_ctcc_catalog_array_dims_v1"
_ALIAS = re.compile(r"[a-z][a-z0-9_]*\Z", re.ASCII)
_DIMS = re.compile(r"(?:\[-?[0-9]+:-?[0-9]+\])+\Z", re.ASCII)

# Exact PostgreSQL17 catalog fields; scalar regproc fields also pass through
# oid explicitly, so their display names can never become allocation IDs.
SCALAR_OIDS = {
    "pg_class": (
        "oid",
        "relnamespace",
        "reltype",
        "reloftype",
        "relowner",
        "relam",
        "reltablespace",
        "reltoastrelid",
        "relrewrite",
    ),
    "pg_attribute": ("attrelid", "atttypid", "attcollation"),
    "pg_attrdef": ("oid", "adrelid"),
    "pg_constraint": (
        "oid",
        "connamespace",
        "conrelid",
        "contypid",
        "conindid",
        "conparentid",
        "confrelid",
    ),
    "pg_trigger": (
        "oid",
        "tgrelid",
        "tgparentid",
        "tgfoid",
        "tgconstraint",
        "tgconstrrelid",
        "tgconstrindid",
    ),
    "pg_index": ("indexrelid", "indrelid"),
    "pg_proc": (
        "oid",
        "pronamespace",
        "proowner",
        "prolang",
        "provariadic",
        "prosupport",
        "prorettype",
    ),
    "pg_type": (
        "oid",
        "typnamespace",
        "typowner",
        "typrelid",
        "typelem",
        "typarray",
        "typbasetype",
        "typcollation",
        "typsubscript",
        "typinput",
        "typoutput",
        "typreceive",
        "typsend",
        "typmodin",
        "typmodout",
        "typanalyze",
    ),
    "pg_sequence": ("seqrelid", "seqtypid"),
    "pg_default_acl": ("oid", "defaclrole", "defaclnamespace"),
    "pg_namespace": ("oid", "nspowner"),
    "pg_database": ("oid", "datdba", "dattablespace"),
    "pg_init_privs": ("objoid", "classoid"),
    "pg_language": ("oid", "lanowner", "lanplcallfoid", "laninline", "lanvalidator"),
    "pg_extension": ("oid", "extowner", "extnamespace"),
    "pg_collation": ("oid", "collnamespace", "collowner"),
    "pg_operator": (
        "oid",
        "oprnamespace",
        "oprowner",
        "oprleft",
        "oprright",
        "oprresult",
        "oprcom",
        "oprnegate",
        "oprcode",
        "oprrest",
        "oprjoin",
    ),
}
OID_ARRAYS = {
    "pg_constraint": ("conpfeqop", "conppeqop", "conffeqop", "conexclop"),
    "pg_index": ("indclass", "indcollation"),
    "pg_proc": ("proargtypes", "proallargtypes", "protrftypes"),
    "pg_extension": ("extconfig",),
}
# Non-OID vectors are also made genuine arrays. Other array values retain the
# original to_jsonb(record) representation; their exact dimensions accompany it.
ARRAYS = {
    "pg_class": ("relacl", "reloptions"),
    "pg_attribute": ("attacl", "attoptions", "attfdwoptions", "attmissingval"),
    "pg_attrdef": (),
    "pg_constraint": (
        "conkey",
        "confkey",
        "conpfeqop",
        "conppeqop",
        "conffeqop",
        "confdelsetcols",
        "conexclop",
    ),
    "pg_trigger": ("tgattr",),
    "pg_index": ("indkey", "indcollation", "indclass", "indoption"),
    "pg_proc": (
        "proargtypes",
        "proallargtypes",
        "proargmodes",
        "proargnames",
        "protrftypes",
        "proconfig",
    ),
    "pg_type": ("typacl",),
    "pg_sequence": (),
    "pg_default_acl": ("defaclacl",),
    "pg_namespace": ("nspacl",),
    "pg_database": ("datacl",),
    "pg_init_privs": ("initprivs",),
    "pg_language": ("lanacl",),
    "pg_extension": ("extconfig", "extcondition"),
    "pg_collation": (),
    "pg_operator": (),
}
_VECTORS = {
    ("pg_proc", "proargtypes"): "oid",
    ("pg_index", "indclass"): "oid",
    ("pg_index", "indcollation"): "oid",
    ("pg_index", "indkey"): "int2",
    ("pg_index", "indoption"): "int2",
    ("pg_trigger", "tgattr"): "int2",
}


def catalog_row_json_v1(catalog, alias):
    if (
        type(catalog) is not str
        or catalog not in SCALAR_OIDS
        or type(alias) is not str
        or _ALIAS.fullmatch(alias) is None
    ):
        raise ValueError("retained0017_catalog_typed_sql_refused") from None
    pairs = []
    for field in SCALAR_OIDS[catalog]:
        pairs.extend(
            (
                "'" + field + "'",
                alias + "." + field + "::pg_catalog.oid::pg_catalog.int8",
            )
        )
    for field in OID_ARRAYS.get(catalog, ()):
        value = alias + "." + field
        if (catalog, field) in _VECTORS:
            value += "::pg_catalog.oid[]"
        pairs.extend(("'" + field + "'", value + "::pg_catalog.int8[]"))
    for (kind, field), element in _VECTORS.items():
        if kind == catalog and element == "int2":
            pairs.extend(
                ("'" + field + "'", alias + "." + field + "::pg_catalog.int2[]")
            )
    dimensions = []
    for field in ARRAYS[catalog]:
        value = alias + "." + field
        if (catalog, field) in _VECTORS:
            value += "::pg_catalog." + _VECTORS[catalog, field] + "[]"
        dimensions.extend(("'" + field + "'", "pg_catalog.array_dims(" + value + ")"))
    pairs.extend(
        (
            "'" + ARRAY_DIMS_FIELD + "'",
            "pg_catalog.jsonb_build_object(" + ",".join(dimensions) + ")",
        )
    )
    return (
        "(pg_catalog.to_jsonb("
        + alias
        + ") || pg_catalog.jsonb_build_object("
        + ",".join(pairs)
        + "))"
    )


def require_catalog_array_metadata_v1(catalog, row):
    if type(catalog) is not str or catalog not in ARRAYS or type(row) is not dict:
        raise ValueError("retained0017_catalog_array_metadata_refused") from None
    dimensions = row.get(ARRAY_DIMS_FIELD)
    if type(dimensions) is not dict or set(dimensions) != set(ARRAYS[catalog]):
        raise ValueError("retained0017_catalog_array_metadata_refused") from None
    for field, value in dimensions.items():
        if field not in row or not (
            value is None or type(value) is str and _DIMS.fullmatch(value) is not None
        ):
            raise ValueError("retained0017_catalog_array_metadata_refused") from None
    for field in OID_ARRAYS.get(catalog, ()):
        value = row[field]
        if value is not None and (
            type(value) is not list
            or any(type(item) is not int or not 0 <= item < 2**32 for item in value)
        ):
            raise ValueError("retained0017_catalog_array_metadata_refused") from None
