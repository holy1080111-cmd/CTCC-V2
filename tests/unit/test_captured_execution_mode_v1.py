"""Synthetic raw packet replay; no native account, credential or order IO."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import captured_execution_mode_v1 as mode
from tests.unit.test_account_current_source_v7 import current_plan_v7, empty_v7_pages
from tests.unit.test_qualification_account_capture import INSTRUMENT, row, verify
from tests.unit.test_qualification_account_v4 import records


def packet(position_mode="net_mode", *, leverage="3.000", sides=None, after_mode=None):
    selected = current_plan_v7()
    pages = empty_v7_pages()
    pages["config_before"] = [[row("config_before", posMode=position_mode)]]
    pages["config_after"] = [
        [
            row(
                "config_after",
                posMode=position_mode if after_mode is None else after_mode,
            )
        ]
    ]
    sides = (
        (("net",) if position_mode == "net_mode" else ("long", "short"))
        if sides is None
        else sides
    )
    for margin in ("cross", "isolated"):
        stream = "leverage_" + margin
        pages[stream] = [[row(stream, posSide=side, lever=leverage) for side in sides]]
    return verify(
        *records(selected=selected, pages=pages, streams=capture.V7_CURRENT_STREAMS)
    )


def derive(value):
    frozen = capture.freeze_demo_account_packet(
        value, expected_plan_sha256=value.plan_sha256
    )
    return mode.derive_captured_execution_mode_v1(
        value,
        expected_plan_sha256=value.plan_sha256,
        expected_packet_sha256=frozen.sha256,
    )


@pytest.mark.parametrize(
    "position_mode,direction,side",
    [
        ("net_mode", "long", "net"),
        ("net_mode", "short", "net"),
        ("long_short_mode", "long", "long"),
        ("long_short_mode", "short", "short"),
    ],
)
def test_actual_mode_and_unique_isolated_side_are_retained(
    position_mode, direction, side
):
    original = packet(position_mode)
    before = capture.freeze_demo_account_packet(
        original, expected_plan_sha256=original.plan_sha256
    )
    observed = derive(original)
    selected = mode.select_captured_isolated_execution_v1(
        observed, instrument_id=INSTRUMENT, direction=direction, requested_leverage=3
    )
    assert selected.position_mode == position_mode and selected.position_side == side
    assert selected.leverage == Decimal("3.000")
    assert json.loads(selected.observation.raw_member_json)["lever"] == "3.000"
    source = json.loads(selected.observation.source_reference_json)
    assert source["stream"] == "leverage_isolated"
    assert source["packet_sha256"] == before.sha256
    assert source["endpoint_exchange_asof"] is None
    assert (
        not selected.execution_authority and not observed.source_authenticity_verified
    )
    assert (
        capture.freeze_demo_account_packet(
            original, expected_plan_sha256=original.plan_sha256
        )
        == before
    )
    assert (
        mode.verify_captured_execution_mode_v1(
            observed,
            original,
            expected_plan_sha256=original.plan_sha256,
            expected_packet_sha256=before.sha256,
        )
        == observed
    )


def test_requested_leverage_cannot_replace_actual_configuration():
    observed = derive(packet(leverage="3"))
    with pytest.raises(
        mode.CapturedExecutionModeError,
        match="^captured_isolated_leverage_missing_duplicate_or_mismatch$",
    ):
        mode.select_captured_isolated_execution_v1(
            observed, instrument_id=INSTRUMENT, direction="long", requested_leverage=4
        )


def test_missing_direction_is_not_inferred_from_opposite_hedge_side():
    observed = derive(packet("long_short_mode", sides=("short",)))
    with pytest.raises(
        mode.CapturedExecutionModeError,
        match="^captured_isolated_leverage_missing_duplicate_or_mismatch$",
    ):
        mode.select_captured_isolated_execution_v1(
            observed, instrument_id=INSTRUMENT, direction="long", requested_leverage=3
        )


def test_duplicate_data_cannot_select_one_convenient_leverage_member():
    observed = derive(packet())
    dirty = replace(observed, leverage_observations=observed.leverage_observations * 2)
    with pytest.raises(
        mode.CapturedExecutionModeError,
        match="^captured_isolated_leverage_missing_duplicate_or_mismatch$",
    ):
        mode.select_captured_isolated_execution_v1(
            dirty, instrument_id=INSTRUMENT, direction="long", requested_leverage=3
        )


def test_wrong_mode_side_is_rejected_instead_of_defaulting_net():
    original = packet("long_short_mode", sides=("net",))
    with pytest.raises(
        mode.CapturedExecutionModeError,
        match="^captured_leverage_side_conflicts_with_position_mode$",
    ):
        derive(original)


def test_config_change_is_rejected_by_original_packet_replay():
    with pytest.raises(capture.AccountCaptureError, match="account_mode_changed"):
        packet("net_mode", after_mode="long_short_mode")
