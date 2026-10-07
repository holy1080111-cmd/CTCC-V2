from datetime import UTC, datetime

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
    build_signature,
    utc_iso_timestamp,
)


def demo_settings(**updates) -> Settings:
    values = {
        "environment": "test",
        "trading_mode": "okx_demo",
        "paper_auto_execution": False,
        "okx_demo_enabled": True,
        "okx_demo_allow_order_writes": True,
        "okx_demo_api_key": "demo-key",
        "okx_demo_api_secret": "demo-secret",
        "okx_demo_api_passphrase": "demo-passphrase",
        "okx_demo_read_max_retries": 0,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_signature_matches_okx_prehash_definition() -> None:
    signature = build_signature(
        timestamp="2020-12-08T09:08:57.715Z",
        method="GET",
        request_path="/api/v5/account/balance?ccy=BTC",
        body="",
        secret="secret",
    )
    assert signature == "wpDvCwYCprcMQsQkxWJiWy+YADoQE4ep+OEKKLimMoY="


def test_utc_timestamp_is_millisecond_iso8601() -> None:
    value = utc_iso_timestamp(datetime(2026, 8, 4, 13, 1, 2, 345678, tzinfo=UTC))
    assert value == "2026-08-04T13:01:02.345Z"


@pytest.mark.asyncio
async def test_authenticated_get_includes_simulated_header_and_signed_query() -> None:
    settings = demo_settings()
    fixed = datetime(2026, 8, 4, 13, 1, 2, 345000, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/account/positions"
        assert request.url.query.decode() == "instId=BTC-USDT-SWAP"
        assert request.headers["x-simulated-trading"] == "1"
        timestamp = request.headers["OK-ACCESS-TIMESTAMP"]
        expected = build_signature(
            timestamp=timestamp,
            method="GET",
            request_path="/api/v5/account/positions?instId=BTC-USDT-SWAP",
            body="",
            secret="demo-secret",
        )
        assert request.headers["OK-ACCESS-SIGN"] == expected
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://www.okx.com"
    ) as client:
        result = await OkxDemoPrivateRestClient(
            client, settings=settings, clock=lambda: fixed
        ).positions("BTC-USDT-SWAP")
    assert result == []


@pytest.mark.asyncio
async def test_unfiltered_positions_query_covers_all_position_instrument_types() -> (
    None
):
    settings = demo_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/account/positions"
        assert request.url.query == b""
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": [{"instType": "FUTURES"}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://www.okx.com",
    ) as client:
        rows = await OkxDemoPrivateRestClient(client, settings=settings).positions()

    assert rows == [{"instType": "FUTURES"}]


@pytest.mark.asyncio
async def test_pending_orders_are_unfiltered_and_page_until_empty_terminal() -> None:
    settings = demo_settings()
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/trade/orders-pending"
        assert request.url.params.get("instType") is None
        assert request.url.params.get("instId") is None
        assert request.url.params["limit"] == "100"
        after = request.url.params.get("after")
        observed.append(after)
        rows = {
            None: [{"ordId": "200"}, {"ordId": "199"}],
            "199": [{"ordId": "198"}],
            "198": [],
        }[after]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://www.okx.com",
    ) as client:
        rows = await OkxDemoPrivateRestClient(
            client, settings=settings
        ).pending_orders()

    assert [row["ordId"] for row in rows] == ["200", "199", "198"]
    assert observed == [None, "199", "198"]


@pytest.mark.asyncio
async def test_pending_algos_use_documented_types_and_empty_cursor_terminators() -> (
    None
):
    settings = demo_settings()
    fixed = datetime(2026, 8, 4, 13, 1, 2, 345000, tzinfo=UTC)
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/trade/orders-algo-pending"
        assert request.url.params["instId"] == "BTC-USDT-SWAP"
        assert request.url.params["limit"] == "100"
        assert request.headers["x-simulated-trading"] == "1"
        order_type = request.url.params["ordType"]
        after = request.url.params.get("after")
        observed.append((order_type, after))
        assert order_type in {"conditional", "oco", "trigger", "move_order_stop"}
        query = request.url.query.decode()
        expected = build_signature(
            timestamp=request.headers["OK-ACCESS-TIMESTAMP"],
            method="GET",
            request_path=f"/api/v5/trade/orders-algo-pending?{query}",
            body="",
            secret="demo-secret",
        )
        assert request.headers["OK-ACCESS-SIGN"] == expected
        rows = {
            ("conditional", None): [{"algoId": "100"}],
            ("conditional", "100"): [{"algoId": "99"}],
            ("conditional", "99"): [],
            ("oco", None): [{"algoId": "80"}],
            ("oco", "80"): [],
            ("trigger", None): [],
            ("move_order_stop", None): [{"algoId": "60"}],
            ("move_order_stop", "60"): [],
        }[(order_type, after)]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://www.okx.com",
    ) as client:
        result = await OkxDemoPrivateRestClient(
            client,
            settings=settings,
            clock=lambda: fixed,
        ).pending_algo_orders("BTC-USDT-SWAP")

    assert [row["algoId"] for row in result] == ["100", "99", "80", "60"]
    assert observed == [
        ("conditional", None),
        ("conditional", "100"),
        ("conditional", "99"),
        ("oco", None),
        ("oco", "80"),
        ("trigger", None),
        ("move_order_stop", None),
        ("move_order_stop", "60"),
    ]


@pytest.mark.asyncio
async def test_pending_algo_cursor_limit_without_empty_terminal_fails_closed() -> None:
    settings = demo_settings()
    fixed = datetime(2026, 8, 4, 13, 1, 2, 345000, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": [{"algoId": "100"}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://www.okx.com",
    ) as client:
        reader = OkxDemoPrivateRestClient(
            client,
            settings=settings,
            clock=lambda: fixed,
        )
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await reader._cursor_chain(
                "/api/v5/trade/orders-algo-pending",
                params={"ordType": "conditional"},
                cursor_field="algoId",
                max_pages=2,
            )

    assert exc_info.value.code == "pagination_incomplete"


@pytest.mark.asyncio
async def test_denied_demo_maintenance_never_reads_exchange_item_error() -> None:
    settings = demo_settings()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [{"ordId": "", "sCode": "51000", "sMsg": "bad order"}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://www.okx.com"
    ) as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxDemoPrivateRestClient(client, settings=settings).cancel_order(
                {"instId": "BTC-USDT-SWAP", "ordId": "synthetic"}
            )
    assert exc_info.value.code == "demo_maintenance_authority_unavailable"
    assert calls == 0
    assert "demo-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_denied_demo_maintenance_has_no_transport_attempt() -> None:
    settings = demo_settings(okx_demo_read_max_retries=5)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("network down", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://www.okx.com"
    ) as client:
        with pytest.raises(OkxPrivateApiError) as exc_info:
            await OkxDemoPrivateRestClient(client, settings=settings).cancel_order(
                {"instId": "BTC-USDT-SWAP", "ordId": "synthetic"}
            )
    assert calls == 0
    assert exc_info.value.code == "demo_maintenance_authority_unavailable"


@pytest.mark.asyncio
async def test_read_retry_refreshes_timestamp_and_signature() -> None:
    settings = demo_settings(okx_demo_read_max_retries=1)
    moments = iter(
        [
            datetime(2026, 8, 4, 13, 1, 2, tzinfo=UTC),
            datetime(2026, 8, 4, 13, 1, 3, tzinfo=UTC),
        ]
    )
    seen_timestamps: list[str] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        seen_timestamps.append(request.headers["OK-ACCESS-TIMESTAMP"])
        if calls == 1:
            raise httpx.ConnectError("temporary network error", request=request)
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://www.okx.com"
    ) as client:
        result = await OkxDemoPrivateRestClient(
            client, settings=settings, clock=lambda: next(moments)
        ).balance()

    assert result == []
    assert seen_timestamps == [
        "2026-08-04T13:01:02.000Z",
        "2026-08-04T13:01:03.000Z",
    ]
