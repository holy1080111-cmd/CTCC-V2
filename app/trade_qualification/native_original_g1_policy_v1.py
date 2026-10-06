"""Fixed native-original G1 inspection policy; never an admission policy.

The numerical limits are conservative engineering inspection bounds. They are
not calibrated trading thresholds and cannot grant candidate, risk or order
authority. In particular, no synthetic numeric profile is imported here.
"""

from decimal import Decimal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import data
from app.trade_qualification.public_market_collector_v2 import SCHEMA_VERSION
from app.trade_qualification.quote_collector_v2 import (
    POLICY_SHA256 as QUOTE_PROFILE_SHA256,
)

POLICY_ID = "ctcc-native-original-g1-inspection-v1"
POLICY_RECORD = canonical(
    {
        "schema_version": "ctcc.native_original_g1_policy.v1",
        "policy_id": POLICY_ID,
        "purpose": "native_original_data_inspection_only",
        "public_packet_schema": SCHEMA_VERSION,
        "quote_profile_sha256": QUOTE_PROFILE_SHA256,
        "calibrated_for_trading": False,
        "candidate_authority": False,
        "execution_authority": False,
        "admission": "DENY",
        "data": {
            "policy_id": POLICY_ID,
            "analysis_version": "ctcc-native-original-g1-inspection-v1",
            "minimum_confirmed_bars": 200,
            "maximum_snapshot_age_seconds": 5,
            "maximum_quote_age_seconds": 5,
            "maximum_reference_age_seconds": 5,
            "maximum_candle_age_intervals": 1,
            "maximum_reference_conflict_bps": "5",
            "maximum_mark_dislocation_bps": "5",
            "maximum_spread_bps": "3",
            "maximum_absolute_funding_bps": "5",
        },
    }
)
# Reviewed source pin: changing any field requires a deliberate policy revision.
POLICY_RECORD_SHA256 = (
    "a106793d6fe6d9bec6a6c4134c458060ff0cacb652be05ebe82578c81df538d1"
)
DATA_POLICY_SHA256 = "54c9904b81ed3111ff9bfd85ba53b24d59cab630ca1f8c360c291dce2b73bc4f"


def fixed_native_original_g1_policy():
    """Rebuild an exact policy from pinned bytes, with no caller parameters."""
    if type(POLICY_RECORD) is not bytes or sha(POLICY_RECORD) != POLICY_RECORD_SHA256:
        raise ValueError("native_original_g1_policy_record_changed")
    record = decode(POLICY_RECORD)
    if (
        canonical(record) != POLICY_RECORD
        or record["schema_version"] != "ctcc.native_original_g1_policy.v1"
        or record["policy_id"] != POLICY_ID
        or record["public_packet_schema"] != SCHEMA_VERSION
        or record["quote_profile_sha256"] != QUOTE_PROFILE_SHA256
        or record["calibrated_for_trading"] is not False
        or record["candidate_authority"] is not False
        or record["execution_authority"] is not False
        or record["admission"] != "DENY"
    ):
        raise ValueError("native_original_g1_policy_record_invalid")
    values = {
        key: Decimal(value) if key.endswith("_bps") else value
        for key, value in record["data"].items()
    }
    policy = data.DataQualificationPolicy.model_validate(values, strict=True)
    if (
        policy.policy_id != POLICY_ID
        or sha(data._canonical(policy)) != DATA_POLICY_SHA256
    ):
        raise ValueError("native_original_g1_data_policy_changed")
    return policy
