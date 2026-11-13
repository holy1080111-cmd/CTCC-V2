"""Resume one report-only append from exact retained DB0056 settlement evidence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select, text

from app.config.settings import get_settings
from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.models.submission_reporting import (
    QualificationReportSpool,
    QualificationSubmissionOutcome,
)
from app.database.repositories import native_closed_reporting_0056_v1 as report56
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.database.repositories.submission_reporting import spool_identity
from app.domain import native_closed_report_schema_v55 as policy55
from app.domain.native_filled_flat_closure_schema_v55 import TRANSITION_REASON
from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence.outbox import OutboxPolicy
from app.trade_evidence.submission_reporting import pin, policy_record, queue
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.reservations import LedgerScope

_CLOSURE = "ctcc.native_owned_filled_flat_closure.v1"
_FORENSICS = "ctcc.native_closed_trade_forensics_readback.v1"
_CLEANUP = "ctcc.native_closed_trade_cleanup.v1"
_READY = "ctcc.native_closed_trade_report_ready.v1"
_CLOCK = text("SELECT pg_catalog.clock_timestamp()")
_XID = text("SELECT pg_catalog.pg_current_xact_id()::text")
_APPEND_FENCE = text("""
    LOCK TABLE public.qualification_account_scopes, public.qualification_reservations,
               public.qualification_reservation_transitions, public.qualification_submission_outcomes,
               public.qualification_report_spool IN ROW EXCLUSIVE MODE NOWAIT
""")


@dataclass(frozen=True, slots=True)
class NativeTerminalReportResumed0056V1:
    cleanup_sha256: str
    report_ready_sha256: str
    spool_sha256: str
    report_sha256: str
    policy_sha256: str
    server_incarnation_sha256: str
    role_route_sha256: str
    schema: Literal["ctcc.db0056.native_terminal_report_resume.v1"] = field(
        default="ctcc.db0056.native_terminal_report_resume.v1", init=False
    )
    database_head: Literal["0056"] = field(default="0056", init=False)
    delivery_authority: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _capture(row):
    raw = row.capture_json.encode()
    value = decode(raw, 140000)
    report56._need(
        type(value) is dict
        and canonical(value) == raw
        and sha(raw) == row.capture_sha256
    )
    return value


def _pending_report(rows, reservation, transition, cleanup_id):
    """Recognize only the exact commit after owned cleanup and before ready."""
    report56._need(
        type(transition) is QualificationReservationTransition
        and transition.reservation_id == reservation.reservation_id
        and transition.state_revision == 4
        and transition.reason_code == TRANSITION_REASON
        and transition.from_state == "uncertain"
        and transition.to_state == "reconciled_flat"
        and transition.occurred_at == reservation.updated_at
    )
    transition_doc = decode(transition.evidence_json.encode(), 140000)
    candidates = {}
    for row in rows:
        capture = _capture(row)
        version = capture.get("version")
        report56._need(version != _READY)
        if version in {_CLOSURE, _FORENSICS, _CLEANUP}:
            report56._need(
                version not in candidates
                and row.observation_kind == "reconciliation"
                and row.status == "acknowledged"
                and capture.get("execution_authority") is False
                and capture.get("order_retry_authority") is False
            )
            candidates[version] = row, capture
    closure, closure_doc = candidates[_CLOSURE]
    forensic, forensic_doc = candidates[_FORENSICS]
    cleanup, cleanup_doc = candidates[_CLEANUP]
    report56._need(
        cleanup is rows[-1]
        and cleanup.outcome_id == cleanup_id
        and closure.sequence + 1 == forensic.sequence == cleanup.sequence - 1
        and closure.recorded_at == reservation.updated_at
        and closure.outcome_id == transition_doc["outcome_id"]
        and closure.outcome_id
        == forensic_doc["closure_outcome_id"]
        == cleanup_doc["closure_outcome_id"]
        and closure_doc["terminal_data_outcome_id"]
        == forensic_doc["terminal_data_outcome_id"]
        == cleanup_doc["terminal_data_outcome_id"]
        and closure_doc["close_intent_outcome_id"]
        == forensic_doc["close_intent_outcome_id"]
        == cleanup_doc["close_intent_outcome_id"]
        and closure_doc["assessment_sha256"] == cleanup_doc["closure_receipt_sha256"]
        and reservation.reservation_id == cleanup_doc["reservation_id"]
        and reservation.request_sha256 == cleanup_doc["request_sha256"]
        and cleanup.intent_sha256 == cleanup_doc["intent_sha256"]
        and closure.intent_sha256 == forensic.intent_sha256 == cleanup.intent_sha256
        and closure.intent_transition_id
        == forensic.intent_transition_id
        == cleanup.intent_transition_id
        and closure.exchange_request_sha256
        == forensic.exchange_request_sha256
        == cleanup.exchange_request_sha256
    )
    report_bytes = forensic_doc["forensics_json"].encode()
    digest = sha(report_bytes)
    report56._need(
        0 < len(report_bytes) <= 32768 and forensic_doc["forensics_sha256"] == digest
    )
    _, policy_json, policy_digest = policy_record(OutboxPolicy())
    return (
        cleanup,
        report_bytes,
        policy_json,
        canonical(
            {
                "version": _READY,
                "reservation_id": reservation.reservation_id,
                "request_sha256": reservation.request_sha256,
                "intent_sha256": cleanup.intent_sha256,
                "closure_outcome_id": closure.outcome_id,
                "cleanup_outcome_id": cleanup.outcome_id,
                "forensics_outcome_id": forensic.outcome_id,
                "report_sha256": digest,
                "policy_sha256": policy_digest,
                "report_policy_version": policy55.REPORT_POLICY_VERSION,
                "report_policy_sha256": policy55.REPORT_POLICY_SHA256,
                "execution_authority": False,
                "order_retry_authority": False,
            }
        ).decode(),
    )


async def _append_report(session, reservation, scope_row, cleanup_id, namespace):
    predicate = (
        QualificationSubmissionOutcome.reservation_id == reservation.reservation_id
    )
    count, size = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(
                        func.octet_length(QualificationSubmissionOutcome.capture_json)
                        + func.octet_length(QualificationSubmissionOutcome.outcome_json)
                    ),
                    0,
                ),
            ).where(predicate)
        )
    ).one()
    report56._need(
        3 <= count < 4096
        and size + len(reservation.request_json.encode()) < 16 * 1024 * 1024
    )
    rows = tuple(
        (
            await session.scalars(
                select(QualificationSubmissionOutcome)
                .where(predicate)
                .order_by(QualificationSubmissionOutcome.sequence)
                .limit(count + 1)
            )
        ).all()
    )
    report56._need(len(rows) == count)
    transition = await session.scalar(
        select(QualificationReservationTransition).filter_by(
            reservation_id=reservation.reservation_id,
            state_revision=4,
        )
    )
    cleanup, report_bytes, policy_json, capture = _pending_report(
        rows, reservation, transition, cleanup_id
    )
    recorded_at = await session.scalar(_CLOCK)
    report56._need(
        type(recorded_at) is datetime
        and recorded_at.tzinfo is not None
        and recorded_at >= scope_row.updated_at
        and recorded_at > cleanup.recorded_at
        and cleanup.ledger_revision <= scope_row.ledger_revision < 2**63 - 1
    )
    recorded_at = recorded_at.astimezone(UTC)
    revision = scope_row.ledger_revision + 1
    scope_row.ledger_revision, scope_row.updated_at = revision, recorded_at
    await session.flush()
    capture_sha = sha(capture.encode())
    outcome_json = canonical(
        {
            "version": "ctcc.native_submission_outcome.v1",
            "reservation_id": reservation.reservation_id,
            "intent_transition_id": cleanup.intent_transition_id,
            "intent_sha256": cleanup.intent_sha256,
            "exchange_request_sha256": cleanup.exchange_request_sha256,
            "request_sha256": reservation.request_sha256,
            "sequence": cleanup.sequence + 1,
            "previous_sha256": cleanup.outcome_id,
            "observation_kind": "reconciliation",
            "status": "acknowledged",
            "capture_sha256": capture_sha,
            "ledger_revision": revision,
            "observed_at": recorded_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "execution_authority": False,
            "order_retry_authority": False,
        }
    ).decode()
    outcome_id, report_sha, policy_sha = (
        sha(outcome_json.encode()),
        sha(report_bytes),
        sha(policy_json.encode()),
    )
    session.add(
        QualificationSubmissionOutcome(
            outcome_id=outcome_id,
            reservation_id=reservation.reservation_id,
            intent_transition_id=cleanup.intent_transition_id,
            sequence=cleanup.sequence + 1,
            previous_sha256=cleanup.outcome_id,
            observation_kind="reconciliation",
            status="acknowledged",
            intent_sha256=cleanup.intent_sha256,
            exchange_request_sha256=cleanup.exchange_request_sha256,
            capture_json=capture,
            capture_sha256=capture_sha,
            outcome_json=outcome_json,
            ledger_revision=revision,
            observed_at=recorded_at,
            recorded_at=recorded_at,
        )
    )
    await session.flush()
    # The installed spool trigger/CHECK replays the full SQL closure and
    # refuses this insert if any transport/resource/control evidence is absent.
    spool = QualificationReportSpool(
        spool_id=spool_identity(outcome_id, report_sha, policy_sha, namespace),
        outcome_id=outcome_id,
        reservation_id=reservation.reservation_id,
        report_id=reservation.report_id,
        report_bytes=report_bytes,
        report_sha256=report_sha,
        policy_json=policy_json,
        policy_sha256=policy_sha,
        queue_namespace=namespace,
        eligible=True,
        created_at=recorded_at,
    )
    session.add(spool)
    await session.flush()
    return spool


async def _read_resumed_report(session, spool, cleanup_id, namespace, roles):
    report56._need(
        type(spool) is QualificationReportSpool
        and spool.eligible is True
        and spool.queue_namespace == namespace
    )
    outcome, report, _ = await report56.historical._replay_native_terminal_spool(
        session, spool
    )
    ready = _capture(outcome)
    report56._need(
        ready["version"] == _READY
        and ready["cleanup_outcome_id"] == cleanup_id
        and report.execution_authority is report.order_retry_authority is False
    )
    return NativeTerminalReportResumed0056V1(
        cleanup_id,
        pin(spool.outcome_id),
        pin(spool.spool_id),
        pin(spool.report_sha256),
        pin(spool.policy_sha256),
        roles.server_incarnation_sha256,
        roles.role_route_sha256,
    )


async def _resume_held(session, cleanup_id, namespace, roles):
    cleanup = await session.get(QualificationSubmissionOutcome, cleanup_id)
    report56._need(
        type(cleanup) is QualificationSubmissionOutcome
        and cleanup.outcome_id == cleanup_id
        and _capture(cleanup).get("version") == _CLEANUP
    )
    reservation = await session.get(
        QualificationReservation, pin(cleanup.reservation_id)
    )
    report56._need(
        type(reservation) is QualificationReservation
        and reservation.environment == "demo"
    )
    scope = LedgerScope(
        environment=reservation.environment,
        account_id=reservation.account_id,
        settlement_currency=reservation.settlement_currency,
    )
    control = await DemoControlRepository._locked(
        session, ControlScope(scope.environment, scope.account_id)
    )
    # Take the exact historical UID and Control locks. A newer valid Control
    # state is left untouched; current arm/stop cannot grant or deny evidence.
    control_state, _ = await DemoControlRepositoryV2._snapshot(session, control)
    report56._need(
        control_state.scope == ControlScope(scope.environment, scope.account_id)
    )
    await session.execute(_APPEND_FENCE)
    scope_row = await session.scalar(
        select(QualificationAccountScope)
        .filter_by(
            environment=scope.environment,
            account_id=scope.account_id,
            settlement_currency=scope.settlement_currency,
        )
        .with_for_update()
    )
    report56._need(type(scope_row) is QualificationAccountScope)
    await session.refresh(reservation)
    report56._need(
        reservation.state == "reconciled_flat"
        and reservation.state_revision == 4
        and (
            reservation.environment,
            reservation.account_id,
            reservation.settlement_currency,
        )
        == (scope.environment, scope.account_id, scope.settlement_currency)
    )
    spool = await session.scalar(
        select(QualificationReportSpool).filter_by(
            reservation_id=reservation.reservation_id,
            eligible=True,
        )
    )
    if spool is None:
        spool = await _append_report(
            session, reservation, scope_row, cleanup_id, namespace
        )
    return await _read_resumed_report(session, spool, cleanup_id, namespace, roles)


async def _resume_native_terminal_report_0056_v1(
    selected, *, cleanup_id, expected_roles
):
    """Append only report data whose owned cleanup is already durable.

    The independent role comparison pins the deployment and grants no startup
    or order authority. The original SQL/report policy decides eligibility.
    """
    try:
        cleanup_id = pin(cleanup_id)
        settings = get_settings()
        namespace = queue(settings.submission_outbox_queue_namespace)
        pins = report56._factory_pins(selected)
        factory, engine, names, _ = pins
        route = report56.locked._direct_tls_route(engine)
        roles = await report56._require_matching_roles(selected, expected_roles)
        async with asyncio.timeout(35):
            async with (
                factory() as session,
                session.begin(),
                report56._verified_terminal_writer(
                    session, engine, roles, names, route
                ) as (catalog, _),
            ):
                report56._need(
                    get_settings() is settings
                    and settings.submission_outbox_queue_namespace == namespace
                    and session.get_bind(mapper=QualificationAccountScope)
                    is engine.sync_engine
                    and all(
                        session.get_bind(clause=statement) is engine.sync_engine
                        for statement in (_XID, _CLOCK, _APPEND_FENCE)
                    )
                )
                written_xid = await session.scalar(_XID)
                report56._need(
                    type(written_xid) is str
                    and written_xid.isascii()
                    and written_xid.isdecimal()
                )
                result = await _resume_held(session, cleanup_id, namespace, roles)
                stable_catalog = (
                    await report56.writer56.observe_restricted_readwrite_writer_0056_v1(
                        session
                    )
                )
                report56._need(stable_catalog == catalog)
                transaction = session.sync_session.get_transaction()
                await report56.locked._compare_held_role_pins(
                    selected,
                    session,
                    expected_roles,
                    stable_catalog,
                    (roles.server_incarnation_sha256, roles.role_route_sha256),
                    lambda: report56._held(session, engine, transaction),
                )
                report56._need(
                    report56._factory_pins(selected) == pins
                    and report56.locked._direct_tls_route(engine) == route
                    and get_settings() is settings
                    and settings.submission_outbox_queue_namespace == namespace
                )
            async with factory() as reader:
                report56._need(reader is not session)
                transaction = await reader.begin()
                try:
                    async with report56._verified_terminal_writer(
                        reader, engine, roles, names, route
                    ):
                        xid = await reader.scalar(_XID)
                        report56._need(
                            type(xid) is str
                            and xid.isascii()
                            and xid.isdecimal()
                            and xid != written_xid
                        )
                        stored = await reader.get(
                            QualificationReportSpool, result.spool_sha256
                        )
                        observed = await _read_resumed_report(
                            reader, stored, cleanup_id, namespace, roles
                        )
                        report56._need(observed == result)
                finally:
                    await transaction.rollback()
        report56._need(
            report56._factory_pins(selected) == pins
            and report56.locked._direct_tls_route(engine) == route
            and get_settings() is settings
            and settings.submission_outbox_queue_namespace == namespace
        )
        return result
    except (asyncio.CancelledError, report56.NativeTerminalReport0056Error):
        raise
    except Exception:  # noqa: BLE001 -- Never expose SQL or private report facts.
        report56._need(False)
