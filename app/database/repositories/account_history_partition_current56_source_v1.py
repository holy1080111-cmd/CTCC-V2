"""Read DB0056's inherited history partition under the actual restricted writer.

The inherited read procedure takes the UID, row and table locks. Its transaction
must be READ WRITE, but this adapter exposes only the original read and replay.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_writer_observation_0056_v1 as writer56
from app.core.native_locked_demo_control_0056_v1 import _direct_tls_route
from app.core.native_role_witness_0056_v1 import (
    RestrictedNativeRoles0056WitnessV1,
    runtime_witness_0056,
)
from app.core.native_startup_control_observation_0056_v1 import _factory_pins
from app.database.repositories import account_history_partition as original
from app.database.repositories.account_history_partition_current55_v2 import (
    _verify_current55_runtime_role_v2,
)
from app.domain.account_history_partition_v1 import HistoryPartitionScopeV1

_READ = text(original._READ_SQL)
_DB_TX = text(
    "SELECT pg_catalog.pg_backend_pid(), "
    "pg_catalog.pg_current_xact_id_if_assigned()::text"
)
_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
    "SET TRANSACTION READ WRITE",
    *roles56.current55._SETTINGS,
    "SET LOCAL synchronous_commit='on'",
    "SET LOCAL row_security='off'",
)


class Db0056SourcePartitionError(ValueError):
    """Fixed refusal; never export SQL, routes or stored account data."""


def _need(value: bool) -> None:
    if value is not True:
        raise Db0056SourcePartitionError(
            "db0056_source_partition_unavailable"
        ) from None


def _require_session(session, engine, transaction) -> None:
    _need(type(session) is AsyncSession)
    writer56._require_same_transaction(session, engine, transaction)
    _need(
        session.autoflush is False
        and not (session.new or session.dirty or session.deleted)
        and all(
            session.get_bind(clause=statement) is engine.sync_engine
            for statement in (_READ, _DB_TX, roles56.current55._IDENTITY_SQL)
        )
    )


def _verify_partition_grants(catalog) -> None:
    original.verify_account_history_partition_catalog(catalog)
    # Reuse the original inherited read-role checks, then assert absence of
    # the old append privilege. The controlled DB0055 append is never invoked.
    _verify_current55_runtime_role_v2(catalog)
    privileges = {row["name"]: row["can_execute"] for row in catalog["functions"]}
    grants = [
        (row["kind"], row["object"], row["grantee_oid"], row["privilege"])
        for row in catalog["acl_rows"]
        if row["grantee_oid"] != row["owner_oid"]
    ]
    _need(
        privileges.get("account_history_partition_v1_read") is True
        and privileges.get("account_history_partition_v1_append") is False
        and grants
        == [
            (
                "function",
                "account_history_partition_v1_read",
                catalog["role"]["oid"],
                "EXECUTE",
            )
        ]
    )


async def verify_held_partition_catalog_0056_v1(
    factories, session: AsyncSession, expected_roles: RestrictedNativeRoles0056WitnessV1
) -> writer56.NativeWriter0056ObservationV1:
    """Verify the exact selected writer without creating or closing the transaction."""
    try:
        _need(
            type(expected_roles) is RestrictedNativeRoles0056WitnessV1
            and expected_roles.observation.database_head == "0056"
            and expected_roles.observation.admission == "DENY"
            and expected_roles.execution_authority is False
            and expected_roles.startup_admission is False
        )
        pins = _factory_pins(factories)
        _, engine, names, _ = pins
        url_route = _direct_tls_route(engine)
        _need(type(session) is AsyncSession)
        transaction = session.sync_session.get_transaction()
        _require_session(session, engine, transaction)

        first = await writer56._session_identity(session, engine)
        actor = await roles56.current55._identity(session, names[0])
        _require_session(session, engine, transaction)
        _need(actor[3:6] == (first[5], first[6], first[8]))
        _need(
            runtime_witness_0056(actor, names, (url_route,) * 3)
            == (
                expected_roles.server_incarnation_sha256,
                expected_roles.role_route_sha256,
            )
        )
        writer = await writer56.observe_restricted_readwrite_writer_0056_v1(session)
        _require_session(session, engine, transaction)
        await roles56.current55._exact_existing_acl(
            await session.connection(), names, actor[3]
        )
        _require_session(session, engine, transaction)
        await roles56._require_no_effective_demo_fitted_execute(session, names[1:])
        _require_session(session, engine, transaction)
        _need(
            writer.native_definitions_sha256
            == expected_roles.observation.inherited_native_definitions_sha256
            and roles56._granted_inherited_acl_shape_sha256(
                names, writer.native_definitions_sha256
            )
            == expected_roles.observation.granted_inherited_acl_shape_sha256
        )
        catalog = await original.read_account_history_partition_catalog(session)
        _require_session(session, engine, transaction)
        _verify_partition_grants(catalog)
        _need(
            catalog["role"]["oid"] == first[5]
            and catalog["server"]["database"] == first[4]
            and await writer56._session_identity(session, engine) == first
        )
        _require_session(session, engine, transaction)
        _need(
            _factory_pins(factories) == pins and _direct_tls_route(engine) == url_route
        )
        return writer
    except (asyncio.CancelledError, Db0056SourcePartitionError):
        raise
    except Exception:  # noqa: BLE001 -- refuse without revealing DB or account details
        _need(False)


class Db0056SourcePartitionReaderV1:
    """DB0056 source-only readback for one selector-owned factory set."""

    def __init__(self, factories, *, clock, expected_roles):
        self._factories = factories
        self._clock = clock
        self._expected_roles = expected_roles

    async def read(
        self, scope: HistoryPartitionScopeV1
    ) -> original.HistoryPartitionReadbackV1:
        try:
            original.records._scope(scope)
            selected = self._factories
            roles = self._expected_roles
            pins = _factory_pins(selected)
            factory, engine, _, _ = pins
            async with (
                asyncio.timeout(15),
                factory() as session,
                session.begin(),
            ):
                transaction = session.sync_session.get_transaction()
                _require_session(session, engine, transaction)
                for statement in _SETTINGS:
                    await session.execute(text(statement))
                    _require_session(session, engine, transaction)
                before = await verify_held_partition_catalog_0056_v1(
                    selected, session, roles
                )
                raw = await session.scalar(
                    _READ,
                    {
                        "environment": scope.environment,
                        "account_id": scope.account_id,
                        "currency": scope.settlement_currency,
                    },
                )
                _require_session(session, engine, transaction)
                actual = original._readback(raw, scope, original._utc(self._clock()))
                db_tx = tuple((await session.execute(_DB_TX)).one())
                _require_session(session, engine, transaction)
                _need(
                    db_tx == (actual.backend_pid, actual.transaction_id)
                    and actual.account_complete is False
                    and actual.execution_authority is False
                )
                after = await verify_held_partition_catalog_0056_v1(
                    selected, session, roles
                )
                _need(before == after)
                _require_session(session, engine, transaction)
            _need(_factory_pins(selected) == pins)
            return actual
        except (asyncio.CancelledError, Db0056SourcePartitionError):
            raise
        except Exception:  # noqa: BLE001 -- refuse without revealing DB or account details
            _need(False)
