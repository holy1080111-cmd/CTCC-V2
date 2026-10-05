"""A controlled account can declare a Demo route, never authenticate market IO."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import demo_public_origin as origin
from app.trade_qualification import demo_public_origin_preflight as preflight
from app.trade_qualification import public_source_runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_qualification_account_collector import credentials


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
        origin.validate_demo_public_request(route, "quote", request)
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
