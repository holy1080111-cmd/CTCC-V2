"""Exact Live4 structural catalog readback from reviewed SOURCE statements.

No DDL, grants, observed-schema adoption or execution authority lives here.
CHECK comparisons use EXPLAIN without ANALYZE after the caller has already
refused unknown executable routines and validated the actual role/catalog.
"""

import re

from sqlalchemy import text

from app.domain import native_live_ledger_schema_v1 as spec
from app.domain.source_primitives import canonical

_CHECK_PLANS = set()


def _need(value):
    if value is not True:
        raise ValueError("native_live_source_catalog_drift") from None


def _parts(statement):
    body = statement[statement.index("(") + 1 : statement.rindex(")")]
    parts, start, level, quoted, index = [], 0, 0, False, 0
    while index < len(body):
        char = body[index]
        if char == "'":
            if quoted and index + 1 < len(body) and body[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        if not quoted:
            level += (char == "(") - (char == ")")
            _need(level >= 0)
            if char == "," and level == 0:
                parts.append(body[start:index].strip())
                start = index + 1
        index += 1
    _need(level == 0 and quoted is False)
    return (*parts, body[start:].strip())


def source_columns_v1():
    """Closed SQL scalar types; no generic parsing of caller SQL."""
    scalar = {
        "bigint": (20, -1),
        "oid": (26, -1),
        "boolean": (16, -1),
        "timestamptz": (1184, -1),
        "text": (25, -1),
    }
    result = {}
    for table, statement in zip(spec.TABLES, spec.UPGRADE_SQL[:4], strict=True):
        _need(statement.startswith("CREATE TABLE public." + table + " ("))
        columns = []
        for item in _parts(statement):
            match = re.match(
                r"^([a-z_][a-z0-9_]*) (varchar\(([0-9]+)\)|numeric\(([0-9]+),([0-9]+)\)|bigint|oid|boolean|timestamptz|text)\b",
                item,
            )
            # Types ending ')' have no trailing word boundary.
            if match is None:
                match = re.match(
                    r"^([a-z_][a-z0-9_]*) (varchar\(([0-9]+)\)|numeric\(([0-9]+),([0-9]+)\))(?= |$)",
                    item,
                )
            if match is None:
                _need(item.startswith("CONSTRAINT "))
                continue
            name, kind = match[1], match[2]
            if kind.startswith("varchar"):
                oid, modifier = 1043, int(match[3]) + 4
            elif kind.startswith("numeric"):
                oid, modifier = 1700, (int(match[4]) << 16) + int(match[5]) + 4
            else:
                oid, modifier = scalar[kind]
            default = re.search(
                r"\bDEFAULT (true|false|clock_timestamp\(\))(?=\s|$)", item
            )
            columns.append(
                (
                    name,
                    oid,
                    modifier,
                    "NOT NULL" in item or "PRIMARY KEY" in item,
                    None if default is None else default[1],
                )
            )
        result[table] = tuple(columns)
    _need(all(result.values()))
    return result


def _plan_shape(value, *, root=True):
    if root and type(value) is str:
        from app.domain.source_primitives import decode

        value = decode(value.encode("utf8"), maximum=1024 * 1024)
    if type(value) is list:
        return [_plan_shape(v, root=False) for v in value]
    if type(value) is dict:
        return {
            key: _plan_shape(item, root=False)
            for key, item in value.items()
            if key not in ("Startup Cost", "Total Cost", "Plan Rows", "Plan Width")
        }
    return value


async def _predicate(session, table, label, expected, actual):
    _need(type(expected) is type(actual) is str and len(actual) <= 8192)
    key = table, label, expected, actual
    if key in _CHECK_PLANS:
        return
    _need(len(_CHECK_PLANS) < 256)
    left = await session.scalar(
        text(
            f"EXPLAIN (FORMAT JSON, VERBOSE, COSTS OFF) SELECT 1 FROM public.{table} WHERE ({expected}) IS NOT FALSE"
        )
    )
    right = await session.scalar(
        text(
            f"EXPLAIN (FORMAT JSON, VERBOSE, COSTS OFF) SELECT 1 FROM public.{table} WHERE ({actual}) IS NOT FALSE"
        )
    )
    _need(canonical(_plan_shape(left)) == canonical(_plan_shape(right)))
    _CHECK_PLANS.add(key)


async def require_live4_source_catalog_v1(session):
    columns = source_columns_v1()
    actual = (
        await session.execute(
            text("""SELECT c.relname::text,a.attnum,a.attname::text,
      a.atttypid::bigint,a.atttypmod,a.attnotnull,a.attgenerated::text,a.attidentity::text,
      pg_get_expr(d.adbin,d.adrelid),a.attcollation=t.typcollation
      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0
      JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
      LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
      WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))
      ORDER BY c.relname,a.attnum"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(
        [tuple(row) for row in actual]
        == [
            (table, index, name, oid, modifier, nullable, "", "", default, True)
            for table in sorted(columns)
            for index, (name, oid, modifier, nullable, default) in enumerate(
                columns[table], 1
            )
        ]
    )
    keys = {
        (spec.TABLES[0], "pk_" + spec.TABLES[0]): (
            "p",
            ("environment", "account_uid"),
            None,
            (),
        ),
        (spec.TABLES[1], "pk_" + spec.TABLES[1]): ("p", ("reservation_id",), None, ()),
        (spec.TABLES[2], "pk_" + spec.TABLES[2]): ("p", ("reservation_id",), None, ()),
        (spec.TABLES[3], "pk_" + spec.TABLES[3]): (
            "p",
            ("reservation_id", "sequence"),
            None,
            (),
        ),
    }
    for table in spec.TABLES:
        keys.update(
            {
                (table, name): ("u", tuple(fields), None, ())
                for name, fields in spec.TABLE_UNIQUES[table]
            }
        )
    for table in spec.TABLES[1:3]:
        keys[(table, "fk_" + table + "_scope")] = (
            "f",
            ("environment", "account_uid"),
            spec.TABLES[0],
            ("environment", "account_uid"),
        )
    for table in spec.TABLES[2:]:
        keys[(table, "fk_" + table + "_reservation")] = (
            "f",
            ("reservation_id",),
            spec.TABLES[1],
            ("reservation_id",),
        )
    checks = {
        (table, name): expression
        for table in spec.TABLES
        for name, expression in spec.TABLE_CHECKS[table]
    }
    actual = (
        await session.execute(
            text("""SELECT c.relname::text,k.conname::text,k.contype::text,
      k.convalidated,k.condeferrable,k.condeferred,pg_get_expr(k.conbin,k.conrelid),
      ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY v(n,i)
       JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=v.n ORDER BY v.i),
      pn.nspname::text,pc.relname::text,
      ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY v(n,i)
       JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=v.n ORDER BY v.i),
      k.confdeltype::text,k.confupdtype::text,k.confmatchtype::text
      FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      LEFT JOIN pg_catalog.pg_class pc ON pc.oid=k.confrelid
      LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid=pc.relnamespace
      WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(len(actual) == len(keys) + len(checks))
    seen = set()
    for (
        table,
        name,
        kind,
        valid,
        defer,
        deferred,
        expression,
        fields,
        parent_schema,
        parent,
        parent_fields,
        delete,
        update,
        match,
    ) in actual:
        identity = table, name
        _need(
            identity not in seen
            and valid is True
            and defer is False
            and deferred is False
        )
        seen.add(identity)
        if kind == "c":
            _need(identity in checks)
            await _predicate(session, table, name, checks[identity], expression)
        else:
            _need(identity in keys)
            expected = keys[identity]
            _need((kind, tuple(fields), parent, tuple(parent_fields)) == expected)
            if kind == "f":
                _need(
                    (parent_schema, delete, update, match) == ("public", "r", "a", "s")
                )
    _need(seen == set(keys) | set(checks))
    indexes = {
        (table, name): (True, kind == "p", fields, None)
        for (table, name), (kind, fields, _, _) in keys.items()
        if kind in ("p", "u")
    }
    indexes[(spec.TABLES[1], "native_live_active_holds_v1")] = (
        False,
        False,
        ("environment", "account_uid"),
        "state<>'reconciled'",
    )
    actual = (
        await session.execute(
            text("""SELECT c.relname::text,ic.relname::text,i.indisunique,i.indisprimary,
      i.indisvalid,i.indisready,i.indislive,i.indimmediate,i.indexprs IS NULL,
      pg_get_indexdef(i.indexrelid),pg_get_expr(i.indpred,i.indrelid)
      FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid
      WHERE n.nspname='public' AND c.relname=ANY(CAST(:names AS text[]))"""),
            {"names": list(spec.TABLES)},
        )
    ).all()
    _need(len(actual) == len(indexes))
    seen = set()
    for (
        table,
        name,
        unique,
        primary,
        valid,
        ready,
        live,
        immediate,
        plain,
        definition,
        predicate,
    ) in actual:
        identity = table, name
        _need(
            identity not in seen
            and identity in indexes
            and valid is True
            and ready is True
            and live is True
            and immediate is True
            and plain is True
        )
        seen.add(identity)
        expected_unique, expected_primary, fields, where = indexes[identity]
        _need(unique is expected_unique and primary is expected_primary)
        prefix = (
            "CREATE "
            + ("UNIQUE " if unique else "")
            + "INDEX "
            + name
            + " ON public."
            + table
            + " USING btree ("
            + ", ".join(fields)
            + ")"
        )
        if where is None:
            _need(definition == prefix and predicate is None)
        else:
            _need(definition.startswith(prefix + " WHERE ") and type(predicate) is str)
            await _predicate(session, table, name, where, predicate)
    _need(seen == set(indexes))
