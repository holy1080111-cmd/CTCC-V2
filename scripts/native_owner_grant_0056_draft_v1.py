"""Default-off DB0056 owner grant transaction draft.

The private transaction is exercised only by an isolated hosted lab. No
submitted hash or diagnostic receipt can enable the public entry. A separate
reviewed approval mechanism and restricted-login readback are still required.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.domain import collector_current_0056_inherited_catalog_v1 as inherited56
from app.domain.source_primitives import canonical, sha
from scripts import current0055_runtime_grants as core_acl
from scripts import current0056_fitted_delta_catalog_v1 as fitted56
from scripts import current0056_native_owner_pregrant_v1 as pregrant56
from scripts.native_owner_route_preflight_0056_v1 import (
    _PROFILE_SQL,
    OwnerRoutePinsV1,
    preflight_owner_route_0056_v1,
)
from scripts.native_owner_route_preflight_0056_v1 import (
    _prepare as prepare_route,
)
from scripts.native_owner_upgrade_0056_v1 import (
    _OWNER_LOCK,
    OwnerUpgradePinsV1,
    _checked_profile,
    _verify_reviewed_archive,
)

_SOURCE = Path(__file__).resolve().parents[1]
_OWNER_GRANT_APPROVAL_SHA256 = ""  # Deliberately unsealed; no public write path.
_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL synchronous_commit='on'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='120000ms'",
)


class OwnerGrantRefusal(ValueError):
    """Fixed denial before COMMIT, without source, role or SQL values."""


class OwnerGrantCommitUnknown(RuntimeError):
    """COMMIT was attempted; reconciliation is required before another action."""


class OwnerGrantPostcommitIncomplete(RuntimeError):
    """COMMIT returned, but independent physical readback did not complete."""


def _need(condition: bool) -> None:
    if condition is not True:
        raise OwnerGrantRefusal("native_owner_0056_grant_denied") from None


def _digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True, repr=False)
class OwnerGrantDraftPinsV1:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    route: OwnerRoutePinsV1
    writer: str
    custody: str
    frame_reader: str
    writer_oid: int
    custody_oid: int
    frame_reader_oid: int
    portable_inherited_definitions_sha256: str
    portable_fitted_pregrant_normalized_sha256: str
    raw_inherited_owner_readonly_sha256: str
    raw_inherited_owner_readwrite_sha256: str
    raw_fitted_owner_readonly_sha256: str
    raw_fitted_owner_readwrite_sha256: str
    exact_42_plan_sha256: str


def _names(pins: OwnerGrantDraftPinsV1) -> tuple[str, str, str]:
    _need(type(pins) is OwnerGrantDraftPinsV1)
    names = (pins.writer, pins.custody, pins.frame_reader)
    _need(
        type(pins.route) is OwnerRoutePinsV1
        and pins.route.database_head == "0056"
        and all(type(value) is str for value in names)
        and len(set(names)) == 3
        and pins.route.owner not in names
        and all(
            type(value) is int and value > 0
            for value in (pins.writer_oid, pins.custody_oid, pins.frame_reader_oid)
        )
        and len({pins.writer_oid, pins.custody_oid, pins.frame_reader_oid}) == 3
        and pins.route.owner_oid
        not in {pins.writer_oid, pins.custody_oid, pins.frame_reader_oid}
        and all(
            _digest(value)
            for value in (
                pins.portable_inherited_definitions_sha256,
                pins.portable_fitted_pregrant_normalized_sha256,
                pins.raw_inherited_owner_readonly_sha256,
                pins.raw_inherited_owner_readwrite_sha256,
                pins.raw_fitted_owner_readonly_sha256,
                pins.raw_fitted_owner_readwrite_sha256,
                pins.exact_42_plan_sha256,
            )
        )
    )
    plan = pregrant56._grant_objects(
        writer=pins.writer, custody=pins.custody, frame_reader=pins.frame_reader
    )
    statements = tuple(item.sql() for item in plan)
    _need(sha("\n".join(statements).encode("utf-8")) == pins.exact_42_plan_sha256)
    return names


def _physical(document: dict) -> dict:
    return {
        key: value
        for key, value in document.items()
        if key not in {"read_only", "repeatable", "read_committed"}
    }


async def _read_before(factory, pins: OwnerGrantDraftPinsV1) -> dict:
    names = _names(pins)
    observed = await pregrant56.observe_owner_pregrant_diagnostic_0056(
        factory,
        source=_SOURCE,
        writer=names[0],
        custody=names[1],
        frame_reader=names[2],
    )
    _need(
        observed.inherited_native_definitions_sha256
        == pins.portable_inherited_definitions_sha256
        and observed.fitted_normalized_owner_catalog_sha256
        == pins.portable_fitted_pregrant_normalized_sha256
        and observed.inherited_raw_owner_graph_sha256
        == pins.raw_inherited_owner_readonly_sha256
        and observed.inherited_readwrite_native_definitions_sha256
        == pins.portable_inherited_definitions_sha256
        and observed.fitted_raw_owner_catalog_sha256
        == pins.raw_fitted_owner_readonly_sha256
        and observed.exact_grant_plan_sha256 == pins.exact_42_plan_sha256
        and observed.cluster_system_identifier_sha256
        == pins.route.cluster_system_identifier_sha256
        and observed.grants_present is False
        and observed.startup_admission is observed.execution_authority is False
    )
    # Capture actual fitted physical fields in its required read-only profile.
    async with factory() as session, session.begin():
        for setting in roles56._OWNER_SETTINGS:
            await session.execute(text(setting))
        identity = await roles56._identity_at_0056(session, pins.route.owner)
        document = await roles56._catalog(session, fitted56.CATALOG_SQL)
        _need(
            identity[3] == pins.route.owner_oid
            and identity == await roles56._identity_at_0056(session, pins.route.owner)
            and fitted56.inspect_catalog_document(
                document, owner=pins.route.owner, demo_roles=names, fitted_roles=None
            )
            == pins.raw_fitted_owner_readonly_sha256
        )
    physical = _physical(document)
    async with factory() as session, session.begin():
        for setting in _SETTINGS:
            await session.execute(text(setting))
        await session.execute(text("SET LOCAL search_path TO pg_catalog, pg_temp"))
        readwrite = await roles56._catalog(
            session, fitted56.OWNER_READWRITE_CATALOG_SQL
        )
        fitted56.inspect_owner_readwrite_catalog(
            readwrite, owner=pins.route.owner, demo_roles=names
        )
        _need(
            sha(canonical(readwrite)) == pins.raw_fitted_owner_readwrite_sha256
            and _physical(readwrite) == physical
        )
    return physical


async def _locked_grant(connection, pins: OwnerGrantDraftPinsV1, physical: dict):
    """Recheck all source facts behind locks and mutate exactly 42 ACLs."""
    names = _names(pins)
    plan = pregrant56._grant_objects(
        writer=pins.writer, custody=pins.custody, frame_reader=pins.frame_reader
    )
    for setting in _SETTINGS:
        await connection.exec_driver_sql(setting)
    await connection.execute(_OWNER_LOCK)
    relations = sorted(
        {item.target for item in plan if item.kind == "TABLE"} | fitted56.TABLES
    )
    await connection.exec_driver_sql(
        "LOCK TABLE "
        + ", ".join(f"public.{name}" for name in relations)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    row = tuple((await connection.execute(_PROFILE_SQL)).one())
    _checked_profile(row, pins.route, "0056", read_only=False)
    identity = await roles56._identity_at_0056(connection, pins.route.owner)
    _need(
        identity[3] == pins.route.owner_oid
        and identity[4] == pins.route.database_oid
        and await connection.scalar(roles56.current55._OWNER_OID_SQL)
        == pins.route.owner_oid
        and tuple(
            (
                await connection.execute(
                    core_acl._OWNER_POLICY_SQL, {"owner": pins.route.owner_oid}
                )
            ).one()
        )
        == (True, True)
    )
    raw_inherited = await connection.scalar(text(inherited56.OWNER_CATALOG_SQL))
    inherited = inherited56.observe_owner_readwrite_installation_0056(raw_inherited)
    _need(
        sha(canonical(raw_inherited)) == pins.raw_inherited_owner_readwrite_sha256
        and inherited["database_head"] == "0056"
        and inherited["role_oid"] == pins.route.owner_oid
        and inherited["native_definitions_sha256"]
        == pins.portable_inherited_definitions_sha256
    )

    async def fitted_physical():
        await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, pg_temp")
        document = await connection.scalar(text(fitted56.OWNER_READWRITE_CATALOG_SQL))
        fitted56.inspect_owner_readwrite_catalog(
            document, owner=pins.route.owner, demo_roles=names
        )
        await connection.exec_driver_sql(
            "SET LOCAL search_path TO pg_catalog, public, pg_temp"
        )
        return _physical(document), sha(canonical(document))

    expected_fitted = (physical, pins.raw_fitted_owner_readwrite_sha256)
    _need(await fitted_physical() == expected_fitted)
    role_oids = await core_acl._roles(connection, names, pins.route.owner_oid)
    _need(
        role_oids
        == dict(
            zip(
                names,
                (pins.writer_oid, pins.custody_oid, pins.frame_reader_oid),
                strict=True,
            )
        )
    )
    await roles56._require_no_effective_demo_fitted_execute(connection, names)
    targets = await pregrant56._exact_acl_phase(
        connection,
        role_oids,
        pins.route.owner_oid,
        writer=names[0],
        custody=names[1],
        frame_reader=names[2],
        granted=False,
    )
    for statement in (item.sql() for item in plan):
        await connection.exec_driver_sql(statement)
    _need(
        await pregrant56._exact_acl_phase(
            connection,
            role_oids,
            pins.route.owner_oid,
            writer=names[0],
            custody=names[1],
            frame_reader=names[2],
            granted=True,
        )
        == targets
    )
    await roles56._require_no_effective_demo_fitted_execute(connection, names)
    _need(
        await fitted_physical() == expected_fitted
        and await core_acl._roles(connection, names, pins.route.owner_oid) == role_oids
        and await roles56._identity_at_0056(connection, pins.route.owner) == identity
    )
    row = tuple((await connection.execute(_PROFILE_SQL)).one())
    _checked_profile(row, pins.route, "0056", read_only=False)


async def _commit_once(engine, pins: OwnerGrantDraftPinsV1, physical: dict) -> None:
    attempted = committed = False
    failure: BaseException | None = None
    try:
        async with asyncio.timeout(180):
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await _locked_grant(connection, pins, physical)
                    attempted = True
                    await transaction.commit()
                    committed = True
                except BaseException:
                    if not attempted:
                        try:
                            await transaction.rollback()
                        except BaseException:  # noqa: BLE001 -- still a refusal.
                            _need(False)
                    raise
    except BaseException as error:  # noqa: BLE001 -- classify COMMIT uncertainty.
        failure = error
    finally:
        try:
            await engine.dispose()
        except BaseException as error:  # noqa: BLE001 -- cleanup is required.
            failure = error
    if failure is not None:
        if committed:
            raise OwnerGrantPostcommitIncomplete(
                "native_owner_0056_grant_readback_required"
            ) from None
        if attempted:
            raise OwnerGrantCommitUnknown(
                "native_owner_0056_grant_commit_unknown"
            ) from None
        _need(False)


async def _readback(engine, pins: OwnerGrantDraftPinsV1, physical: dict) -> None:
    """New owner sessions prove committed ACL shape, not restricted-login auth."""
    names = _names(pins)
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    async with asyncio.timeout(65):
        async with factory() as session, session.begin():
            for setting in pregrant56._OWNER_SETTINGS:
                await session.execute(text(setting))
            identity = await roles56._identity_at_0056(session, pins.route.owner)
            _need(
                identity[3] == pins.route.owner_oid
                and identity[4] == pins.route.database_oid
            )
            connection = await session.connection()
            role_oids = await core_acl._roles(connection, names, pins.route.owner_oid)
            _need(
                role_oids
                == dict(
                    zip(
                        names,
                        (pins.writer_oid, pins.custody_oid, pins.frame_reader_oid),
                        strict=True,
                    )
                )
            )
            await pregrant56._exact_acl_phase(
                connection,
                role_oids,
                pins.route.owner_oid,
                writer=names[0],
                custody=names[1],
                frame_reader=names[2],
                granted=True,
            )
            await roles56._require_no_effective_demo_fitted_execute(connection, names)
            # The owner-mode catalog intentionally describes a pregrant state
            # and must not be replayed after the 42 authorized ACL changes.
            # The exact granted ACL is checked above; a separate restricted
            # login must replay the full native definitions before admission.
            _need(
                await roles56._identity_at_0056(session, pins.route.owner) == identity
            )
        async with factory() as session, session.begin():
            for setting in roles56._OWNER_SETTINGS:
                await session.execute(text(setting))
            document = await roles56._catalog(session, fitted56.CATALOG_SQL)
            fitted56.inspect_catalog_document(
                document, owner=pins.route.owner, demo_roles=names, fitted_roles=None
            )
            _need(_physical(document) == physical)


async def _draft_once(
    pins: OwnerGrantDraftPinsV1,
    *,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
) -> dict[str, object]:
    """Lab-only complete transaction path; external approval is not supplied."""
    _names(pins)
    _verify_reviewed_archive(
        OwnerUpgradePinsV1(
            pins.source_commit,
            pins.source_tree,
            pins.source_archive_sha256,
            pins.route,
        ),
        source_archive_file,
    )
    route = prepare_route(pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
    prior = await preflight_owner_route_0056_v1(
        pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
    )
    _need(
        prior["status"] == "PASS"
        and prior["database_head"] == "0056"
        and prior["route_sha256"] == route.route_sha256
        and prior["startup_admission"] is prior["execution_authority"] is False
    )
    engine = create_async_engine(
        route.url,
        poolclass=NullPool,
        connect_args={"ssl": route.tls, "timeout": 10},
        hide_parameters=True,
    )
    try:
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        physical = await _read_before(factory, pins)
    except BaseException:  # noqa: BLE001 -- no write was attempted.
        try:
            await engine.dispose()
        finally:
            _need(False)
    await _commit_once(engine, pins, physical)
    try:
        independent = create_async_engine(
            route.url,
            poolclass=NullPool,
            connect_args={"ssl": route.tls, "timeout": 10},
            hide_parameters=True,
        )
    except BaseException:  # noqa: BLE001 -- COMMIT returned; no retry.
        raise OwnerGrantPostcommitIncomplete(
            "native_owner_0056_grant_readback_required"
        ) from None
    try:
        await _readback(independent, pins, physical)
    except BaseException:  # noqa: BLE001 -- never retry an accepted COMMIT.
        raise OwnerGrantPostcommitIncomplete(
            "native_owner_0056_grant_readback_required"
        ) from None
    finally:
        try:
            await independent.dispose()
        except BaseException:  # noqa: BLE001 -- cleanup still required.
            raise OwnerGrantPostcommitIncomplete(
                "native_owner_0056_grant_readback_required"
            ) from None
    return {
        "schema": "ctcc.native_owner_0056.grant_draft_readback.v1",
        "status": "LAB_ONLY",
        "grant_statements": 42,
        "owner_acl_physical_readbacks": 2,
        "direct_login_readback_pending": True,
        "external_approval_authenticated": False,
        "startup_admission": False,
        "execution_authority": False,
    }


async def grant_reviewed_owner_0056_v1(*_args, **_kwargs):
    """Public entry remains closed until external approval and role readback exist."""
    _need(_digest(_OWNER_GRANT_APPROVAL_SHA256))
    raise OwnerGrantRefusal("native_owner_0056_grant_denied") from None
