"""Pinned existing synthetic fixture policy, explicitly absent from native config."""

from decimal import Decimal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification.data import DataQualificationPolicy
from app.trade_qualification.economics import EconomicsPolicy
from app.trade_qualification.executable_quote_v2 import (
    POLICY_SHA256 as QUOTE_PROFILE_SHA256,
)
from app.trade_qualification.full_public_numeric_v2_guard import deny, pin

PROFILE_ID = "ctcc-fixed-synthetic-full-public-numeric-v2"
DATA = {
    "policy_id": "synthetic-prefix-g1-policy",
    "analysis_version": "synthetic-prefix-source-v1",
    "minimum_confirmed_bars": 200,
    "maximum_snapshot_age_seconds": 5,
    "maximum_quote_age_seconds": 10,
    "maximum_reference_age_seconds": 10,
    "maximum_candle_age_intervals": 2,
    "maximum_reference_conflict_bps": "10",
    "maximum_mark_dislocation_bps": "10",
    "maximum_spread_bps": "5",
    "maximum_absolute_funding_bps": "10",
}
PROTECTION = {
    "expected_slippage_bps": "1",
    "cost_bps": "10",
    "min_net_rr": "2",
    "min_stop_distance_atr": "1",
    "atr_buffer_multiplier": "0.25",
    "minimum_buffer_bps": "5",
}
COST = {
    "policy_id": "synthetic-cost-policy",
    "round_trip_fee_bps": "10",
    "round_trip_slippage_bps": "4",
    "funding_buffer_bps": "2",
    "funding_periods": 1,
    "minimum_net_rr": "2",
    "maximum_spread_bps": "8",
    "maximum_funding_bps": "30",
    "maximum_quote_age_seconds": 30,
}
PROFILE_BYTES = canonical(
    {
        "schema_version": "ctcc.full_public_numeric_profile.v2",
        "profile_id": PROFILE_ID,
        "origin": "existing_unit_fixture_policy_projected_costs_only",
        "data": DATA,
        "protection": PROTECTION,
        "economics": COST,
        "quote_profile_sha256": QUOTE_PROFILE_SHA256,
        "native_profile_registered": False,
        "actual_fee_source": None,
        "execution_authority": False,
        "admission": "DENY",
    }
)
PROFILE_SHA256 = sha(PROFILE_BYTES)


def fixed_profile(profile_id, expected_sha256):
    pin(expected_sha256)
    if (
        type(profile_id) is not str
        or profile_id != PROFILE_ID
        or expected_sha256 != PROFILE_SHA256
    ):
        deny("numeric_v2_fixed_synthetic_profile_required")
    sealed = decode(PROFILE_BYTES)
    if sha(PROFILE_BYTES) != PROFILE_SHA256:
        deny("numeric_v2_fixed_profile_bytes_changed")
    data_values = {
        key: Decimal(value) if key.endswith("_bps") else value
        for key, value in sealed["data"].items()
    }
    cost_values = {
        key: Decimal(value)
        if key.endswith("_bps") or key == "minimum_net_rr"
        else value
        for key, value in sealed["economics"].items()
    }
    return (
        DataQualificationPolicy(**data_values),
        EconomicsPolicy(**cost_values),
        {key: Decimal(value) for key, value in sealed["protection"].items()},
    )
