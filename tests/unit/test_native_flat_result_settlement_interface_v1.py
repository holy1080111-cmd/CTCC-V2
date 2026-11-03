"""DATA constructors never grant owned physical-cleanup permission."""

from dataclasses import replace

import pytest

from app.exchange.okx import native_demo_write_v2 as write
from app.trade_qualification import native_postsubmit_reconciliation_v1 as native


def result():
    return native.NativeReconciliationResultV1(
        "a" * 64, "terminal_zero_fill_flat", "b" * 64, "c" * 64, False
    )


def test_result_fields_and_false_risk_hold_are_not_an_owned_move():
    data = result()
    assert replace(data) == data and replace(data) is not data
    with pytest.raises(native.NativePostsubmitError):
        native._take_native_flat_result_for_root_settlement_v1(
            data, key=write._POSTSUBMIT_SETTLEMENT_KEY_V1
        )


def test_exact_view_constructor_and_copy_cannot_fake_private_replay():
    data = result()
    response = native.NativeSubmissionResponseDataV1(
        object(), object(), object(), object()
    )
    view = native.NativeFlatResultForRootSettlementV1(
        object(),
        response.observed,
        data,
        response,
        data.outcome_id,
        data.receipt_sha256,
        (),
    )
    for candidate in (view, replace(view)):
        with pytest.raises(native.NativePostsubmitError):
            native._replay_native_flat_result_for_root_settlement_v1(
                candidate, key=write._POSTSUBMIT_SETTLEMENT_KEY_V1
            )


def test_foreign_private_key_is_not_accepted():
    with pytest.raises(native.NativePostsubmitError):
        native._take_native_flat_result_for_root_settlement_v1(result(), key=object())
