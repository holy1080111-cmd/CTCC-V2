"""Verify one historical terminal report through the actual restricted DB0056 writer."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core import native_locked_demo_control_0056_v1 as locked
from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_role_witness_0056_v1 as witness56
from app.core import native_writer_observation_0056_v1 as writer56
from app.core.native_role_identity_0056_v2 import NativeRoleIdentity0056V2
from app.core.native_role_preflight_v1 import _exact_existing_acl
from app.core.native_startup_control_observation_0056_v1 import _factory_pins
from app.database.models.qualification_ledger import (
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt,
    QualificationReportSpool,
    QualificationSubmissionOutcome,
)
from app.database.repositories import native_closed_reporting_v55 as historical
from app.database.repositories.qualification_native_checkpoint_v2 import (
    _profile_for_original_roles_v1,
)
from app.database.repositories.submission_reporting import _projection_lock_key
from app.domain import native_database_profile_v1 as profiles
from app.trade_evidence import outbox, post_submit, storage
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from app.trade_evidence.submission_reporting import pin, queue

_SOURCE = Path(__file__).resolve().parents[3]
_MODEL_SET = (
    QualificationReservation,
    QualificationReservationTransition,
    QualificationReportProjectionReceipt,
    QualificationReportSpool,
    QualificationSubmissionOutcome,
)
_ELIGIBILITY_ACCESS = text("""
SELECT pg_catalog.has_function_privilege(
    CURRENT_USER,
    pg_catalog.to_regprocedure(
        'public.qualification_native_report_eligible_v55(text,text,text,text)'
    ),
    'EXECUTE'
)
""")
_PROJECTION_LOCK = text(
    "SELECT pg_catalog.pg_try_advisory_xact_lock(CAST(:lock_key AS bigint))"
)


class NativeTerminalReport0056Error(ValueError):
    """Fixed refusal without report contents, account identity or SQL details."""


@dataclass(frozen=True, slots=True)
class NativeTerminalLineage0056V1:
    spool_sha256: str
    report_sha256: str
    policy_sha256: str
    projection_receipt_envelope_sha256: str | None
    server_incarnation_sha256: str
    role_route_sha256: str
    schema: str = field(default="ctcc.db0056.native_terminal_lineage_observation.v1")
    database_head: Literal["0056", "0057", "0058"] = field(default="0056")
    local_outbox_verified: Literal[False] = field(default=False, init=False)
    delivery_authority: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


@dataclass(frozen=True, slots=True)
class NativeTerminalLocalProjection0056V1:
    spool_sha256: str
    report_sha256: str
    policy_sha256: str
    projection_receipt_envelope_sha256: str
    server_incarnation_sha256: str
    role_route_sha256: str
    schema: str = field(default="ctcc.db0056.native_terminal_local_projection.v1")
    database_head: Literal["0056", "0057", "0058"] = field(default="0056")
    local_outbox_verified: Literal[True] = field(default=True, init=False)
    delivery_authority: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(condition: bool) -> None:
    if condition is not True:
        raise NativeTerminalReport0056Error(
            "native_terminal_report_0056_unavailable"
        ) from None


def _held(session, engine, transaction):
    writer56._require_same_transaction(session, engine, transaction)
    _need(
        session.autoflush is False
        and not (session.new or session.dirty or session.deleted)
        and all(
            session.get_bind(mapper=model) is engine.sync_engine for model in _MODEL_SET
        )
        and session.get_bind(clause=_ELIGIBILITY_ACCESS) is engine.sync_engine
    )


def _holds_observed_roles(
    roles, catalog, actor, names, route, *, profile=profiles.DB0056
) -> None:
    _need(
        type(roles) is witness56.RestrictedNativeRoles0056WitnessV1
        and _profile_for_original_roles_v1(roles) is profiles.checked(profile)
        and type(catalog) is writer56.NativeWriter0056ObservationV1
        and catalog.database_head == profiles.head(profile)
        and catalog.schema
        == profiles.schema(
            profile, "ctcc.current0056.restricted_readwrite_writer_observation.v1"
        )
        and catalog.admission == "DENY"
        and catalog.native_baseline_admission is False
        and catalog.account_complete
        is catalog.execution_authority
        is catalog.startup_admission
        is False
        and roles.observation.admission == "DENY"
        and roles.startup_admission is roles.execution_authority is False
        and catalog.native_definitions_sha256
        == roles.observation.inherited_native_definitions_sha256
        and witness56.runtime_witness_0056(
            actor, names, (route,) * 3, **profiles.arguments(profile)
        )
        == (roles.server_incarnation_sha256, roles.role_route_sha256)
    )


def _read_projection_digest(receipt, spool) -> str | None:
    if receipt is None:
        return None
    _need(
        type(receipt) is QualificationReportProjectionReceipt
        and receipt.spool_id == spool.spool_id
        and receipt.report_sha256 == spool.report_sha256
        and receipt.policy_sha256 == spool.policy_sha256
        and receipt.queue_namespace == spool.queue_namespace
        and receipt.verified_at >= spool.created_at
    )
    return pin(receipt.envelope_sha256)


@asynccontextmanager
async def _verified_terminal_writer(
    session, engine, roles, names, route, *, profile=profiles.DB0056
):
    _need(_profile_for_original_roles_v1(roles) is profiles.checked(profile))
    _need(type(session) is AsyncSession and not session.identity_map)
    transaction = session.sync_session.get_transaction()
    _held(session, engine, transaction)
    # The inherited writer check requires READ COMMITTED. The eligibility
    # function takes the account and row locks in this same transaction.
    await session.execute(
        text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE")
    )
    for setting in locked._LOCAL_SETTINGS:
        await session.execute(text(setting))
    await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
    _held(session, engine, transaction)
    first = await writer56._session_identity(
        session, engine, **profiles.arguments(profile)
    )
    actor = await roles56._identity_at_0056(
        session, names[0], **profiles.arguments(profile)
    )
    _need(actor[3:6] == (first[5], first[6], first[8]))
    catalog = await writer56.observe_restricted_readwrite_writer_0056_v1(
        session, **profiles.arguments(profile)
    )
    _holds_observed_roles(
        roles, catalog, actor, names, route, **profiles.arguments(profile)
    )
    if profile is profiles.DB0056:
        await _exact_existing_acl(await session.connection(), names, actor[3])
    else:
        await profiles.require_exact_acl(
            await session.connection(),
            names,
            actor[3],
            profile=profile,
            read_only=False,
        )
    await roles56._require_no_effective_demo_fitted_execute(session, names)
    _need(await session.scalar(_ELIGIBILITY_ACCESS) is True)
    _held(session, engine, transaction)
    yield catalog, actor
    _need(
        await writer56._session_identity(session, engine, **profiles.arguments(profile))
        == first
    )
    _need(
        await roles56._identity_at_0056(
            session, names[0], **profiles.arguments(profile)
        )
        == actor
    )
    _held(session, engine, transaction)


async def _read_held(session, engine, roles, names, route, spool_id, *, lock=False):
    profile = _profile_for_original_roles_v1(roles)
    transaction = session.sync_session.get_transaction()
    async with _verified_terminal_writer(
        session, engine, roles, names, route, **profiles.arguments(profile)
    ):
        if lock:
            _need(session.get_bind(clause=_PROJECTION_LOCK) is engine.sync_engine)
            _need(
                await session.scalar(
                    _PROJECTION_LOCK, {"lock_key": _projection_lock_key(spool_id)}
                )
                is True
            )
        spool = await session.get(QualificationReportSpool, spool_id)
        _need(type(spool) is QualificationReportSpool and spool.spool_id == spool_id)
        _held(session, engine, transaction)
        _, report, policy = await historical._replay_native_terminal_spool(
            session, spool
        )
        _held(session, engine, transaction)
        _need(
            type(report) is historical.NativeTerminalReportDataV55
            and report.execution_authority is report.order_retry_authority is False
        )
        projection = await session.get(QualificationReportProjectionReceipt, spool_id)
        envelope = _read_projection_digest(projection, spool)
    return spool, report, policy, envelope


async def _observe_held(session, engine, roles, names, route, spool_id):
    profile = _profile_for_original_roles_v1(roles)
    spool, _, _, envelope = await _read_held(
        session, engine, roles, names, route, spool_id
    )
    return NativeTerminalLineage0056V1(
        spool_id,
        pin(spool.report_sha256),
        pin(spool.policy_sha256),
        envelope,
        roles.server_incarnation_sha256,
        roles.role_route_sha256,
        schema=profiles.schema(
            profile, "ctcc.db0056.native_terminal_lineage_observation.v1"
        ),
        database_head=profiles.head(profile),
    )


async def observe_terminal_lineage_0056_v1(selected, *, spool_id: str):
    """Use a fresh actor and owning rollback-only transaction. Return opaque data."""
    try:
        requested = pin(spool_id)
        pins = _factory_pins(selected)
        factory, engine, names, _ = pins
        route = locked._direct_tls_route(engine)
        profile = await _profile_from_selected_head(selected)
        roles = await witness56.observe_restricted_native_roles_0056_witness_v1(
            selected, source=_SOURCE, **profiles.arguments(profile)
        )
        _need(_profile_for_original_roles_v1(roles) is profile)
        async with factory() as session:
            transaction = await session.begin()
            try:
                async with asyncio.timeout(20):
                    result = await _observe_held(
                        session, engine, roles, names, route, requested
                    )
            finally:
                async with asyncio.timeout(5):
                    await transaction.rollback()
            _need(not session.in_transaction())
        _need(
            _factory_pins(selected) == pins
            and locked._direct_tls_route(engine) == route
        )
        return result
    except (asyncio.CancelledError, NativeTerminalReport0056Error):
        raise
    except Exception:  # noqa: BLE001 -- Never expose historical account or SQL facts.
        _need(False)


def _read_local_envelope(root, spool, report, policy, expected):
    view = outbox.validate_outbox_view(outbox.read_job(root, report.report_id))
    _need(
        view.envelope.envelope_sha256 == expected
        and view.envelope.policy == policy
        and view.envelope.payload
        == post_submit._outbox_payload(report, spool.report_sha256)
    )


def _configured_projection_queue(settings):
    root = storage._root_path(
        require_private_evidence_base_v2(settings.submission_outbox_root)
    )
    if settings.notion_outbox_root:
        _need(storage._root_path(Path(settings.notion_outbox_root)) == root)
    return root, queue(settings.submission_outbox_queue_namespace)


def _profile_from_expected_identity(expected):
    _need(type(expected) is NativeRoleIdentity0056V2)
    _need(
        type(expected.schema) is str
        and type(expected.database_oid) is type(expected.owner_oid) is int
        and expected.database_oid > 0
        and expected.owner_oid > 0
        and all(
            type(value) is str
            and len(value) == 64
            and all(c in "0123456789abcdef" for c in value)
            for value in (
                expected.stable_identity_sha256,
                expected.server_incarnation_sha256,
                expected.role_route_sha256,
                expected.inherited_native_definitions_sha256,
                expected.granted_inherited_acl_shape_sha256,
            )
        )
    )
    profile = (
        profiles.DB0056
        if expected.schema == "ctcc.native_only.restricted_role_identity0056.v2"
        else profiles.DB0057
        if expected.schema == "ctcc.native_only.restricted_role_identity0057.v2"
        else profiles.DB0058
        if expected.schema == "ctcc.native_only.restricted_role_identity0058.v2"
        else None
    )
    _need(
        profile is not None
        and expected.deployment_reference_authenticated is False
        and expected.startup_admission is expected.execution_authority is False
        and expected.admission == "DENY"
    )
    return profile


async def _profile_from_selected_head(selected):
    """Read actual bounded metadata only; the full role/catalog replay follows."""
    pins = _factory_pins(selected)
    factory, engine, _, _ = pins
    route = locked._direct_tls_route(engine)
    query = text("SELECT version_num FROM public.alembic_version LIMIT 2")
    async with asyncio.timeout(5), factory() as session:
        _need(
            type(session) is AsyncSession
            and session.get_bind(clause=query) is engine.sync_engine
        )
        transaction = await session.begin()
        try:
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
            rows = (await session.execute(query)).all()
            _need(
                len(rows) == 1
                and type(rows[0][0]) is str
                and rows[0][0] in ("0056", "0057", "0058")
            )
        finally:
            await transaction.rollback()
    _need(_factory_pins(selected) == pins and locked._direct_tls_route(engine) == route)
    return profiles.from_head(rows[0][0])


async def _require_matching_roles(selected, expected):
    profile = _profile_from_expected_identity(expected)
    actual = await witness56.observe_restricted_native_roles_0056_witness_v1(
        selected, source=_SOURCE, **profiles.arguments(profile)
    )
    _need(
        type(expected) is NativeRoleIdentity0056V2
        and _profile_for_original_roles_v1(actual) is profile
        and actual.server_incarnation_sha256 == expected.server_incarnation_sha256
        and actual.role_route_sha256 == expected.role_route_sha256
        and actual.observation.inherited_native_definitions_sha256
        == expected.inherited_native_definitions_sha256
        and actual.observation.granted_inherited_acl_shape_sha256
        == expected.granted_inherited_acl_shape_sha256
    )
    return actual


async def _project_terminal_report_0056_v1(
    selected,
    *,
    spool_id: str,
    root: Path,
    queue_namespace: str,
    expected_roles: NativeRoleIdentity0056V2,
) -> NativeTerminalLocalProjection0056V1:
    """Project one immutable spool, then replay the committed receipt and local pair."""
    try:
        requested = pin(spool_id)
        namespace = queue(queue_namespace)
        settings = get_settings()
        _need(_configured_projection_queue(settings) == (root, namespace))
        pins = _factory_pins(selected)
        factory, engine, names, _ = pins
        route = locked._direct_tls_route(engine)
        roles = await _require_matching_roles(selected, expected_roles)
        profile = _profile_for_original_roles_v1(roles)
        async with asyncio.timeout(30):
            async with factory() as session, session.begin():
                spool, report, policy, envelope = await _read_held(
                    session, engine, roles, names, route, requested, lock=True
                )
                _need(spool.eligible is True and spool.queue_namespace == namespace)
                _need(
                    get_settings() is settings
                    and _configured_projection_queue(settings) == (root, namespace)
                )
                report_digest, policy_digest = (
                    pin(spool.report_sha256),
                    pin(spool.policy_sha256),
                )
                if envelope is None:
                    published = historical.enqueue_native_terminal_report_v55(
                        root,
                        report,
                        report_digest,
                        policy=policy,
                        clock=outbox.actual_utc,
                    )
                    envelope = pin(published.outbox_envelope_sha256)
                    _read_local_envelope(root, spool, report, policy, envelope)
                    verified_at = outbox.actual_utc()
                    _need(verified_at >= spool.created_at)
                    session.add(
                        QualificationReportProjectionReceipt(
                            spool_id=requested,
                            report_sha256=report_digest,
                            policy_sha256=policy_digest,
                            queue_namespace=namespace,
                            envelope_sha256=envelope,
                            verified_at=verified_at,
                        )
                    )
                    await session.flush()
                else:
                    # An existing receipt cannot authorize replacement of a lost pair.
                    _read_local_envelope(root, spool, report, policy, envelope)
            async with factory() as readback:
                _need(readback is not session)
                transaction = await readback.begin()
                try:
                    spool, report, policy, observed = await _read_held(
                        readback, engine, roles, names, route, requested
                    )
                    _need(
                        spool.eligible is True
                        and (
                            spool.report_sha256,
                            spool.policy_sha256,
                            spool.queue_namespace,
                            observed,
                        )
                        == (report_digest, policy_digest, namespace, envelope)
                    )
                    _read_local_envelope(root, spool, report, policy, envelope)
                finally:
                    await transaction.rollback()
        _need(
            _factory_pins(selected) == pins
            and locked._direct_tls_route(engine) == route
            and get_settings() is settings
            and _configured_projection_queue(settings) == (root, namespace)
        )
        return NativeTerminalLocalProjection0056V1(
            requested,
            report_digest,
            policy_digest,
            envelope,
            roles.server_incarnation_sha256,
            roles.role_route_sha256,
            schema=profiles.schema(
                profile, "ctcc.db0056.native_terminal_local_projection.v1"
            ),
            database_head=profiles.head(profile),
        )
    except (asyncio.CancelledError, NativeTerminalReport0056Error):
        raise
    except Exception:  # noqa: BLE001 -- A durable pair/commit may need readback after failure.
        _need(False)
