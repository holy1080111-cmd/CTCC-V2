"""Independent physical58 reporting engines and guarded DATA transactions.

No exchange client, Arm, qualification or trading registry is imported here.
Each worker owns three fresh verified-CA SQL engines on its own event loop.
Factories, caller flags and DTOs never replace actual58 role/catalog readback.
"""

import asyncio
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings as configured
from app.core import native_role_identity_0056_v2 as identity
from app.database import native_pg_tls_v1 as tls
from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt as Projection,
)
from app.database.models.submission_reporting import QualificationReportSpool as Spool
from app.database.repositories import native_closed_reporting_0056_v1 as reporting
from app.database.repositories import native_protective_reporting_v58 as protective_data
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain import native_database_profile_v1 as profiles
from app.trade_evidence import outbox
from app.trade_evidence.submission_reporting import pin
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_continuation_guard_v1 as guard

_ROOT = Path(__file__).resolve().parents[2]
_LEASES = WeakKeyDictionary()
_UNKNOWN = {}
_CONTEXT_KEY_V1 = object()


def _need(value):
    if value is not True:
        raise ValueError("native_current58_reporting_unavailable") from None


class OwnedCurrent58ReportingV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


def _settings():
    value = configured.get_settings()
    _need(
        type(value) is configured.Settings
        and value.ctcc_native_demo_manual_lifespan_0056_v1 is True
        and value.trading_mode == "okx_demo"
        and value.submission_outbox_enabled is True
    )
    return value


def _record(cap):
    value = _LEASES.get(cap) if type(cap) is OwnedCurrent58ReportingV1 else None
    _need(type(value) is dict)
    guard.check_saved_native_guard_v1(value["guard"])
    _need(
        value["phase"] == "READY"
        and value["task"] is asyncio.current_task()
        and value["loop"] is asyncio.get_running_loop()
        and value["pid"] == os.getpid()
        and value["thread"] == threading.get_ident()
        and value["settings"] is _settings()
        and value["settings_pin"] == account._v3_data_pin(value["settings"].__dict__)
        and reporting._factory_pins(value["selected"]) == value["pins"]
        and reporting.locked._direct_tls_route(value["pins"][1]) == value["route"]
    )
    return value


async def acquire_current58_reporting_v1():
    """No caller factory, clock, role witness, CA or catalog is accepted."""
    settings = _settings()
    _need(len(_LEASES) + len(_UNKNOWN) < 16)
    context = tls.configured_native_pg_tls_context_v1(
        ca_file=settings.ctcc_native_pg_tls_ca_file,
        ca_sha256=settings.ctcc_native_pg_tls_ca_sha256,
        database_url=settings.database_url,
    )
    _need(context is not None)
    urls = tuple(
        tls.reviewed_native_pg_tls_route_v1(value)
        for value in (
            settings.database_url,
            settings.okx_demo_native_custody_database_url.get_secret_value(),
            settings.okx_demo_native_frame_reader_database_url.get_secret_value(),
        )
    )
    _need(
        len({selectors._database_route(url) for url in urls}) == 1
        and len({url.username for url in urls}) == 3
    )
    engines, kept = [], False
    try:
        for url in urls:
            engines.append(
                create_async_engine(
                    url,
                    poolclass=NullPool,
                    pool_pre_ping=True,
                    connect_args={"ssl": context, "timeout": 5, "command_timeout": 10},
                )
            )
        factories = tuple(
            async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            for engine in engines
        )
        selected = selectors.ConfiguredNativeDemoFactoriesV2(
            *factories, engines[1], engines[2]
        )
        pins = reporting._factory_pins(selected)
        roles = await identity.observe_restricted_role_identity_0056_v2(
            selected, source=_ROOT, profile=profiles.DB0058
        )
        _need(roles.schema == "ctcc.native_only.restricted_role_identity0058.v2")
        cap = object.__new__(OwnedCurrent58ReportingV1)
        value = {
            "settings": settings,
            "settings_pin": account._v3_data_pin(settings.__dict__),
            "selected": selected,
            "pins": pins,
            "route": reporting.locked._direct_tls_route(engines[0]),
            "identity": roles,
            "engines": tuple(engines),
            "task": asyncio.current_task(),
            "loop": asyncio.get_running_loop(),
            "pid": os.getpid(),
            "thread": threading.get_ident(),
            "guard": {},
            "phase": "READY",
        }
        guard.save_native_guard_v1(value["guard"], _NATIVE_GUARD)
        _LEASES[cap] = value
        _record(cap)
        kept = True
        return cap
    finally:
        if not kept:
            cleanup_failure = None
            for engine in engines:
                try:
                    async with asyncio.timeout(5):
                        await engine.dispose()
                except BaseException as error:  # noqa: BLE001 -- retain failed engine custody and attempt every owned engine before propagating shutdown.
                    _UNKNOWN[id(engine)] = tuple(engines)
                    cleanup_failure = cleanup_failure or error
            if cleanup_failure is not None:
                if isinstance(cleanup_failure, asyncio.CancelledError):
                    raise asyncio.CancelledError from None
                _need(False)


def current58_reporting_context_v1(cap, *, key):
    """Private same-task DATA handles, never an exchange/source capability."""
    _need(key is _CONTEXT_KEY_V1)
    value = _record(cap)
    return value["selected"], value["identity"]


@asynccontextmanager
async def current58_reporting_transaction_v1(cap, *, key):
    _need(key is _CONTEXT_KEY_V1)
    value = _record(cap)
    selected, expected = value["selected"], value["identity"]
    roles = await reporting._require_matching_roles(selected, expected)
    _record(cap)
    factory, engine, names, _ = value["pins"]
    async with (
        factory() as session,
        session.begin(),
        reporting._verified_terminal_writer(
            session, engine, roles, names, value["route"], profile=profiles.DB0058
        ),
    ):
        _record(cap)
        yield session
        _record(cap)


async def close_current58_reporting_v1(cap):
    value = _record(cap)
    value["phase"] = "CLOSING"
    failure = None
    for engine in value["engines"]:
        try:
            async with asyncio.timeout(5):
                await engine.dispose()
        except BaseException as error:  # noqa: BLE001 -- partial disposal permanently inhibits this DATA lease.
            _UNKNOWN[id(cap)] = value
            failure = failure or error
    if failure is not None:
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError from None
        _need(False)
    del _LEASES[cap]


async def current58_reporting_ids_v1(cap, *, namespace, after, limit, projected):
    _need(type(projected) is bool and type(limit) is int and 1 <= limit <= 16)
    _need(type(after) is str and (after == "" or pin(after) == after))
    value = _record(cap)
    _, expected_namespace = reporting._configured_projection_queue(value["settings"])
    _need(namespace == expected_namespace)
    query = select(Spool.spool_id).where(
        Spool.eligible.is_(True),
        Spool.queue_namespace == namespace,
        Spool.spool_id > after,
    )
    query = query.join(
        Projection, Projection.spool_id == Spool.spool_id, isouter=True
    ).where(
        Projection.spool_id.is_not(None) if projected else Projection.spool_id.is_(None)
    )
    async with current58_reporting_transaction_v1(cap, key=_CONTEXT_KEY_V1) as session:
        rows = tuple(
            (await session.scalars(query.order_by(Spool.spool_id).limit(limit))).all()
        )
        _record(cap)
    return tuple(pin(value) for value in rows)


async def project_current58_report_v1(cap, *, spool_id, root, namespace):
    value = _record(cap)
    _need(
        reporting._configured_projection_queue(value["settings"]) == (root, namespace)
    )
    result = await reporting._project_terminal_report_0056_v1(
        value["selected"],
        spool_id=pin(spool_id),
        root=root,
        queue_namespace=namespace,
        expected_roles=value["identity"],
    )
    _record(cap)
    _need(result.database_head == "0058" and result.local_outbox_verified is True)
    return result


_REPORTING_DEPENDENCIES_V1 = (
    reporting,
    reporting.writer56,
    reporting.roles56,
    reporting.roles56.current55,
    reporting.witness56,
    reporting.locked,
    reporting.writer56.inherited,
    reporting.writer56.inherited.inherited,
    reporting.writer56.inherited.protective58,
    reporting.writer56.inherited.protective58.schema,
    reporting.writer56.inherited.protective58.submission,
    reporting.writer56.inherited.protective58.report,
    reporting.writer56.inherited.protective58.report_catalog,
    reporting.historical,
    reporting.historical.catalog,
    reporting.historical.report_schema,
    reporting.historical.post_submit,
    reporting.storage,
    protective_data,
    protective_data.journal,
    protective_data.mapper,
    protective_data.mapper.shared,
    protective_data.mapper.shared.core,
    protective_data.mapper.shared.entry,
    protective_data.mapper.shared.execution,
    protective_data.mapper.shared.protective,
    protective_data.mapper.shared.facts,
    protective_data.wire,
)

_NATIVE_GUARD = guard.install_native_guards_v1(
    sys.modules[__name__],
    (
        "acquire_current58_reporting_v1",
        "current58_reporting_context_v1",
        "close_current58_reporting_v1",
        "current58_reporting_ids_v1",
        "project_current58_report_v1",
    ),
    dependencies=(
        configured,
        tls,
        identity,
        selectors,
        profiles,
        outbox,
        account,
        *_REPORTING_DEPENDENCIES_V1,
    ),
)
