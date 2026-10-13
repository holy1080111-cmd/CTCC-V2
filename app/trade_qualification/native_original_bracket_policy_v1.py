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
