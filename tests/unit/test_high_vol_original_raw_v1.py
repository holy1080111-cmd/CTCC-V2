"""Real raw-G1 profile lineage on declared fixtures; no runtime/venue authority.

The retained normal expansion tape exercises the opt-in profile's original
path without inventing HighVol permission or altering an observed source.
"""

import pytest

from app.domain.source_primitives import decode
from app.trade_qualification import high_vol_momentum_qualification_v1 as profile
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from tests.unit import test_native_owned_eight_family_v3 as fixture


@pytest.mark.parametrize("direction", ("long", "short"))
def test_profile_raw_g1_original_replay_keeps_event_entry_zone_and_expiry(
    tmp_path, direction
):
    source = fixture._owned_full_bracket_raw_source_v3(
        "volatility_expansion", direction
    )
    diagnostic, packet, args, legacy = fixture._capture_raw_family_values_v3(
        source, "volatility_expansion", tmp_path / "raw"
    )
    selected = {
        **args,
        "high_vol_profile_sha256": profile.PROFILE_SHA256,
        "expected_policy_sha256": precursor.OWNED_HIGH_VOL_POLICY_SHA256,
    }
    actual = precursor.derive_owned_original_candidate_precursor_v3(
        diagnostic.packet, packet, **selected
    )
    assert actual.intent is not None
    old, new = decode(legacy.receipt_json), decode(actual.receipt_json)
    assert new["high_vol_profile_sha256"] == profile.PROFILE_SHA256
    assert new["policy_sha256"] != old["policy_sha256"]
    for key in (
        "intent",
        "event_key",
        "detection",
        "initial_entry",
        "zone",
        "created_at",
        "expires_at",
        "original_event_expires_at",
        "conditions",
        "public_bundle_sha256",
        "g1",
        "timeline_sha256",
        "route",
    ):
        assert new[key] == old[key]
    assert (
        precursor.verify_owned_original_candidate_precursor_v3(
            actual, diagnostic.packet, packet, **selected
        ).receipt_json
        == actual.receipt_json
    )
    with pytest.raises(ValueError, match="replay_changed"):
        precursor.verify_owned_original_candidate_precursor_v3(
            legacy, diagnostic.packet, packet, **selected
        )
    with pytest.raises(ValueError, match="replay_changed"):
        precursor.verify_owned_original_candidate_precursor_v3(
            actual, diagnostic.packet, packet, **args
        )
    computed = fixture._family_replay_values_v3(
        "volatility_expansion", direction, diagnostic, packet, selected, actual
    )[-1]
    assert all(gate.passed for gate in computed.result.gates)
    assert computed.intent == legacy.intent
    assert computed.detection.model_dump(mode="json") == old["detection"]
    assert computed.original_zone.model_dump(mode="json") == old["zone"]
    assert (
        decode(computed.family_history_json)["high_vol_profile_sha256"]
        == profile.PROFILE_SHA256
    )
    assert (
        computed.result.gates[1].measured_values["high_vol_profile_sha256"]
        == profile.PROFILE_SHA256
    )
    assert computed.execution_authority is False
