"""Native DB0056 observation contract; neither a provisioner nor API preflight."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text

from app.core import native_role_preflight_v1 as current55
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as native_factories,
)
from app.database.repositories import (
    collector_current_0056_multirole_catalog_v1 as multirole,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain import account_history_original_frame_v1 as frame_policy
from app.domain import account_history_partition_catalog_v1 as partition_policy
from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
from app.domain.source_primitives import canonical, sha
from scripts import current0056_fitted_delta_catalog_v1 as fitted

EXPECTED_0056_NATIVE_REFERENCE_SHA256 = ""
_HEAD = text("""
SELECT version_num::text,
       EXISTS (
           SELECT 1 FROM pg_catalog.pg_stat_ssl
           WHERE pid = pg_catalog.pg_backend_pid() AND ssl
       )
FROM public.alembic_version
""")
_OWNER_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
    "SET TRANSACTION READ ONLY",
    "SET LOCAL search_path TO pg_catalog, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL synchronous_commit = 'on'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)


class NativeRoles0056ObservationError(ValueError):
    """Fixed refusal without a route, role name, or catalog document."""


@dataclass(frozen=True, slots=True)
class NativeRoles0056ObservationV1:
    inherited_native_definitions_sha256: str
    frame_writer_observation_sha256: str
    raw_custody_catalog_sha256: str
    fitted_owner_catalog_sha256: str
    schema: Literal["ctcc.native_only.actual0056_role_observation.v1"] = field(
        default="ctcc.native_only.actual0056_role_observation.v1", init=False
    )
    database_head: Literal["0056"] = field(default="0056", init=False)
    native_reference_sealed: Literal[False] = field(default=False, init=False)
    native_baseline_admission: Literal[False] = field(default=False, init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(condition: bool) -> None:
    if condition is not True:
        raise NativeRoles0056ObservationError(
            "native_roles_0056_observation_unavailable"
        )


def _is_digest(value) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


async def _catalog(session, sql):
    return (await session.execute(text(sql))).scalar_one()


async def _identity_at_0056(session, name):
    observed = await current55._identity(session, name)
    rows = (await session.execute(_HEAD)).all()
    _need(len(rows) == 1 and rows[0][0] == "0056" and rows[0][1] is True)
    return observed


async def _restricted_identity(session, name, *, frame):
    await current55._session_settings(session, frame=frame)
    return await _identity_at_0056(session, name)


async def _owner_fitted_catalog(owner_factory, owner_name, demo_names):
    async with owner_factory() as session, session.begin():
        for statement in _OWNER_SETTINGS:
            await session.execute(text(statement))
        identity = await _identity_at_0056(session, owner_name)
        first = await _catalog(session, fitted.CATALOG_SQL)
        repeated = await _catalog(session, fitted.CATALOG_SQL)
        _need(canonical(first) == canonical(repeated))
        fingerprint = fitted.inspect_catalog_document(
            first, owner=owner_name, demo_roles=demo_names, fitted_roles=None
        )
        _need(_is_digest(fingerprint))
        _need(await _identity_at_0056(session, owner_name) == identity)
    return identity, fingerprint


async def _restricted_frame_writer(writer, frame, names):
    async with (
        writer() as writer_session,
        writer_session.begin(),
        frame() as frame_session,
        frame_session.begin(),
    ):
        writer_identity = await _restricted_identity(
            writer_session, names[0], frame=False
        )
        # Preserve the historical frame policy's physical transaction setting.
        # This observer executes only SET and SELECT in the frame transaction.
        frame_identity = await _restricted_identity(frame_session, names[2], frame=True)
        owner_oid = (
            await writer_session.execute(current55._OWNER_OID_SQL)
        ).scalar_one()
        _need(type(owner_oid) is int and owner_oid > 0)
        await current55._exact_existing_acl(
            await writer_session.connection(), names[:3], writer_identity[3]
        )

        partition = await _catalog(frame_session, partition_policy.CATALOG_SQL)
        partition_policy.verify_account_history_partition_catalog(partition)
        _need(partition["tables"][0]["owner_oid"] == owner_oid)
        original = await _catalog(frame_session, frame_policy.CATALOG_SQL)
        frame_policy.verify_catalog(
            original, partition, approved_collector_role_oid=frame_identity[3]
        )
        writer_graph = await _catalog(writer_session, inherited.ACTOR_CATALOG_SQL)
        frame_graph = await _catalog(frame_session, inherited.ACTOR_CATALOG_SQL)
        pair = multirole.verify_0056_pair(
            frame_graph,
            writer_graph,
            raw_name=names[2],
            writer_name=names[0],
            approved_raw_role_oid=frame_identity[3],
            approved_writer_role_oid=writer_identity[3],
            parent_owner_oid=owner_oid,
        )
        _need(
            pair["schema"] == "ctcc.collector_current0056.observed_multirole_catalog.v1"
            and pair["database_head"] == "0056"
            and pair["actual_separate_roles_observed"] is True
            and pair["native_baseline_admission"] is False
            and pair["account_complete"] is False
            and pair["execution_authority"] is False
            and _is_digest(pair["native_definitions_sha256"])
        )
        pair_sha = sha(
            canonical(
                {
                    "partition": partition,
                    "original_frame": original,
                    "current0056": {
                        "profile": pair,
                        "raw": frame_graph,
                        "writer": writer_graph,
                    },
                }
            )
        )
    return writer_identity, frame_identity, owner_oid, pair, pair_sha


async def observe_native_roles_0056_v1(
    selected, *, owner_factory, source: Path
) -> NativeRoles0056ObservationV1:
    """Inspect an owner-run, pre-provisioned Demo-only native DB0056 profile."""
    try:
        _need(type(selected) is selectors.ConfiguredNativeDemoFactoriesV2)
        writer = selected.writer_factory
        carrier = selected.custody_factory
        frame = selected.frame_reader_factory
        factories = (writer, carrier, frame, owner_factory)
        routes = tuple(
            native_factories._factory_route(factory) for factory in factories
        )
        names = tuple(route[3] for route in routes)
        url_routes = tuple(
            selectors._database_route(factory.kw["bind"].url) for factory in factories
        )
        _need(
            len(set(names)) == 4
            and len(set(url_routes)) == 1
            and all(route[2] == routes[0][2] for route in routes[1:3])
            and carrier.kw["bind"] is selected.custody_engine
            and frame.kw["bind"] is selected.frame_reader_engine
        )
        fitted.pinned_migration(source)
        raw_custody = custody.RuntimeBundleCustodyRepository(carrier)
        async with asyncio.timeout(55):
            owner = await _owner_fitted_catalog(owner_factory, names[3], names[:3])
            (
                writer_identity,
                frame_identity,
                owner_oid,
                pair,
                pair_sha,
            ) = await _restricted_frame_writer(writer, frame, names)
            async with carrier() as session, session.begin():
                # One snapshot binds the observed 0056 head to all custody
                # queries, whose historical repository supports other heads.
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
                await session.execute(text("SET TRANSACTION READ ONLY"))
                for statement in current55._SETTINGS:
                    await session.execute(text(statement))
                await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
                custody_identity = await _identity_at_0056(session, names[1])
                custody_sha = await raw_custody._catalog(session)
                _need(_is_digest(custody_sha))
            async with writer() as session, session.begin():
                writer_after = await _restricted_identity(
                    session, names[0], frame=False
                )
            owner_after = await _owner_fitted_catalog(
                owner_factory, names[3], names[:3]
            )
            current55._same_server(
                writer_identity, custody_identity, frame_identity, writer_after
            )
            _need(
                owner == owner_after
                and owner[0][3] == owner_oid
                and owner[0][4:] == writer_identity[4:]
                and owner_oid
                not in (writer_identity[3], custody_identity[3], frame_identity[3])
                and tuple(
                    native_factories._factory_route(factory) for factory in factories
                )
                == routes
            )
        return NativeRoles0056ObservationV1(
            pair["native_definitions_sha256"], pair_sha, custody_sha, owner[1]
        )
    except asyncio.CancelledError:
        raise
    except NativeRoles0056ObservationError:
        raise
    except Exception:  # noqa: BLE001 -- preserve cancellation and redact role/route details.
        raise NativeRoles0056ObservationError(
            "native_roles_0056_observation_unavailable"
        ) from None
