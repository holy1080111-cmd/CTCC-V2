"""HELD pure composite replay tests; no TLS/native/account authority credit.

Every source comes from a fresh declared original capture. The original B3
receipt/witness chain and boundary32 checkpoint are replayed by the real V1
engine; a saved receipt or caller approval cannot substitute for those bytes.
"""

import asyncio
import hashlib
import json
from contextlib import aclosing, contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain import account_history_partition_v1 as partition
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_global_financial_v1 as financial
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_observation_index as observed
from tests.unit import test_account_history_global_financial_v1 as originals
from tests.unit import test_account_lifecycle_history as acquisition
from tests.unit.test_account_financial_operands_v2 import native_position_source
from tests.unit.test_account_native_proof import synthetic_tls_chain
from tests.unit.test_qualification_account_capture import INSTRUMENT, MAIN_UID, NOW, UID
from tests.unit.test_qualification_account_v4 import script as all_product_script

SCOPE = partition.HistoryPartitionScopeV1("demo", UID, "USDT")


def _fresh_all_product_plan(**times):
    # Build this exact plan before a new acquisition. Never take a captured
    # Regional object and convert its fields or relabel its existing bytes.
    assert set(times) == {"created_at", "history_start", "history_end"}
    return capture.AllProductDemoAccountCapturePlan(
        plan_id="declared_synthetic_wire_global_financial",
        created_at=times["created_at"],
        expected_uid=UID,
        expected_main_uid=MAIN_UID,
        session_binding_id="synthetic-private-session",
        settlement_currency="USDT",
        leverage_instrument_ids=(INSTRUMENT,),
        history_start=times["history_start"],
        history_end=times["history_end"],
        registration_region="global",
        origin="https://openapi.okx.com",
        registration_evidence_sha256=hashlib.sha256(
            b"DECLARED_SYNTHETIC_WIRE_REGISTRATION_NOT_AUTHENTICATED"
        ).hexdigest(),
        contract_version="ctcc.demo_account_plan.v5",
        capture_scope="all_standard_products_v5_documented_algos",
    )


def _all_product_rows(*, pages):
    # Preserve declared original SWAP rows, while issuing every V5 query. The
    # fixture explicitly supplies empty other-product populations before IO.
    return all_product_script(pages=pages, source=pages)


@contextmanager
def _wire_construction(monkeypatch):
    real_plan = acquisition.regional
    real_script = acquisition.script
    real_capture = acquisition.original_capture

    async def original_capture(patches, at, **values):
        original = await real_capture(patches, at, **values)
        # These freshly acquired synthetic MemoryJournal events have not been
        # made into B3 documents or persisted in a PostgreSQL source table.
        # Explicit synthetic-wire annotation gives ZERO TLS/native credit.
        construction = synthetic_tls_chain(original)
        reference, _, _, _ = proof._source(construction, acquisition.SCOPE)
        assert reference == observed.source_reference(construction)
        return construction

    try:
        with monkeypatch.context() as construction:
            construction.setattr(acquisition, "regional", _fresh_all_product_plan)
            construction.setattr(acquisition, "script", _all_product_rows)
            construction.setattr(acquisition, "original_capture", original_capture)
            yield construction
    finally:
        assert acquisition.regional is real_plan
        assert acquisition.script is real_script
        assert acquisition.original_capture is real_capture


async def acquired_wire(monkeypatch, **values):
    with _wire_construction(monkeypatch) as construction:
        return await originals.acquired(construction, **values)


async def acquired_wire_without_repeated_opening(monkeypatch):
    with _wire_construction(monkeypatch) as construction:
        return await originals.acquired_without_repeated_opening(construction)


async def acquire_one_wire(monkeypatch, at, **values):
    with _wire_construction(monkeypatch) as construction:
        return await acquisition.original_capture(construction, at, **values)


def frames_for(sources, batches):
    frames, checkpoints = [], []
    for sequence, ((chain, _), batch) in enumerate(
        zip(sources, batches, strict=True), 1
    ):
        witnesses = (
            ()
            if sequence == 1
            else (
                stream.OriginalHistoryWitnessV1(sequence - 1, sources[sequence - 2][0]),
            )
        )
        frame = stream.OriginalHistoryFrameV1(chain, batch, witnesses)
        if sequence % partition.PARTITION_SPAN == 0:
            members = (*frames[-(partition.PARTITION_SPAN - 1) :], frame)
            checkpoint = partition.build_partition_checkpoint_v1(
                scope=SCOPE,
                b3_documents=tuple(item.batch.document_json for item in members),
                native_registrations=tuple(
                    item.native_registration for item in members
                ),
                previous_checkpoints=tuple(checkpoints),
                expected_b3_policy_sha256=observed.POLICY_SHA256,
                expected_b3_head_sha256=batch.event_sha256,
            ).record_json
            checkpoints.append(checkpoint)
            frame = replace(frame, checkpoint=checkpoint)
        frames.append(frame)
    return tuple(frames)


def pages_for(frames):
    previous = None
    for offset in range(0, len(frames), stream.MAX_PAGE_FRAMES):
        page = stream.OriginalHistoryPageV1(
            offset // stream.MAX_PAGE_FRAMES,
            previous,
            frames[offset : offset + stream.MAX_PAGE_FRAMES],
        )
        previous = stream.original_history_page_sha256_v1(page)
        yield page


def arguments(frames, **changes):
    checkpoint = next(
        (
            frame.checkpoint
            for frame in reversed(frames)
            if frame.checkpoint is not None
        ),
        None,
    )
    return {
        "scope": SCOPE,
        "expected_through_sequence": len(frames),
        "expected_b3_head_sha256": frames[-1].batch.event_sha256,
        "expected_checkpoint_sha256": None
        if checkpoint is None
        else journal.digest(checkpoint),
        "checked_at": originals.CHECKED,
        "expected_policy_sha256": stream.original_history_financial_stream_policy_sha256_v2(),
        **changes,
    }


def v1_replay(frames):
    values = arguments(frames)
    del values["checked_at"]
    values["expected_policy_sha256"] = stream.POLICY_SHA256
    return stream.replay_original_history_stream_v1(pages_for(frames), **values)


@pytest.mark.asyncio
async def test_composite_original32_checkpoint_then33_keeps_global_run_and_component_bytes(
    monkeypatch,
):
    sources, batches = await acquired_wire(monkeypatch, opens=31)
    frames = frames_for(sources, batches)
    assert (
        len(frames) == 33
        and frames[31].checkpoint is not None
        and frames[32].checkpoint is None
    )
    before = v1_replay(frames)
    result = stream.replay_original_history_financial_stream_v2(
        pages_for(frames),
        **arguments(frames),
        position_source=native_position_source(sources[-1][1]),
    )
    joined = json.loads(result.receipt_json)
    facts = json.loads(result.financial_facts.receipt_json)
    assert result.original_replay.receipt_json == before.receipt_json
    assert joined["original_stream_receipt_sha256"] == before.receipt_sha256
    assert joined["global_financial_receipt_sha256"] == journal.digest(
        result.financial_facts.receipt_json
    )
    assert joined["checkpoint_sha256"] == journal.digest(frames[31].checkpoint)
    assert joined["through_sequence"] == facts["through_sequence"] == 33
    assert facts["closed_runs"][0]["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert len(facts["original_current_sources"]) == 33
    assert len(facts["observed_lifecycles"][0]["original_source_rows"]) == 2
    assert any(
        locator["capture_id"] == sources[1][1].capture_id
        for row in facts["closed_runs"][0]["original_fill_rows"]
        for locator in row["locators"]
    )
    assert "original_stream_replay_not_bound" in facts["missing_obligations"]
    assert joined["resolved_composite_obligations"] == [
        "original_stream_replay_not_bound"
    ]
    assert "original_stream_replay_not_bound" not in joined["remaining_obligations"]
    assert "native_acquisition_not_owned" in joined["remaining_obligations"]
    assert (
        joined["original_stream_blocking_reasons"]
        == json.loads(before.receipt_json)["blocking_reasons"]
    )
    assert facts["original_stream_prefix_verified"] is False
    assert result.execution_authority is result.account_complete is False
    assert (
        joined["native_acquisition_owned"]
        is joined["native_history_prefix_verified"]
        is False
    )
    assert (
        joined["account_revision_published"] is False
        and joined["portfolio_revision"] is None
    )
    assert result.admission == joined["admission"] == "DENY"
    consumed = []
    real_consume = financial.OriginalHistoryFinancialAccumulatorV1.consume_current

    def observe(self, **values):
        consumed.append(values["batch"].sequence)
        return real_consume(self, **values)

    monkeypatch.setattr(
        financial.OriginalHistoryFinancialAccumulatorV1, "consume_current", observe
    )
    bad = (
        *frames[:31],
        replace(frames[31], checkpoint=journal.canonical({"members": []})),
        frames[32],
    )
    with pytest.raises(
        stream.OriginalHistoryStreamError,
        match="^stream_original_checkpoint_members_changed$",
    ):
        stream.replay_original_history_financial_stream_v2(
            pages_for(bad), **arguments(frames)
        )
    # A bad original boundary cannot be booked by the financial consumer.
    assert consumed == list(range(1, 32))


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["head", "through"])
async def test_original_final_pins_must_finish_before_global_finish(
    monkeypatch, changed
):
    sources, batches = await acquired_wire(monkeypatch)
    frames = frames_for(sources, batches)
    finished = []
    real_finish = financial.OriginalHistoryFinancialAccumulatorV1.finish

    def observe(self, **values):
        finished.append(True)
        return real_finish(self, **values)

    monkeypatch.setattr(
        financial.OriginalHistoryFinancialAccumulatorV1, "finish", observe
    )
    changes = (
        {"expected_b3_head_sha256": "f" * 64}
        if changed == "head"
        else {"expected_through_sequence": len(frames) + 1}
    )
    with pytest.raises(
        stream.OriginalHistoryStreamError,
        match="^stream_incomplete_or_changed_pinned_head$",
    ):
        stream.replay_original_history_financial_stream_v2(
            pages_for(frames), **arguments(frames, **changes)
        )
    assert not finished


@pytest.mark.asyncio
async def test_policy_mismatch_is_rejected_before_consuming_any_page(monkeypatch):
    sources, batches = await acquired_wire(monkeypatch)
    frames = frames_for(sources, batches)
    consumed = []

    def forbidden():
        consumed.append(True)
        yield next(pages_for(frames))

    with pytest.raises(
        stream.OriginalHistoryStreamError, match="^stream_financial_policy_mismatch$"
    ):
        stream.replay_original_history_financial_stream_v2(
            forbidden(), **arguments(frames, expected_policy_sha256="f" * 64)
        )
    assert not consumed


@pytest.mark.asyncio
async def test_async_and_sync_join_are_deterministic_and_missing_positions_stay_unknown(
    monkeypatch,
):
    sources, batches = await acquired_wire(monkeypatch)
    frames = frames_for(sources, batches)
    direct = stream.replay_original_history_financial_stream_v2(
        pages_for(frames), **arguments(frames)
    )

    async def available():
        for page in pages_for(frames):
            yield page

    async with aclosing(available()) as producer:
        asynchronous = await stream.replay_original_history_financial_stream_async_v2(
            producer, **arguments(frames)
        )
    assert asynchronous.receipt_json == direct.receipt_json
    assert (
        asynchronous.original_replay.receipt_json == direct.original_replay.receipt_json
    )
    assert (
        asynchronous.financial_facts.receipt_json == direct.financial_facts.receipt_json
    )
    data = json.loads(direct.financial_facts.receipt_json)
    assert data["native_position_archive_sha256"] is None
    assert "native_position_source_missing" in data["missing_obligations"]
    for name in (
        "funding_accrual_events",
        "daily_net_pnl",
        "weekly_net_pnl",
        "loss_streak_seed",
        "loss_streak",
        "historical_native_hwm",
        "current_history_tail_finality",
    ):
        assert data[name] is None


@pytest.mark.asyncio
async def test_source_identity_conflict_yields_no_composite_and_destroys_accumulator(
    monkeypatch,
):
    sources, _ = await acquired_wire(monkeypatch)
    changed = await acquire_one_wire(
        monkeypatch,
        NOW + timedelta(seconds=1900),
        base=NOW,
        filled=True,
        case="A21",
        variant=True,
    )
    sources = (*sources, (changed, observed.source_reference(changed)))
    batches = originals.original_batches(sources, originals.WINDOW)
    frames = frames_for(sources, batches)
    states = []
    real_cancel = financial.OriginalHistoryFinancialAccumulatorV1.cancel

    def observe(self):
        real_cancel(self)
        states.append(self._state)

    monkeypatch.setattr(
        financial.OriginalHistoryFinancialAccumulatorV1, "cancel", observe
    )
    with pytest.raises(
        stream.OriginalHistoryStreamError, match="^stream_financial_replay_failed$"
    ) as rejected:
        stream.replay_original_history_financial_stream_v2(
            pages_for(frames), **arguments(frames)
        )
    assert type(rejected.value.__context__) is financial.GlobalFinancialHistoryError
    assert rejected.value.__context__.args == (
        "global_financial_conflicting_row_identity",
    )
    assert states and all(value is None for value in states)


@pytest.mark.asyncio
async def test_saved_v1_receipt_cannot_replace_original_pages(monkeypatch):
    sources, batches = await acquired_wire(monkeypatch)
    frames = frames_for(sources, batches)
    saved = v1_replay(frames)
    with pytest.raises(
        stream.OriginalHistoryStreamError, match="^stream_exact_page_required$"
    ):
        stream.replay_original_history_financial_stream_v2(
            (saved,), **arguments(frames)
        )


@pytest.mark.asyncio
async def test_real_cancellation_after_prefix_preserves_cancel_and_managed_producer_close(
    monkeypatch,
):
    sources, batches = await acquired_wire(monkeypatch)
    frames = frames_for(sources, batches)
    closed, states = [], []
    real_cancel = financial.OriginalHistoryFinancialAccumulatorV1.cancel

    def observe(self):
        real_cancel(self)
        states.append(self._state)

    monkeypatch.setattr(
        financial.OriginalHistoryFinancialAccumulatorV1, "cancel", observe
    )

    async def scenario():
        async def interrupted():
            try:
                yield next(pages_for(frames))
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            finally:
                closed.append(True)

        async with aclosing(interrupted()) as producer:
            with pytest.raises(asyncio.CancelledError) as rejected:
                await stream.replay_original_history_financial_stream_async_v2(
                    producer, **arguments(frames)
                )
        assert rejected.value.args == ()
        while asyncio.current_task().cancelling():
            asyncio.current_task().uncancel()

    await asyncio.create_task(scenario())
    assert closed == [True] and states and all(value is None for value in states)


@pytest.mark.asyncio
async def test_composite_full34_keeps_early_opening_absent_from_every_last32_current(
    monkeypatch,
):
    sources, batches = await acquired_wire_without_repeated_opening(monkeypatch)
    frames = frames_for(sources, batches)
    early = sources[1][1].capture_id
    assert len(frames) == 34 and early not in {
        reference.capture_id for _, reference in sources[-32:]
    }
    before = v1_replay(frames)
    result = stream.replay_original_history_financial_stream_v2(
        pages_for(frames),
        **arguments(frames),
        position_source=native_position_source(sources[-1][1]),
    )
    data = json.loads(result.financial_facts.receipt_json)
    joined = json.loads(result.receipt_json)
    run = data["closed_runs"][0]
    assert run["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert any(
        locator["capture_id"] == early
        for row in run["original_fill_rows"]
        for locator in row["locators"]
    )
    assert len(data["original_current_sources"]) == joined["through_sequence"] == 34
    assert result.original_replay.receipt_json == before.receipt_json
    assert joined["original_stream_receipt_sha256"] == before.receipt_sha256
    # A new declared tail-only genesis lacks the opening; it cannot reproduce
    # the global closed-run fact or use its local sequence to replace full34.
    tail = sources[-32:]
    tail_batches = originals.original_batches(tail, originals.WINDOW)
    tail_frames = frames_for(tail, tail_batches)
    tail_result = stream.replay_original_history_financial_stream_v2(
        pages_for(tail_frames), **arguments(tail_frames)
    )
    tail_data = json.loads(tail_result.financial_facts.receipt_json)
    assert all(
        item["reconciled_closed_run_net_pnl"] is None
        for item in tail_data["closed_runs"]
    )
    assert all(
        locator["capture_id"] != early
        for row in tail_data["retained_source_index"]
        for locator in row["locators"]
    )
    with pytest.raises(
        stream.OriginalHistoryStreamError,
        match="^stream_incomplete_or_changed_pinned_head$",
    ):
        stream.replay_original_history_financial_stream_v2(
            pages_for(tail_frames), **arguments(frames)
        )


@pytest.mark.asyncio
async def test_composite_default_regional_capture_still_denies_original_scope(
    monkeypatch,
):
    sources, batches = await originals.acquired(monkeypatch)
    frames = frames_for(sources, batches)
    with pytest.raises(
        stream.OriginalHistoryStreamError, match="^stream_financial_replay_failed$"
    ) as rejected:
        stream.replay_original_history_financial_stream_v2(
            pages_for(frames), **arguments(frames)
        )
    assert type(rejected.value.__context__) is proof.NativeAccountProofError
    assert rejected.value.__context__.args == (
        "native_account_source_scope_unsupported",
    )


@pytest.mark.asyncio
async def test_composite_fresh_all_product_without_synthetic_wire_annotation_still_denies_tls(
    monkeypatch,
):
    real_plan, real_script, real_capture = (
        acquisition.regional,
        acquisition.script,
        acquisition.original_capture,
    )
    with monkeypatch.context() as construction:
        construction.setattr(acquisition, "regional", _fresh_all_product_plan)
        construction.setattr(acquisition, "script", _all_product_rows)
        sources, batches = await originals.acquired(construction)
    assert acquisition.regional is real_plan and acquisition.script is real_script
    assert acquisition.original_capture is real_capture
    frames = frames_for(sources, batches)
    with pytest.raises(
        stream.OriginalHistoryStreamError, match="^stream_financial_replay_failed$"
    ) as rejected:
        stream.replay_original_history_financial_stream_v2(
            pages_for(frames), **arguments(frames)
        )
    assert type(rejected.value.__context__) is proof.NativeAccountProofError
    assert rejected.value.__context__.args == ("native_account_original_tls_required",)
