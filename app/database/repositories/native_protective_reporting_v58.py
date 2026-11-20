"""Exact original-OCO terminal DATA replay; no submit or source authority.

The caller must hold its independently verified physical58 writer transaction.
The existing outbox projector uses this replay only for the exact new ready
version. No own-close intent, preflight group or repaired report is accepted.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy import select, text

from app.database.models.qualification_ledger import (
    QualificationReservation as Reservation,
)
from app.database.models.qualification_ledger import (
    QualificationReservationTransition as Transition,
)
from app.database.models.submission_reporting import (
    QualificationReportSpool as Spool,
)
from app.database.models.submission_reporting import (
    QualificationSubmissionOutcome as Outcome,
)
from app.database.repositories import native_closed_reporting_v55 as existing
from app.database.repositories import native_postsubmit_v1 as journal
from app.domain import native_protective_report_schema_v58 as schema
from app.domain.source_primitives import decode, sha
from app.trade_evidence import forensics, outbox
from app.trade_evidence import native_protective_forensics_v58 as mapper
from app.trade_evidence.submission_reporting import policy_record, queue
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import native_reconciliation_facts_v1 as facts


def _need(value):
    existing._need(value)


@dataclass(frozen=True, slots=True, repr=False)
class ProtectiveForensicReadbackV58:
    outcome_id: str
    report: mapper.NativeProtectiveForensicsDataV58 = field(repr=False)
    original_manifest_id: str
    final_manifest_id: str
    assessment_sha256: str
    write_metadata: tuple = field(repr=False)
    read_metadata: tuple = field(repr=False)
    execution_authority: bool = field(default=False, init=False)


async def append_owned_original_oco_forensic_v58(binding):
    """Append only while the genuine original recovery source is still LIVE."""
    state, _ = journal._source(binding)
    history, terminal = (
        state["normal_protected_history"],
        state["protective_durable_assessment"],
    )
    _need(
        state["kind"] == "recovery"
        and state["target"].current_state == "uncertain"
        and state.get("protective_forensic_readback") is None
    )
    report = mapper.reconstruct_native_protective_forensics_v58(
        state["target"].facts, history.original_observations, terminal[2]
    )
    hold = decode(history.hold_json, 140000)
    capture = {
        "version": schema.FORENSICS_VERSION,
        "reservation_id": state["reservation_id"],
        "request_sha256": sha(state["target"].request_json.encode()),
        "intent_sha256": state["target"].intent.sha256,
        "hold_outcome_id": history.hold_outcome_id,
        "original_manifest_outcome_id": hold["original_manifest_outcome_id"],
        "final_manifest_outcome_id": terminal[0],
        "assessment_sha256": sha(terminal[1].receipt_json),
        "forensics_json": report.receipt_json.decode(),
        "forensics_sha256": sha(report.receipt_json),
        "risk_hold_retained": True,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    async with journal._transaction(binding) as parts:
        outcome = await journal._append(binding, parts, capture, kind="reconciliation")
        written = parts[-1]
    async with journal._transaction(binding) as parts:
        rows = await journal._outcomes(parts[3], state["target"])
        _need(
            parts[-1][8] != written[8]
            and rows[-1].outcome_id == outcome
            and decode(rows[-1].capture_json.encode(), 140000) == capture
        )
        _, original = journal._read_manifest(
            rows, capture["original_manifest_outcome_id"]
        )
        _, final = journal._read_manifest(rows, terminal[0])
        replay = mapper.reconstruct_native_protective_forensics_v58(
            state["target"].facts, original, final
        )
        _need(replay.receipt_json == report.receipt_json)
        snapshot = ProtectiveForensicReadbackV58(
            outcome,
            report,
            capture["original_manifest_outcome_id"],
            terminal[0],
            capture["assessment_sha256"],
            written,
            parts[-1],
        )
    state["protective_forensic_readback"] = snapshot
    return snapshot


async def replay_protective_terminal_spool_data_v58(session, spool):
    """Full retained rows/raw/forensics replay after exact58 catalog verification.

    A result is local DATA. It does not authenticate a source, mark a remote
    delivery, reserve risk or grant any write to an exchange.
    """
    from app.database.repositories.submission_reporting import spool_identity

    _need(type(spool) is Spool and spool.eligible is True)
    _need(
        await session.scalar(text("SELECT version_num FROM public.alembic_version"))
        == "0058"
    )
    reservation = await session.get(
        Reservation, spool.reservation_id, populate_existing=True
    )
    ready = await session.get(Outcome, spool.outcome_id, populate_existing=True)
    _need(type(reservation) is Reservation and type(ready) is Outcome)
    _need((reservation.state, reservation.state_revision) == ("reconciled_flat", 4))
    _need(ready.reservation_id == reservation.reservation_id)
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
    document = decode(ready.capture_json.encode(), 140000)
    _need(
        document["version"] == schema.READY_VERSION
        and document["report_policy_version"] == schema.REPORT_POLICY_VERSION
        and document["report_policy_sha256"] == schema.REPORT_POLICY_SHA256
    )
    request = wire.checked_request(
        wire.NativeReservationRequestV4.model_validate_json(
            reservation.request_json, strict=True
        )
    )
    intent = await session.scalar(
        select(Transition).filter_by(
            reservation_id=reservation.reservation_id, state_revision=2
        )
    )
    _need(
        type(intent) is Transition
        and intent.id == ready.intent_transition_id
        and sha(intent.evidence_json.encode()) == ready.intent_sha256
    )
    # The original pure journal validator recomputes every canonical document,
    # sequence, digest and intent/exact-order binding; this DTO creates no owner.
    inner = decode(intent.evidence_json.encode(), wire.MAX_NATIVE_BYTES)
    rows = await journal._outcomes(
        session,
        SimpleNamespace(
            reservation_id=reservation.reservation_id,
            request_json=reservation.request_json,
            intent=SimpleNamespace(
                canonical_json=intent.evidence_json, sha256=ready.intent_sha256
            ),
        ),
    )
    _need(
        len(rows) <= 4096
        and sum(
            len(row.capture_json.encode()) + len(row.outcome_json.encode())
            for row in rows
        )
        + len(reservation.request_json.encode())
        <= 16 * 1024 * 1024
    )
    by_id = {row.outcome_id: row for row in rows}
    _need(len(by_id) == len(rows) and by_id[ready.outcome_id] is ready)
    closure = by_id[document["closure_outcome_id"]]
    cleanup = by_id[document["cleanup_outcome_id"]]
    forensic = by_id[document["forensics_outcome_id"]]
    closed, clean, retained = tuple(
        decode(row.capture_json.encode(), 140000)
        for row in (closure, cleanup, forensic)
    )
    _need(
        retained["version"] == schema.FORENSICS_VERSION
        and clean["version"] == schema.CLEANUP_VERSION
        and closed["forensics_outcome_id"] == forensic.outcome_id
        and closed["forensics_sha256"] == spool.report_sha256
        and retained["final_manifest_outcome_id"] == closed["manifest_outcome_id"]
        and clean["forensics_outcome_id"] == forensic.outcome_id
    )
    hold = by_id[closed["hold_outcome_id"]]
    hold_doc = decode(hold.capture_json.encode(), 140000)
    original_filled = by_id[hold_doc["original_filled_outcome_id"]]
    original_doc = decode(original_filled.capture_json.encode(), 140000)
    _need(
        retained["original_manifest_outcome_id"] == original_doc["manifest_outcome_id"]
    )
    target = facts.NativeReconciliationTargetV1(
        request,
        reservation.request_json,
        intent.evidence_json,
        ready.intent_sha256,
        reservation.reservation_id,
        closed["original_main_uid"],
        closed["original_origin"],
        request.credential_session_sha256,
        original_doc["assessment"]["durable_lineage_sha256"],
    )
    _, original = journal._read_manifest(rows, retained["original_manifest_outcome_id"])
    _, final = journal._read_manifest(rows, retained["final_manifest_outcome_id"])
    rebuilt = mapper.reconstruct_native_protective_forensics_v58(
        target, original, final
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
    policy, policy_json, policy_sha = policy_record(policy)
    _need(
        policy_json == spool.policy_json
        and policy_sha == spool.policy_sha256
        and spool.spool_id
        == spool_identity(
            ready.outcome_id,
            spool.report_sha256,
            policy_sha,
            queue(spool.queue_namespace),
        )
        and spool.report_id
        == reservation.report_id
        == rebuilt.analysis.inputs.candidate.report_id
        and spool.created_at == ready.recorded_at
        and rows[0].observation_kind == "initial"
    )
    order = decode(inner["intent_json"].encode(), wire.MAX_NATIVE_BYTES)["exact_order"]
    view = existing.NativeTerminalReportDataV55(
        rebuilt.analysis.inputs.candidate,
        spool.report_id,
        request.origin.candidate.strategy,
        order["clOrdId"],
        request.origin.publication_completed_at,
        rows[0].recorded_at,
    )
    _need(view.evidence_completed_at <= view.completed_at)
    return ready, view, policy
