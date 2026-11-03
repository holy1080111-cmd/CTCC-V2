"""Negative ownership/retirement contracts; no fake genuine Source/DB caps."""

import pytest

from app.exchange.okx import native_demo_write_v2 as write
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_ledger_runtime_v2 as ledger
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post


@pytest.mark.parametrize(
    "kind",
    (
        post._OwnedNativeFlatSettlementV1,
        post.OwnedNativeFlatCleanupCompletionV1,
        ledger._OwnedNativeFlatLedgerCleanupV1,
    ),
)
def test_cleanup_capabilities_are_not_data_constructible(kind):
    with pytest.raises(
        (post.NativePostsubmitError, ledger.ledger.QualificationLedgerError)
    ):
        kind()


def test_false_risk_hold_and_known_schema_are_not_settlement_ownership():
    data = post.NativeReconciliationResultV1(
        "a" * 64, "terminal_zero_fill_flat", "b" * 64, "c" * 64, False
    )
    with pytest.raises(post.NativePostsubmitError):
        post.settle_owned_native_reconciliation_v1(data)


def test_root_completion_cannot_be_forged_even_with_object_new():
    # object.__new__ bypasses Python construction, not the genuine registry.
    forged = object.__new__(post.OwnedNativeFlatCleanupCompletionV1)
    with pytest.raises(post.NativePostsubmitError):
        post._take_native_flat_cleanup_completion_for_root_v1(
            forged, key=write._POSTSUBMIT_SETTLEMENT_KEY_V1
        )


def test_ledger_opaque_shape_without_actual_registry_does_not_retire():
    forged = object.__new__(ledger._OwnedNativeFlatLedgerCleanupV1)
    for operation in (
        ledger.replay_native_flat_ledger_cleanup_v1,
        ledger.retire_native_flat_ledger_cleanup_v1,
        ledger.finalize_native_flat_ledger_cleanup_v1,
    ):
        with pytest.raises(ledger.ledger.QualificationLedgerError):
            operation(forged, key=post._SOURCE_SETTLEMENT_KEY_V1)


def test_data_tuple_cannot_claim_actual_root_finalization():
    data = ("ctcc.native_transport_resources_finalized.v1", "b" * 64, "c" * 64)
    with pytest.raises(post.NativePostsubmitError):
        post._replay_native_flat_ledger_after_finalization_v1(
            data, key=ledger._FLAT_CLEANUP_KEY_V1
        )


def test_cleanup_source_bootstrap_has_only_fixed_private_purpose():
    with pytest.raises(
        ValueError, match="native_flat_cleanup_captured_source_required"
    ):
        account._require_native_flat_cleanup_source_v1(key=object())


def test_unstarted_cleanup_close_keeps_actual_held_object_graph():
    # No permit is fabricated: an unstarted negative cleanup coroutine must
    # retain even arbitrary held objects, without reaching Root/Source APIs.
    root, source, transaction = object(), object(), object()
    state = {"cleanup_status": "STARTED", "held": (root, source, transaction)}
    attempt = post._CleanupAttempt(state)
    attempt.close()
    assert state["cleanup_status"] == "UNKNOWN"
    assert state["held"] == (root, source, transaction)
