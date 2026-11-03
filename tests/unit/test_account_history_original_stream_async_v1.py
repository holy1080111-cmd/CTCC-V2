"""Synthetic original-wire replay tests; no native database/source acceptance."""

import asyncio
from dataclasses import replace

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_history_source_v1 as native_source
from tests.unit.test_account_history_original_stream_v1 import (
    SCOPE,
    original_chain,
    originals,
    pages,
    replay,
)


def arguments(frames):
    checkpoints = [frame.checkpoint for frame in frames if frame.checkpoint is not None]
    return {
        "scope": SCOPE,
        "expected_through_sequence": len(frames),
        "expected_b3_head_sha256": frames[-1].batch.event_sha256,
        "expected_checkpoint_sha256": None
        if not checkpoints
        else journal.digest(checkpoints[-1]),
    }


@pytest.mark.asyncio
async def test_async_pages_replay_same_original33_bytes_in_the_acquiring_task(
    monkeypatch,
):
    frames = originals(await original_chain(monkeypatch), native_at=(32, 33))
    expected = replay(frames)
    task = asyncio.current_task()
    produced = []
    original_verifier = stream._source
    verified = []

    def verify_source(chain, scope):
        assert asyncio.current_task() is task
        verified.append(len(produced))
        return original_verifier(chain, scope)

    async def producer():
        for page in pages(frames):
            assert asyncio.current_task() is task
            # Real suspension between bounded source pages, without a worker.
            await asyncio.sleep(0)
            produced.append(page.index)
            yield page
            assert len(verified) == min(len(produced) * 8, len(frames))

    monkeypatch.setattr(stream, "_source", verify_source)
    result = await stream.replay_original_history_stream_async_v1(
        producer(), **arguments(frames)
    )
    assert produced == [0, 1, 2, 3, 4]
    assert len(verified) == 33
    assert result.receipt_json == expected.receipt_json
    assert result.account_complete is result.execution_authority is False
    with pytest.raises(native_source.NativeHistorySourceError):
        native_source.consume_owned_native_history_source_facts_v1(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["early_eof", "none_page", "extra_page", "wrong_previous"]
)
async def test_async_page_failure_never_returns_a_partial_replay(monkeypatch, change):
    frames = originals(await original_chain(monkeypatch), count=2)
    original_pages = tuple(pages(frames, width=1))

    async def producer():
        yield original_pages[0]
        await asyncio.sleep(0)
        if change == "early_eof":
            return
        if change == "none_page":
            yield None
        elif change == "wrong_previous":
            yield replace(original_pages[1], previous_page_sha256="f" * 64)
        else:
            yield original_pages[1]
            yield original_pages[1]

    with pytest.raises(stream.OriginalHistoryStreamError):
        await stream.replay_original_history_stream_async_v1(
            producer(), **arguments(frames)
        )


@pytest.mark.asyncio
async def test_async_producer_exception_is_redacted_after_an_original_page(monkeypatch):
    frames = originals(await original_chain(monkeypatch), count=2)

    async def producer():
        yield next(pages(frames, width=1))
        await asyncio.sleep(0)
        raise RuntimeError("declared-synthetic-private-page-error")

    with pytest.raises(stream.OriginalHistoryStreamError) as error:
        await stream.replay_original_history_stream_async_v1(
            producer(), **arguments(frames)
        )
    assert str(error.value) == "stream_original_replay_failed"
    assert error.value.__cause__ is None


@pytest.mark.asyncio
async def test_async_cancellation_closes_the_original_engine_without_a_receipt(
    monkeypatch,
):
    frames = originals(await original_chain(monkeypatch), count=2)
    original_engine = stream._replay_engine
    closed = []

    def tracked_engine(*args):
        try:
            return (yield from original_engine(*args))
        finally:
            closed.append(True)

    async def producer():
        yield next(pages(frames, width=1))
        await asyncio.sleep(0)
        raise asyncio.CancelledError

    monkeypatch.setattr(stream, "_replay_engine", tracked_engine)
    with pytest.raises(asyncio.CancelledError):
        await stream.replay_original_history_stream_async_v1(
            producer(), **arguments(frames)
        )
    assert closed == [True]
