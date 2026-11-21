"""Catalog-only PG17 storage proof; never call an observed AM handler.

The fixed OIDs and internal symbol are from PostgreSQL REL_17_STABLE's
pg_am.dat, pg_proc.dat, pg_proc.h and pg_type.dat. These predicates describe
catalog DATA, not permission to read caller-selected relations.
"""

from scripts.native_owner_index_catalog_v1 import NATIVE_BTREE_CATALOG_PREDICATE_V1

NATIVE_SCALAR_OIDS_V1 = frozenset(
    {
        16,
        17,
        20,
        21,
        23,
        25,
        26,
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

HEAP_RELATION_PREDICATE_V1 = """(c.relam=2 AND EXISTS(
 SELECT 1 FROM pg_catalog.pg_am native_am
 JOIN pg_catalog.pg_proc native_handler ON native_handler.oid=native_am.amhandler
 JOIN pg_catalog.pg_namespace native_namespace ON native_namespace.oid=native_handler.pronamespace
 JOIN pg_catalog.pg_language native_language ON native_language.oid=native_handler.prolang
 WHERE native_am.oid=2 AND native_am.amname='heap' AND native_am.amtype='t'
 AND native_am.amhandler=3 AND native_handler.oid=3
 AND native_handler.proname='heap_tableam_handler' AND native_handler.pronamespace=11
 AND native_namespace.nspname='pg_catalog' AND native_handler.proowner=10
 AND native_handler.prolang=12 AND native_language.lanname='internal'
 AND native_handler.prorettype=269 AND native_handler.pronargs=1
 AND native_handler.proargtypes::pg_catalog.text='2281'
 AND native_handler.prosrc='heap_tableam_handler' AND native_handler.prokind='f'
 AND native_handler.provolatile='v' AND native_handler.proparallel='s'
 AND native_handler.proisstrict AND NOT native_handler.prosecdef
 AND NOT native_handler.proretset AND NOT native_handler.proleakproof
 AND native_handler.provariadic=0 AND native_handler.pronargdefaults=0
 AND native_handler.prosupport=0 AND native_handler.procost=1 AND native_handler.prorows=0
 AND native_handler.proconfig IS NULL AND native_handler.probin IS NULL
 AND native_handler.proallargtypes IS NULL AND native_handler.proargmodes IS NULL
 AND native_handler.proargnames IS NULL AND native_handler.proargdefaults IS NULL
 AND native_handler.protrftypes IS NULL AND native_handler.prosqlbody IS NULL
 AND native_handler.proacl IS NULL))"""

ALL_PUBLIC_TABLES_HEAP_V1 = (
    """pg_catalog.current_setting('default_table_access_method')='heap'
 AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p','m','f','v') AND (
 c.relkind<>'r' OR c.relpersistence<>'p' OR c.relhasrules OR c.relrowsecurity
 OR c.relforcerowsecurity OR c.relispartition OR c.relhassubclass OR NOT """
    + HEAP_RELATION_PREDICATE_V1
    + """))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class parent
 JOIN pg_catalog.pg_namespace pn ON pn.oid=parent.relnamespace
 LEFT JOIN pg_catalog.pg_class c ON c.oid=parent.reltoastrelid
 WHERE pn.nspname='public' AND parent.reltoastrelid<>0 AND (
 c.oid IS NULL OR c.relkind<>'t' OR c.relpersistence<>'p' OR c.relhasrules
 OR c.relrowsecurity OR c.relforcerowsecurity OR c.relispartition OR c.relhassubclass OR NOT """
    + HEAP_RELATION_PREDICATE_V1
    + """))
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0
 AND (a.attisdropped OR a.atttypid NOT IN ("""
    + ",".join(str(oid) for oid in sorted(NATIVE_SCALAR_OIDS_V1))
    + "))) AND "
    + NATIVE_BTREE_CATALOG_PREDICATE_V1
)

NATIVE_MARKER_AND_HEAP_SQL_V1 = (
    "SELECT "
    + ALL_PUBLIC_TABLES_HEAP_V1
    + """
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname='alembic_version' AND c.relkind='r' AND c.relpersistence='p'
 AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relispartition AND NOT c.relhasrules
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
 AND (SELECT pg_catalog.count(*) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0)=1
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum=1
 AND a.attname='version_num' AND a.atttypid=1043 AND a.atttypmod=36 AND a.attnotnull
 AND NOT a.attisdropped AND NOT a.atthasdef AND a.attgenerated='' AND a.attidentity=''))
"""
)


class NativeOwnerStorageRefused(ValueError):
    pass


async def require_native_owner_storage_v1(connection):
    """Read only fixed pg_catalog metadata; do not open an application table."""
    from sqlalchemy import text

    if await connection.scalar(text(NATIVE_MARKER_AND_HEAP_SQL_V1)) is not True:
        raise NativeOwnerStorageRefused(
            "native_owner_storage_not_builtin_heap"
        ) from None


async def lock_native_owner_marker_v1(connection):
    """Prove metadata first, hold the native marker, then repeat the proof."""
    from sqlalchemy import text

    await require_native_owner_storage_v1(connection)
    await connection.execute(
        text("LOCK TABLE public.alembic_version IN ACCESS SHARE MODE NOWAIT")
    )
    await require_native_owner_storage_v1(connection)
