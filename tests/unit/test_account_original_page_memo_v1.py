"""SOURCE-only pure memo fixtures; synthetic RPC metadata grants no ownership."""

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories import account_history_original_frame_v1 as wire
from app.database.repositories import account_history_original_native_read_v1 as native
from app.domain import account_history_partition_v1 as partition
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_history_query_verifier as query
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_original_page_memo_v1 as memo
from tests.unit import test_account_lifecycle_history as original
from tests.unit.test_qualification_account_capture import NOW, UID


async def source(monkeypatch):
    chain = await original.original_capture(
        monkeypatch, NOW + timedelta(seconds=1800), base=NOW, filled=True, case="A01"
    )
    reference = observed.source_reference(chain)
    payload = next(
        point.event.packet_payload for point in chain if point.event.packet_payload
    )
    return chain, reference, payload


@contextmanager
def page(reference, *, fold=0):
    expires = (datetime.now(UTC) + timedelta(seconds=20)).replace(fold=fold)
    fence = native._Fence(expires)
    request = wire.OriginalFrameRequestV1(
        partition.HistoryPartitionScopeV1("demo", UID, "USDT"),
        1,
        reference.head_sha256,
        0,
        None,
        1,
        1,
        1,
        expires,
    )
    received = fence.check()
    with memo._native_page_context(fence, request, received, received):
        memo._bind_validated_rpc(received, 7, "1")
        yield fence


def private_query(chain, reference, **changes):
    return query._verify_original_page_query(
        chain, **{**observed._pins(reference, original.SCOPE), **changes}
    )


def private_packet(payload, reference):
    return capture._verify_original_page_packet(
        payload,
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )


@pytest.mark.asyncio
async def test_private_query_keeps_receipt_bytes_and_returns_new_actual_type(
    monkeypatch,
):
    chain, reference, _ = await source(monkeypatch)
    with page(reference):
        first = private_query(chain, reference)
        second = private_query(chain, reference)
        assert first is not second and first.receipt_json == second.receipt_json
        assert type(first) is type(second) is query.HistoryQueryVerification
        context = memo._native_current_context()
        assert len(context._query_receipts) == len(context._packets) == 1
        assert all(type(value) is bytes for value in context._query_receipts.values())
    assert memo._native_current_context() is None


@pytest.mark.asyncio
async def test_mutated_returned_packet_cannot_change_the_private_copy(monkeypatch):
    _, reference, payload = await source(monkeypatch)
    with page(reference):
        first = private_packet(payload, reference)
        original_pin = first.packet_sha256
        observation_index, row_index = next(
            (i, j)
            for i, observation in enumerate(first.observations)
            for j, _row in enumerate(observation.rows)
        )
        original_observation = first.observations[observation_index]
        original_row = original_observation.rows[row_index]
        original_receipt, original_canonical = (
            original_observation.receipt_sha256,
            original_row.canonical_json,
        )
        object.__setattr__(first, "packet_sha256", "f" * 64)
        object.__setattr__(original_observation, "receipt_sha256", "e" * 64)
        object.__setattr__(
            original_row,
            "canonical_json",
            '{"declared_adversarial_fixture_mutation":true}',
        )
        second = private_packet(payload, reference)
        second_observation = second.observations[observation_index]
        second_row = second_observation.rows[row_index]
        assert second is not first and second.packet_sha256 == original_pin
        assert second_observation is not original_observation
        assert second_row is not original_row
        assert second_observation.receipt_sha256 == original_receipt
        assert second_row.canonical_json == original_canonical
        object.__setattr__(second, "packet_sha256", "d" * 64)
        object.__setattr__(
            second_row, "canonical_json", '{"second_declared_fixture_mutation":true}'
        )
        third = private_packet(payload, reference)
        assert third.packet_sha256 == original_pin
        assert (
            third.observations[observation_index].rows[row_index].canonical_json
            == original_canonical
        )
        assert len(memo._native_current_context()._packets) == 1


@pytest.mark.asyncio
async def test_public_query_and_packet_still_execute_full_inside_an_active_page(
    monkeypatch,
):
    chain, reference, payload = await source(monkeypatch)
    actual_query, actual_packet = query._verify, capture.verify_demo_account_packet
    query_calls, packet_calls = [], []

    def full_query(*args, **kwargs):
        query_calls.append(1)
        return actual_query(*args, **kwargs)

    def full_packet(*args, **kwargs):
        packet_calls.append(1)
        return actual_packet(*args, **kwargs)

    with page(reference):
        private_query(chain, reference)
        with monkeypatch.context() as measured:
            measured.setattr(query, "_verify", full_query)
            measured.setattr(capture, "verify_demo_account_packet", full_packet)
            for _ in range(2):
                query.verify_history_query_chain(
                    chain, **observed._pins(reference, original.SCOPE)
                )
            assert len(query_calls) == len(packet_calls) == 2
            for _ in range(2):
                capture.verify_demo_account_packet(
                    payload,
                    expected_sha256=reference.packet_sha256,
                    expected_plan_sha256=reference.plan_sha256,
                )
            assert len(packet_calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,value",
    [
        ("expected_head_sha256", "a" * 64),
        ("expected_plan_sha256", "b" * 64),
        ("expected_packet_sha256", "c" * 64),
        ("expected_account_id", "710001"),
        ("expected_settlement_currency", "BTC"),
        ("expected_policy_sha256", "d" * 64),
    ],
)
async def test_every_expected_pin_changes_key_or_retains_policy_denial(
    monkeypatch, name, value
):
    chain, reference, _ = await source(monkeypatch)
    with page(reference):
        private_query(chain, reference)
        with pytest.raises(query.HistoryQueryVerificationError):
            private_query(chain, reference, **{name: value})


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["event_json", "raw_body", "packet_payload"])
async def test_changed_original_byte_field_cannot_reuse_unchanged_head_and_packet_pins(
    monkeypatch, field
):
    chain, reference, _ = await source(monkeypatch)
    changed = copy.deepcopy(chain)
    with page(reference):
        private_query(chain, reference)
        point = next(item for item in changed if getattr(item.event, field) is not None)
        original_bytes = getattr(point.event, field)
        object.__setattr__(point.event, field, original_bytes + b" ")
        with pytest.raises(query.HistoryQueryVerificationError):
            private_query(changed, reference)


@pytest.mark.asyncio
async def test_changed_readback_timestamp_requires_a_second_verified_entry(monkeypatch):
    chain, reference, _ = await source(monkeypatch)
    changed = (
        replace(chain[0], readback_at=chain[0].readback_at + timedelta(microseconds=1)),
        *chain[1:],
    )
    with page(reference):
        private_query(chain, reference)
        private_query(changed, reference)
        assert len(memo._native_current_context()._query_receipts) == 2


@pytest.mark.asyncio
async def test_next_rpc_has_a_distinct_empty_memo(monkeypatch):
    chain, reference, _ = await source(monkeypatch)
    for _ in range(2):
        with page(reference):
            context = memo._native_current_context()
            assert context._query_receipts == context._packets == {}
            private_query(chain, reference)
            assert len(context._query_receipts) == 1
        assert context.closed and context._query_receipts == context._packets == {}


@pytest.mark.asyncio
async def test_overflow_runs_full_and_does_not_expand_budget(monkeypatch):
    chain, reference, _ = await source(monkeypatch)
    monkeypatch.setattr(memo, "_MAX_BYTES", 1)
    with page(reference):
        first = private_query(chain, reference)
        second = private_query(chain, reference)
        context = memo._native_current_context()
        assert first is not second and first.receipt_json == second.receipt_json
        assert context.retained_bytes == 0
        assert context._query_receipts == context._packets == {}


@pytest.mark.asyncio
async def test_foreign_task_cannot_use_inherited_page_context(monkeypatch):
    chain, reference, _ = await source(monkeypatch)

    async def other_task():
        with pytest.raises(memo.OriginalPageMemoError):
            private_query(chain, reference)

    with page(reference):
        private_query(chain, reference)
        await asyncio.create_task(other_task())
    assert memo._native_current_context() is None


@pytest.mark.asyncio
async def test_real_original_fence_monotonic_expiry_wipes_memo(monkeypatch):
    chain, reference, _ = await source(monkeypatch)
    with (
        pytest.raises(
            native.OriginalHistoryPgReadError, match="^original_pg_original_expiry$"
        ),
        page(reference) as fence,
    ):
        private_query(chain, reference)
        monkeypatch.setattr(
            native.time, "monotonic_ns", lambda: fence.monotonic_deadline
        )
        private_query(chain, reference)
    assert memo._ACTIVE.get() is None


@pytest.mark.asyncio
async def test_pending_real_task_cancel_never_returns_cached_result(monkeypatch):
    chain, reference, _ = await source(monkeypatch)

    async def cancelled_task():
        with page(reference):
            private_query(chain, reference)
            asyncio.current_task().cancel()
            private_query(chain, reference)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(cancelled_task())
    assert memo._ACTIVE.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner,name",
    [
        (capture, "verify_demo_account_packet"),
        (capture, "_replay_packet"),
        (query, "_verify"),
        (query, "_chain"),
        (memo, "_digest_tokens"),
        (memo, "_request_key"),
    ],
)
async def test_direct_or_inner_dependency_patch_cannot_fill_then_survive_restore(
    monkeypatch, owner, name
):
    chain, reference, payload = await source(monkeypatch)

    def fabricated(*args, **kwargs):
        pytest.fail("patched_callee_must_not_execute_inside_owned_memo")

    with (
        pytest.raises(memo.OriginalPageMemoError),
        monkeypatch.context() as changed,
        page(reference),
    ):
        changed.setattr(owner, name, fabricated)
        if owner is capture:
            private_packet(payload, reference)
        else:
            private_query(chain, reference)
    assert memo._ACTIVE.get() is None
    with page(reference):
        actual = private_query(chain, reference)
        assert type(actual) is query.HistoryQueryVerification
        assert b'"admission":"DENY"' in actual.receipt_json


@pytest.mark.asyncio
async def test_without_active_context_private_helpers_preserve_public_mock_behavior(
    monkeypatch,
):
    chain, reference, payload = await source(monkeypatch)
    packet, receipt = object(), object()
    monkeypatch.setattr(
        capture, "verify_demo_account_packet", lambda *args, **kwargs: packet
    )
    monkeypatch.setattr(
        query, "verify_history_query_chain", lambda *args, **kwargs: receipt
    )
    assert private_packet(payload, reference) is packet
    assert private_query(chain, reference) is receipt


@pytest.mark.asyncio
async def test_unsupported_surrogate_pin_uses_closed_full_verifier_denial(monkeypatch):
    chain, reference, _ = await source(monkeypatch)
    with page(reference), pytest.raises(query.HistoryQueryVerificationError):
        private_query(chain, reference, expected_account_id="\ud800")


@pytest.mark.asyncio
async def test_originally_valid_UTC_fold1_expiry_disables_memo_without_rejection(
    monkeypatch,
):
    chain, reference, _ = await source(monkeypatch)
    with page(reference, fold=1):
        first = private_query(chain, reference)
        second = private_query(chain, reference)
        assert first.receipt_json == second.receipt_json
        assert memo._native_current_context() is None
        assert memo._ACTIVE.get()._query_receipts == memo._ACTIVE.get()._packets == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["aclose", "cancel"])
async def test_foreign_task_generator_close_always_wipes_and_preserves_cancel(
    monkeypatch, operation
):
    chain, reference, _ = await source(monkeypatch)

    async def pages():
        with page(reference):
            private_query(chain, reference)
            yield memo._native_current_context()

    generator = pages()
    context = await anext(generator)

    async def foreign_close():
        if operation == "cancel":
            await generator.athrow(asyncio.CancelledError())
        else:
            await generator.aclose()

    expected = (
        asyncio.CancelledError if operation == "cancel" else memo.OriginalPageMemoError
    )
    try:
        with pytest.raises(expected):
            await asyncio.create_task(foreign_close())
        assert context.closed and context._query_receipts == context._packets == {}
        # The original Context can retain only a closed, empty, unusable object.
        with pytest.raises(memo.OriginalPageMemoError):
            private_query(chain, reference)
    finally:
        # Explicit cleanup of this deliberately misused test-owned Context.
        # No original native runtime context or account authority is repaired.
        assert context.closed
        memo._ACTIVE.set(None)
        await generator.aclose()


@pytest.mark.asyncio
async def test_changed_DB_recorded_metadata_cannot_hit_with_the_same_readback_time(
    monkeypatch,
):
    chain, reference, _ = await source(monkeypatch)
    changed = copy.deepcopy(chain)
    with page(reference):
        private_query(chain, reference)
        # This is an adversarial in-memory copy, never a stored DB row. Make
        # recorded later than unchanged receipt, so real original validation
        # MUST deny rather than the memo returning its previously valid bytes.
        point = changed[0]
        original_readback = point.readback_at
        object.__setattr__(
            point, "db_recorded_at", original_readback + timedelta(microseconds=1)
        )
        assert point.readback_at == original_readback
        with pytest.raises(query.HistoryQueryVerificationError):
            private_query(changed, reference)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("control", 2),
        ("scope", partition.HistoryPartitionScopeV1("demo", "710001", "USDT")),
    ],
)
async def test_mutated_request_revision_or_scope_cannot_reuse_the_live_page(
    monkeypatch, field, value
):
    chain, reference, _ = await source(monkeypatch)
    with pytest.raises(memo.OriginalPageMemoError), page(reference):
        private_query(chain, reference)
        context = memo._native_current_context()
        object.__setattr__(context.request, field, value)
        private_query(chain, reference)
    assert memo._ACTIVE.get() is None
