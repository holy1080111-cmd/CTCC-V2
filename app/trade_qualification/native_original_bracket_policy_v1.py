"""Pinned structural-bracket inspection assumptions, never a trading policy.

The numbers describe one conservative diagnostic scenario. Passing this
inspection cannot establish G6--G11, calibrated economics, or order authority.
"""

from decimal import Decimal

from app.domain.source_primitives import canonical, decode, sha

POLICY_RECORD = canonical(
    {
        "schema_version": "ctcc.native_original_bracket_inspection_policy.v1",
        "purpose": "source_owned_fixed_geometry_inspection_only",
        "strategy_family": "base_four_only",
        "expected_slippage_bps": "5",
        "cost_bps": "30",
        "min_net_rr": "2",
        "min_stop_distance_atr": "1",
        "atr_buffer_multiplier": "0.25",
        "minimum_buffer_bps": "5",
        "calibrated_for_trading": False,
        "g8_g9_evaluated": False,
        "execution_authority": False,
        "admission": "DENY",
    }
)
POLICY_RECORD_SHA256 = (
    "f45da0fda482f0ed72bb3432e3b2ebf3506d953e56ee97322a60866c41ab1cf3"
)
_NUMERIC = (
    "expected_slippage_bps",
    "cost_bps",
    "min_net_rr",
    "min_stop_distance_atr",
    "atr_buffer_multiplier",
    "minimum_buffer_bps",
)


def fixed_native_original_bracket_policy() -> dict[str, Decimal]:
    """Return fresh exact Decimals after checking the versioned source pin."""
    if type(POLICY_RECORD) is not bytes or sha(POLICY_RECORD) != POLICY_RECORD_SHA256:
        raise ValueError("native_original_bracket_policy_changed")
    record = decode(POLICY_RECORD)
    if (
        canonical(record) != POLICY_RECORD
        or record["schema_version"]
        != "ctcc.native_original_bracket_inspection_policy.v1"
        or record["purpose"] != "source_owned_fixed_geometry_inspection_only"
        or record["strategy_family"] != "base_four_only"
        or record["calibrated_for_trading"] is not False
        or record["g8_g9_evaluated"] is not False
        or record["execution_authority"] is not False
        or record["admission"] != "DENY"
    ):
        raise ValueError("native_original_bracket_policy_invalid")
    return {name: Decimal(record[name]) for name in _NUMERIC}


# New owned-family selection contract. Historical base-four policy stays exact.
OWNED_FAMILY_CONTRACTS_V2 = (
    ("trend_pullback", "base_v1", "base_v1"),
    ("breakout_continuation", "base_v1", "base_v1"),
    ("fvg_return", "base_v1", "base_v1"),
    ("order_block_return", "base_v1", "base_v1"),
    ("volatility_expansion", "history_v2", "base_v1"),
    ("structure_reversal", "history_v3", "reversal_v1"),
    ("range_reversal", "history_v5", "neutral_range_v1"),
    ("liquidity_sweep_reversal", "history_swap_v2", "sweep_extreme_swap_v2"),
)
OWNED_FAMILY_POLICY_V2 = canonical(
    {
        "schema_version": "ctcc.native_owned_strategy_family_policy.v2",
        "families": OWNED_FAMILY_CONTRACTS_V2,
        "original_source": "same_held_raw_public_V2_and_actual_G1",
        "history": "actual_original_closed_prefix_and_page_membership",
        "event": "fixed_original_setup_pivot_trigger_and_native_expiry",
        "current": "same_family_permission_and_original_event_path_after_G12",
        "protection": "same_original_joint_bracket_no_price_or_geometry_repair",
        "numeric_floor_source_sha256": POLICY_RECORD_SHA256,
        "configured_policy": "captured_settings_may_tighten_existing_numeric_floors",
        "router_unknown_relabelled_to_trend": False,
        "continuous_tick_path_or_past_receipt_availability_claimed": False,
        "execution_authority": False,
    }
)
OWNED_FAMILY_POLICY_V2_SHA256 = sha(OWNED_FAMILY_POLICY_V2)


def fixed_native_owned_family_policy_v2(strategy):
    if type(strategy) is not str:
        raise ValueError("native_owned_family_policy_invalid")
    records = [row for row in OWNED_FAMILY_CONTRACTS_V2 if row[0] == strategy]
    if (
        len(records) != 1
        or sha(OWNED_FAMILY_POLICY_V2) != OWNED_FAMILY_POLICY_V2_SHA256
    ):
        raise ValueError("native_owned_family_policy_invalid")
    policy = decode(OWNED_FAMILY_POLICY_V2)
    if (
        canonical(policy) != OWNED_FAMILY_POLICY_V2
        or policy["schema_version"] != "ctcc.native_owned_strategy_family_policy.v2"
        or policy["router_unknown_relabelled_to_trend"] is not False
        or policy["execution_authority"] is not False
    ):
        raise ValueError("native_owned_family_policy_invalid")
    return canonical(
        {
            "schema_version": "ctcc.native_owned_selected_family_policy.v2",
            "policy_sha256": OWNED_FAMILY_POLICY_V2_SHA256,
            "strategy": strategy,
            "history_contract": records[0][1],
            "selection_contract": records[0][2],
            "execution_authority": False,
        }
    )
