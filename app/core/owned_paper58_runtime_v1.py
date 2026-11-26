"""Recover persistent Paper through real, separately owned PG58 sessions."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import (
    reviewed_native_pg_tls_route_v1,
    verified_native_pg_tls_context_v1,
)
from app.database.repositories.persistence import PersistenceRepository
from app.domain.recovery import RecoveryStatus
from app.paper.engine import PaperBroker
from app.paper.execution_service import PaperExecutionService
from scripts.paper58_actor_acl_v1 import (
    _NAME,
    Paper58ActorAclRefused,
    require_restricted_actor_inventory_data_v1,
)
from scripts.paper58_actor_inventory_sql_v1 import (
    capture_restricted_actor_inventory_data_v1,
)
from scripts.paper58_actor_overlay_v1 import (
    capture_fresh_unpinned_postprovision_owner_v1,
)

_SESSION_LEASE_KEY = 0x4354434350414C31


class Paper58RuntimeRefused(RuntimeError):
    """The refusal omits database URLs, settings, and credentials."""


def _need(condition):
    if condition is not True:
        raise Paper58RuntimeRefused("paper58_runtime_refused") from None


def _startup_failure(stage, error):
    kinds = []
    sqlstate = None
    for _ in range(4):
        name = type(error).__name__
        kinds.append(
            name
            if len(name) <= 80 and name.isascii() and name.isidentifier()
            else "Exception"
        )
        if isinstance(error, Paper58ActorAclRefused) and error.category is not None:
            kinds.append(error.category)
        if isinstance(error, DBAPIError):
            state = getattr(error.orig, "sqlstate", None)
            if (
                type(state) is str
                and len(state) == 5
                and state.isascii()
                and state.isalnum()
                and state == state.upper()
            ):
                sqlstate = state
        error = error.__cause__
        if error is None:
            break
    code = ".".join(("paper58_runtime_startup_refused", stage, *kinds))
    return Paper58RuntimeRefused(code + (f".sqlstate_{sqlstate}" if sqlstate else ""))


@dataclass(frozen=True, slots=True)
class OwnedPaper58Runtime:
    service: PaperExecutionService
    recovery: RecoveryStatus
    _connection: AsyncConnection

    def require_active(self) -> None:
        _need(not self._connection.closed and not self._connection.invalidated)
        self.service.require_confirmed_state()

    async def probe(self) -> None:
        self.require_active()
        async with self._connection.begin():
            _need(await self._connection.scalar(text("SELECT 1")) == 1)


def _routes(owner_database_url, actor_database_url):
    owner = reviewed_native_pg_tls_route_v1(owner_database_url)
    actor = reviewed_native_pg_tls_route_v1(actor_database_url)
    _need(
        (owner.host, owner.port, owner.database)
        == (actor.host, actor.port, actor.database)
    )
    _need(owner.username != actor.username)
    _need(
        all(
            _NAME.fullmatch(name) and not name.startswith("pg_")
            for name in (
                owner.username,
                actor.username,
                actor.database,
            )
        )
    )
    return owner, actor


def _engine(route, tls, *, search_path):
    return create_async_engine(
        route,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "ssl": tls,
            "server_settings": {
                "search_path": search_path,
                "timezone": "UTC",
                "role": "none",
                "synchronous_commit": "on",
                "statement_timeout": "30000",
                "lock_timeout": "1000",
            },
        },
    )


async def _read_only_catalog_transaction(connection):
    await connection.exec_driver_sql(
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY"
    )
    await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")


async def _recover_paper_service(connection):
    sessions = async_sessionmaker(
        bind=connection, expire_on_commit=False, autoflush=False
    )
    repository = PersistenceRepository(sessions)
    service = PaperExecutionService(PaperBroker(), repository)
    result = await service.recover()
    service.require_confirmed_state()
    _need(
        result.initialized is True
        and result.persistence_enabled is True
        and result.last_error is None
        and result.consistent is True
        and result.database_checksum == result.memory_checksum
        and type(result.database_checksum) is str
        and not connection.in_transaction()
    )
    return OwnedPaper58Runtime(service, result, connection)


@asynccontextmanager
async def open_fresh_owned_paper58_runtime_v1(
    *,
    owner_database_url: str,
    actor_database_url: str,
    ca_file: Path,
    ca_sha256: str,
):
    """Open a recovered Paper service on the same restricted PG session throughout.

    The owner connection holds the actual startup observations and source table
    locks through recovery. The separately authenticated actor acquires a
    Paper process lease and retains the same backend for every Paper transaction.
    This path accepts no caller success data, config environment, or provider.
    """
    async with AsyncExitStack() as resources:
        stage = "route_and_tls"
        try:
            owner_route, actor_route = _routes(owner_database_url, actor_database_url)
            tls = verified_native_pg_tls_context_v1(ca_file, ca_sha256)
            owner_engine = _engine(owner_route, tls, search_path="pg_catalog")
            actor_engine = _engine(
                actor_route, tls, search_path="pg_catalog, public, pg_temp"
            )
            resources.push_async_callback(actor_engine.dispose)
            stage = "owner_connection"
            async with owner_engine.connect() as held, held.begin():
                await _read_only_catalog_transaction(held)
                stage = "owner_actor_identity"
                actor_oid = await held.scalar(
                    text("""
SELECT oid::pg_catalog.int8 FROM pg_catalog.pg_roles
 WHERE rolname=CAST(:actor_name AS pg_catalog.name)
   AND rolcanlogin AND NOT rolsuper AND NOT rolinherit AND NOT rolcreaterole
   AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls
"""),
                    {"actor_name": actor_route.username},
                )
                _need(type(actor_oid) is int and actor_oid >= 16384)
                stage = "owner_acl_overlay"
                (
                    _,
                    preprovision,
                    _,
                ) = await capture_fresh_unpinned_postprovision_owner_v1(
                    held,
                    actor_oid=actor_oid,
                    actor_name=actor_route.username,
                )
                stage = "actor_connection"
                actual = await resources.enter_async_context(actor_engine.connect())
                async with actual.begin():
                    await _read_only_catalog_transaction(actual)
                    stage = "actor_catalog_query"
                    inventory = await capture_restricted_actor_inventory_data_v1(actual)
                    stage = "actor_acl_comparison"
                    require_restricted_actor_inventory_data_v1(
                        inventory,
                        preprovision_catalog=preprovision,
                        actor_oid=actor_oid,
                        actor_name=actor_route.username,
                    )
                    stage = "actor_process_lease"
                    _need(
                        await actual.scalar(
                            text("""
SELECT pg_catalog.pg_try_advisory_lock(CAST(:lease_key AS pg_catalog.int8))
"""),
                            {"lease_key": _SESSION_LEASE_KEY},
                        )
                        is True
                    )
                stage = "paper_recovery"
                runtime = await _recover_paper_service(actual)
                stage = "owner_transaction_close"
        except Exception as error:  # noqa: BLE001 - only fixed stages, types and SQLSTATE are returned
            raise _startup_failure(stage, error) from None
        finally:
            if "owner_engine" in locals():
                await owner_engine.dispose()
        try:
            yield runtime
        finally:
            if not actual.closed and not actual.invalidated:
                await runtime.service.persist_now("paper_state_shutdown_checkpoint")
