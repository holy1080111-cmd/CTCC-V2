"""Owner-only DB0056 pre-grant observation; no grant or startup authority.

The two independent physical references are deliberately unsealed. Until both
are pinned from reviewed native installations, the production pregrant entry
point refuses before opening a session. The separate isolated diagnostic can
observe actual owner catalogs but cannot seal a reference or authorize startup.
This module contains no provisioner or command-line entry.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.domain import collector_current_0056_inherited_catalog_v1 as inherited56
from app.domain.source_primitives import canonical, sha
from scripts import current0055_native_account_source_acl_v1 as account_acl
from scripts import current0055_native_lifecycle_acl_v1 as lifecycle_acl
from scripts import current0055_runtime_grants as core_acl
from scripts import current0056_fitted_catalog_pair_v1 as fitted_pair
from scripts import current0056_fitted_delta_catalog_v1 as fitted56

# Frozen from the 04ce905 28+11+3 grant objects, with login names normalized to
# writer/custody/frame_reader. A different 42-line matrix is still refused.
EXACT_42_PLAN_SHAPE_SHA256 = (
    "60d98897f8cd07d79dacb1a607359e4a70f400e02e11d9016cd73b2d90a70421"
)
# Owner provisioning needs its own explicitly reviewed inherited-definition
# reference. The diagnostic's reserved full-role reference has no declared
# digest semantics yet, so it must not be interpreted as this component hash.
EXPECTED_0056_OWNER_INHERITED_NATIVE_SHA256 = ""


class NativeOwnerPregrant0056Error(RuntimeError):
    """Fixed refusal without names, catalog values, SQL or connection details."""


_OWNER_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
    "SET TRANSACTION READ ONLY",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL synchronous_commit = 'on'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)


@dataclass(frozen=True, slots=True)
class NativeOwnerPregrant0056Observation:
    native_definitions_sha256: str
    fitted_raw_owner_catalog_sha256: str
    exact_grant_plan_sha256: str
    schema: Literal["ctcc.native_owner_0056.pregrant_observation.v1"] = field(
        default="ctcc.native_owner_0056.pregrant_observation.v1", init=False
    )
    database_head: Literal["0056"] = field(default="0056", init=False)
    grant_statements: Literal[42] = field(default=42, init=False)
    direct_login_readback_pending: Literal[True] = field(default=True, init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


@dataclass(frozen=True, slots=True)
class NativeOwnerPregrant0056Diagnostic:
    """Actual pregrant hashes only; no independent reference or permit."""

    inherited_native_definitions_sha256: str
    inherited_readwrite_native_definitions_sha256: str
    inherited_raw_owner_graph_sha256: str
    cluster_system_identifier_sha256: str
    fitted_raw_owner_catalog_sha256: str
    fitted_normalized_owner_catalog_sha256: str
    exact_grant_plan_sha256: str
    schema: Literal["ctcc.native_owner_0056.pregrant_diagnostic.inert.v1"] = field(
        default="ctcc.native_owner_0056.pregrant_diagnostic.inert.v1", init=False
    )
    database_head: Literal["0056"] = field(default="0056", init=False)
    grant_statements: Literal[42] = field(default=42, init=False)
    grants_present: Literal[False] = field(default=False, init=False)
    fitted_normalized_reference_available: Literal[False] = field(
        default=False, init=False
    )
    actual_measurement_independently_attested: Literal[False] = field(
        default=False, init=False
    )
    native_reference_sealed: Literal[False] = field(default=False, init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(condition: bool, code: str = "native_owner_0056_pregrant_denied") -> None:
    if condition is not True:
        raise NativeOwnerPregrant0056Error(code) from None


def _digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class _OwnerReferencePins:
    inherited_native_definitions_sha256: str
    # SHA256 of the full owner-specific fitted.CATALOG_SQL JSON document. It
    # must never be filled from a normalized writer/frame pair digest.
    fitted_raw_owner_catalog_sha256: str


def _references() -> _OwnerReferencePins:
    native = EXPECTED_0056_OWNER_INHERITED_NATIVE_SHA256
    fitted_raw = fitted56.EXPECTED_NATIVE_REFERENCE_SHA256
    _need(
        _digest(native) and _digest(fitted_raw),
        "native_owner_0056_independent_reference_unsealed",
    )
    return _OwnerReferencePins(native, fitted_raw)


def _grant_objects(*, writer: str, custody: str, frame_reader: str):
    names = {"writer": writer, "custody": custody, "frame_reader": frame_reader}
    core, account = account_acl._plans(**names)
    previous, lifecycle = lifecycle_acl._plans(**names)
    plan = previous + lifecycle
    statements = tuple(item.sql() for item in plan)
    roles = {login: role for role, login in names.items()}
    shape = "\n".join(
        "|".join(
            (
                item.kind,
                item.target,
                ",".join(item.rights),
                roles.get(item.login, "invalid"),
            )
        )
        for item in plan
    )
    _need(
        previous == core + account
        and (len(core), len(account), len(lifecycle), len(plan)) == (28, 11, 3, 42)
        and len(set(statements)) == 42
        and hashlib.sha256(shape.encode("utf-8")).hexdigest()
        == EXACT_42_PLAN_SHAPE_SHA256
        and all(
            item.login in names.values()
            and item.kind in {"SCHEMA", "TABLE", "SEQUENCE", "FUNCTION"}
            and not item.target.startswith("gate3_fitted_")
            for item in plan
        )
    )
    return plan


def exact_42_grant_sql_0056(
    *, writer: str, custody: str, frame_reader: str
) -> tuple[str, ...]:
    """Render the inherited exact statements; the return value is no permit."""
    return tuple(
        item.sql()
        for item in _grant_objects(
            writer=writer, custody=custody, frame_reader=frame_reader
        )
    )


async def _exact_acl_phase(
    connection,
    role_oids,
    owner_oid,
    *,
    writer: str,
    custody: str,
    frame_reader: str,
    granted: bool,
):
    """Check every effective right before or after a future owner transaction."""
    _need(type(granted) is bool)
    plan = _grant_objects(writer=writer, custody=custody, frame_reader=frame_reader)
    expected = core_acl._expected_acl(plan, role_oids, owner_oid)
    targets = await core_acl._target_oids(
        connection, expected, owner_oid, granted=granted
    )
    await core_acl._access(
        connection, plan, role_oids, granted=granted, targets=targets
    )
    account_keys = core_acl._expected_acl(plan[28:39], role_oids, owner_oid)
    _need(
        await account_acl._delta_relations(
            connection,
            {key: targets[key] for key in account_keys},
            owner_oid,
        )
        == {key: targets[key] for key in account_keys}
    )
    lifecycle_tables = {key: targets[key] for key in lifecycle_acl._TABLES}
    _need(
        await account_acl._delta_relations(connection, lifecycle_tables, owner_oid)
        == lifecycle_tables
    )
    await lifecycle_acl._guard(connection, lifecycle_tables, owner_oid)
    return targets


async def _read_owner_pregrant_graph_0056(
    owner_factory: async_sessionmaker,
    owner_name: str,
    names: tuple[str, str, str],
    *,
    readwrite: bool = False,
) -> tuple[tuple, str, str, str]:
    """Read the inherited graph twice and prove absence of all 42 grants."""
    _need(type(readwrite) is bool)
    settings = (
        (_OWNER_SETTINGS[0], "SET TRANSACTION READ WRITE", *_OWNER_SETTINGS[2:])
        if readwrite
        else _OWNER_SETTINGS
    )
    async with owner_factory() as session, session.begin():
        for setting in settings:
            await session.execute(text(setting))
        identity = await roles56._identity_at_0056(session, owner_name)
        first = (
            await session.execute(text(inherited56.OWNER_CATALOG_SQL))
        ).scalar_one()
        repeated = (
            await session.execute(text(inherited56.OWNER_CATALOG_SQL))
        ).scalar_one()
        _need(canonical(first) == canonical(repeated))
        observed = (
            inherited56.observe_owner_readwrite_installation_0056(first)
            if readwrite
            else inherited56.observe_owner_installation_0056(first)
        )
        _need(
            observed["database_head"] == "0056"
            and observed["role_oid"] == identity[3]
            and observed["native_baseline_admission"] is False
            and observed["execution_authority"] is False
            and _digest(observed["native_definitions_sha256"])
        )
        connection = await session.connection()
        owner_oid = identity[3]
        cluster_identifier = await connection.scalar(
            text("SELECT system_identifier::text FROM pg_catalog.pg_control_system()")
        )
        _need(
            type(cluster_identifier) is str
            and re.fullmatch(r"[1-9][0-9]{0,19}", cluster_identifier) is not None
        )
        _need(
            await connection.scalar(roles56.current55._OWNER_OID_SQL) == owner_oid
            and tuple(
                (
                    await connection.execute(
                        core_acl._OWNER_POLICY_SQL, {"owner": owner_oid}
                    )
                ).one()
            )
            == (True, True)
        )
        role_oids = await core_acl._roles(connection, names, owner_oid)
        await _exact_acl_phase(
            connection,
            role_oids,
            owner_oid,
            writer=names[0],
            custody=names[1],
            frame_reader=names[2],
            granted=False,
        )
        _need(await roles56._identity_at_0056(session, owner_name) == identity)
    return (
        identity,
        observed["native_definitions_sha256"],
        sha(canonical(first)),
        sha(cluster_identifier.encode("ascii")),
    )


async def _read_fitted_owner_pregrant_catalog_0056(
    owner_factory: async_sessionmaker,
    owner_name: str,
    names: tuple[str, str, str],
) -> tuple[tuple, str, str]:
    """Validate actual pre-fitted catalog and retain distinct raw/neutral hashes."""
    async with owner_factory() as session, session.begin():
        for setting in roles56._OWNER_SETTINGS:
            await session.execute(text(setting))
        identity = await roles56._identity_at_0056(session, owner_name)
        first = await roles56._catalog(session, fitted56.CATALOG_SQL)
        repeated = await roles56._catalog(session, fitted56.CATALOG_SQL)
        _need(canonical(first) == canonical(repeated))
        raw_sha = fitted56.inspect_catalog_document(
            first, owner=owner_name, demo_roles=names, fitted_roles=None
        )
        normalized = fitted_pair.normalized_catalog_document(
            first, owner=owner_name, demo_roles=names, fitted_roles=None
        )
        normalized_sha = fitted_pair.verify_normalized_catalog_document(
            normalized, pregrant=True
        )
        _need(
            _digest(raw_sha)
            and _digest(normalized_sha)
            and await roles56._identity_at_0056(session, owner_name) == identity
        )
    return identity, raw_sha, normalized_sha


async def observe_owner_pregrant_diagnostic_0056(
    owner_factory: async_sessionmaker,
    *,
    source: Path,
    writer: str,
    custody: str,
    frame_reader: str,
) -> NativeOwnerPregrant0056Diagnostic:
    """Capture real DB0056 owner digests before grants, without a sealed pin.

    This fixed owner-only diagnostic reads two independent fresh owner
    transactions. Its raw owner hash is not the normalized fitted pair hash and
    must not be copied into a production reference without independent review.
    """
    try:
        plan = _grant_objects(writer=writer, custody=custody, frame_reader=frame_reader)
        _need(type(owner_factory) is async_sessionmaker and isinstance(source, Path))
        engine = owner_factory.kw.get("bind")
        _need(
            type(engine) is AsyncEngine
            and engine.dialect.name == "postgresql"
            and engine.dialect.driver == "asyncpg"
            and type(engine.url.username) is str
            and engine.url.username not in {writer, custody, frame_reader}
        )
        fitted56.pinned_migration(source)
        names = (writer, custody, frame_reader)
        async with asyncio.timeout(55):
            fitted_before = await _read_fitted_owner_pregrant_catalog_0056(
                owner_factory, engine.url.username, names
            )
            inherited_before = await _read_owner_pregrant_graph_0056(
                owner_factory, engine.url.username, names
            )
            inherited_readwrite = await _read_owner_pregrant_graph_0056(
                owner_factory, engine.url.username, names, readwrite=True
            )
            fitted_after = await _read_fitted_owner_pregrant_catalog_0056(
                owner_factory, engine.url.username, names
            )
            inherited_after = await _read_owner_pregrant_graph_0056(
                owner_factory, engine.url.username, names
            )
            _need(
                fitted_before == fitted_after
                and inherited_before == inherited_after
                and fitted_before[0] == inherited_before[0]
                and inherited_readwrite[0] == inherited_before[0]
                and inherited_readwrite[1] == inherited_before[1]
                and inherited_readwrite[3] == inherited_before[3]
                and all(
                    _digest(value)
                    for value in (*fitted_before[1:], *inherited_before[1:])
                )
                and all(_digest(value) for value in inherited_readwrite[1:])
            )
        statements = tuple(item.sql() for item in plan)
        return NativeOwnerPregrant0056Diagnostic(
            inherited_native_definitions_sha256=inherited_before[1],
            inherited_readwrite_native_definitions_sha256=inherited_readwrite[1],
            inherited_raw_owner_graph_sha256=inherited_before[2],
            cluster_system_identifier_sha256=inherited_before[3],
            fitted_raw_owner_catalog_sha256=fitted_before[1],
            fitted_normalized_owner_catalog_sha256=fitted_before[2],
            exact_grant_plan_sha256=hashlib.sha256(
                "\n".join(statements).encode("utf-8")
            ).hexdigest(),
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - fixed refusal; never reveal SQL or role.
        raise NativeOwnerPregrant0056Error(
            "native_owner_0056_pregrant_diagnostic_denied"
        ) from None


async def observe_owner_pregrant_0056(
    owner_factory: async_sessionmaker,
    *,
    source: Path,
    writer: str,
    custody: str,
    frame_reader: str,
) -> NativeOwnerPregrant0056Observation:
    """Read actual 0056 owner and denied ACLs; never mutate or authorize.

    A later provisioner must repeat every check inside its own write transaction
    and independently read back committed roles. This observation is no lease.
    """
    pins = _references()  # Refuse before DB I/O.
    try:
        plan = _grant_objects(writer=writer, custody=custody, frame_reader=frame_reader)
        statements = tuple(item.sql() for item in plan)
        _need(type(owner_factory) is async_sessionmaker and isinstance(source, Path))
        engine = owner_factory.kw.get("bind")
        _need(
            type(engine) is AsyncEngine
            and engine.dialect.name == "postgresql"
            and engine.dialect.driver == "asyncpg"
            and type(engine.url.username) is str
            and engine.url.username not in {writer, custody, frame_reader}
        )
        fitted56.pinned_migration(source)
        names = (writer, custody, frame_reader)
        async with asyncio.timeout(55):
            # Fitted physical SQL has a distinct required transaction profile.
            fitted_identity, fitted_sha = await roles56._owner_fitted_catalog(
                owner_factory, engine.url.username, names
            )
            # _owner_fitted_catalog validates PUBLIC/direct function and table
            # ACLs plus watched role membership. An independent effective
            # fitted-routine no-EXECUTE probe is a required combined scope
            # before any future production grant writer is admitted.
            _need(fitted_sha == pins.fitted_raw_owner_catalog_sha256)

            async with owner_factory() as session, session.begin():
                for setting in _OWNER_SETTINGS:
                    await session.execute(text(setting))
                identity = await roles56._identity_at_0056(session, engine.url.username)
                _need(identity == fitted_identity)
                owner_oid = identity[3]
                observed = inherited56.observe_owner_installation_0056(
                    (
                        await session.execute(text(inherited56.OWNER_CATALOG_SQL))
                    ).scalar_one()
                )
                _need(
                    observed["database_head"] == "0056"
                    and observed["role_oid"] == owner_oid
                    and observed["native_definitions_sha256"]
                    == pins.inherited_native_definitions_sha256
                    and observed["native_baseline_admission"] is False
                    and observed["execution_authority"] is False
                )
                connection = await session.connection()
                _need(
                    await connection.scalar(roles56.current55._OWNER_OID_SQL)
                    == owner_oid
                    and tuple(
                        (
                            await connection.execute(
                                core_acl._OWNER_POLICY_SQL, {"owner": owner_oid}
                            )
                        ).one()
                    )
                    == (True, True)
                )
                role_oids = await core_acl._roles(connection, names, owner_oid)
                await _exact_acl_phase(
                    connection,
                    role_oids,
                    owner_oid,
                    writer=writer,
                    custody=custody,
                    frame_reader=frame_reader,
                    granted=False,
                )
                _need(
                    await roles56._identity_at_0056(session, engine.url.username)
                    == identity
                )
        return NativeOwnerPregrant0056Observation(
            native_definitions_sha256=pins.inherited_native_definitions_sha256,
            fitted_raw_owner_catalog_sha256=pins.fitted_raw_owner_catalog_sha256,
            exact_grant_plan_sha256=hashlib.sha256(
                "\n".join(statements).encode("utf-8")
            ).hexdigest(),
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - fixed refusal; never reveal SQL or role.
        raise NativeOwnerPregrant0056Error(
            "native_owner_0056_pregrant_denied"
        ) from None
