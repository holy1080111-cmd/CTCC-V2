"""Read the actual DB0056 frame catalog in a caller-held restricted transaction.

This is a source observation, not a startup, account-completeness, or trade
admission. The caller owns the frame transaction and its lifetime. No frame
session is substituted, and all catalog values come from actual SQL.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_role_preflight_v1 as roles55
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as factories55,
)
from app.database.repositories import (
    collector_current_0056_multirole_catalog_v1 as pair56,
)
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain import account_history_original_frame_v1 as frame_policy
from app.domain import account_history_partition_catalog_v1 as partition_policy
from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
from app.domain.source_primitives import canonical


class NativeFrameReader0056Error(ValueError):
    """Closed refusal without a role, catalog document, or SQL value."""


_FRAME_CONTEXT = text("""
SELECT current_setting('transaction_isolation')::text,
       current_setting('transaction_read_only')::text
""")
_WRITER_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
    "SET TRANSACTION READ ONLY",
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='1000ms'",
)


def _deny() -> None:
    raise NativeFrameReader0056Error("native_frame_reader_0056_unavailable") from None


def _need(value: bool) -> None:
    if value is not True:
        _deny()


async def _execute_one(session: AsyncSession, fence, statement):
    fence.check()
    value = (await session.execute(statement)).scalar_one()
    fence.check()
    return value


async def _execute_row(session: AsyncSession, fence, statement):
    fence.check()
    value = tuple((await session.execute(statement)).one())
    fence.check()
    return value


async def _identity(session: AsyncSession, fence, role_name: str):
    fence.check()
    value = await roles56._identity_at_0056(session, role_name)
    fence.check()
    return value


def _checked_factories(frame_session, writer_factory, custody_factory, frame_factory):
    factories = (writer_factory, custody_factory, frame_factory)
    routes = tuple(factories55._factory_route(factory) for factory in factories)
    names = tuple(route[3] for route in routes)
    url_routes = tuple(
        selectors._database_route(factory.kw["bind"].url) for factory in factories
    )
    _need(
        frame_session.in_transaction()
        and frame_session.get_nested_transaction() is None
        and not frame_session.new
        and not frame_session.dirty
        and not frame_session.deleted
        and len(set(names)) == 3
        and len(set(url_routes)) == 1
        and routes[0][2] == routes[1][2] == routes[2][2]
        and frame_session.get_bind() is routes[2][0][1].sync_engine
        and custody_factory.kw["bind"] is routes[1][0][1]
        and frame_factory.kw["bind"] is routes[2][0][1]
        and writer_factory.kw["bind"] is routes[0][0][1]
    )
    return routes, names


async def verify_held_frame_catalog_0056_v1(
    frame_session: AsyncSession,
    fence,
    writer_factory,
    custody_factory,
    frame_factory,
) -> bytes:
    """Verify DB0056 historical frame lineage without replacing frame transaction.

    The frame session must already be in its source-owned READ COMMITTED,
    READ WRITE transaction. A distinct restricted writer transaction checks
    the exact existing 42 grants and fitted EXECUTE exclusion. The returned
    canonical bytes contain only catalog observations and explicit denial.
    """
    writer = None
    try:
        _need(
            type(frame_session) is AsyncSession
            and frame_session.in_transaction()
            and frame_session.get_nested_transaction() is None
            and frame_session.get_bind().dialect.name == "postgresql"
            and callable(fence.check)
        )
        frame_transaction = frame_session.sync_session.get_transaction()
        _need(frame_transaction is not None)
        fence.check()
        routes, names = _checked_factories(
            frame_session, writer_factory, custody_factory, frame_factory
        )

        class BoundFence:
            def check(self):
                fence.check()
                _need(frame_session.sync_session.get_transaction() is frame_transaction)
                _need(
                    _checked_factories(
                        frame_session, writer_factory, custody_factory, frame_factory
                    )[0]
                    == routes
                )

        bound = BoundFence()
        context = await _execute_row(frame_session, bound, _FRAME_CONTEXT)
        _need(context == ("read committed", "off"))
        frame_identity = await _identity(frame_session, bound, names[2])
        partition = await _execute_one(
            frame_session, bound, text(partition_policy.CATALOG_SQL)
        )
        bound.check()
        partition_policy.verify_account_history_partition_catalog(partition)
        bound.check()
        original = await _execute_one(
            frame_session, bound, text(frame_policy.CATALOG_SQL)
        )
        bound.check()
        frame_policy.verify_catalog(
            original, partition, approved_collector_role_oid=frame_identity[3]
        )
        bound.check()
        frame_graph = await _execute_one(
            frame_session, bound, text(inherited.ACTOR_CATALOG_SQL)
        )
        writer = writer_factory()
        _need(
            type(writer) is AsyncSession
            and writer is not frame_session
            and writer.get_bind() is routes[0][0][1].sync_engine
            and not writer.new
            and not writer.dirty
            and not writer.deleted
        )
        bound.check()
        await writer.begin()
        bound.check()
        for statement in _WRITER_SETTINGS:
            bound.check()
            await writer.execute(text(statement))
            bound.check()
        writer_identity = await _identity(writer, bound, names[0])
        _need(
            writer_identity[3] != frame_identity[3]
            and writer_identity[4:] == frame_identity[4:]
        )
        owner_oid = await _execute_one(writer, bound, roles55._OWNER_OID_SQL)
        _need(type(owner_oid) is int and owner_oid > 0)
        bound.check()
        await roles55._exact_existing_acl(
            await writer.connection(), names, writer_identity[3]
        )
        bound.check()
        await roles56._require_no_effective_demo_fitted_execute(writer, names)
        bound.check()
        writer_graph = await _execute_one(
            writer, bound, text(inherited.ACTOR_CATALOG_SQL)
        )
        _need(partition["tables"][0]["owner_oid"] == owner_oid)
        bound.check()
        pair = pair56.verify_0056_pair(
            frame_graph,
            writer_graph,
            raw_name=names[2],
            writer_name=names[0],
            approved_raw_role_oid=frame_identity[3],
            approved_writer_role_oid=writer_identity[3],
            parent_owner_oid=owner_oid,
        )
        _need(
            type(pair) is dict
            and pair["schema"]
            == "ctcc.collector_current0056.observed_multirole_catalog.v1"
            and pair["database_head"] == "0056"
            and pair["actual_separate_roles_observed"] is True
            and pair["native_baseline_admission"] is False
            and pair["account_complete"] is False
            and pair["execution_authority"] is False
            and roles56._is_digest(pair["native_definitions_sha256"])
        )
        acl_sha = roles56._granted_inherited_acl_shape_sha256(
            names, pair["native_definitions_sha256"]
        )
        _need(roles56._is_digest(acl_sha))
        _need(await _identity(writer, bound, names[0]) == writer_identity)
        _need(await _identity(frame_session, bound, names[2]) == frame_identity)
        bound.check()
        result = canonical(
            {
                "schema": "ctcc.native_only.held_frame_catalog_0056.v1",
                "database_head": "0056",
                "partition": partition,
                "frame": original,
                "current0056": {
                    "profile": pair,
                    "raw": frame_graph,
                    "writer": writer_graph,
                },
                "granted_inherited_acl_shape_sha256": acl_sha,
                "account_complete": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        bound.check()
        await writer.commit()
        bound.check()
        return result
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- never emit SQL, role or private catalog.
        _deny()
    finally:
        if writer is not None:
            await factories55._close_owned(writer)
