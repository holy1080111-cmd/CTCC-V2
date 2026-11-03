"""Constructed display DATA never clears an owned service/controller hold."""

import pytest

from app.demo_automation import native_caller_v2 as caller
from app.demo_automation.native_requests_v2 import NativeDemoFilledCompleteResultV3
from app.trade_qualification import native_controlled_close_v1 as close
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post


@pytest.mark.asyncio
@pytest.mark.parametrize("as_display_model", (False, True))
async def test_constructed_completed_data_never_releases_controller(as_display_model):
    data = (
        NativeDemoFilledCompleteResultV3(
            invocation_id="synthetic",
            closure_outcome_id="a" * 64,
            cleanup_outcome_id="b" * 64,
            forensics_outcome_id="c" * 64,
            report_delivery_status="not_eligible_under_current_report_policy",
        )
        if as_display_model
        else post.NativeFilledReconciliationSettlementResultV1(
            "d" * 64, "a" * 64, "e" * 64, "b" * 64, "c" * 64, None
        )
    )
    before = tuple(post._FILLED_SETTLED_RESULTS.items())
    with pytest.raises(post.NativePostsubmitError):
        post._take_native_filled_caller_completion_v1(
            data, key=caller._SETTLEMENT_KEY_V1
        )
    assert tuple(post._FILLED_SETTLED_RESULTS.items()) == before


@pytest.mark.asyncio
async def test_completed_display_flag_cannot_start_owned_close():
    data = post.NativeReconciliationResultV1(
        "a" * 64, "filled_and_protected_hold_retained", "b" * 64, "c" * 64, True
    )
    before = tuple(post._FILLED_WORKFLOWS.items())
    with pytest.raises(post.NativePostsubmitError):
        post.complete_owned_native_filled_demo_v1(data)
    assert tuple(post._FILLED_WORKFLOWS.items()) == before


@pytest.mark.asyncio
async def test_foreign_close_compaction_cannot_remove_retirement_data():
    before = tuple(close._FILLED_RETIRED.items())
    with pytest.raises(close.NativeControlledCloseError):
        close.compact_native_filled_close_retirement_v1(
            object(), object(), key=post._FILLED_SOURCE_SETTLEMENT_KEY_V1
        )
    assert tuple(close._FILLED_RETIRED.items()) == before
