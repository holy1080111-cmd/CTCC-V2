"""Reviewed Demo routes are pure policy; no credential or capture authority."""

import httpx
import pytest

from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import demo_public_origin as origin
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import quote_collector as quotes


@pytest.mark.parametrize(
    ("region", "rest", "ws_host"),
    [
        ("global", "https://openapi.okx.com", "wspap.okx.com"),
        ("us_au", "https://us.okx.com", "wsuspap.okx.com"),
        ("eea", "https://eea.okx.com", "wseeapap.okx.com"),
    ],
)
def test_reviewed_demo_routes_have_exact_rest_ws_tls_and_no_authority(
    region, rest, ws_host
):
    route = origin.reviewed_demo_public_route(region)
    assert route.rest_origin == rest
    assert route.ws_origin == f"wss://{ws_host}:443/ws/v5/public"
    assert route.rest_hostname == rest.removeprefix("https://")
    assert route.ws_hostname == ws_host
    assert route.environment == "demo" and route.admission == "DENY"
    assert route.source_authenticity_verified is False
    assert route.execution_authority is False
    origin.validate_demo_public_ws(route, route.ws_origin, ws_host)


@pytest.mark.parametrize("region", ["tr", "us", "production", "", None, 1])
def test_unreviewed_region_is_not_inferred(region):
    with pytest.raises(
        origin.DemoPublicOriginError, match="demo_public_region_unreviewed"
    ):
        origin.reviewed_demo_public_route(region)


@pytest.mark.parametrize(
    "role,path,params",
    [
        ("quote", "/api/v5/market/ticker", {"instId": "BTC-USDT-SWAP"}),
        (
            "quote",
            "/api/v5/public/mark-price",
            {"instId": "BTC-USDT-SWAP", "instType": "SWAP"},
        ),
        ("quote", "/api/v5/public/funding-rate", {"instId": "BTC-USDT-SWAP"}),
        (
            "candles",
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "bar": "4H", "limit": "300"},
        ),
        (
            "market_aux",
            "/api/v5/market/books",
            {"instId": "BTC-USDT-SWAP", "sz": "5"},
        ),
        (
            "market_aux",
            "/api/v5/public/open-interest",
            {"instId": "BTC-USDT-SWAP", "instType": "SWAP"},
        ),
    ],
)
def test_demo_rest_policy_requires_simulated_header_and_exact_region(
    role, path, params
):
    route = origin.reviewed_demo_public_route("eea")
    request = httpx.Request(
        "GET",
        route.rest_origin + path,
        params=params,
        headers=origin.demo_public_headers(route, role),
    )
    origin.validate_demo_public_request(
        route, role, request, expected_instrument_id="BTC-USDT-SWAP"
    )
    assert request.headers["x-simulated-trading"] == "1"


@pytest.mark.parametrize(
    "mutation",
    [
        "header_missing",
        "header_zero",
        "wrong_region",
        "production",
        "auth",
        "cookie",
        "post",
        "private_path",
        "extra_header",
        "duplicate_header",
    ],
)
def test_demo_rest_policy_rejects_source_or_header_substitution(mutation):
    route = origin.reviewed_demo_public_route("global")
    headers = origin.demo_public_headers(route, "quote")
    base = route.rest_origin
    path = "/api/v5/market/ticker"
    method = "GET"
    if mutation == "header_missing":
        headers.pop("x-simulated-trading")
    elif mutation == "header_zero":
        headers["x-simulated-trading"] = "0"
    elif mutation == "wrong_region":
        base = "https://us.okx.com"
    elif mutation == "production":
        base = "https://www.okx.com"
    elif mutation == "auth":
        headers["Authorization"] = "Bearer example"
    elif mutation == "cookie":
        headers["Cookie"] = "session=example"
    elif mutation == "post":
        method = "POST"
    elif mutation == "private_path":
        path = "/api/v5/account/balance"
    elif mutation == "duplicate_header":
        headers = tuple(headers.items()) + (("x-simulated-trading", "1"),)
    else:
        headers["X-Forwarded-Host"] = "openapi.okx.com"
    request = httpx.Request(
        method, base + path, params={"instId": "BTC-USDT-SWAP"}, headers=headers
    )
    with pytest.raises(
        origin.DemoPublicOriginError, match="demo_public_request_invalid"
    ):
        origin.validate_demo_public_request(
            route, "quote", request, expected_instrument_id="BTC-USDT-SWAP"
        )


@pytest.mark.parametrize(
    ("role", "path", "params"),
    [
        ("quote", "/api/v5/market/ticker", {}),
        ("quote", "/api/v5/market/ticker", {"instId": "ETH-USDT-SWAP"}),
        (
            "quote",
            "/api/v5/market/ticker",
            (("instId", "BTC-USDT-SWAP"), ("instId", "BTC-USDT-SWAP")),
        ),
        (
            "quote",
            "/api/v5/market/ticker",
            {"instId": "BTC-USDT-SWAP", "extra": "1"},
        ),
        ("quote", "/api/v5/public/mark-price", {"instId": "BTC-USDT-SWAP"}),
        (
            "market_aux",
            "/api/v5/market/books",
            {"instId": "BTC-USDT-SWAP", "sz": "10"},
        ),
        (
            "market_aux",
            "/api/v5/public/open-interest",
            {"instId": "BTC-USDT-SWAP", "instType": "FUTURES"},
        ),
        (
            "candles",
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "limit": "300"},
        ),
        (
            "candles",
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "bar": "1m", "limit": "300"},
        ),
        (
            "candles",
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "bar": "4H", "limit": "301"},
        ),
        (
            "candles",
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "bar": "4H", "limit": "0300"},
        ),
        (
            "candles",
            "/api/v5/market/candles",
            {
                "instId": "BTC-USDT-SWAP",
                "bar": "4H",
                "limit": "300",
                "after": "old-cursor",
            },
        ),
    ],
)
def test_demo_rest_policy_rejects_wrong_or_duplicate_query(role, path, params):
    route = origin.reviewed_demo_public_route("global")
    request = httpx.Request(
        "GET",
        route.rest_origin + path,
        params=params,
        headers=origin.demo_public_headers(route, role),
    )
    with pytest.raises(origin.DemoPublicOriginError, match="request_invalid"):
        origin.validate_demo_public_request(
            route, role, request, expected_instrument_id="BTC-USDT-SWAP"
        )


def test_demo_candle_cursor_is_bounded_and_instrument_pin_required():
    route = origin.reviewed_demo_public_route("us_au")
    request = httpx.Request(
        "GET",
        route.rest_origin + "/api/v5/market/candles",
        params={
            "instId": "BTC-USDT-SWAP",
            "bar": "15m",
            "limit": "100",
            "after": "1770000000000",
        },
        headers=origin.demo_public_headers(route, "candles"),
    )
    origin.validate_demo_public_request(
        route, "candles", request, expected_instrument_id="BTC-USDT-SWAP"
    )
    with pytest.raises(origin.DemoPublicOriginError, match="request_invalid"):
        origin.validate_demo_public_request(
            route, "candles", request, expected_instrument_id="BTCUSDT"
        )


@pytest.mark.parametrize(
    ("endpoint", "hostname"),
    [
        ("wss://ws.okx.com:443/ws/v5/public", "ws.okx.com"),
        ("wss://wsuspap.okx.com:443/ws/v5/public", "wsuspap.okx.com"),
        ("wss://wspap.okx.com:443/ws/v5/public", "ws.okx.com"),
    ],
)
def test_demo_ws_policy_rejects_production_cross_region_or_tls_mismatch(
    endpoint, hostname
):
    route = origin.reviewed_demo_public_route("global")
    with pytest.raises(
        origin.DemoPublicOriginError, match="demo_public_ws_origin_mismatch"
    ):
        origin.validate_demo_public_ws(route, endpoint, hostname)


def test_route_string_or_mutation_cannot_enable_native_capture():
    route = origin.reviewed_demo_public_route("global")
    with pytest.raises(
        origin.DemoPublicOriginError, match="demo_public_route_required"
    ):
        origin.demo_public_headers(route.ws_origin, "quote")
    with pytest.raises(
        runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        runtime._require_trusted_v2_demo_origin_profile(
            {
                "environment": "demo",
                "registration_region": route.registration_region,
                "rest_origin": route.rest_origin,
                "ws_origin": route.ws_origin,
                "trusted_demo_public_origin_profile": route,
            }
        )
    object.__setattr__(route, "rest_origin", "https://www.okx.com")
    with pytest.raises(
        origin.DemoPublicOriginError, match="demo_public_route_not_reviewed"
    ):
        origin.demo_public_headers(route, "quote")


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["global", "us_au", "eea"])
@pytest.mark.parametrize(
    "role,path,params",
    [
        ("quote", "/api/v5/market/ticker", {"instId": "BTC-USDT-SWAP"}),
        (
            "candles",
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "bar": "15m", "limit": "100"},
        ),
        (
            "market_aux",
            "/api/v5/market/books",
            {"instId": "BTC-USDT-SWAP", "sz": "5"},
        ),
    ],
)
async def test_demo_request_builder_exact_route_header_and_mock_transport(
    region, role, path, params
):
    route = origin.reviewed_demo_public_route(region)
    request = origin.build_demo_public_request(
        route,
        role,
        path,
        params,
        instrument_id="BTC-USDT-SWAP",
        timeout_seconds=2,
    )
    observed = []

    def respond(sent):
        observed.append(sent)
        origin.validate_demo_public_request(
            route, role, sent, expected_instrument_id="BTC-USDT-SWAP"
        )
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False
    ) as client:
        response = await client.send(request, follow_redirects=False)
    origin.validate_demo_public_response(
        route, role, request, response, expected_instrument_id="BTC-USDT-SWAP"
    )
    assert observed == [request]
    assert request.headers["x-simulated-trading"] == "1"
    assert request.url.host == route.rest_hostname
    assert origin.build_demo_public_ws_endpoint(route) == route.ws_origin


@pytest.mark.parametrize(
    "mutation", ["missing_header", "wrong_header", "cross_region", "production"]
)
def test_demo_builder_request_mutation_is_rejected(mutation):
    route = origin.reviewed_demo_public_route("global")
    request = origin.build_demo_public_request(
        route,
        "quote",
        "/api/v5/market/ticker",
        {"instId": "BTC-USDT-SWAP"},
        instrument_id="BTC-USDT-SWAP",
        timeout_seconds=2,
    )
    if mutation == "missing_header":
        del request.headers["x-simulated-trading"]
    elif mutation == "wrong_header":
        request.headers["x-simulated-trading"] = "0"
    else:
        host = "us.okx.com" if mutation == "cross_region" else "www.okx.com"
        request.url = request.url.copy_with(host=host)
    with pytest.raises(origin.DemoPublicOriginError, match="request_invalid"):
        origin.validate_demo_public_request(
            route, "quote", request, expected_instrument_id="BTC-USDT-SWAP"
        )


@pytest.mark.asyncio
async def test_demo_redirect_is_not_followed_and_response_is_rejected():
    route = origin.reviewed_demo_public_route("global")
    request = origin.build_demo_public_request(
        route,
        "quote",
        "/api/v5/market/ticker",
        {"instId": "BTC-USDT-SWAP"},
        instrument_id="BTC-USDT-SWAP",
        timeout_seconds=2,
    )
    observed = []

    def redirect(sent):
        observed.append(sent)
        return httpx.Response(302, headers={"location": "https://www.okx.com/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), trust_env=False
    ) as client:
        response = await client.send(request, follow_redirects=False)
    assert len(observed) == 1 and response.status_code == 302
    with pytest.raises(origin.DemoPublicOriginError, match="response_invalid"):
        origin.validate_demo_public_response(
            route, "quote", request, response, expected_instrument_id="BTC-USDT-SWAP"
        )


@pytest.mark.asyncio
async def test_proxy_configured_client_fails_isolation_before_any_request():
    async with httpx.AsyncClient(proxy="http://127.0.0.1:9", trust_env=False) as client:
        with pytest.raises(quotes.QuoteCollectionError, match="not_isolated"):
            quotes._public_client(client)


@pytest.mark.parametrize("region", ["global", "us_au", "eea"])
def test_demo_ws_builder_is_exact_443_and_rejects_other_region(region):
    route = origin.reviewed_demo_public_route(region)
    endpoint = origin.build_demo_public_ws_endpoint(route)
    assert endpoint == f"wss://{route.ws_hostname}:443/ws/v5/public"
    other = origin.reviewed_demo_public_route(
        "global" if region != "global" else "us_au"
    )
    with pytest.raises(origin.DemoPublicOriginError, match="ws_origin_mismatch"):
        origin.validate_demo_public_ws(route, other.ws_origin, other.ws_hostname)


@pytest.mark.parametrize("region", ["global", "us_au", "eea"])
def test_region_tls_proof_requires_exact_rest_and_ws_hostnames(region):
    route = origin.reviewed_demo_public_route(region)
    for expected in (route.rest_hostname, route.ws_hostname):
        proof = {
            "classification": "owned_native_tls",
            "hostname": expected,
            "version": "TLSv1.3",
            "peer_sha256": "a" * 64,
        }
        assert journal._tls(proof, expected) is True
        with pytest.raises(journal.PublicReceiptError, match="runtime_tls_invalid"):
            journal._tls(proof, "www.okx.com")


@pytest.mark.parametrize("region", ["global", "us_au", "eea"])
@pytest.mark.parametrize("role", ["quote", "candles", "market_aux"])
def test_routed_journal_header_receipt_matches_exact_builder(region, role):
    route = origin.reviewed_demo_public_route(region)
    path, params = {
        "quote": ("/api/v5/market/ticker", {"instId": "BTC-USDT-SWAP"}),
        "candles": (
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "bar": "15m", "limit": "100"},
        ),
        "market_aux": (
            "/api/v5/market/books",
            {"instId": "BTC-USDT-SWAP", "sz": "5"},
        ),
    }[role]
    request = origin.build_demo_public_request(
        route,
        role,
        path,
        params,
        instrument_id="BTC-USDT-SWAP",
        timeout_seconds=2,
    )
    observed = [
        [name.lower(), value]
        for name, value in sorted(
            request.headers.items(), key=lambda pair: pair[0].lower()
        )
    ]
    plan = {"registration_region": region, "rest_origin": route.rest_origin}
    assert journal._routed_request_headers(plan, role) == observed
    wrong = {**plan, "rest_origin": "https://www.okx.com"}
    with pytest.raises(
        journal.PublicReceiptError, match="runtime_demo_request_headers_invalid"
    ):
        journal._routed_request_headers(wrong, role)


def test_route_policy_does_not_infer_registration_region_from_hostname():
    route = origin.reviewed_demo_public_route("us_au")
    plan = {
        "schema_version": "ctcc.public.initial_runtime_plan.v2",
        "environment": "demo",
        "rest_origin": route.rest_origin,
        "ws_origin": route.ws_origin,
    }
    assert runtime._planned_public_route(plan) is None
    with pytest.raises(runtime.PublicSourceRuntimeError, match="origin_mismatch"):
        runtime._planned_public_route({**plan, "registration_region": "global"})
