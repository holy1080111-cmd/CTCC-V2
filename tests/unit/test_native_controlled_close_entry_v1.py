"""Ownership negatives only; synthetic DATA cannot become a close request."""

import pytest

from app.trade_qualification import native_controlled_close_v1 as close
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post


@pytest.mark.parametrize(
    "kind", (close.OwnedNativeCloseEntryV1, close.OwnedNativeCloseIntentV1)
)
def test_close_entry_and_intent_are_not_data_constructible(kind):
    with pytest.raises(close.NativeControlledCloseError):
        kind()


def test_exact_looking_filled_data_cannot_mint_owned_close_entry():
    supplied = post.NativeReconciliationResultV1(
        "a" * 64, "filled_and_protected_hold_retained", "b" * 64, "c" * 64, True
    )
    with pytest.raises(ValueError):
        close.take_owned_native_close_entry_v1(supplied)


def test_object_new_does_not_forge_post_read_registration():
    supplied = object.__new__(close.OwnedNativeCloseEntryV1)
    with pytest.raises(ValueError):
        post._register_native_close_read_v1(supplied, key=close._SOURCE_KEY_V1)


def test_current_zero_fill_result_cannot_be_repurposed_as_close_entry():
    supplied = post.NativeReconciliationResultV1(
        "a" * 64, "terminal_zero_fill_flat", "b" * 64, "c" * 64, False
    )
    with pytest.raises(ValueError):
        close.take_owned_native_close_entry_v1(supplied)
