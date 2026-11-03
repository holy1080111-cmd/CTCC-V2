"""Fresh declared source replay only; no native/PG/account acceptance credit."""

import json

import pytest

from app.domain.source_primitives import sha
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from tests.unit import test_account_original_balance_measurement_v1 as original

pytestmark = pytest.mark.asyncio


def _consume(sources, batches):
    value = bank.OriginalAccountRawProofBankAccumulatorV1(scope=original.SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        value.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original.batches_fixture.original_witnesses(sources, batch),
        )
    return value


def _finish(value, batches, *, head=None):
    return value.finish(
        expected_through=len(batches),
        expected_head=batches[-1].event_sha256 if head is None else head,
        checked_at=original.CHECKED,
    )


async def test_full_original_rows_and_balance_body_survive_original_fold_clear(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    value = _consume(sources, batches)
    result = _finish(value, batches)
    raw, core = (
        json.loads(result.raw_source_json),
        json.loads(result.financial_facts.receipt_json),
    )
    assert raw["through_sequence"] == 3
    assert sha(result.raw_source_json) == result.raw_source_sha256
    assert len(raw["original_CURRENT_sections"]) == len(sources)
    for section, (chain, reference), batch in zip(
        raw["original_CURRENT_sections"], sources, batches, strict=True
    ):
        packet = lifecycle._packet(chain, reference)
        assert section["batch_sha256"] == batch.event_sha256
        for retained in section["pages"]:
            page = packet.observations[retained["request_index"]]
            assert bytes.fromhex(retained["response_body_hex"]) == page.response_body
            assert retained["original_rows"] == [
                row.canonical_json for row in page.rows
            ]
            assert retained["request_started_at"] == page.request_started_at.isoformat()
            assert (
                retained["headers_received_at"] == page.headers_received_at.isoformat()
            )
    assert raw["original_current_sources"] == core["original_current_sources"]
    assert (
        core["funding_accrual_events"]
        is core["loss_streak_seed"]
        is core["current_history_tail_finality"]
        is None
    )
    assert (
        result.account_complete
        is result.account_revision_issued
        is result.execution_authority
        is False
    )
    assert (
        value._fold is None
        and value._frozen_raw is None
        and value._current_sections == []
    )


async def test_financial_core_alone_cannot_replace_original_anchor_capture(monkeypatch):
    sources, batches, _, _ = await original.population(monkeypatch)
    value = bank.OriginalAccountRawProofBankAccumulatorV1(scope=original.SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        value._fold.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original.batches_fixture.original_witnesses(sources, batch),
        )
    with pytest.raises(
        bank.OriginalRawProofBankError, match="current_anchor_population_missing"
    ):
        _finish(value, batches)
    assert value._fold is None


@pytest.mark.parametrize(
    "damage",
    ["omit_anchor", "modify_raw", "reorder_anchors", "wrong_head", "changed_policy"],
)
async def test_raw_source_omission_or_change_never_returns_partial_bank(
    monkeypatch, damage
):
    sources, batches, _, _ = await original.population(monkeypatch)
    value = _consume(sources, batches)
    head = None
    if damage == "omit_anchor":
        value._current_sections.pop()
    elif damage == "modify_raw":
        value._current_sections[0]["pages"][0]["response_body_hex"] = "00"
    elif damage == "reorder_anchors":
        value._current_sections[0], value._current_sections[1] = (
            value._current_sections[1],
            value._current_sections[0],
        )
    elif damage == "wrong_head":
        head = "f" * 64
    else:
        value._policy_sha256 = "f" * 64
    with pytest.raises(bank.OriginalRawProofBankError):
        _finish(value, batches, head=head)
    assert value._fold is None


async def test_byte_overflow_destroys_fold_without_truncating_rows(monkeypatch):
    sources, batches, _, _ = await original.population(monkeypatch)
    value = bank.OriginalAccountRawProofBankAccumulatorV1(scope=original.SCOPE)
    monkeypatch.setattr(bank, "MAX_RAW_BYTES", 1)
    with pytest.raises(bank.OriginalRawProofBankError):
        value.consume_current(
            chain=sources[0][0], reference=sources[0][1], batch=batches[0]
        )
    assert value._fold is None and value._current_sections == []


async def test_explicit_cancel_prevents_result_or_reuse(monkeypatch):
    sources, batches, _, _ = await original.population(monkeypatch)
    value = _consume(sources, batches)
    value.cancel()
    with pytest.raises(bank.OriginalRawProofBankError):
        _finish(value, batches)
    assert value._fold is None
