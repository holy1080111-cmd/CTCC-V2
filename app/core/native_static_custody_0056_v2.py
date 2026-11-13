"""DB0056 custody identity measured under the unchanged strict native catalog.

The full catalog hash remains per-incarnation evidence. This stable projection
is only a candidate for a separately authenticated deployment reference.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import text

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_role_witness_0056_v1 as witness56
from app.database.repositories import public_runtime_bundle_custody as custody
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import canonical, sha


class NativeStaticCustody0056Error(ValueError):
    """Fixed refusal with no role, route, credential, or catalog details."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeStaticCustody0056V2:
    database_oid: int
    database_name: str
    custody_role_name: str
    custody_role_oid: int
    table_owner_oid: int
    route: tuple[str, str, int, str]
    inherited_native_definitions_sha256: str
    granted_inherited_acl_shape_sha256: str
    static_identity_sha256: str
    full_current_catalog_sha256: str
    server_incarnation_sha256: str
    role_route_sha256: str
    schema: Literal["ctcc.native_only.verified_static_custody0056.v2"] = field(
        default="ctcc.native_only.verified_static_custody0056.v2", init=False
    )
    deployment_reference_authenticated: Literal[False] = field(
        default=False, init=False
    )
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(condition: bool) -> None:
    if condition is not True:
        raise NativeStaticCustody0056Error(
            "native_static_custody_0056_denied"
        ) from None


def _static_identity_sha256(
    database_oid: int,
    database_name: str,
    custody_role_name: str,
    custody_role_oid: int,
    table_owner_oid: int,
    route: tuple[str, str, int, str],
    inherited_native_definitions_sha256: str,
    granted_inherited_acl_shape_sha256: str,
) -> str:
    """Hash typed stable fields only after the strict full catalog has passed."""
    _need(
        type(database_oid) is int
        and database_oid > 0
        and type(database_name) is str
        and bool(database_name)
        and type(custody_role_name) is str
        and bool(custody_role_name)
        and type(custody_role_oid) is int
        and custody_role_oid > 0
        and type(table_owner_oid) is int
        and table_owner_oid > 0
        and table_owner_oid != custody_role_oid
        and type(route) is tuple
        and len(route) == 4
        and type(route[0]) is str
        and route[0] == "postgresql+asyncpg"
        and type(route[1]) is str
        and bool(route[1])
        and type(route[2]) is int
        and 1 <= route[2] <= 65535
        and type(route[3]) is str
        and route[3] == database_name
        and roles56._is_digest(inherited_native_definitions_sha256)
        and roles56._is_digest(granted_inherited_acl_shape_sha256)
    )
    return sha(
        canonical(
            {
                "schema": "ctcc.native_only.db0056_policy_verified_custody.v2",
                "migration_head": "0056",
                "database_oid": database_oid,
                "database_name": database_name,
                "custody_role": [custody_role_name, custody_role_oid],
                "table_owner_oid": table_owner_oid,
                "route": list(route),
                "inherited_native_definitions_sha256": (
                    inherited_native_definitions_sha256
                ),
                "granted_inherited_acl_shape_sha256": (
                    granted_inherited_acl_shape_sha256
                ),
            }
        )
    )


def compare_static_custody_0056_v2(
    observed: NativeStaticCustody0056V2,
    *,
    expected_database_oid: int,
    expected_database_name: str,
    expected_custody_role_name: str,
    expected_custody_role_oid: int,
    expected_table_owner_oid: int,
    expected_route: tuple[str, str, int, str],
    expected_inherited_native_definitions_sha256: str,
    expected_granted_inherited_acl_shape_sha256: str,
    expected_static_identity_sha256: str,
) -> dict[str, object]:
    """Pure diagnostic match; caller-supplied expectations grant no authority."""
    _need(
        type(observed) is NativeStaticCustody0056V2
        and roles56._is_digest(expected_static_identity_sha256)
        and roles56._is_digest(observed.full_current_catalog_sha256)
        and roles56._is_digest(observed.server_incarnation_sha256)
        and roles56._is_digest(observed.role_route_sha256)
        and observed.database_oid == expected_database_oid
        and observed.database_name == expected_database_name
        and observed.custody_role_name == expected_custody_role_name
        and observed.custody_role_oid == expected_custody_role_oid
        and observed.table_owner_oid == expected_table_owner_oid
        and observed.route == expected_route
        and observed.inherited_native_definitions_sha256
        == expected_inherited_native_definitions_sha256
        and observed.granted_inherited_acl_shape_sha256
        == expected_granted_inherited_acl_shape_sha256
        and observed.static_identity_sha256
        == expected_static_identity_sha256
        == _static_identity_sha256(
            expected_database_oid,
            expected_database_name,
            expected_custody_role_name,
            expected_custody_role_oid,
            expected_table_owner_oid,
            expected_route,
            expected_inherited_native_definitions_sha256,
            expected_granted_inherited_acl_shape_sha256,
        )
        and observed.deployment_reference_authenticated is False
        and observed.startup_admission is False
        and observed.execution_authority is False
        and observed.admission == "DENY"
    )
    return {
        "schema": "ctcc.native_only.verified_static_custody_match0056.inert.v2",
        "static_identity_sha256": observed.static_identity_sha256,
        "full_current_catalog_sha256": observed.full_current_catalog_sha256,
        "deployment_reference_authenticated": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


async def _read_typed_custody(selected, name: str, route: tuple[str, str, int, str]):
    factory = selected.custody_factory
    repository = custody.RuntimeBundleCustodyRepository(factory)
    async with factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await session.execute(text("SET TRANSACTION READ ONLY"))
        for statement in roles56.current55._SETTINGS:
            await session.execute(text(statement))
        await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
        identity = await roles56._identity_at_0056(session, name)
        full_before = await repository._catalog(session)
        row = (await session.execute(custody._ROLE_SQL)).one_or_none()
        head = await session.scalar(custody._HEAD_SQL)
        full_after = await repository._catalog(session)
        _need(await roles56._identity_at_0056(session, name) == identity)
        _need(
            row is not None
            and head == "0056"
            and row.direct_login is True
            and type(row.database_oid) is int
            and row.database_oid == identity[4]
            and type(row.database_name) is str
            and row.database_name == route[3]
            and type(row.login_oid) is int
            and row.login_oid == identity[3]
            and type(row.owner_oid) is int
            and row.owner_oid > 0
            and row.owner_oid != row.login_oid
            and type(row.server_started) is datetime
            and row.server_started == identity[5]
            and row.server_address == identity[7]
            and row.server_port == identity[8]
            and roles56._is_digest(full_before)
            and full_before == full_after
        )
    return (
        row.database_oid,
        row.database_name,
        name,
        row.login_oid,
        row.owner_oid,
        full_before,
    )


async def observe_static_custody_0056_v2(
    selected, *, source: Path
) -> NativeStaticCustody0056V2:
    """Two fresh strict witnesses bracket a typed custody read, always DENY."""
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
        _need(
            len(set(names)) == 3
            and len(set(url_routes)) == 1
            and selected.custody_factory.kw["bind"] is selected.custody_engine
        )
        async with asyncio.timeout(180):
            before = await witness56.observe_restricted_native_roles_0056_witness_v1(
                selected, source=source
            )
            typed = await _read_typed_custody(selected, names[1], url_routes[0])
            after = await witness56.observe_restricted_native_roles_0056_witness_v1(
                selected, source=source
            )
        _need(
            before == after
            and typed[5] == before.observation.raw_custody_catalog_sha256
            and tuple(roles56.native_factories._factory_route(f) for f in factories)
            == routes
            and tuple(selectors._database_route(f.kw["bind"].url) for f in factories)
            == url_routes
        )
        static = _static_identity_sha256(
            *typed[:5],
            url_routes[0],
            before.observation.inherited_native_definitions_sha256,
            before.observation.granted_inherited_acl_shape_sha256,
        )
        return NativeStaticCustody0056V2(
            *typed[:5],
            url_routes[0],
            before.observation.inherited_native_definitions_sha256,
            before.observation.granted_inherited_acl_shape_sha256,
            static,
            typed[5],
            before.server_incarnation_sha256,
            before.role_route_sha256,
        )
    except asyncio.CancelledError:
        raise
    except NativeStaticCustody0056Error:
        raise
    except Exception:  # noqa: BLE001 -- redact private role, route, and catalog details
        raise NativeStaticCustody0056Error(
            "native_static_custody_0056_unavailable"
        ) from None
