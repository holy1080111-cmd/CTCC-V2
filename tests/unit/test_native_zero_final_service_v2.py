"""Historical DATA and ownership denials; no native/DB/Control cap is forged."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.database.repositories import native_postsubmit_v1 as repository
from app.demo_automation import native_caller_v2 as caller
from app.demo_automation.native_requests_v2 import NativeDemoZeroCompleteResultV3
from app.domain.source_primitives import canonical
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_controlled_account_source_v6 as source
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


def row(version, identity, *, status="rejected", reservation="a" * 64):
    return repository.Outcome(
        outcome_id=identity * 64,
        reservation_id=reservation,
        status=status,
        observation_kind="reconciliation",
        capture_json=canonical({"version": version}).decode(),
    )


def test_later_cleanup_ack_does_not_replace_original_immutable_zero_closure():
    original = row("ctcc.native_zero_fok_flat_closure.v1", "b")
    cleanup = row("ctcc.native_zero_trade_cleanup.v2", "c", status="acknowledged")
    assert (
        repository._zero_closure_outcome_v2((original, cleanup), "a" * 64) is original
    )
    # Subsequent same-scope activity is not a rewrite of this event's row.
    assert original.outcome_id == "b" * 64 and original.status == "rejected"


def test_duplicate_or_cross_event_closure_data_remains_ambiguous():
    original = row("ctcc.native_zero_fok_flat_closure.v1", "b")
    duplicate = row("ctcc.native_zero_fok_flat_closure.v1", "c")
    foreign = row(
        "ctcc.native_zero_trade_cleanup.v2",
        "d",
        status="acknowledged",
        reservation="e" * 64,
    )
    for rows in ((original, duplicate), (original, foreign), ()):
        with pytest.raises(repository.NativePostsubmitRepositoryError):
            repository._zero_closure_outcome_v2(rows, "a" * 64)


@pytest.mark.parametrize("status", ("acknowledged", "uncertain"))
def test_a_cleanup_or_ack_label_never_becomes_zero_rejection_evidence(status):
    supplied = row("ctcc.native_zero_fok_flat_closure.v1", "b", status=status)
    with pytest.raises(repository.NativePostsubmitRepositoryError):
        repository._zero_closure_outcome_v2((supplied,), "a" * 64)


@pytest.mark.parametrize("copy", (False, True))
def test_constructed_private_result_data_cannot_release_caller_controller(copy):
    supplied = post.NativeZeroReconciliationSettlementResultV2(
        "a" * 64, "b" * 64, "c" * 64, "d" * 64
    )
    if copy:
        supplied = replace(supplied)
    before = tuple(post._ZERO_SETTLED_RESULTS_V2)
    with pytest.raises(ValueError):
        post._take_native_zero_caller_completion_v2(
            supplied, key=caller._SETTLEMENT_KEY_V1
        )
    assert tuple(post._ZERO_SETTLED_RESULTS_V2) == before


def test_output_complete_true_is_never_the_private_completion_capability():
    displayed = NativeDemoZeroCompleteResultV3(
        invocation_id="public", closure_outcome_id="a" * 64, cleanup_outcome_id="b" * 64
    )
    assert displayed.execution_complete is True and displayed.trade_created is False
    with pytest.raises(ValueError):
        post._take_native_zero_caller_completion_v2(
            displayed, key=caller._SETTLEMENT_KEY_V1
        )


def test_object_new_cannot_compact_source_without_committed_actual_row():
    forged = object.__new__(post.OwnedNativeZeroCleanupCommittedV2)
    with pytest.raises(ValueError):
        post._take_native_zero_cleanup_committed_for_source_v2(
            forged, key=source._ZERO_FINAL_CLEANUP_KEY_V2
        )
    retirement = object.__new__(source._RetiredZeroFinalCleanupV2)
    with pytest.raises(ValueError):
        source.compact_native_zero_final_source_retirement_v2(
            retirement, forged, key=post._SOURCE_SETTLEMENT_KEY_V1
        )


def test_unstarted_cleanup_close_retains_full_unknown_graph():
    binding, roots = object(), (object(), object(), object())
    state = {"binding": binding, "held": roots, "cleanup_status": "STARTED"}
    post._UNKNOWN[binding] = state
    try:
        attempt = post._CleanupAttempt(state)
        attempt.close()
        assert post._UNKNOWN[binding] is state and state["held"] is roots
        assert state["cleanup_status"] == "UNKNOWN"
    finally:
        post._UNKNOWN.pop(binding)


def test_control_algorithm_dispatch_needs_exact_owned_registry_not_copied_flag():
    token, marker = object(), object()
    record = {"token": token, "zero_final_marker": marker}
    calls = []

    def zero(actual):
        calls.append(actual)
        return "negative_primitive_only"

    def filled(_actual):
        raise ValueError("not_in_filled_registry")

    primitive = foreign_primitive_v1(
        source._negative_cleanup_control_check_v13,
        {
            "_FLAT_CLEANUP_RECORDS_V6": {token: record},
            "_ZERO_FINAL_MARKER_V2": marker,
            "_zero_final_check_v2": zero,
            "_filled_cleanup_check_v6": filled,
        },
    )
    assert primitive(record) == "negative_primitive_only"
    with pytest.raises(ValueError):
        primitive(dict(record))
    assert calls == [record]
    with pytest.raises(guard.NativeSourceClosureError):
        guard._function_pin(primitive, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("filled", (False, True))
async def test_already_sql_flat_cleanup_error_never_calls_scoped_stop_on_newer_owner(
    filled,
):
    calls = []

    class OldOwner:
        @staticmethod
        def _revoke(actual, *, stop):
            calls.append((actual, stop))

    owner = OldOwner()

    def unavailable(_state):
        raise ValueError("negative_foreign_primitive_only")

    selected = post._run_filled_flat_cleanup_v1 if filled else post._run_flat_cleanup_v1
    binding = (
        "_captured_filled_cleanup_source_v1"
        if filled
        else "_captured_flat_cleanup_source_v1"
    )
    primitive = foreign_primitive_v1(
        selected,
        {
            binding: unavailable,
            "_control_owner55": SimpleNamespace(
                original=SimpleNamespace(DemoControlOwner=OldOwner)
            ),
        },
    )
    state = {"source": SimpleNamespace(control_owner=owner)}
    with pytest.raises(post.NativePostsubmitError):
        await primitive(state)
    assert calls == [(owner, False)]
    assert state["filled_cleanup_status" if filled else "cleanup_status"] == "UNKNOWN"
    with pytest.raises(guard.NativeSourceClosureError):
        guard._function_pin(primitive, {})
