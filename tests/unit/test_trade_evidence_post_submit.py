"""Synthetic post-submit reporting, not permission to submit or proof of a fill.

Candidate and consumed-reservation records are local claims; no DB, account,
private API, startup or order callback is used. The memory root verifies protocol
only. Native POSIX publication is explicitly not Windows filesystem proof.
"""

import asyncio
import hashlib
import json
import os
from datetime import timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext

import httpx
import pytest
from pydantic import ValidationError, create_model

from app.domain.okx_demo import OkxDemoOrderAcknowledgement, OkxDemoWriteResult
from app.trade_evidence import outbox
from app.trade_evidence import post_submit as module
from app.trade_evidence.forensics import candidate_sha256
from app.trade_evidence.notion_adapter import NotionDeliveryAdapter
from app.trade_evidence.outbox_worker import OutboxWorkerPolicy
from app.trade_qualification.reservations import (
    LedgerScope,
    ReservationReceipt,
    RiskCoverage,
    ScenarioOperands,
    digest,
    exact_coverage,
    reservation_id,
)
from tests.unit.test_trade_evidence_notion_adapter import (
    TOKEN,
    client_for,
    listing,
    page,
    schema,
)
from tests.unit.test_trade_evidence_notion_adapter import (
    adapter as notion_adapter,
)
from tests.unit.test_trade_evidence_outbox import Clock, MemoryBackend
from tests.unit.test_trade_forensics import at
from tests.unit.test_trade_forensics import fixture as forensics_fixture

D = Decimal
CLIENT_ORDER_ID = "ctccSyntheticSubmit001"
EXCHANGE_ORDER_ID = "123456789012345678"
EVENT = "3" * 64


@pytest.fixture
def memory(monkeypatch):
    backend = MemoryBackend()
    monkeypatch.setattr(outbox, "_root_context", backend.context)
    return backend


@pytest.fixture
def clock():
    return Clock(at(12))


def candidate(direction="long"):
    return forensics_fixture(direction)[0].candidate


def reservation(value, **updates):
    scope = LedgerScope(
        account_id=value.account_id, settlement_currency=value.settlement_currency
    )
    sample = ScenarioOperands(
        entry=value.entry,
        stop_loss=value.stop_loss,
        cost_per_base=D("0.1"),
        contracts=value.planned_contracts,
        contract_value=value.contract_value_base,
        leverage=5,
    )
    coverage = RiskCoverage(
        candidate=sample, execution=sample, **exact_coverage(sample, sample)
    )
    values = {
        "scope": scope,
        "reservation_id": reservation_id(scope, EVENT),
        "original_event_key": EVENT,
        "report_id": value.report_id,
        "instrument_id": value.instrument_id,
        "direction": value.direction,
        "correlation_group": "synthetic-crypto",
        "request_sha256": "4" * 64,
        "coverage": coverage,
        "state": "consumed",
        "state_revision": 2,
        "account_revision": 1,
        "ledger_revision": 2,
        "created_at": at(1),
        "updated_at": at(9),
        "deadline": at(25),
    }
    return ReservationReceipt(**(values | updates))


def acknowledgement(**updates):
    return OkxDemoOrderAcknowledgement(
        **(
            {
                "order_id": EXCHANGE_ORDER_ID,
                "client_order_id": CLIENT_ORDER_ID,
                "exchange_code": "0",
                "exchange_message": "synthetic response only",
            }
            | updates
        )
    )


def write_result(**updates):
    return OkxDemoWriteResult(
        **(
            {
                "action": "place_order",
                "acknowledged": True,
                "acknowledgement": acknowledgement(),
                "completed_at": at(12),
                "protection_confirmed": False,
                "reconciled": False,
            }
            | updates
        )
    )


def inputs(direction="long"):
    value = candidate(direction)
    receipt = reservation(value)
    return value, {
        "expected_candidate_sha256": candidate_sha256(value),
        "reservation_before_submit": receipt,
        "expected_reservation_sha256": digest(receipt),
        "expected_original_event_key": EVENT,
        "evidence_completed_at": at(8),
        "strategy": "trend_pullback",
        "client_order_id": CLIENT_ORDER_ID,
        "submit_started_at": at(10),
        "completed_at": at(12),
        "write_result": write_result(),
    }


def report(direction="long", **updates):
    value, values = inputs(direction)
    return module.build_submission_report(value, **(values | updates))


@pytest.mark.parametrize("direction", ["long", "short"])
def test_acknowledged_report_pins_exact_preexisting_candidate_and_consumed_reservation(
    direction,
):
    value, values = inputs(direction)
    result = module.build_submission_report(value, **values)
    assert result.status == "acknowledged" and result.report_id == value.report_id
    assert (
        result.candidate == value
        and result.candidate_sha256 == values["expected_candidate_sha256"]
    )
    assert result.reservation_before_submit == values["reservation_before_submit"]
    assert result.reservation_sha256 == values["expected_reservation_sha256"]
    assert result.original_event_key == EVENT
    assert result.evidence_completed_at == at(8)
    assert result.exchange_order_id == EXCHANGE_ORDER_ID and result.exchange_code == "0"
    assert result.execution_authority is result.source_authenticity_verified is False
    assert result.durability_not_verified is True
    assert not result.requires_upstream_reconciliation
    assert not hasattr(result, "filled") and not hasattr(result, "protection_confirmed")
    assert module.validate_submission_report(result) == result


def test_explicit_rejection_requires_matching_client_nonzero_code_and_no_exchange_order():
    result = report(
        write_result=write_result(
            acknowledged=False,
            acknowledgement=acknowledgement(order_id="", exchange_code="51000"),
        )
    )
    assert result.status == "rejected" and not result.requires_upstream_reconciliation
    assert result.exchange_order_id is None and result.exchange_code == "51000"


@pytest.mark.parametrize(
    "value",
    [
        None,
        write_result(acknowledgement=None),
        write_result(acknowledged=False, acknowledgement=None),
        write_result(acknowledged=False),
        write_result(acknowledgement=acknowledgement(exchange_code="51000")),
        write_result(acknowledgement=acknowledgement(order_id="")),
        write_result(acknowledgement=acknowledgement(client_order_id="otherClient")),
        write_result(acknowledgement=acknowledgement(client_order_id=None)),
        write_result(
            acknowledged=False,
            acknowledgement=acknowledgement(
                order_id="", exchange_code="51000", client_order_id="otherClient"
            ),
        ),
    ],
)
def test_unknown_or_contradictory_write_acknowledgement_never_becomes_success(value):
    result = report(write_result=value)
    assert result.status == "uncertain" and result.requires_upstream_reconciliation
    assert result.execution_authority is False


class Unreadable:
    def __iter__(self):
        pytest.fail("ignored legacy metadata must not be traversed")

    def __repr__(self):
        pytest.fail("ignored legacy metadata must not be serialized")

    def model_dump(self, *args, **kwargs):
        pytest.fail("ignored legacy metadata must not be serialized")


@pytest.mark.parametrize(
    "field",
    [
        "order",
        "protection_confirmed",
        "protection_client_order_id",
        "exchange_data",
        "warnings",
        "reconciled",
    ],
)
def test_legacy_order_protection_and_raw_fields_are_not_consulted_or_serialized(field):
    dirty = write_result().model_copy(update={field: Unreadable()})
    result = report(write_result=dirty)
    assert result.status == "acknowledged"
    raw = module.freeze_submission_report(result)
    assert b"Unreadable" not in raw and b"synthetic response only" not in raw


@pytest.mark.parametrize("action", ["cancel_order", "close_position", "set_leverage"])
def test_non_place_order_actions_are_invalid_not_post_submit_evidence(action):
    with pytest.raises(module.PostSubmitError):
        report(write_result=write_result(action=action))


@pytest.mark.parametrize(
    "field",
    [
        "expected_candidate_sha256",
        "expected_reservation_sha256",
        "expected_original_event_key",
    ],
)
@pytest.mark.parametrize("value", ["0" * 64, " " + "1" * 64, "A" * 64, None, True])
def test_independent_expected_pins_must_be_exact_and_match(field, value):
    with pytest.raises(module.PostSubmitError):
        report(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "reserved"),
        ("state", "uncertain"),
        ("state", "reconciled_flat"),
        ("report_id", "foreign_report"),
        ("instrument_id", "ETH-USDT-SWAP"),
        ("direction", "short"),
        ("updated_at", at(11)),
        ("original_event_key", "5" * 64),
    ],
)
def test_even_rehashed_reservation_scope_or_pre_submit_state_mismatch_is_rejected(
    field, value
):
    value_candidate, values = inputs()
    receipt = reservation(value_candidate, **{field: value})
    values.update(
        reservation_before_submit=receipt, expected_reservation_sha256=digest(receipt)
    )
    with pytest.raises(module.PostSubmitError):
        module.build_submission_report(value_candidate, **values)


@pytest.mark.parametrize(
    "field,value", [("account_id", "987654321"), ("settlement_currency", "USDC")]
)
def test_receipt_account_and_settlement_scope_must_match_candidate_even_with_fresh_hash(
    field, value
):
    value_candidate, values = inputs()
    scope = LedgerScope(
        **(
            {
                "account_id": value_candidate.account_id,
                "settlement_currency": value_candidate.settlement_currency,
            }
            | {field: value}
        )
    )
    receipt = reservation(
        value_candidate, scope=scope, reservation_id=reservation_id(scope, EVENT)
    )
    values.update(
        reservation_before_submit=receipt, expected_reservation_sha256=digest(receipt)
    )
    with pytest.raises(module.PostSubmitError):
        module.build_submission_report(value_candidate, **values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry", D("101")),
        ("stop_loss", D("94")),
        ("contracts", D("11")),
        ("contract_value", D("0.2")),
    ],
)
def test_rehashed_valid_coverage_cannot_describe_different_candidate_geometry(
    field, value
):
    value_candidate, values = inputs()
    receipt = values["reservation_before_submit"]
    sample = ScenarioOperands.model_validate(
        receipt.coverage.candidate.model_dump() | {field: value}, strict=True
    )
    coverage = RiskCoverage(
        candidate=sample, execution=sample, **exact_coverage(sample, sample)
    )
    receipt = reservation(value_candidate, coverage=coverage)
    values.update(
        reservation_before_submit=receipt, expected_reservation_sha256=digest(receipt)
    )
    with pytest.raises(module.PostSubmitError):
        module.build_submission_report(value_candidate, **values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_completed_at", at(-1)),
        ("evidence_completed_at", at(11)),
        ("submit_started_at", at(7)),
        ("completed_at", at(9)),
        ("evidence_completed_at", at(8).replace(tzinfo=None)),
        ("submit_started_at", at(10).isoformat()),
        ("completed_at", True),
    ],
)
def test_explicit_source_evidence_and_submission_clock_order_is_required(field, value):
    with pytest.raises(module.PostSubmitError):
        report(**{field: value})


def test_late_submission_is_still_recorded_and_never_hidden_by_entry_deadline_gate():
    result = report(
        submit_started_at=at(30),
        completed_at=at(32),
        write_result=write_result(completed_at=at(32)),
    )
    assert (
        result.status == "acknowledged"
        and result.submit_started_at > result.candidate.entry_deadline
    )
    assert result.execution_authority is False


@pytest.mark.parametrize("direction", ["long", "short"])
def test_submission_json_is_canonical_frozen_and_roundtrips_with_expected_hash(
    direction,
):
    result = report(direction)
    raw = module.freeze_submission_report(result)
    assert type(raw) is bytes
    pin = hashlib.sha256(raw).hexdigest()
    assert module.verify_submission_report(raw, expected_sha256=pin) == result
    assert (
        module.DemoSubmissionReport.model_validate_json(
            result.model_dump_json(), strict=True
        )
        == result
    )
    with pytest.raises(ValidationError):
        result.status = "rejected"
    with pytest.raises(ValidationError):
        result.candidate.entry = D("200")
    with pytest.raises(ValidationError):
        result.reservation_before_submit.state = "reserved"


@pytest.mark.parametrize(
    "mode", ["wrong_hash", "whitespace", "duplicate", "tamper", "type"]
)
def test_submission_verify_rejects_wrong_pin_noncanonical_or_rehashed_inconsistent_bytes(
    mode,
):
    raw = module.freeze_submission_report(report())
    pin = hashlib.sha256(raw).hexdigest()
    if mode == "wrong_hash":
        pin = "0" * 64
    elif mode == "whitespace":
        raw += b" "
    elif mode == "duplicate":
        raw = b'{"status":"acknowledged",' + raw[1:]
    elif mode == "tamper":
        data = json.loads(raw)
        data["candidate_sha256"] = "0" * 64
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    else:
        raw = raw.decode()
    if mode not in {"wrong_hash", "type"}:
        pin = hashlib.sha256(raw).hexdigest()
    with pytest.raises(module.PostSubmitError):
        module.verify_submission_report(raw, expected_sha256=pin)


@pytest.mark.parametrize("status", ["rejected", "uncertain"])
def test_non_acknowledged_submission_never_calls_outbox_or_clock(memory, status):
    value = (
        None
        if status == "uncertain"
        else write_result(
            acknowledged=False,
            acknowledgement=acknowledgement(order_id="", exchange_code="51000"),
        )
    )
    source = report(write_result=value)

    def never():
        pytest.fail("rejected or unknown submissions do not enqueue")

    result = module.enqueue_post_submit(memory.root, source, clock=never)
    assert result.submission_status == status and result.status == "deferred"
    assert result.code == f"post_submit_{status}"
    assert result.outbox_status is None and result.outbox_envelope_sha256 is None
    assert not result.durable_outbox_readback and not memory.operations
    assert result.requires_upstream_reconciliation == (status == "uncertain")


def test_acknowledged_submission_enqueues_immutable_metadata_with_real_readback(
    memory, clock
):
    source = report()
    first = module.enqueue_post_submit(memory.root, source, clock=clock)
    assert first.status == "enqueued" and first.code == "post_submit_enqueued"
    assert first.outbox_status == "queued" and first.durable_outbox_readback
    view = outbox.read_job(memory.root, source.report_id, clock=clock)
    assert first.outbox_envelope_sha256 == view.envelope.envelope_sha256
    assert (
        view.envelope.payload.source_sha256 == source.candidate.original_source_sha256
    )
    assert (
        view.envelope.payload.evidence_report_sha256
        == source.candidate.original_evidence_sha256
    )
    assert view.envelope.payload.evidence_completed_at == source.evidence_completed_at
    assert view.envelope.payload.submission_receipt_sha256 == first.report_sha256
    assert (
        first.report_sha256
        == hashlib.sha256(module.freeze_submission_report(source)).hexdigest()
    )
    assert first.durability_not_verified is True
    assert (
        first.execution_authority
        is first.order_retry_authority
        is first.notion_delivery_verified
        is False
    )
    before = dict(memory.files)
    clock.advance(1)
    duplicate = module.enqueue_post_submit(memory.root, source, clock=clock)
    assert duplicate.status == "enqueued" and memory.files == before


def test_colliding_report_never_replaces_queued_bytes(memory, clock):
    source = report()
    module.enqueue_post_submit(memory.root, source, clock=clock)
    before = dict(memory.files)
    changed = report(
        write_result=write_result(acknowledgement=acknowledgement(order_id="999999"))
    )
    result = module.enqueue_post_submit(memory.root, changed, clock=clock)
    assert result.status == "failed" and result.code == "post_submit_enqueue_failed"
    assert not result.durable_outbox_readback and memory.files == before


@pytest.mark.parametrize("failure", ["before", "after", "readback"])
def test_outbox_publication_fault_never_claims_durable_readback_or_order_retry(
    memory, clock, failure
):
    source = report()
    path = (source.report_id + ".json",)
    faults = {
        "before": memory.fail_publish_before,
        "after": memory.fail_publish_after,
        "readback": memory.fail_read_after_publish,
    }
    faults[failure].add(path)
    result = module.enqueue_post_submit(memory.root, source, clock=clock)
    assert result.status == "failed" and result.code == "post_submit_enqueue_failed"
    assert (
        result.execution_authority
        is result.order_retry_authority
        is result.notion_delivery_verified
        is False
    )
    assert not result.durable_outbox_readback
    assert (path in memory.files) == (failure != "before")


@pytest.mark.parametrize(
    "location",
    [
        "candidate",
        "reservation",
        "scope",
        "coverage",
        "candidate_operands",
        "execution_operands",
    ],
)
@pytest.mark.parametrize("mutation", ["extra", "private", "subclass"])
def test_original_nested_candidate_and_reservation_objects_are_checked_before_serialization(
    location, mutation
):
    value, values = inputs()
    receipt = values["reservation_before_submit"]
    targets = {
        "candidate": value,
        "reservation": receipt,
        "scope": receipt.scope,
        "coverage": receipt.coverage,
        "candidate_operands": receipt.coverage.candidate,
        "execution_operands": receipt.coverage.execution,
    }
    target = targets[location]
    if mutation == "extra":
        target = target.model_copy(update={"hidden": "synthetic only"})
    elif mutation == "private":
        object.__setattr__(target, "__pydantic_private__", {})
    else:
        kind = create_model(
            "DeclaredHidden", hidden=(str, "synthetic only"), __base__=type(target)
        )
        target = kind(**target.model_dump())
    if location == "candidate":
        value = target
    elif location == "reservation":
        receipt = target
    elif location in {"scope", "coverage"}:
        receipt = receipt.model_copy(update={location: target})
    else:
        coverage = receipt.coverage.model_copy(
            update={
                "candidate" if location == "candidate_operands" else "execution": target
            }
        )
        receipt = receipt.model_copy(update={"coverage": coverage})
    values["reservation_before_submit"] = receipt
    with pytest.raises(module.PostSubmitError):
        module.build_submission_report(value, **values)


@pytest.mark.parametrize("location", ["write_result", "acknowledgement"])
@pytest.mark.parametrize("mutation", ["extra", "private", "subclass"])
def test_legacy_result_and_ack_shape_cannot_hide_declared_or_private_fields(
    location, mutation
):
    result = write_result()
    target = result if location == "write_result" else result.acknowledgement
    if mutation == "extra":
        target = target.model_copy(update={"hidden": True})
    elif mutation == "private":
        object.__setattr__(target, "__pydantic_private__", {})
    else:
        kind = create_model(
            "HiddenWrite", hidden=(str, "synthetic"), __base__=type(target)
        )
        target = kind(**target.model_dump())
    if location == "write_result":
        result = target
    else:
        result = result.model_copy(update={"acknowledgement": target})
    with pytest.raises(module.PostSubmitError):
        report(write_result=result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry", "100"),
        ("entry", True),
        ("entry", D("NaN")),
        ("planned_contracts", D("Infinity")),
        ("recorded_at", at(11)),
        ("recorded_at", at(0).isoformat()),
        ("environment", "live"),
    ],
)
def test_candidate_model_copy_invalid_scalars_are_not_repaired(field, value):
    original, values = inputs()
    with pytest.raises(module.PostSubmitError):
        module.build_submission_report(
            original.model_copy(update={field: value}), **values
        )


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_model_copy_acknowledgement_boolean_requires_exact_type(value):
    with pytest.raises(module.PostSubmitError):
        report(write_result=write_result().model_copy(update={"acknowledged": value}))


@pytest.mark.parametrize("when", [at(9), at(13), at(12).replace(tzinfo=None)])
def test_write_result_clock_cannot_precede_call_or_exceed_explicit_completion(when):
    with pytest.raises(module.PostSubmitError):
        report(write_result=write_result().model_copy(update={"completed_at": when}))


def test_write_result_completion_allows_explicit_observation_overhead():
    result = report(completed_at=at(13))
    assert result.completed_at == at(13) and result.status == "acknowledged"


@pytest.mark.parametrize(
    "field", ["evidence_completed_at", "submit_started_at", "completed_at"]
)
def test_python_datetime_strings_are_not_accepted_by_json_roundtrip_fix(field):
    result = report()
    values = dict(result.__dict__)
    values[field] = values[field].isoformat()
    with pytest.raises(ValueError):
        module.DemoSubmissionReport.model_validate(values, strict=True)


@pytest.mark.parametrize("location", ["report", "candidate", "reservation"])
@pytest.mark.parametrize("mutation", ["extra", "private", "subclass"])
def test_submission_output_revalidation_refuses_tamper_at_every_boundary(
    location, mutation
):
    result = report()
    target = {
        "report": result,
        "candidate": result.candidate,
        "reservation": result.reservation_before_submit,
    }[location]
    if mutation == "extra":
        target = target.model_copy(update={"hidden": True})
    elif mutation == "private":
        object.__setattr__(target, "__pydantic_private__", {})
    else:
        kind = create_model(
            "HiddenOutput", hidden=(str, "synthetic"), __base__=type(target)
        )
        target = kind.model_construct(**dict(target.__dict__))
    if location == "report":
        result = target
    else:
        result = result.model_copy(
            update={
                "candidate"
                if location == "candidate"
                else "reservation_before_submit": target
            }
        )
    with pytest.raises(module.PostSubmitError):
        module.validate_submission_report(result)
    with pytest.raises(module.PostSubmitError):
        module.freeze_submission_report(result)


@pytest.mark.parametrize(
    "field",
    ["execution_authority", "source_authenticity_verified", "durability_not_verified"],
)
@pytest.mark.parametrize("value", [0, 1, "false"])
def test_submission_authority_flags_are_not_coerced_from_other_scalar_types(
    field, value
):
    result = report().model_copy(update={field: value})
    with pytest.raises(module.PostSubmitError):
        module.validate_submission_report(result)


def test_decimal_hostile_context_does_not_change_report_bytes_or_pins():
    value, values = inputs("short")
    expected = module.freeze_submission_report(
        module.build_submission_report(value, **values)
    )
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = context.traps[Rounded] = True
        result = module.build_submission_report(value, **values)
        assert module.freeze_submission_report(result) == expected


def test_offset_times_are_normalized_without_changing_submission_pin():
    expected = report()
    zone = timezone(timedelta(hours=8))
    result = report(
        evidence_completed_at=at(8).astimezone(zone),
        submit_started_at=at(10).astimezone(zone),
        completed_at=at(12).astimezone(zone),
        write_result=write_result(completed_at=at(12).astimezone(zone)),
    )
    assert result == expected


@pytest.mark.parametrize(
    "field", ["evidence_completed_at", "submit_started_at", "completed_at"]
)
def test_json_timestamps_without_offsets_are_not_repaired_to_utc(field):
    data = json.loads(module.freeze_submission_report(report()))
    data[field] = data[field].removesuffix("Z")
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(module.PostSubmitError):
        module.verify_submission_report(
            raw, expected_sha256=hashlib.sha256(raw).hexdigest()
        )


@pytest.mark.parametrize(
    "field,value", [("durable_record_only", 1), ("all_fill_prices_covered", 0)]
)
@pytest.mark.parametrize("transport", ["python", "json"])
def test_nested_ledger_literal_flags_reject_integer_substitution_before_coercion(
    field, value, transport
):
    source = report()
    if transport == "json":
        data = json.loads(module.freeze_submission_report(source))
        target = data["reservation_before_submit"]
        if field == "all_fill_prices_covered":
            target = target["coverage"]
        target[field] = value
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(ValueError):
            module.DemoSubmissionReport.model_validate_json(raw, strict=True)
        with pytest.raises(module.PostSubmitError):
            module.verify_submission_report(
                raw, expected_sha256=hashlib.sha256(raw).hexdigest()
            )
    else:
        receipt = source.reservation_before_submit
        if field == "all_fill_prices_covered":
            receipt = receipt.model_copy(
                update={"coverage": receipt.coverage.model_copy(update={field: value})}
            )
        else:
            receipt = receipt.model_copy(update={field: value})
        source = source.model_copy(update={"reservation_before_submit": receipt})
        with pytest.raises(module.PostSubmitError):
            module.validate_submission_report(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_authority", 0),
        ("order_retry_authority", 0),
        ("notion_delivery_verified", 0),
        ("durability_not_verified", 1),
        ("durable_outbox_readback", 1),
        ("requires_upstream_reconciliation", 0),
        ("order_retry_authority", True),
        ("notion_delivery_verified", True),
    ],
)
def test_enqueue_output_flags_cannot_claim_order_or_notion_authority(
    memory, clock, field, value
):
    result = module.enqueue_post_submit(memory.root, report(), clock=clock)
    assert (
        module.PostSubmitReportingResult.model_validate_json(
            result.model_dump_json(), strict=True
        )
        == result
    )
    with pytest.raises(ValueError):
        module.PostSubmitReportingResult.model_validate(
            result.model_copy(update={field: value}), strict=True
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "enqueued"),
        ("durable_outbox_readback", True),
        ("outbox_status", "delivered"),
    ],
)
def test_unknown_submission_output_cannot_be_relabelled_as_confirmed_enqueue(
    memory, field, value
):
    result = module.enqueue_post_submit(memory.root, report(write_result=None))
    with pytest.raises(ValueError):
        module.PostSubmitReportingResult.model_validate(
            result.model_copy(update={field: value}), strict=True
        )


def notion_client_for_root(root, clock, *, failure=None):
    calls = []
    created_page = None

    def handler(request):
        nonlocal created_page
        calls.append(request)
        source = report()
        view = outbox.read_job(root, source.report_id, clock=clock)
        assert view.status == "dispatching"
        if request.method == "GET":
            data = schema()
        elif request.url.path == "/v1/pages":
            if failure == "create_timeout":
                raise httpx.ReadTimeout("synthetic create outcome unknown")
            created_page = page(view.envelope.payload, outbox._claim(view))
            data = created_page
        else:
            data = listing() if created_page is None else listing(created_page)
        return httpx.Response(200, json=data)

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ), calls


@pytest.mark.asyncio
async def test_real_memory_post_submit_worker_and_notion_adapter_are_separate_from_enqueue(
    memory, clock
):
    source = report()
    queued = module.enqueue_post_submit(memory.root, source, clock=clock)
    before = dict(memory.files)
    client, calls = notion_client_for_root(memory.root, clock)
    async with client:
        adapter = notion_adapter(client, clock=clock)
        assert type(adapter) is NotionDeliveryAdapter and calls == []
        result = await module.run_post_submit_pass(
            memory.root,
            report_ids=(source.report_id,),
            worker_id="reporter",
            adapter=adapter,
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
    assert result.items[0].outbox_status == "delivered" and len(calls) == 4
    assert sum(call.url.path == "/v1/pages" for call in calls) == 1
    assert all(call.url.host == "api.notion.com" for call in calls)
    assert all(memory.files[path] == data for path, data in before.items())
    assert (
        queued.notion_delivery_verified is False and result.execution_authority is False
    )
    assert TOKEN.get_secret_value() not in b"".join(memory.files.values()).decode()


@pytest.mark.asyncio
async def test_notion_create_timeout_is_uncertain_without_reenqueue_or_order_retry(
    memory, clock
):
    source = report()
    module.enqueue_post_submit(memory.root, source, clock=clock)
    client, calls = notion_client_for_root(memory.root, clock, failure="create_timeout")
    async with client:
        adapter = notion_adapter(client, clock=clock)
        first = await module.run_post_submit_pass(
            memory.root,
            report_ids=(source.report_id,),
            worker_id="reporter",
            adapter=adapter,
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
        assert first.items[0].outbox_status == "uncertain"
        count = len(calls)
        before = dict(memory.files)
        again = await module.run_post_submit_pass(
            memory.root,
            report_ids=(source.report_id,),
            worker_id="reporter",
            adapter=adapter,
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
        assert again.items[0].status == "skipped" and len(calls) == count
        assert memory.files == before
    assert sum(call.url.path == "/v1/pages" for call in calls) == 1


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self, entered):
        self.entered = entered
        self.closed = 0

    async def __aiter__(self):
        self.entered.set()
        await asyncio.Future()
        yield b""

    async def aclose(self):
        self.closed += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [True, False])
async def test_real_notion_wait_cancel_or_worker_timeout_preserves_unknown_submission_reporting(
    memory, clock, cancel
):
    source = report()
    module.enqueue_post_submit(memory.root, source, clock=clock)
    entered = asyncio.Event()
    stream = WaitingStream(entered)
    client, calls = client_for(
        [
            httpx.Response(
                200, headers={"Content-Type": "application/json"}, stream=stream
            )
        ]
    )
    async with client:
        adapter = notion_adapter(client, clock=clock)
        task = asyncio.create_task(
            module.run_post_submit_pass(
                memory.root,
                report_ids=(source.report_id,),
                worker_id="reporter",
                adapter=adapter,
                policy=OutboxWorkerPolicy(pass_timeout_seconds=1),
                clock=clock,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()
        else:
            result = await asyncio.wait_for(task, timeout=2)
            assert result.status == "deadline_reached"
        view = outbox.read_job(memory.root, source.report_id, clock=clock)
        assert view.status == "uncertain" and stream.closed == 1
        before = dict(memory.files)
        again = await module.run_post_submit_pass(
            memory.root,
            report_ids=(source.report_id,),
            worker_id="reporter",
            adapter=adapter,
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
        assert again.items[0].status == "skipped" and memory.files == before
    assert len(calls) == 1 and all(call.url.path != "/v1/pages" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [None, lambda *_: None, object()])
async def test_reporting_pass_never_accepts_arbitrary_callback_as_an_order_or_notion_adapter(
    memory, clock, adapter
):
    with pytest.raises(
        module.PostSubmitError, match="^post_submit_notion_adapter_required$"
    ):
        await module.run_post_submit_pass(
            memory.root,
            report_ids=(candidate().report_id,),
            worker_id="reporter",
            adapter=adapter,
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
    assert not memory.operations and not clock.calls


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name == "nt",
    reason="Genuine POSIX post-submit journal + Mock Notion proof; not Windows publication evidence",
)
async def test_native_posix_post_submit_and_separate_worker_publish_and_verify_real_journal(
    tmp_path, clock
):
    root = tmp_path / "trusted-post-submit"
    root.mkdir()
    source = report()
    queued = module.enqueue_post_submit(root, source, clock=clock)
    assert queued.status == "enqueued" and queued.durable_outbox_readback
    client, calls = notion_client_for_root(root, clock)
    async with client:
        result = await module.run_post_submit_pass(
            root,
            report_ids=(source.report_id,),
            worker_id="reporter",
            adapter=notion_adapter(client, clock=clock),
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
    assert result.items[0].outbox_status == "delivered" and len(calls) == 4
    assert outbox.read_job(root, source.report_id, clock=clock).status == "delivered"
    assert len(list((root / f"{source.report_id}.state").iterdir())) == 4


@pytest.mark.parametrize("mode", ["overlimit", "duplicate", "nonfinite"])
def test_direct_model_json_is_bounded_and_rejects_duplicate_or_nonfinite_records(mode):
    raw = module.freeze_submission_report(report())
    if mode == "overlimit":
        raw += b" " * (module.MAX_REPORT_BYTES + 1)
    elif mode == "duplicate":
        raw = b'{"status":"acknowledged",' + raw[1:]
    else:
        raw = b'{"completed_at":NaN,' + raw[1:]
    with pytest.raises(ValueError):
        module.DemoSubmissionReport.model_validate_json(raw, strict=True)


@pytest.mark.parametrize("boundary", ["validate", "enqueue"])
def test_invalid_report_keeps_safely_available_report_identity_without_any_storage(
    memory, clock, boundary
):
    source = report()
    dirty = source.model_copy(update={"candidate_sha256": "0" * 64})
    with pytest.raises(module.PostSubmitError) as captured:
        if boundary == "validate":
            module.validate_submission_report(dirty)
        else:
            module.enqueue_post_submit(memory.root, dirty, clock=clock)
    assert captured.value.report_id == source.report_id
    assert not memory.operations and not clock.calls


@pytest.mark.parametrize("exchange_code", ["00", "00001"])
def test_noncanonical_exchange_codes_are_uncertain_not_explicit_rejections(
    exchange_code,
):
    source = report(
        write_result=write_result(
            acknowledged=False,
            acknowledgement=acknowledgement(order_id="", exchange_code=exchange_code),
        )
    )
    assert source.status == "uncertain" and source.requires_upstream_reconciliation
    assert source.exchange_code is None and source.exchange_order_id is None
