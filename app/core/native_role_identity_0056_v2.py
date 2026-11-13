"""Owner-free stable restricted role identity, observed without startup authority.

The historic N234/N256 observers are unchanged. Their full custody hash contains
the PostgreSQL postmaster start time, so it is compared only within one boot.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_role_witness_0056_v1 as witness56
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import canonical, sha

_DATABASE_NAME_SQL = text("SELECT pg_catalog.current_database()::text")


class NativeRoleIdentity0056Error(ValueError):
    """Fixed refusal; no role, route, credential or catalog data in errors."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeRoleIdentity0056V2:
    database_oid: int
    owner_oid: int
    role_names: tuple[str, str, str]
    role_oids: tuple[int, int, int]
    stable_identity_sha256: str
    server_incarnation_sha256: str
    role_route_sha256: str
    inherited_native_definitions_sha256: str
    granted_inherited_acl_shape_sha256: str
    schema: Literal["ctcc.native_only.restricted_role_identity0056.v2"] = field(
        default="ctcc.native_only.restricted_role_identity0056.v2", init=False
    )
    deployment_reference_authenticated: Literal[False] = field(
        default=False, init=False
    )
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(value: bool) -> None:
    if value is not True:
        raise NativeRoleIdentity0056Error("native_role_identity_0056_denied") from None


def _static_identity_sha256(
    database_oid: int,
    owner_oid: int,
    names: tuple[str, str, str],
    oids: tuple[int, int, int],
    route: tuple[str, str, int, str],
) -> str:
    """Exclude postmaster start; include all actual role OIDs and DB route."""
    _need(
        type(database_oid) is int
        and database_oid > 0
        and type(owner_oid) is int
        and owner_oid > 0
        and type(names) is tuple
        and len(names) == 3
        and len(set(names)) == 3
        and all(type(name) is str and name for name in names)
        and type(oids) is tuple
        and len(oids) == 3
        and len(set(oids)) == 3
        and all(type(oid) is int and oid > 0 and oid != owner_oid for oid in oids)
        and type(route) is tuple
        and len(route) == 4
        and type(route[0]) is str
        and route[0] == "postgresql+asyncpg"
        and type(route[1]) is str
        and bool(route[1])
        and type(route[2]) is int
        and 1 <= route[2] <= 65535
        and type(route[3]) is str
        and bool(route[3])
    )
    return sha(
        canonical(
            {
                "schema": "ctcc.native_only.db0056_stable_role_route.v2",
                "database_oid": database_oid,
                "owner_oid": owner_oid,
                "roles": [[name, oid] for name, oid in zip(names, oids, strict=True)],
                "route": list(route),
            }
        )
    )


def compare_static_role_identity_0056_v2(
    observed: NativeRoleIdentity0056V2,
    *,
    expected_database_oid: int,
    expected_owner_oid: int,
    expected_role_names: tuple[str, str, str],
    expected_role_oids: tuple[int, int, int],
    expected_route: tuple[str, str, int, str],
    expected_sha256: str,
) -> dict[str, object]:
    """Pure match only. Caller-supplied expected values never become trust."""
    _need(
        type(observed) is NativeRoleIdentity0056V2
        and type(expected_sha256) is str
        and roles56._is_digest(expected_sha256)
        and observed.database_oid == expected_database_oid
        and observed.owner_oid == expected_owner_oid
        and observed.role_names == expected_role_names
        and observed.role_oids == expected_role_oids
        and observed.stable_identity_sha256
        == expected_sha256
        == _static_identity_sha256(
            expected_database_oid,
            expected_owner_oid,
            expected_role_names,
            expected_role_oids,
            expected_route,
        )
        and observed.deployment_reference_authenticated is False
        and observed.startup_admission is False
        and observed.execution_authority is False
        and observed.admission == "DENY"
    )
    return {
        "schema": "ctcc.native_only.restricted_role_identity_match0056.inert.v2",
        "stable_identity_sha256": observed.stable_identity_sha256,
        "deployment_reference_authenticated": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


async def _read_identity(
    factory, name: str, expected_database: str, *, with_owner: bool
):
    async with factory() as session, session.begin():
        # This V2 identity pass issues only SET/SELECT, including for the frame
        # reader. The historical V1 frame policy separately checks its required
        # READ WRITE transaction context and actual catalog before/after here.
        await roles56.current55._session_settings(session, frame=False)
        identity = await roles56._identity_at_0056(session, name)
        _need(await session.scalar(_DATABASE_NAME_SQL) == expected_database)
        owner_oid = (
            await session.scalar(roles56.current55._OWNER_OID_SQL)
            if with_owner
            else None
        )
        _need(await roles56._identity_at_0056(session, name) == identity)
    return identity, owner_oid


async def observe_restricted_role_identity_0056_v2(
    selected, *, source: Path
) -> NativeRoleIdentity0056V2:
    """Bracket fresh actual role OIDs with full V1 graph/custody readbacks."""
    try:
        _need(type(selected) is selectors.ConfiguredNativeDemoFactoriesV2)
        _need(isinstance(source, Path))
        factories = (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
        routes = tuple(roles56.native_factories._factory_route(f) for f in factories)
        names = tuple(route[3] for route in routes)
        url_routes = tuple(
            selectors._database_route(f.kw["bind"].url) for f in factories
        )
        _need(len(set(names)) == 3 and len(set(url_routes)) == 1)
        async with asyncio.timeout(180):
            before = await witness56.observe_restricted_native_roles_0056_witness_v1(
                selected, source=source
            )
            writer, owner_oid = await _read_identity(
                factories[0], names[0], url_routes[0][3], with_owner=True
            )
            custody, _ = await _read_identity(
                factories[1], names[1], url_routes[0][3], with_owner=False
            )
            frame, _ = await _read_identity(
                factories[2], names[2], url_routes[0][3], with_owner=False
            )
            writer_after, _ = await _read_identity(
                factories[0], names[0], url_routes[0][3], with_owner=False
            )
            roles56.current55._same_server(writer, custody, frame, writer_after)
            _need(
                type(owner_oid) is int
                and owner_oid > 0
                and owner_oid not in (writer[3], custody[3], frame[3])
                and all(row[4] == writer[4] for row in (custody, frame))
                and witness56.runtime_witness_0056(writer, names, url_routes)
                == (before.server_incarnation_sha256, before.role_route_sha256)
                and tuple(roles56.native_factories._factory_route(f) for f in factories)
                == routes
            )
            after = await witness56.observe_restricted_native_roles_0056_witness_v1(
                selected, source=source
            )
            _need(before == after)
        role_oids = (writer[3], custody[3], frame[3])
        stable = _static_identity_sha256(
            writer[4], owner_oid, names, role_oids, url_routes[0]
        )
        return NativeRoleIdentity0056V2(
            writer[4],
            owner_oid,
            names,
            role_oids,
            stable,
            before.server_incarnation_sha256,
            before.role_route_sha256,
            before.observation.inherited_native_definitions_sha256,
            before.observation.granted_inherited_acl_shape_sha256,
        )
    except asyncio.CancelledError:
        raise
    except NativeRoleIdentity0056Error:
        raise
    except Exception:  # noqa: BLE001 -- redact private role/route/catalog details
        raise NativeRoleIdentity0056Error(
            "native_role_identity_0056_unavailable"
        ) from None
