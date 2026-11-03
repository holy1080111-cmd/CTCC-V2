"""Real original stream/fold hooks with declared synthetic wire sources only.

These tests do not create a DB, native source owner or complete account.
"""

import asyncio

import pytest

from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_original_position_path_v1 as position
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from tests.unit import test_account_history_original_financial_stream_v2 as wire
from tests.unit import test_account_original_balance_measurement_v1 as original

pytestmark = pytest.mark.asyncio


def _engine(frames, proof_bank, api_population=None):
    return stream._replay_engine(
        wire.SCOPE,
        len(frames),
        frames[-1].batch.event_sha256,
        None,
        stream.POLICY_SHA256,
        _financial_v2=proof_bank._fold,
        _raw_bank_v1=proof_bank,
        _api_population_v2=api_population,
    )


def _finish_original(engine, proof_bank, frames):
    original_replay = stream._finish_replay_engine(engine)
    proof_bank._freeze_verified_raw_state_v1()
    replay = stream._finish_financial_stream_v3(
        original_replay,
        proof_bank._fold,
        wire.SCOPE,
        len(frames),
        frames[-1].batch.event_sha256,
        None,
        original.CHECKED,
        None,
        stream._financial_stream_policy_bytes_v3(),
        asyncio.current_task(),
    )
    raw = proof_bank._finish_after_original_stream_v1(
        replay.financial_facts,
        expected_through=len(frames),
        expected_head=frames[-1].batch.event_sha256,
        checked_at=original.CHECKED,
    )
    return replay, raw


async def test_real_stream_hook_derives_every_current_once_and_joins_original_prefix(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    frames = wire.frames_for(sources, batches)
    api = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    proof_bank = api._original
    engine = _engine(frames, proof_bank, api)
    try:
        next(engine)
        for page in wire.pages_for(frames):
            engine.send(page)
        replay, raw = _finish_original(engine, proof_bank, frames)
        value = api._finish_after_original_stream_v2(
            raw,
            expected_through=len(frames),
            expected_head=batches[-1].event_sha256,
            checked_at=original.CHECKED,
        )
        assert [anchor.account_equity_usdt for anchor in value.coupled_anchors] == [
            1000,
            1200,
            900,
        ]
        assert value.original_raw_bank is raw
        assert bank.decode(value.receipt_json)["through_sequence"] == len(frames)
        assert (
            replay.account_complete
            is value.account_complete
            is value.execution_authority
            is False
        )
        assert api._original is None
    finally:
        engine.close()
        api.cancel()


async def test_default_v1_stream_component_bytes_are_unchanged_by_v2_hook_selection(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    frames = wire.frames_for(sources, batches)
    legacy = bank.OriginalAccountRawProofBankAccumulatorV1(scope=original.SCOPE)
    api = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    results = []
    for proof_bank, selected in ((legacy, None), (api._original, api)):
        engine = _engine(frames, proof_bank, selected)
        try:
            next(engine)
            for page in wire.pages_for(frames):
                engine.send(page)
            replay, raw = _finish_original(engine, proof_bank, frames)
            results.append(
                (
                    replay.original_replay.receipt_json,
                    replay.receipt_json,
                    raw.raw_source_json,
                    raw.receipt_json,
                )
            )
        finally:
            engine.close()
            proof_bank.cancel()
    api.cancel()
    assert results[0] == results[1]


async def test_original_page_chain_conflict_never_reaches_an_api_population_result(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    frames = wire.frames_for(sources, batches)
    api = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    engine = _engine(frames, api._original, api)
    try:
        next(engine)
        first = stream.OriginalHistoryPageV1(0, None, frames[:1])
        engine.send(first)
        assert len(api._anchors) == 1
        with pytest.raises(stream.OriginalHistoryStreamError):
            engine.send(stream.OriginalHistoryPageV1(1, "f" * 64, frames[1:]))
    finally:
        engine.close()
        api.cancel()
    assert api._anchors == [] and api._original is None


async def test_api_companion_must_own_the_same_exact_original_fold(monkeypatch):
    sources, batches, _, _ = await original.population(monkeypatch)
    frames = wire.frames_for(sources, batches)
    api = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    different = bank.OriginalAccountRawProofBankAccumulatorV1(scope=original.SCOPE)
    engine = _engine(frames, different, api)
    try:
        with pytest.raises(
            stream.OriginalHistoryStreamError, match="accumulator_scope_changed"
        ):
            next(engine)
    finally:
        engine.close()
        api.cancel()
        different.cancel()


async def test_stream_hook_cannot_run_before_original_financial_current_validation(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    api = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    with pytest.raises(
        bank.OriginalRawProofBankError, match="original_CURRENT_changed"
    ):
        api._capture_after_verified_current_v2(
            chain=sources[0][0], reference=sources[0][1], batch=batches[0]
        )
    assert api._original is None and api._anchors == []


async def test_cancellation_in_real_current_hook_propagates_and_preserves_original_journal(
    monkeypatch,
):
    sources, batches, _, _ = await original.population(monkeypatch)
    frames = wire.frames_for(sources, batches)
    before = tuple(
        tuple(point.event for point in chain) for chain, _reference in sources
    )
    api = bank.OriginalAccountApiPopulationAccumulatorV2(scope=original.SCOPE)
    engine = _engine(frames, api._original, api)

    def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(position, "derive_original_coupled_risk_anchor_v2", cancelled)
    try:
        next(engine)
        with pytest.raises(asyncio.CancelledError):
            engine.send(next(iter(wire.pages_for(frames))))
    finally:
        engine.close()
        api.cancel()
    assert api._original is None and api._anchors == []
    assert (
        tuple(tuple(point.event for point in chain) for chain, _reference in sources)
        == before
    )
