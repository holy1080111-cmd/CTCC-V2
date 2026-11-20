"""Observe the explicit DB0057 42+2 role ACL; never grant or select a runtime."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain import g12_custody_catalog_0057_v1 as g12
from app.domain import native_database_profile_v1 as profiles
from app.domain.source_primitives import canonical, sha
from scripts import current0055_native_account_source_acl_v1 as account
from scripts import current0055_native_lifecycle_acl_v1 as lifecycle
from scripts import current0055_runtime_grants as core
from scripts import current0056_fitted_delta_catalog_v1 as fitted

_HEAD_SQL = text("""
SELECT (SELECT array_agg(version_num::text) FROM public.alembic_version),
       r.oid::bigint,r.rolname::text,current_user=session_user,
       pg_catalog.current_setting('role'),pg_catalog.current_setting('server_version_num'),
       pg_catalog.current_setting('transaction_read_only'),
       pg_catalog.current_setting('search_path'),pg_catalog.current_setting('TimeZone'),
       pg_catalog.current_setting('synchronous_commit'),
       EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
""")
_OWNER_SQL = text(
    "SELECT relowner::bigint FROM pg_catalog.pg_class WHERE oid='public.alembic_version'::regclass"
)
_FITTED_ACCESS_SQL = text("""
SELECT r.rolname::text,p.proname::text,
       pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE')
FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
WHERE r.rolname::text=ANY(CAST(:names AS text[])) AND n.nspname='public'
 AND pg_catalog.starts_with(p.proname::text,'gate3_fitted_')
""")


class Current0057G12ProfileError(ValueError):
    """Fixed refusal; retain no private role, route, SQL or catalog in errors."""


def _need(value):
    if value is not True:
        raise Current0057G12ProfileError("current0057_g12_profile_refused") from None


def _plans(writer, custody, frame_reader):
    inherited, tail = lifecycle._plans(writer, custody, frame_reader)
    previous = inherited + tail
    _need(
        len(previous) == 42 and all(item.target not in g12.TABLES for item in previous)
    )
    delta = tuple(
        core._Grant("TABLE", table, ("SELECT", "INSERT"), writer)
        for table in g12.TABLES
    )
    return previous, delta


def current57_native_g12_grant_sql_v1(
    *, writer: str, custody: str, frame_reader: str
) -> tuple[str, ...]:
    """Render two fixed statements only; this does not approve or apply them."""
    _, delta = _plans(writer, custody, frame_reader)
    return tuple(item.sql() for item in delta)


async def observe_current0057_g12_writer_profile_v1(
    connection: AsyncConnection,
    *,
    writer: str,
    custody: str,
    frame_reader: str,
    expected_writer_oid: int,
    read_only: bool = True,
    profile=profiles.DB0057,
) -> dict:
    """Read exact effective 44-grant ACL and G12 guards in one held transaction.

    No migration, grant, role selection or promotion is performed. The inherited
    native graph and startup pins require a separate explicit 0057 admission.
    """
    try:
        head = profiles.head(profile)
        _need(head in {"0057", "0058"})
        previous, delta = _plans(writer, custody, frame_reader)
        names = (writer, custody, frame_reader)
        _need(type(connection) is AsyncConnection and connection.in_transaction())
        _need(
            connection.dialect.name == "postgresql"
            and connection.dialect.driver == "asyncpg"
        )
        _need(type(expected_writer_oid) is int and 0 < expected_writer_oid < 2**32)
        _need(type(read_only) is bool)
        first = tuple((await connection.execute(_HEAD_SQL)).one())
        _need(
            first[:5] == ([head], expected_writer_oid, writer, True, "none")
            and type(first[5]) is str
            and first[5].isascii()
            and first[5].isdecimal()
            and 170000 <= int(first[5]) < 180000
            and first[6:]
            == (
                "on" if read_only else "off",
                "pg_catalog, public, pg_temp",
                "UTC",
                "on",
                True,
            )
        )
        owner = await connection.scalar(_OWNER_SQL)
        _need(type(owner) is int and owner > 0 and owner != expected_writer_oid)
        roles = await core._roles(connection, names, owner)
        _need(roles[writer] == expected_writer_oid)
        _need(
            tuple(
                (
                    await connection.execute(core._OWNER_POLICY_SQL, {"owner": owner})
                ).one()
            )
            == (True, True)
        )
        combined = previous + delta
        expected = core._expected_acl(combined, roles, owner)
        targets = await core._target_oids(connection, expected, owner, granted=True)
        await core._access(connection, combined, roles, granted=True, targets=targets)
        fitted_names = {
            signature.partition("(")[0]
            for signature in fitted.INTERNAL | fitted.APIS.keys()
        }
        fitted_access = (
            await connection.execute(_FITTED_ACCESS_SQL, {"names": list(names)})
        ).all()
        _need(
            len(fitted_access) == len(names) * len(fitted_names)
            and {(r[0], r[1]) for r in fitted_access}
            == {(name, function) for name in names for function in fitted_names}
            and all(r[2] is False for r in fitted_access)
        )
        # Preserve the inherited relation and Control guard invariants as well
        # as checking every effective table/sequence/direct routine privilege.
        _, source = account._plans(*names)
        for additions in (
            source,
            tuple(
                item
                for item in previous
                if (item.kind, item.target) in lifecycle._TABLES
            ),
            delta,
        ):
            subset = core._expected_acl(additions, roles, owner)
            _need(
                await account._delta_relations(connection, subset, owner)
                == {key: targets[key] for key in subset}
            )
        await lifecycle._guard(
            connection, {key: targets[key] for key in lifecycle._TABLES}, owner
        )
        value = await connection.scalar(text(g12.CATALOG_SQL))
        observation = g12.observe_catalog_0057(
            value,
            owner=owner,
            writer=expected_writer_oid,
            **({} if profile is profiles.DB0057 else {"profile": profile}),
        )
        repeated = await connection.scalar(text(g12.CATALOG_SQL))
        _need(canonical(repeated) == canonical(value))
        _need(tuple((await connection.execute(_HEAD_SQL)).one()) == first)
        labels = dict(zip(names, ("writer", "custody", "frame_reader"), strict=True))
        plan = [
            [item.kind, item.target, list(item.rights), labels[item.login]]
            for item in combined
        ]
        return {
            "schema": f"ctcc.current{head}.g12_writer_profile.observation.v1",
            "database_head": head,
            "writer_oid": expected_writer_oid,
            "g12_catalog_sha256": observation["catalog_sha256"],
            "acl_shape_sha256": sha(
                canonical({"database_head": head, "grant_plan": plan})
            ),
            "grant_statements": 44,
            "g12_grant_statements": 2,
            "inherited_native_definitions_verified": False,
            "startup_admission": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    except Current0057G12ProfileError:
        raise
    except Exception:  # noqa: BLE001 -- SQL and role/connection details are private
        raise Current0057G12ProfileError(
            "current0057_g12_profile_unavailable"
        ) from None
