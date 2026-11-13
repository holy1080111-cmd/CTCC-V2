"""Historical native terminal DB0018 verifier and existing local-outbox adapter.

It accepts no eligible/source-authenticated flag and mints no trading or remote
delivery authority. Every verification rereads the sealed actual55 catalog,
immutable reservation/entry/close/all3 raw groups/cleanup/forensics/spool.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, text

from app.database.models.qualification_ledger import (
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt,
    QualificationReportSpool,
    QualificationSubmissionOutcome,
)
from app.database.repositories import (
    account_revision_collector_lineage_current55_v1 as lineage55,
)
from app.domain import collector_current_0055_catalog_v1 as catalog
from app.domain import native_closed_report_schema_v55 as report_schema
from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence import forensics, outbox, post_submit
from app.trade_evidence import native_closed_forensics_v55 as forensic
from app.trade_evidence.submission_reporting import (
    SubmissionReportingError,
    policy_record,
    queue,
)
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import native_reconciliation_facts_v1 as facts

_SOURCE_FACTORY_KEY_V55 = object()


@dataclass(frozen=True, slots=True, repr=False)
class NativeTerminalReportDataV55:
    candidate: forensics.TradeCandidate = field(repr=False)
    report_id: str
    strategy: str
    client_order_id: str
    evidence_completed_at: datetime
    completed_at: datetime
    status: str = field(default="acknowledged", init=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class NativeTerminalEnqueueDataV55:
    outbox_envelope_sha256: str
    status: str = field(default="enqueued", init=False)
    durable_outbox_readback: bool = field(default=True, init=False)


def _need(condition):
    if condition is not True:
        raise SubmissionReportingError(
            "native_terminal_report_lineage_unavailable"
        ) from None


async def verify_native_terminal_spool_v55(repository, session, spool):
    """A version selector only routes DATA; actual rows/catalog decide admission."""
    try:
        actual_catalog = await session.scalar(text(catalog.ACTOR_CATALOG_SQL))
        read_only = await session.scalar(
            text("SELECT current_setting('transaction_read_only')")
        )
        verifier = (
            catalog.verify_actual_readonly_writer_catalog
            if read_only == "on"
            else catalog.verify_actual_writer_catalog
        )
        verifier(actual_catalog)
        return await _replay_native_terminal_spool(session, spool)
    except Exception:  # noqa: BLE001 -- source, role, SQL and private data remain redacted.
        raise SubmissionReportingError(
            "native_terminal_report_lineage_unavailable"
        ) from None


async def _replay_native_terminal_spool(session, spool):
    """Replay the inherited row policy after the owning path verifies its catalog."""
    from app.database.repositories import native_postsubmit_v1 as native
    from app.database.repositories.submission_reporting import spool_identity

    try:
        outcome = await session.get(
            QualificationSubmissionOutcome, spool.outcome_id, populate_existing=True
        )
        reservation = await session.get(
            QualificationReservation, spool.reservation_id, populate_existing=True
        )
        _need(
            type(outcome) is QualificationSubmissionOutcome
            and type(reservation) is QualificationReservation
            and outcome.reservation_id == reservation.reservation_id
            and spool.eligible is True
            and reservation.state == "reconciled_flat"
            and reservation.state_revision == 4
        )
        retained = (
            await session.execute(
                text("""
            SELECT count(*),coalesce(sum(octet_length(capture_json)+octet_length(outcome_json)),0)
            FROM public.qualification_submission_outcomes WHERE reservation_id=:rid
        """),
                {"rid": spool.reservation_id},
            )
        ).one()
        _need(
            type(retained[0]) is int
            and 1 <= retained[0] <= 4096
            and type(retained[1]) is int
            and retained[1] + len(reservation.request_json.encode()) <= 16 * 1024 * 1024
        )
        valid = await session.scalar(
            text(
                "SELECT public.qualification_native_report_eligible_v55(:oid,:rid,:report,:policy)"
            ),
            {
                "oid": spool.outcome_id,
                "rid": spool.reservation_id,
                "report": spool.report_sha256,
                "policy": spool.policy_sha256,
            },
        )
        _need(valid is True)
        rows = tuple(
            (
                await session.scalars(
                    select(QualificationSubmissionOutcome)
                    .filter_by(reservation_id=spool.reservation_id)
                    .order_by(QualificationSubmissionOutcome.sequence)
                )
            ).all()
        )
        _need(
            1 <= len(rows) <= 4096
            and len({row.outcome_id for row in rows}) == len(rows)
        )
        by_id = {row.outcome_id: row for row in rows}
        ready = decode(outcome.capture_json.encode(), 140000)
        _need(
            ready["version"] == "ctcc.native_closed_trade_report_ready.v1"
            and ready["report_policy_version"] == report_schema.REPORT_POLICY_VERSION
            and ready["report_policy_sha256"] == report_schema.REPORT_POLICY_SHA256
        )
        closure_row = by_id[ready["closure_outcome_id"]]
        closure = decode(closure_row.capture_json.encode(), 140000)
        cleanup_row = by_id[ready["cleanup_outcome_id"]]
        cleanup = decode(cleanup_row.capture_json.encode(), 140000)
        forensic_row = by_id[ready["forensics_outcome_id"]]
        retained = decode(forensic_row.capture_json.encode(), 140000)
        close_row = by_id[closure["close_intent_outcome_id"]]
        close_document = decode(close_row.capture_json.encode(), 140000)
        original_row = by_id[close_document["original_filled_outcome_id"]]
        original_document = decode(original_row.capture_json.encode(), 140000)
        _need(
            cleanup["version"] == "ctcc.native_closed_trade_cleanup.v1"
            and retained["version"] == "ctcc.native_closed_trade_forensics_readback.v1"
            and retained["original_manifest_outcome_id"]
            == original_document["manifest_outcome_id"]
            and retained["preflight_manifest_outcome_id"]
            == close_document["preflight_manifest_outcome_id"]
            and retained["final_manifest_outcome_id"] == closure["manifest_outcome_id"]
        )
        request = wire.checked_request(
            wire.NativeReservationRequestV4.model_validate_json(
                reservation.request_json, strict=True
            )
        )
        transition = await session.scalar(
            select(QualificationReservationTransition).filter_by(
                reservation_id=reservation.reservation_id, state_revision=2
            )
        )
        _need(
            type(transition) is QualificationReservationTransition
            and transition.id == outcome.intent_transition_id
            and sha(transition.evidence_json.encode()) == outcome.intent_sha256
        )
        target = facts.NativeReconciliationTargetV1(
            request,
            reservation.request_json,
            transition.evidence_json,
            outcome.intent_sha256,
            reservation.reservation_id,
            closure["original_main_uid"],
            closure["original_origin"],
            request.credential_session_sha256,
            original_document["assessment"]["durable_lineage_sha256"],
        )
        _, original = native._read_manifest(
            rows, retained["original_manifest_outcome_id"]
        )
        _, preflight = native._read_manifest(
            rows, retained["preflight_manifest_outcome_id"]
        )
        _, final = native._read_manifest(rows, retained["final_manifest_outcome_id"])
        rebuilt = forensic.reconstruct_native_closed_forensics_v55(
            target, original, preflight, close_row.capture_json.encode(), final
        )
        _need(
            rebuilt.receipt_json
            == retained["forensics_json"].encode()
            == spool.report_bytes
            and sha(rebuilt.receipt_json)
            == retained["forensics_sha256"]
            == spool.report_sha256
        )
        forensics.validate_forensics(
            rebuilt.analysis,
            expected_candidate_sha256=forensics.candidate_sha256(
                rebuilt.analysis.inputs.candidate
            ),
        )
        policy = outbox.OutboxPolicy.model_validate_json(spool.policy_json, strict=True)
        policy, policy_json, policy_sha256 = policy_record(policy)
        _need(
            policy_json == spool.policy_json
            and policy_sha256 == spool.policy_sha256
            and spool.spool_id
            == spool_identity(
                outcome.outcome_id,
                spool.report_sha256,
                policy_sha256,
                queue(spool.queue_namespace),
            )
            and spool.report_id
            == reservation.report_id
            == rebuilt.analysis.inputs.candidate.report_id
            and spool.created_at == outcome.recorded_at
            and rows[0].observation_kind == "initial"
        )
        view = NativeTerminalReportDataV55(
            rebuilt.analysis.inputs.candidate,
            spool.report_id,
            request.origin.candidate.strategy,
            decode(closure["exact_order_json"])["clOrdId"],
            request.origin.publication_completed_at,
            rows[0].recorded_at,
        )
        _need(view.evidence_completed_at <= view.completed_at)
        return outcome, view, policy
    except Exception:  # noqa: BLE001 -- source, role, SQL and private data remain redacted.
        raise SubmissionReportingError(
            "native_terminal_report_lineage_unavailable"
        ) from None


def enqueue_native_terminal_report_v55(root, report, report_sha256, *, policy, clock):
    """Only the existing bounded local queue; this does not start a worker."""
    _need(type(report) is NativeTerminalReportDataV55)
    payload = post_submit._outbox_payload(report, report_sha256)
    try:
        queued = outbox.enqueue(root, payload, policy=policy, clock=clock)
    except outbox.OutboxError as error:
        if error.code != "outbox_enqueue_incomplete":
            raise
        # The projector supplies a replayed spool. Complete only its matching
        # initial journal and preserve the original enqueue time and bytes.
        queued = outbox.recover_incomplete_enqueue(
            root, payload, policy=policy, clock=clock
        )
    view = outbox.validate_outbox_view(queued)
    _need(view.envelope.payload == payload and view.envelope.policy == policy)
    return NativeTerminalEnqueueDataV55(view.envelope.envelope_sha256)


@dataclass(frozen=True, slots=True, repr=False)
class NativeNotionProjectionReadbackV55:
    proof_json: bytes = field(repr=False)
    database_metadata: tuple = field(repr=False)
    session: object = field(repr=False)
    execution_authority: bool = field(default=False, init=False)


async def read_native_notion_projection_v55(binding, *, key):
    """Actual private issuer transaction; no public factory or eligibility flag."""
    from app.database.repositories.submission_reporting import (
        SubmissionReportingRepository,
    )
    from app.trade_evidence import native_notion_delivery_source_v55 as source

    _need(key is source._REPOSITORY_KEY_V55)
    context = source._replay_actual_reporting_factory_v55(
        binding, key=_SOURCE_FACTORY_KEY_V55
    )
    factory, root, report_id, namespace, expected = context
    repository = SubmissionReportingRepository(factory, clock=outbox.actual_utc)
    async with factory() as session, session.begin():
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='5000ms'",
        ):
            await session.execute(text(statement))
            source._replay_actual_reporting_factory_v55(
                binding, key=_SOURCE_FACTORY_KEY_V55
            )
        writable = await session.scalar(
            text("SELECT current_setting('transaction_read_only')")
        )
        source._replay_actual_reporting_factory_v55(
            binding, key=_SOURCE_FACTORY_KEY_V55
        )
        _need(writable == "off")
        rows = tuple(
            (
                await session.scalars(
                    select(QualificationReportSpool)
                    .filter_by(report_id=report_id, queue_namespace=namespace)
                    .limit(2)
                )
            ).all()
        )
        source._replay_actual_reporting_factory_v55(
            binding, key=_SOURCE_FACTORY_KEY_V55
        )
        _need(len(rows) == 1 and type(rows[0]) is QualificationReportSpool)
        spool = rows[0]
        outcome, report, policy = await verify_native_terminal_spool_v55(
            repository, session, spool
        )
        source._replay_actual_reporting_factory_v55(
            binding, key=_SOURCE_FACTORY_KEY_V55
        )
        _need(type(report) is NativeTerminalReportDataV55)
        projection = await session.get(
            QualificationReportProjectionReceipt, spool.spool_id, populate_existing=True
        )
        source._replay_actual_reporting_factory_v55(
            binding, key=_SOURCE_FACTORY_KEY_V55
        )
        _need(
            type(projection) is QualificationReportProjectionReceipt
            and projection.envelope_sha256 == expected
            and projection.report_sha256 == spool.report_sha256
            and projection.policy_sha256 == spool.policy_sha256
            and projection.queue_namespace == namespace
            and projection.verified_at >= spool.created_at
        )
        view = outbox.validate_outbox_view(
            outbox.read_job(root, report_id, clock=outbox.actual_utc)
        )
        _need(
            view.envelope.envelope_sha256 == expected
            and view.envelope.payload
            == post_submit._outbox_payload(report, spool.report_sha256)
            and view.envelope.policy == policy
        )
        metadata = await lineage55._metadata(session)
        source._replay_actual_reporting_factory_v55(
            binding, key=_SOURCE_FACTORY_KEY_V55
        )
        proof = canonical(
            {
                "schema_version": "ctcc.native_historical_notion_projection.v1",
                "spool_id": spool.spool_id,
                "outcome_id": outcome.outcome_id,
                "reservation_id": spool.reservation_id,
                "report_id": report_id,
                "report_sha256": spool.report_sha256,
                "policy_sha256": spool.policy_sha256,
                "policy_json": spool.policy_json,
                "queue_namespace": namespace,
                "created_at": spool.created_at.isoformat(),
                "projection_verified_at": projection.verified_at.isoformat(),
                "envelope_sha256": expected,
                "payload_json": view.envelope.payload.model_dump_json(round_trip=True),
                "execution_authority": False,
                "order_retry_authority": False,
            }
        )
        _need(0 < len(proof) <= 65536)
    source._replay_actual_reporting_factory_v55(binding, key=_SOURCE_FACTORY_KEY_V55)
    return NativeNotionProjectionReadbackV55(proof, tuple(metadata), session)
