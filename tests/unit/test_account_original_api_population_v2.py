"""Original synthetic wire/chain replay; no account/native/DB acceptance credit."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_original_position_path_v1 as position
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from tests.unit import test_account_original_balance_measurement_v1 as original
from tests.unit.test_qualification_account_capture import ms, row

pytestmark = pytest.mark.asyncio


async def _source(monkeypatch, mutate=None):
    if mutate is None:
        return await original.fresh_v5(monkeypatch)
    original_pages = original.acquisition.flat_pages

    def pages():
        value = deepcopy(original_pages())
        mutate(value)
        return value

    with monkeypatch.context() as supplied:
        supplied.setattr(original.acquisition, "flat_pages", pages)
        return await original.fresh_v5(supplied)


def _anchor(chain, reference, *, checked_at=original.CHECKED):
    return position.derive_original_coupled_risk_anchor_v2(
        chain, reference=reference, scope=original.SCOPE, checked_at=checked_at
    )


def _consume(sources, batches):
    producer = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        producer.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original.batches_fixture.original_witnesses(sources, batch),
        )
    return producer


def _finish(producer, batches, **changes):
    return producer.finish(
        **{
            "expected_through": len(batches),
            "expected_head": batches[-1].event_sha256,
            "checked_at": original.CHECKED,
            **changes,
        }
    )


async def test_coupled_equity_comes_from_original_risk_response_and_retains_whole_packet(
    monkeypatch,
):
    chain, reference = await _source(monkeypatch)
    value = _anchor(chain, reference)
    packet = original.lifecycle._packet(chain, reference)
    receipt = bank.decode(value.receipt_json)
    assert value.account_equity_usdt == 1000
    assert value.separately_observed_available_margin_usdt == 800
    assert value.positions == ()
    assert value.original_packet == next(
        point.event.packet_payload for point in chain if point.event.packet_payload
    )
    pages = receipt["requested_api_population"]
    assert [page["request"]["stream"] for page in pages] == [
        page.request.stream for page in packet.observations
    ]
    for actual, source in zip(pages, packet.observations, strict=True):
        assert actual["page_receipt_sha256"] == source.receipt_sha256
        assert actual["body_sha256"] == source.body_sha256
        assert actual["cursor"] == source.after
        assert actual["previous_page_sha256"] == source.previous_page_sha256
        assert actual["terminal"] is source.terminal
    assert receipt["available_margin_is_same_response_anchor_operand"] is False


async def test_nonflat_short_contract_population_is_a_real_supported_source_fact(
    monkeypatch,
):
    def supplied(pages):
        held = row(
            "positions",
            "991",
            pos="-2",
            mgnMode="isolated",
            uTime=ms(original.NOW - timedelta(milliseconds=2)),
        )
        pages["positions"] = [[held]]
        pages["account_position_risk"][0][0]["posData"] = [deepcopy(held)]

    chain, reference = await _source(monkeypatch, supplied)
    value = _anchor(chain, reference)
    (held,) = value.positions
    assert held.contracts == Fraction(-2)
    assert held.margin_mode == "isolated"
    assert held.position_side == "net"
    assert held.original_position_row_sha256 == held.original_risk_row_sha256
    assert value.account_equity_usdt == 1000


async def test_unicode_original_rows_keep_the_original_utf8_identity(monkeypatch):
    def supplied(pages):
        pages["account_position_risk"][0][0]["note"] = "臺灣原始來源"

    chain, reference = await _source(monkeypatch, supplied)
    value = _anchor(chain, reference)
    packet = original.lifecycle._packet(chain, reference)
    source = next(
        page
        for page in packet.observations
        if page.request.stream == "account_position_risk"
    )
    receipt = bank.decode(value.receipt_json)
    retained = receipt["requested_api_population"][receipt["risk_request_index"]]
    assert retained["original_row_sha256"] == [
        journal.digest(source.rows[0].canonical_json.encode("utf-8"))
    ]
    assert value.original_packet == next(
        point.event.packet_payload for point in chain if point.event.packet_payload
    )


async def test_current_v7_source_is_supported_without_renewing_its_original_time(
    monkeypatch,
):
    chain, reference = await original.fresh_v6_or_v7(monkeypatch, "V7")
    value = _anchor(chain, reference)
    packet = original.lifecycle._packet(chain, reference)
    assert value.original_packet_completed_at == packet.completed_at
    assert value.account_source_updated_at <= value.risk_body_completed_at
    assert value.risk_body_completed_at <= packet.completed_at
    assert value.account_source_updated_at != original.CHECKED


@pytest.mark.parametrize(
    "field", ["head_sha256", "packet_sha256", "session_binding_sha256"]
)
async def test_changed_external_reference_cannot_relabel_original_packet(
    monkeypatch, field
):
    chain, reference = await _source(monkeypatch)
    with pytest.raises(position.OriginalPositionPathError):
        _anchor(chain, replace(reference, **{field: "f" * 64}))


async def test_unavailable_original_source_cannot_be_claimed_at_an_earlier_cutoff(
    monkeypatch,
):
    chain, reference = await _source(monkeypatch)
    with pytest.raises(position.OriginalPositionPathError):
        _anchor(chain, reference, checked_at=original.NOW - timedelta(seconds=2))


async def test_disagreeing_original_position_population_is_rejected(monkeypatch):
    def supplied(pages):
        held = row(
            "positions",
            "991",
            pos="2",
            uTime=ms(original.NOW - timedelta(milliseconds=2)),
        )
        pages["positions"] = [[held]]
        pages["account_position_risk"][0][0]["posData"] = []

    chain, reference = await _source(monkeypatch, supplied)
    with pytest.raises(position.OriginalPositionPathError):
        _anchor(chain, reference)


@pytest.mark.parametrize("equity,available", [("0", "0"), ("1000", "1001")])
async def test_unknown_or_invalid_equity_and_margin_are_not_zero_filled(
    monkeypatch, equity, available
):
    chain, reference = await original.fresh_v5(
        monkeypatch, equity=equity, available=available
    )
    with pytest.raises(position.OriginalPositionPathError):
        _anchor(chain, reference)


async def test_v2_full_prefix_invokes_actual_coupled_producer_and_preserves_v1_meaning(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    legacy = bank.OriginalAccountRawProofBankAccumulatorV1(scope=original.SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        legacy.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original.batches_fixture.original_witnesses(sources, batch),
        )
    old = legacy.finish(
        expected_through=len(batches),
        expected_head=batches[-1].event_sha256,
        checked_at=original.CHECKED,
    )
    producer = _consume(sources, batches)
    value = _finish(producer, batches)
    assert value.original_raw_bank.raw_source_json == old.raw_source_json
    assert value.original_raw_bank.receipt_json == old.receipt_json
    assert [anchor.account_equity_usdt for anchor in value.coupled_anchors] == [
        1000,
        1200,
        900,
    ]
    receipt = bank.decode(value.receipt_json)
    assert len(receipt["original_packet_population"]) == 3
    assert receipt["through_sequence"] == 3
    assert receipt["index_head_sha256"] == batches[-1].event_sha256
    assert all(
        receipt[key] is None
        for key in (
            "current_history_tail_finality",
            "loss_streak_seed",
            "cashflow_and_funding_effective_time_finality",
            "continuous_peak",
        )
    )
    assert (
        value.account_complete
        is value.account_revision_issued
        is value.execution_authority
        is False
    )
    assert producer._original is None and producer._anchors == []


@pytest.mark.parametrize("damage", ["wrong_head", "missing_prefix", "changed_policy"])
async def test_prefix_or_policy_failure_erases_v2_state_and_returns_no_partial_facts(
    monkeypatch, damage
):
    sources, batches, _, _ = await original.population(monkeypatch)
    producer = _consume(sources, batches)
    changes = {}
    if damage == "wrong_head":
        changes["expected_head"] = "f" * 64
    elif damage == "missing_prefix":
        producer._anchors.pop()
    else:
        producer._policy_sha256 = "f" * 64
    with pytest.raises(bank.OriginalRawProofBankError):
        _finish(producer, batches, **changes)
    assert producer._original is None and producer._anchors == []


async def test_finite_retention_cap_rejects_without_truncation_and_destroys_partial_bank(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    producer = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    monkeypatch.setattr(bank, "MAX_API_RETAINED_BYTES_V2", 1)
    with pytest.raises(bank.OriginalRawProofBankError, match="retained_byte_bound"):
        producer.consume_current(
            chain=sources[0][0], reference=sources[0][1], batch=batches[0]
        )
    assert producer._original is None and producer._anchors == []


async def test_explicit_cancel_cannot_return_or_reuse_original_source(monkeypatch):
    sources, batches, _, _ = await original.population(monkeypatch)
    producer = _consume(sources, batches)
    producer.cancel()
    with pytest.raises(bank.OriginalRawProofBankError):
        _finish(producer, batches)


async def test_cancellation_during_actual_coupled_derivation_preserves_original_journal(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    chain, reference = sources[0]
    before = tuple(point.event for point in chain)

    def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(position, "derive_original_coupled_risk_anchor_v2", cancelled)
    producer = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    with pytest.raises(asyncio.CancelledError):
        producer.consume_current(chain=chain, reference=reference, batch=batches[0])
    assert producer._original is None and producer._anchors == []
    assert tuple(point.event for point in chain) == before
