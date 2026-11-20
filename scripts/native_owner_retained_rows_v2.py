"""Fresh native owner row inventories; DATA only, never an upgrade permit."""

from __future__ import annotations

import re
from enum import Enum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

ROW_ENCODING = "ctcc.owner.retained_row_sql_null_bitmap.v2"
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_RELATIONS = 512
# Closed native scalar column types used by the reviewed source schemas.
# User domains/enums/composites/base types have their own OIDs and can execute
# foreign output functions during to_jsonb; they are not an extensible fallback.
_NATIVE_SCALAR_OIDS = frozenset(
    {
        16,
        17,
        20,
        21,
        23,
        25,
        26,  # pg_catalog.oid: reviewed55/56 publisher/observer/role identities.
        114,
        700,
        701,
        869,
        1042,
        1043,
        1082,
        1114,
        1184,
        1700,
        2950,
        3802,
    }
)


class MarkerScopeV2(Enum):
    INCLUDE = "include_the_original_version_marker"
    EXCLUDE_UPGRADE_MARKER = "exclude_only_the_separately_verified_upgrade_marker"


class RetainedRowsV2Refused(ValueError):
    pass


def _need(value):
    if value is not True:
        raise RetainedRowsV2Refused("native_owner_retained_rows_v2_refused") from None


def row_expression_v2(columns):
    _need(
        type(columns) is list
        and 0 < len(columns) <= 1600
        and all(type(c) is str and _NAME.fullmatch(c) is not None for c in columns)
        and len(columns) == len(set(columns))
    )
    # SQL NULL and JSONB `null` otherwise collapse in PostgreSQL to_jsonb(row).
    nulls = ",".join('(r."' + c + '" IS NULL)' for c in columns)
    return (
        "pg_catalog.jsonb_build_array(pg_catalog.to_jsonb(r),pg_catalog.to_jsonb(ARRAY["
        + nulls
        + "]))::pg_catalog.text"
    )


def row_multiset_sql_v2(name, columns):
    _need(type(name) is str and _NAME.fullmatch(name) is not None)
    row = row_expression_v2(columns)
    table = 'public."' + name + '" r'
    return (
        text(
            "SELECT pg_catalog.count(*),COALESCE(pg_catalog.sum(pg_catalog.octet_length("
            + row
            + ")),0)::pg_catalog.int8 FROM "
            + table
        ),
        text(
            "SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(COALESCE(pg_catalog.string_agg(row_sha,'' ORDER BY row_sha COLLATE pg_catalog.\"C\"),''),'UTF8')),'hex') FROM (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
            + row
            + ",'UTF8')),'hex') row_sha FROM "
            + table
            + ") q"
        ),
    )


_LAYOUT_SQL = text("""SELECT c.relname::pg_catalog.text,c.oid::pg_catalog.int8,c.relkind::pg_catalog.text,c.relowner::pg_catalog.int8,
 c.relpersistence::pg_catalog.text,c.relrowsecurity,c.relforcerowsecurity,c.relispartition,c.relhassubclass,c.relhasrules,
 COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(a.attname,a.atttypid::pg_catalog.int8,a.atttypmod,a.attnotnull,a.attcollation::pg_catalog.int8,a.attidentity::pg_catalog.text,a.attgenerated::pg_catalog.text)
  ORDER BY a.attnum) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),'[]'::pg_catalog.jsonb)
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p','m','f','v') ORDER BY c.relname COLLATE pg_catalog."C"
""")
_SEQUENCES_SQL = text("""SELECT c.relname::pg_catalog.text,c.oid::pg_catalog.int8,c.relowner::pg_catalog.int8,
 pg_catalog.jsonb_build_array(s.seqtypid::pg_catalog.int8,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle),
 COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(d.refobjid::pg_catalog.int8,d.refobjsubid,d.deptype::pg_catalog.text) ORDER BY d.refobjid,d.refobjsubid,d.deptype)
  FROM pg_catalog.pg_depend d WHERE d.classid='pg_catalog.pg_class'::pg_catalog.regclass AND d.objid=c.oid
  AND d.refclassid='pg_catalog.pg_class'::pg_catalog.regclass AND d.deptype IN ('a','i')),'[]'::pg_catalog.jsonb)
 FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' ORDER BY c.relname COLLATE pg_catalog."C"
""")


def _expected_relations(relations):
    _need(type(relations) is tuple and 0 < len(relations) <= _MAX_RELATIONS)
    expected = {}
    oids = set()
    for item in relations:
        _need(type(item) is tuple and len(item) == 3)
        name, oid, kind = item
        _need(
            type(name) is str
            and _NAME.fullmatch(name) is not None
            and name not in expected
            and type(oid) is int
            and oid > 0
            and oid not in oids
            and kind == "r"
        )
        expected[name] = (oid, kind)
        oids.add(oid)
    _need("alembic_version" in expected)
    return expected


def _checked_layouts(rows, expected, owner_oid):
    _need(type(owner_oid) is int and owner_oid > 0 and len(rows) == len(expected))
    layouts = []
    names = set()
    for row in rows:
        _need(type(row) is tuple and len(row) == 11)
        (
            name,
            oid,
            kind,
            owner,
            persistence,
            rls,
            forced,
            partition,
            children,
            rules,
            columns,
        ) = row
        _need(
            type(name) is str
            and type(oid) is int
            and type(kind) is str
            and name in expected
            and name not in names
            and (oid, kind) == expected[name]
            and owner == owner_oid
            and type(owner) is int
            and persistence == "p"
            and rls is forced is partition is children is rules is False
        )
        _need(type(columns) is list and 0 < len(columns) <= 1600)
        for column in columns:
            _need(
                type(column) is list
                and len(column) == 7
                and type(column[0]) is str
                and _NAME.fullmatch(column[0]) is not None
                and type(column[1]) is int
                and column[1] in _NATIVE_SCALAR_OIDS
                and type(column[2]) is int
                and type(column[3]) is bool
                and type(column[4]) is int
                and column[4] >= 0
                and type(column[5]) is str
                and type(column[6]) is str
            )
        row_expression_v2([column[0] for column in columns])
        layouts.append([name, oid, kind, columns])
        names.add(name)
    _need(names == set(expected))
    return layouts


async def snapshot_rows_v2(
    connection,
    relations,
    *,
    owner_oid,
    marker_scope,
    max_rows,
    max_table_bytes,
    max_total_bytes,
):
    """No saved rows/cache; actual transaction/catalog admission stays upstream.

    Bounds are fixed source-call parameters, never CLI or persisted authority.
    An upgrade excludes only its separately checked Alembic marker VALUE;
    marker columns, relation OID and sequence identity are still retained.
    """
    _need(
        type(connection) is AsyncConnection
        and connection.in_transaction()
        and connection.dialect.name == "postgresql"
        and connection.dialect.driver == "asyncpg"
    )
    _need(
        type(marker_scope) is MarkerScopeV2
        and type(max_rows) is int
        and 0 < max_rows <= 1_000_000
        and type(max_table_bytes) is int
        and 0 < max_table_bytes <= 256 * 1024 * 1024
        and type(max_total_bytes) is int
        and max_table_bytes <= max_total_bytes <= 256 * 1024 * 1024 * _MAX_RELATIONS
    )
    expected = _expected_relations(relations)
    raw_layouts = tuple(
        tuple(row) for row in (await connection.execute(_LAYOUT_SQL)).all()
    )
    layouts = _checked_layouts(raw_layouts, expected, owner_oid)
    rows = []
    total = 0
    for name, _oid, _kind, columns in layouts:
        if (
            name == "alembic_version"
            and marker_scope is MarkerScopeV2.EXCLUDE_UPGRADE_MARKER
        ):
            continue
        size_sql, hash_sql = row_multiset_sql_v2(
            name, [column[0] for column in columns]
        )
        count, size = tuple((await connection.execute(size_sql)).one())
        _need(
            type(count) is int
            and 0 <= count <= max_rows
            and type(size) is int
            and 0 <= size <= max_table_bytes
        )
        total += size
        _need(total <= max_total_bytes)
        digest = await connection.scalar(hash_sql)
        _need(type(digest) is str and _HEX.fullmatch(digest) is not None)
        rows.append([name, count, digest])
    raw_sequences = tuple(
        tuple(row) for row in (await connection.execute(_SEQUENCES_SQL)).all()
    )
    _need(len(raw_sequences) <= _MAX_RELATIONS)
    sequences = []
    names = set()
    for name, oid, owner, definition, ownership in raw_sequences:
        _need(
            type(name) is str
            and _NAME.fullmatch(name) is not None
            and name not in names
            and type(oid) is int
            and oid > 0
            and type(owner) is int
            and owner == owner_oid
            and type(definition) is list
            and len(definition) == 7
            and all(type(v) is int for v in definition[:6])
            and type(definition[6]) is bool
            and type(ownership) is list
            and len(ownership) <= 1
        )
        for binding in ownership:
            _need(
                type(binding) is list
                and len(binding) == 3
                and type(binding[0]) is int
                and binding[0] > 0
                and type(binding[1]) is int
                and binding[1] > 0
                and type(binding[2]) is str
                and binding[2] in {"a", "i"}
            )
        value, called = tuple(
            (
                await connection.execute(
                    text('SELECT last_value,is_called FROM public."' + name + '"')
                )
            ).one()
        )
        _need(type(value) is int and type(called) is bool)
        sequences.append([name, oid, owner, definition, ownership, value, called])
        names.add(name)
    _need(
        tuple(tuple(row) for row in (await connection.execute(_LAYOUT_SQL)).all())
        == raw_layouts
    )
    _need(
        tuple(tuple(row) for row in (await connection.execute(_SEQUENCES_SQL)).all())
        == raw_sequences
    )
    for name, _oid, _owner, _definition, _ownership, value, called in sequences:
        _need(
            tuple(
                (
                    await connection.execute(
                        text('SELECT last_value,is_called FROM public."' + name + '"')
                    )
                ).one()
            )
            == (value, called)
        )
    return {
        "row_encoding": ROW_ENCODING,
        "rows": rows,
        "row_layouts": layouts,
        "sequence_state": sequences,
    }
