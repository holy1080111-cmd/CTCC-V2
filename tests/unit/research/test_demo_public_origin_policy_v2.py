"""The V2 route record replays policy, never authenticated Demo provenance."""

import json
from types import MappingProxyType

import pytest

from app.domain import source_primitives as primitives
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import demo_public_origin as origin
from app.trade_qualification import demo_public_origin_policy_v2 as policy
from app.trade_qualification import public_source_runtime
from app.trade_qualification import quote_collector_v2 as quote_v2


@pytest.mark.parametrize("region", ("global", "us_au", "eea"))
def test_source_owned_v2_replay_contract_matches_qualification_policy(region):
    """Neither package may silently change this version's route or hashes."""
    route = origin.reviewed_demo_public_route(region)
    assert primitives.demo_public_v2_route(region) == (
        route.rest_origin,
        route.ws_origin,
        route.rest_hostname,
        route.ws_hostname,
    )
    assert primitives.demo_public_v2_policy_sha256(region) == sha(
        policy.freeze_demo_public_origin_policy_v2(region)
    )
    assert primitives.demo_public_v2_quote_transport_sha256(
        region
    ) == quote_v2._transport_policy_sha256(route)
    for role in ("quote", "candles", "market_aux"):
        assert primitives.demo_public_v2_headers(
            region, role
        ) == origin.demo_public_headers(route, role)


@pytest.mark.parametrize(
    ("region", "rest", "ws", "expected_sha256"),
    [
        (
            "global",
            "https://openapi.okx.com",
            "wss://wspap.okx.com:443/ws/v5/public",
            "ffd50e9b66afc08745c2e4c51d5cc23802a7965a609342debe47d6f07f6fd9b4",
        ),
        (
            "us_au",
            "https://us.okx.com",
            "wss://wsuspap.okx.com:443/ws/v5/public",
            "338ad01e1d7ab5761b50ce083d6877a4af6a81f2b388c36404bf8a86b72957ef",
        ),
        (
            "eea",
            "https://eea.okx.com",
            "wss://wseeapap.okx.com:443/ws/v5/public",
            "68d188696483fa68487f326baf588164c62f5552bd9b692f00639ab8be473e39",
        ),
    ],
)
def test_exact_versioned_policy_replays_without_source_or_order_authority(
    region, rest, ws, expected_sha256
):
    raw = policy.freeze_demo_public_origin_policy_v2(region)
    assert len(raw) <= policy.MAX_POLICY_BYTES
    assert raw == policy.replay_demo_public_origin_policy_v2(
        raw, expected_registration_region=region
    )
    assert sha(raw) == expected_sha256
    value = json.loads(raw)
    assert value["schema_version"] == policy.SCHEMA_VERSION
    assert (value["rest_origin"], value["ws_origin"]) == (rest, ws)
    assert value["rest_roles"]["quote"]["headers"]["x-simulated-trading"] == "1"
    assert value["rest_roles"]["candles"]["headers"]["x-simulated-trading"] == "1"
    assert value["rest_roles"]["market_aux"]["headers"]["x-simulated-trading"] == "1"
    assert value["admission"] == "DENY"
    for field in (
        "request_observed",
        "response_observed",
        "account_region_authenticated",
        "source_authenticity_verified",
        "execution_authority",
    ):
        assert value[field] is False


@pytest.mark.parametrize("region", ["tr", "production", "", None, 1])
def test_unreviewed_region_cannot_issue_or_replay_v2_policy(region):
    with pytest.raises(origin.DemoPublicOriginError, match="v2_region_unreviewed"):
        policy.freeze_demo_public_origin_policy_v2(region)
    with pytest.raises(origin.DemoPublicOriginError, match="v2_region_unreviewed"):
        policy.replay_demo_public_origin_policy_v2(
            policy.freeze_demo_public_origin_policy_v2("global"),
            expected_registration_region=region,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_rest_region",
        "wrong_ws_region",
        "production_ws",
        "missing_simulated_header",
        "wrong_simulated_header",
        "missing_endpoint",
        "wrong_tls_hostname",
        "claimed_request",
        "claimed_response",
        "claimed_registration",
        "claimed_source",
        "claimed_execution",
        "claimed_admission",
        "extra_caller_field",
    ],
)
def test_replay_rejects_changed_route_or_caller_claimed_source(mutation):
    document = json.loads(policy.freeze_demo_public_origin_policy_v2("global"))
    if mutation == "wrong_rest_region":
        document["rest_origin"] = "https://us.okx.com"
    elif mutation == "wrong_ws_region":
        document["ws_origin"] = "wss://wsuspap.okx.com:443/ws/v5/public"
    elif mutation == "production_ws":
        document["ws_origin"] = "wss://ws.okx.com:443/ws/v5/public"
    elif mutation == "missing_simulated_header":
        del document["rest_roles"]["quote"]["headers"]["x-simulated-trading"]
    elif mutation == "wrong_simulated_header":
        document["rest_roles"]["candles"]["headers"]["x-simulated-trading"] = "0"
    elif mutation == "missing_endpoint":
        document["rest_roles"]["market_aux"]["paths"].pop()
    elif mutation == "wrong_tls_hostname":
        document["ws_tls_hostname"] = "ws.okx.com"
    elif mutation == "claimed_request":
        document["request_observed"] = True
    elif mutation == "claimed_response":
        document["response_observed"] = True
    elif mutation == "claimed_registration":
        document["account_region_authenticated"] = True
    elif mutation == "claimed_source":
        document["source_authenticity_verified"] = True
    elif mutation == "claimed_execution":
        document["execution_authority"] = True
    elif mutation == "claimed_admission":
        document["admission"] = "PASS"
    else:
        document["caller_says_trusted"] = True
    with pytest.raises(origin.DemoPublicOriginError, match="v2_policy_replay_invalid"):
        policy.replay_demo_public_origin_policy_v2(
            canonical(document), expected_registration_region="global"
        )


def test_coherent_other_region_cannot_replace_independently_pinned_region():
    raw = policy.freeze_demo_public_origin_policy_v2("eea")
    with pytest.raises(origin.DemoPublicOriginError, match="v2_policy_replay_invalid"):
        policy.replay_demo_public_origin_policy_v2(
            raw, expected_registration_region="global"
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"ctcc.public.runtime_plan.v2"}',
        b"not-json",
        b"x" * (policy.MAX_POLICY_BYTES + 1),
        {"schema_version": policy.SCHEMA_VERSION},
    ],
)
def test_old_or_malformed_bytes_cannot_be_promoted_as_v2_route_policy(raw):
    with pytest.raises(origin.DemoPublicOriginError, match="v2_policy_replay_invalid"):
        policy.replay_demo_public_origin_policy_v2(
            raw, expected_registration_region="global"
        )


def test_noncanonical_or_duplicate_json_keys_are_rejected():
    raw = policy.freeze_demo_public_origin_policy_v2("global")
    changed = (
        json.dumps(json.loads(raw), indent=2).encode(),
        raw[:-1] + b',"environment":"demo"}',
    )
    for value in changed:
        with pytest.raises(
            origin.DemoPublicOriginError, match="v2_policy_replay_invalid"
        ):
            policy.replay_demo_public_origin_policy_v2(
                value, expected_registration_region="global"
            )


def test_v2_bytes_replay_after_future_route_table_change_but_new_issue_denies(
    monkeypatch,
):
    raw = policy.freeze_demo_public_origin_policy_v2("global")
    monkeypatch.setattr(
        origin,
        "_ROUTES",
        MappingProxyType(
            {
                "global": (
                    "https://www.okx.com",
                    "wss://ws.okx.com:443/ws/v5/public",
                )
            }
        ),
    )
    assert (
        policy.replay_demo_public_origin_policy_v2(
            raw, expected_registration_region="global"
        )
        == raw
    )
    with pytest.raises(origin.DemoPublicOriginError, match="v2_current_policy_changed"):
        policy.freeze_demo_public_origin_policy_v2("global")


def test_matching_pure_policy_does_not_lift_native_demo_capture_hard_deny():
    raw = policy.freeze_demo_public_origin_policy_v2("global")
    document = json.loads(
        policy.replay_demo_public_origin_policy_v2(
            raw, expected_registration_region="global"
        )
    )
    with pytest.raises(
        public_source_runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        public_source_runtime._require_trusted_v2_demo_origin_profile(document)
