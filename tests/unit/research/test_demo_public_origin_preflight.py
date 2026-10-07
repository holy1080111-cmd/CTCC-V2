"""A controlled account can declare a Demo route, never authenticate market IO."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import account_capture as capture
from app.trade_qualification import demo_public_origin as origin
from app.trade_qualification import demo_public_origin_preflight as preflight
from app.trade_qualification import post_g12_public_runtime as post_g12
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import public_source_runtime
from app.trade_qualification import qualification_runtime as initial_public
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.research.test_owned_public_runtime_v2 import policy as public_policy
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_one_shot import inputs as inputs  # noqa: PLC0414


def session(region="global", rest="https://openapi.okx.com"):
    plan = current_plan(registration_region=region, origin=rest)
    return ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "rest", "ws_host"),
    [
        ("global", "https://openapi.okx.com", "wspap.okx.com"),
        ("us_au", "https://us.okx.com", "wsuspap.okx.com"),
        ("eea", "https://eea.okx.com", "wseeapap.okx.com"),
    ],
)
async def test_exact_route_comes_only_from_same_controlled_session(
    region, rest, ws_host
):
    controlled = session(region, rest)
    prepared = preflight._prepare_controlled_demo_route(controlled)
    route, pin = preflight._consume_controlled_demo_route(prepared, controlled)
    assert pin == controlled._pin
    assert route.registration_region == region
    assert route.rest_origin == rest
    assert route.ws_hostname == ws_host
    assert route.admission == "DENY"
    assert route.source_authenticity_verified is False
    assert route.execution_authority is False
    with pytest.raises(
        preflight.DemoPublicOriginPreflightError, match="route_not_owned"
    ):
        preflight._consume_controlled_demo_route(prepared, controlled)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "rest", "other_rest", "other_ws"),
    [
        (
            "global",
            "https://openapi.okx.com",
            "https://us.okx.com",
            "wss://wsuspap.okx.com:443/ws/v5/public",
        ),
        (
            "us_au",
            "https://us.okx.com",
            "https://eea.okx.com",
            "wss://wseeapap.okx.com:443/ws/v5/public",
        ),
        (
            "eea",
            "https://eea.okx.com",
            "https://openapi.okx.com",
            "wss://wspap.okx.com:443/ws/v5/public",
        ),
    ],
)
async def test_each_controlled_region_rejects_other_rest_and_ws_routes(
    region, rest, other_rest, other_ws
):
    controlled = session(region, rest)
    prepared = preflight._prepare_controlled_demo_route(controlled)
    route, _ = preflight._consume_controlled_demo_route(prepared, controlled)
    request = httpx.Request(
        "GET",
        other_rest + "/api/v5/market/ticker",
        headers=origin.demo_public_headers(route, "quote"),
    )
    with pytest.raises(origin.DemoPublicOriginError, match="request_invalid"):
        origin.validate_demo_public_request(
            route, "quote", request, expected_instrument_id="BTC-USDT-SWAP"
        )
    with pytest.raises(origin.DemoPublicOriginError, match="ws_origin_mismatch"):
        origin.validate_demo_public_ws(route, other_ws, httpx.URL(other_ws).host)
    with pytest.raises(
        public_source_runtime.PublicSourceRuntimeError,
        match="demo_public_origin_mismatch",
    ):
        public_source_runtime._require_trusted_v2_demo_origin_profile(
            {
                "environment": "demo",
                "registration_region": region,
                "rest_origin": other_rest,
                "ws_origin": route.ws_origin,
            }
        )


@pytest.mark.asyncio
async def test_cross_task_or_cross_session_cannot_consume_prepared_region():
    controlled = session()
    prepared = preflight._prepare_controlled_demo_route(controlled)

    async def other_task():
        with pytest.raises(
            preflight.DemoPublicOriginPreflightError, match="route_not_owned"
        ):
            preflight._consume_controlled_demo_route(prepared, controlled)

    await asyncio.create_task(other_task())
    with pytest.raises(
        preflight.DemoPublicOriginPreflightError, match="route_not_owned"
    ):
        preflight._consume_controlled_demo_route(prepared, session())
    route, _ = preflight._consume_controlled_demo_route(prepared, controlled)
    assert route.registration_region == "global"


@pytest.mark.asyncio
async def test_caller_cannot_substitute_route_or_mutate_pinned_session():
    controlled = session("eea", "https://eea.okx.com")
    with pytest.raises(TypeError):
        preflight._prepare_controlled_demo_route(
            controlled, rest_origin="https://www.okx.com"
        )
    prepared = preflight._prepare_controlled_demo_route(controlled)
    controlled._plan = current_plan(
        registration_region="us_au", origin="https://us.okx.com"
    )
    with pytest.raises(
        preflight.DemoPublicOriginPreflightError, match="route_scope_invalid"
    ):
        preflight._consume_controlled_demo_route(prepared, controlled)
    with pytest.raises(
        preflight.DemoPublicOriginPreflightError, match="route_not_owned"
    ):
        preflight._consume_controlled_demo_route(prepared, controlled)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "rest"),
    [
        ("global", "https://openapi.okx.com"),
        ("us_au", "https://us.okx.com"),
        ("eea", "https://eea.okx.com"),
    ],
)
async def test_old_packets_and_matching_route_claims_remain_denied(region, rest):
    for packet in (b"old-public-packet", SimpleNamespace(environment="demo")):
        with pytest.raises(
            preflight.DemoPublicOriginPreflightError,
            match="controlled_demo_account_session_required",
        ):
            preflight._prepare_controlled_demo_route(packet)
    controlled = session(region, rest)
    prepared = preflight._prepare_controlled_demo_route(controlled)
    route, _ = preflight._consume_controlled_demo_route(prepared, controlled)
    with pytest.raises(
        public_source_runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        public_source_runtime._require_trusted_v2_demo_origin_profile(
            {
                "environment": "demo",
                "registration_region": route.registration_region,
                "rest_origin": route.rest_origin,
                "ws_origin": route.ws_origin,
                "prepared_demo_public_route": prepared,
            }
        )


@pytest.mark.asyncio
async def test_unreviewed_region_cannot_be_prepared():
    with pytest.raises(
        preflight.DemoPublicOriginPreflightError, match="route_scope_invalid"
    ):
        preflight._prepare_controlled_demo_route(session("tr", "https://tr.okx.com"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "rest"),
    [
        ("global", "https://openapi.okx.com"),
        ("us_au", "https://us.okx.com"),
        ("eea", "https://eea.okx.com"),
    ],
)
async def test_same_session_route_declaration_has_exact_replayable_plan_but_no_io(
    region, rest
):
    declared = preflight._declared_demo_public_plan(session(region, rest))
    reviewed = origin.reviewed_demo_public_route(region)
    assert declared["registration_region"] == region
    assert declared["rest_origin"] == reviewed.rest_origin
    assert declared["ws_origin"] == reviewed.ws_origin
    assert declared["registration_region_authenticated"] is False
    selected = public_policy()
    route_policy = public_v2._policy_document(selected, reviewed)
    assert route_policy["schema_version"] == public_v2.ROUTED_POLICY_VERSION
    assert route_policy["rest_origin"] == reviewed.rest_origin
    assert route_policy["ws_origin"] == reviewed.ws_origin
    assert route_policy["registration_region_authenticated"] is False
    replayed_policy, replayed_route = public_v2._policy_from_document(route_policy)
    assert replayed_policy == selected and replayed_route == reviewed
    assert public_v2._policy_digest(selected, reviewed) != public_v2._policy_digest(
        selected
    )
    now = datetime(2026, 10, 7, tzinfo=UTC)
    invocation = "a" * 32
    plan = {
        "schema_version": "ctcc.public.initial_runtime_plan.v2",
        "stage": "initial_public",
        "invocation_id": invocation,
        "environment": "demo",
        "report_id": "initial-" + invocation,
        "instrument_id": "BTC-USDT-SWAP",
        "invocation_started": {
            "utc_ns": int(now.timestamp() * 1_000_000_000),
            "monotonic_ns": 100,
        },
        "expires_at": (now + timedelta(seconds=30)).isoformat(),
        "policy_sha256": "b" * 64,
        **public_v2._plan_pins(reviewed),
        **declared,
    }
    journal._validate_plan(plan)
    packet_scope = {
        "invocation_id": invocation,
        "stage": "initial_public",
        "environment": "demo",
        "report_id": plan["report_id"],
        "instrument_id": plan["instrument_id"],
        "barrier_completed_at": None,
        **declared,
    }
    assert public_v2._scope_route(packet_scope) == reviewed
    with pytest.raises(public_v2.PublicMarketV2Error, match="route_scope_invalid"):
        public_v2._scope_route(
            {**packet_scope, "ws_origin": "wss://ws.okx.com:443/ws/v5/public"}
        )
    historical = {
        key: value
        for key, value in plan.items()
        if key
        not in {
            "registration_region",
            "registration_region_authenticated",
            "account_plan_sha256",
            "demo_public_origin_policy_sha256",
        }
    }
    historical["rest_origin"] = "https://www.okx.com"
    historical["ws_origin"] = "wss://ws.okx.com:443/ws/v5/public"
    historical.update(public_v2._plan_pins())
    journal._validate_plan(historical)
    with pytest.raises(
        public_source_runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        public_source_runtime._require_trusted_v2_demo_origin_profile(plan)
    for mutation in (
        {"rest_origin": "https://www.okx.com"},
        {"ws_origin": "wss://ws.okx.com:443/ws/v5/public"},
        {"registration_region_authenticated": True},
        {"demo_public_origin_policy_sha256": "0" * 64},
        {"account_plan_sha256": "0"},
    ):
        with pytest.raises(ValueError):
            journal._validate_plan({**plan, **mutation})
    with pytest.raises(ValueError, match="runtime_v2_route_plan_invalid"):
        journal._validate_plan(
            {key: value for key, value in plan.items() if key != "account_plan_sha256"}
        )
    with pytest.raises(ValueError, match="runtime_v2_route_packet_mismatch"):
        packet = {
            "schema_version": "ctcc.collected_public_market.v2",
            "quote": {"schema_version": plan["quote_collector_schema"]},
            **{
                name: plan[name]
                for name in (
                    "stage",
                    "invocation_id",
                    "environment",
                    "policy_sha256",
                    "public_packet_schema",
                    "quote_collector_schema",
                    "quote_profile_sha256",
                    "quote_transport_policy_sha256",
                )
            },
        }
        journal._packet_join(plan, packet, {}, [])


@pytest.mark.asyncio
async def test_controlled_route_declaration_cannot_start_native_v2_capture(
    tmp_path, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("unverified_demo_route_entered_clock_or_io")

    monkeypatch.setattr(initial_public, "native_stamp", forbidden)
    monkeypatch.setattr(public_source_runtime, "_runtime_attempt", forbidden)
    monkeypatch.setattr(public_source_runtime, "_new_owned_client", forbidden)
    monkeypatch.setattr(public_source_runtime, "_ws_options", forbidden)
    result = await initial_public.capture_initial_public_market_v2(
        tmp_path,
        instrument_id="BTC-USDT-SWAP",
        market_policy=public_policy(),
        demo_session=session(),
    )
    assert result.code == "initial_public_v2_denied"
    assert result.packet is None and result.journal_sha256 is None
    assert result.admission == "DENY" and result.execution_authority is False


@pytest.mark.asyncio
async def test_controlled_route_declaration_cannot_publish_g12_before_region_proof(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs

    def forbidden(*_args, **_kwargs):
        pytest.fail("unverified_demo_route_entered_publication_or_io")

    monkeypatch.setattr(post_g12, "native_stamp", forbidden)
    monkeypatch.setattr(
        post_g12, "publish_qualification_evidence_liquidity_v2", forbidden
    )
    monkeypatch.setattr(public_source_runtime, "_runtime_attempt", forbidden)
    result = await post_g12.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "public",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=public_policy(),
        demo_session=session(),
    )
    assert result.code == "public_v2_denied"
    assert result.evidence is None and result.packet is None
    assert not (tmp_path / "g12").exists()
    assert result.admission == "DENY" and result.execution_authority is False
