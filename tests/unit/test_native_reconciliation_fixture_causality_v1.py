"""Synthetic chronology regression; no native account/DB/transport owner."""

import json
from datetime import datetime, timedelta

import pytest

from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import reservations as ledger
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414
)
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414
)


def lineage(target):
    request = target.request
    outer, consumed = bound.replay_control_bound_intent(
        target.intent_json, request, expected_sha256=target.intent_sha256
    )
    raw = json.loads(outer.canonical_json)["reservation_binding_json"]
    reserved, control, event = bound.replay_reserved_evidence(raw, request)
    return reserved, consumed, control, event


def test_declared_fixture_orders_proof_reserve_consume_without_changing_source(
    synthetic, target
):
    source, request = synthetic
    reserved, consumed, _, _ = lineage(target)
    proof_at = datetime.fromisoformat(json.loads(request.r7_proof_json)["observed_at"])
    assert proof_at == source.latest_source.evaluated_at
    assert request.origin.publication_completed_at < proof_at
    assert (
        reserved.created_at
        == reserved.updated_at
        == proof_at + timedelta(microseconds=1)
    )
    assert consumed.created_at == reserved.created_at
    assert (
        consumed.updated_at
        == proof_at + timedelta(microseconds=2)
        < request.origin.deadline
    )


def test_reservation_before_captured_proof_is_still_rejected(target):
    reserved, _, control, event = lineage(target)
    proof_at = datetime.fromisoformat(
        json.loads(target.request.r7_proof_json)["observed_at"]
    )
    before = proof_at - timedelta(microseconds=1)
    values = reserved.model_dump(mode="python", round_trip=True)
    values.update(created_at=before, updated_at=before)
    impossible = ledger.ReservationReceipt.model_validate(values, strict=True)
    with pytest.raises(
        ledger.QualificationLedgerError,
        match="bound_control_native_packet_causality_invalid",
    ):
        bound.build_reserved_evidence(
            target.request, impossible, control, event, checked_at=before
        )
