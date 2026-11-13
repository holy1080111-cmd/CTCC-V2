"""New DB0056 restricted witness; historical N234 observer stays immutable."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import text

from app.core import native_role_preflight_0056_full_diagnostic_v1 as old
from app.domain.source_primitives import canonical, sha


@dataclass(frozen=True, slots=True, repr=False)
class RestrictedNativeRoles0056WitnessV1:
    """Actual restricted graph plus opaque per-incarnation identity; DENY."""

    observation: old.RestrictedNativeRoles0056ObservationV1
    server_incarnation_sha256: str
    role_route_sha256: str
    schema: Literal["ctcc.native_only.restricted0056_runtime_witness.v1"] = field(
        default="ctcc.native_only.restricted0056_runtime_witness.v1", init=False
    )
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def runtime_witness_0056(writer_identity, names, url_routes) -> tuple[str, str]:
    """Hash original actor/server and three sanitized configured routes."""
    old._need(
        type(writer_identity) is tuple
        and len(writer_identity) == 9
        and type(names) is tuple
        and len(names) == 3
        and len(set(names)) == 3
        and all(type(name) is str and name for name in names)
        and writer_identity[:3] == (names[0], names[0], "none")
        and type(writer_identity[3]) is int
        and writer_identity[3] > 0
        and type(writer_identity[4]) is int
        and writer_identity[4] > 0
        and type(writer_identity[5]) is datetime
        and writer_identity[5].utcoffset() is not None
        and type(writer_identity[6]) is int
        and 170000 <= writer_identity[6] < 180000
        and type(writer_identity[7]) is str
        and writer_identity[7]
        and type(writer_identity[8]) is int
        and 1 <= writer_identity[8] <= 65535
        and type(url_routes) is tuple
        and len(url_routes) == 3
        and len(set(url_routes)) == 1
        and all(
            type(route) is tuple
            and len(route) == 4
            and all(type(part) in (str, int) for part in route)
            for route in url_routes
        )
    )
    server_sha = sha(
        canonical(
            {
                "schema": "ctcc.native_only.pg0056_incarnation.v1",
                "database_oid": writer_identity[4],
                "postmaster_started_at": writer_identity[5]
                .astimezone(UTC)
                .isoformat(timespec="microseconds"),
                "server_version_num": writer_identity[6],
                "server_address": writer_identity[7],
                "server_port": writer_identity[8],
            }
        )
    )
    route_sha = sha(
        canonical(
            {
                "schema": "ctcc.native_only.pg0056_restricted_route.v1",
                "route": list(url_routes[0]),
                "role_names": list(names),
                "writer_role_oid": writer_identity[3],
            }
        )
    )
    return server_sha, route_sha


async def observe_restricted_native_roles_0056_witness_v1(
    selected, *, source: Path
) -> RestrictedNativeRoles0056WitnessV1:
    """Read the original three roles and their identity in held transactions."""
    try:
        old._need(type(selected) is old.selectors.ConfiguredNativeDemoFactoriesV2)
        old._need(isinstance(source, Path))
        writer = selected.writer_factory
        carrier = selected.custody_factory
        frame = selected.frame_reader_factory
        factories = (writer, carrier, frame)
        routes = tuple(old.native_factories._factory_route(f) for f in factories)
        names = tuple(route[3] for route in routes)
        url_routes = tuple(
            old.selectors._database_route(f.kw["bind"].url) for f in factories
        )
        old._need(
            len(set(names)) == 3
            and len(set(url_routes)) == 1
            and all(route[2] == routes[0][2] for route in routes[1:])
            and carrier.kw["bind"] is selected.custody_engine
            and frame.kw["bind"] is selected.frame_reader_engine
        )
        old.fitted.pinned_migration(source)
        raw_custody = old.custody.RuntimeBundleCustodyRepository(carrier)
        async with asyncio.timeout(55):
            (
                writer_identity,
                frame_identity,
                owner_oid,
                pair,
                pair_sha,
                acl_phase_sha,
            ) = await old._restricted_frame_writer(writer, frame, names)
            async with carrier() as session, session.begin():
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
                await session.execute(text("SET TRANSACTION READ ONLY"))
                for statement in old.current55._SETTINGS:
                    await session.execute(text(statement))
                await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
                custody_identity = await old._identity_at_0056(session, names[1])
                custody_sha = await raw_custody._catalog(session)
                old._need(old._is_digest(custody_sha))
            writer_after = await old._recheck_granted_writer(
                writer, names, pair["native_definitions_sha256"], acl_phase_sha
            )
            old.current55._same_server(
                writer_identity, custody_identity, frame_identity, writer_after
            )
            old._need(
                owner_oid
                not in (writer_identity[3], custody_identity[3], frame_identity[3])
                and tuple(old.native_factories._factory_route(f) for f in factories)
                == routes
            )
            witness = runtime_witness_0056(writer_identity, names, url_routes)
        observation = old.RestrictedNativeRoles0056ObservationV1(
            pair["native_definitions_sha256"], pair_sha, custody_sha, acl_phase_sha
        )
        return RestrictedNativeRoles0056WitnessV1(observation, *witness)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- redact private routes and catalog facts
        raise old.NativeRoles0056ObservationError(
            "native_roles_0056_observation_unavailable"
        ) from None
