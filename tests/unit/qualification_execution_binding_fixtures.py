"""Fictional raw account pages and real computational replay; no source trust."""

import hashlib
import json
from datetime import timedelta

from app.trade_qualification import account_capture as accounts
from app.trade_qualification.recheck import evaluate_recorded_recheck
from app.trade_qualification.reservations import (
    ReservationReceipt,
    digest,
    prepare_reservation,
    reservation_id,
)
from app.trade_qualification.submission_intent import SubmissionExecutionBinding
from tests.unit.test_qualification_account_capture import config, ms, plan, row, wire


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def document(value):
    return canonical(value.model_dump(mode="json", round_trip=True))


def consumed_receipt(fixture):
    request = fixture.request
    coverage, _ = prepare_reservation(
        request, fixture.claims, (), observed_at=fixture.now
    )
    return ReservationReceipt(
        scope=request.scope,
        reservation_id=reservation_id(request.scope, request.origin.original_event_key),
        original_event_key=request.origin.original_event_key,
        report_id=request.origin.candidate.report_id,
        instrument_id=request.origin.evidence.pre_evidence.prefix.intent.instrument_id,
        direction=request.origin.candidate.direction,
        correlation_group=request.risk_inputs.instrument.correlation_group,
        request_sha256=digest(request),
        coverage=coverage,
        state="consumed",
        state_revision=2,
        account_revision=1,
        ledger_revision=3,
        created_at=fixture.now,
        updated_at=fixture.now,
        deadline=request.origin.deadline,
    )


def execution_binding(
    fixture,
    *,
    position_mode="net_mode",
    raw_changes=None,
    account_barrier=None,
    account_start_delay=timedelta(),
):
    source, request = fixture.source, fixture.request
    at = (
        request.origin.publication_completed_at
        if account_barrier is None
        else account_barrier
    )
    selected = plan(
        created_at=at.replace(microsecond=0),
        expected_uid=request.scope.account_id,
        history_start=at.replace(microsecond=0) - timedelta(days=7),
        history_end=at.replace(microsecond=0),
    )
    rows = {
        "config_before": [config(uid=request.scope.account_id, posMode=position_mode)],
        "config_after": [config(uid=request.scope.account_id, posMode=position_mode)],
        "account_position_risk": [{"ts": ms(at), "balData": [], "posData": []}],
        "balance": [
            {"uTime": ms(at), "totalEq": "1000", "availEq": "800", "details": []}
        ],
        "account_instruments": [
            row(
                "account_instruments",
                tickSz=str(
                    request.origin.evidence.pre_evidence.policy.prefix.tick_size
                ),
                lotSz=str(request.risk_inputs.instrument.lot_size),
                minSz=str(request.risk_inputs.instrument.min_contracts),
                maxLmtSz=str(request.risk_inputs.instrument.max_contracts),
                ctVal=str(request.risk_inputs.instrument.contract_value),
            )
        ],
    }
    for stream in ("leverage_cross", "leverage_isolated"):
        rows[stream] = [
            row(
                stream,
                lever=str(request.risk_inputs.requested_leverage),
                posSide="net"
                if position_mode == "net_mode"
                else request.origin.candidate.direction,
            )
        ]
    if raw_changes:
        raw_changes(rows)
    observations = []
    identity = None
    pin = accounts.plan_sha256(selected)
    for index, stream in enumerate(accounts.STREAMS):
        start = at + account_start_delay + timedelta(milliseconds=1 + index * 3)
        observed = accounts.parse_demo_account_observation(
            wire(rows.get(stream, [])),
            plan=selected,
            expected_plan_sha256=pin,
            stream=stream,
            request_started_at=start,
            headers_received_at=start + timedelta(milliseconds=1),
            body_completed_at=start + timedelta(milliseconds=2),
            barrier_completed_at=at,
            identity_receipt_sha256=identity,
        )
        observations.append(observed)
        if stream == "config_before":
            identity = observed.receipt_sha256
    packet = accounts.verify_demo_account_records(
        tuple(observations),
        plan=selected,
        expected_plan_sha256=pin,
        barrier_completed_at=at,
    )
    frozen = accounts.freeze_demo_account_packet(packet, expected_plan_sha256=pin)
    assessment = evaluate_recorded_recheck(
        source.original_source.market,
        source.latest_source.market,
        origin=request.origin,
        original_inputs=dict(source.original_inputs),
        quote=source.latest_source.quote,
        reference=source.latest_source.reference,
        current_risk_inputs=request.risk_inputs,
        consumed_event_keys=frozenset(),
        observed_at=fixture.now,
    )
    assert assessment.computational_checks_passed, assessment.code
    original_inputs = {
        name: json.loads(document(value))
        if hasattr(value, "model_dump")
        else sorted(value)
        if name == "consumed_event_keys"
        else value.isoformat()
        for name, value in source.original_inputs.items()
    }
    return SubmissionExecutionBinding(
        account_packet_json=frozen.payload.decode(),
        account_packet_sha256=frozen.sha256,
        account_plan_sha256=pin,
        original_market_json=document(source.original_source.market),
        current_market_json=document(source.latest_source.market),
        original_inputs_json=canonical(original_inputs),
        quote_json=document(source.latest_source.quote),
        reference_json=document(source.latest_source.reference),
        recheck_json=document(assessment),
        consumed_event_keys=(),
    )


def repin_json(value):
    raw = canonical(value)
    return raw, hashlib.sha256(raw.encode()).hexdigest()
