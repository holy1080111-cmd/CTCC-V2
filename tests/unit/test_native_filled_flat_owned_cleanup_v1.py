"""Ownership negatives for the actual filled-flat companion, no fake permit."""

import copy

import pytest

from app.exchange.okx import native_demo_write_v2 as write
from app.trade_qualification import native_controlled_close_v1 as close
from app.trade_qualification import native_ledger_runtime_v2 as ledger
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post


@pytest.mark.parametrize(
    "kind",
    (
        post.OwnedNativeFilledFlatCleanupCompletionV1,
        post.OwnedNativeFilledFlatRetirementV1,
        post.OwnedNativeFilledCleanupCommittedV1,
        ledger._OwnedNativeFilledFlatLedgerCleanupV1,
        close._RetiredNativeFilledCloseV1,
    ),
)
def test_filled_cleanup_opaque_owners_cannot_be_constructed(kind):
    with pytest.raises(
        (
            post.NativePostsubmitError,
            close.NativeControlledCloseError,
            ledger.ledger.QualificationLedgerError,
        )
    ):
        kind()


@pytest.mark.parametrize(
    "kind",
    (
        post.OwnedNativeFilledFlatCleanupCompletionV1,
        post.OwnedNativeFilledFlatRetirementV1,
        post.OwnedNativeFilledCleanupCommittedV1,
    ),
)
def test_filled_cleanup_object_new_does_not_make_copyable_owner(kind):
    foreign = object.__new__(kind)
    with pytest.raises(post.NativePostsubmitError):
        copy.copy(foreign)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "classification,hold",
    (
        ("own_entry_and_close_filled_flat", False),
        ("filled_and_protected_hold_retained", True),
        ("terminal_zero_fill_flat", False),
    ),
)
async def test_constructed_result_never_enters_filled_cleanup(classification, hold):
    forged = post.NativeReconciliationResultV1(
        "a" * 64, classification, "b" * 64, "c" * 64, hold
    )
    with pytest.raises(post.NativePostsubmitError):
        post.settle_owned_native_filled_reconciliation_v1(forged)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,kind",
    (
        (
            "_take_native_filled_flat_cleanup_completion_for_root_v1",
            post.OwnedNativeFilledFlatCleanupCompletionV1,
        ),
        (
            "_take_native_filled_flat_retirement_for_root_v1",
            post.OwnedNativeFilledFlatRetirementV1,
        ),
    ),
)
async def test_root_cannot_adopt_unregistered_object_new_cap(name, kind):
    before = tuple(
        map(
            len,
            (
                post._FILLED_CLEANUP_COMPLETIONS,
                post._FILLED_RETIREMENTS,
                post._FILLED_SETTLEMENTS,
                post._UNKNOWN,
            ),
        )
    )
    with pytest.raises(post.NativePostsubmitError):
        getattr(post, name)(
            object.__new__(kind), key=write._POSTSUBMIT_FILLED_SETTLEMENT_KEY_V1
        )
    assert before == tuple(
        map(
            len,
            (
                post._FILLED_CLEANUP_COMPLETIONS,
                post._FILLED_RETIREMENTS,
                post._FILLED_SETTLEMENTS,
                post._UNKNOWN,
            ),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    (
        "prepare_native_filled_flat_ledger_cleanup_v1",
        "replay_native_filled_flat_ledger_cleanup_v1",
        "retire_native_filled_flat_ledger_cleanup_v1",
        "finalize_native_filled_flat_ledger_cleanup_v1",
    ),
)
async def test_foreign_ledger_cleanup_never_removes_unknown_hold(name):
    before = tuple(ledger._UNKNOWN.items())
    with pytest.raises(
        (post.NativePostsubmitError, ledger.ledger.QualificationLedgerError)
    ):
        getattr(ledger, name)(object(), key=post._FILLED_SOURCE_SETTLEMENT_KEY_V1)
    assert tuple(ledger._UNKNOWN.items()) == before


@pytest.mark.asyncio
async def test_foreign_close_retirement_never_pops_actual_registries():
    before = tuple(
        map(len, (close._ENTRIES, close._INTENTS, close._POST_READS, close._UNKNOWN))
    )
    with pytest.raises((post.NativePostsubmitError, close.NativeControlledCloseError)):
        close.retire_native_filled_close_resources_v1(
            object(), key=post._FILLED_SOURCE_SETTLEMENT_KEY_V1
        )
    assert before == tuple(
        map(len, (close._ENTRIES, close._INTENTS, close._POST_READS, close._UNKNOWN))
    )


@pytest.mark.asyncio
async def test_foreign_final_persistence_binding_cannot_renew_any_source():
    before = tuple(map(len, (post._FILLED_PERSISTENCE, post._LIVE, post._UNKNOWN)))
    with pytest.raises(post.NativePostsubmitError):
        post._replay_persistence_source_v1(
            object(), key=post._repository._SOURCE_KEY_V1
        )
    assert before == tuple(
        map(len, (post._FILLED_PERSISTENCE, post._LIVE, post._UNKNOWN))
    )
