"""Real Demo transport containment with synthetic IO; no exchange acceptance."""

from decimal import Decimal

import httpx
import pytest

from app.domain.okx_demo import OkxDemoLeverageRequest
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.okx_demo.service import OkxDemoService
from tests.unit.exchange.test_private_rest import demo_settings
from tests.unit.test_okx_demo_service import FakePrivate, FakePublic, request, settings

DENIED = "demo_qualification_authority_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("write", (True, False))
@pytest.mark.parametrize("method", ("POST", "post"))
@pytest.mark.parametrize(
    "path",
    (
        "/api/v5/trade/order",
        "/api/v5/trade/batch-orders",
        "/api/v5/trade/order-algo",
        "/api/v5/trade/amend-order",
        "/api/v5/trade/unknown-new-write",
        "/api/v5/account/set-leverage",
        "/api/v5/trade/cancel-order/../order",
        "/api/v5/trade/%6frder",
        "/api/v5/trade/order?passed=true",
        "https://www.okx.com/api/v5/trade/order",
    ),
)
async def test_all_unqualified_entry_routes_have_zero_http_io(method, path, write):
    requests = []

    def handler(value):
        requests.append(value)
        return httpx.Response(200, json={"code": "0", "data": [{"sCode": "0"}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(http, settings=demo_settings())
        with pytest.raises(OkxPrivateApiError) as caught:
            await client._request(
                method,
                path,
                write=write,
                body={
                    "instId": "BTC-USDT-SWAP",
                    "passed": True,
                    "execution_authority": True,
                    "reduceOnly": True,
                    "reservation": {"state": "consumed"},
                    "intent": {
                        "record_kind": "durable_intent_not_execution_permission"
                    },
                    "receipt": {"published": True},
                },
            )
        assert caught.value.code == DENIED
    assert requests == []


@pytest.mark.asyncio
async def test_entry_is_refused_before_credential_access_or_payload_callbacks():
    class Payload(dict):
        def items(self):
            raise AssertionError("untrusted payload was inspected")

    class NoCredentialRead(OkxDemoPrivateRestClient):
        def _credentials(self):
            raise AssertionError("entry denial must precede signing")

    client = NoCredentialRead(settings=demo_settings())
    with pytest.raises(OkxPrivateApiError) as caught:
        await client.place_order(Payload(passed=True))
    assert caught.value.code == DENIED


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", ("market", "limit", "fok"))
@pytest.mark.parametrize("automation_callback", (True, False))
async def test_manual_and_automation_service_entry_reaches_common_denial(
    order_type, automation_callback
):
    requests = []
    callback_calls = []

    def handler(value):
        requests.append(value)
        return httpx.Response(200, json={"code": "0", "data": [{"sCode": "0"}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        config = settings()
        transport = OkxDemoPrivateRestClient(http, settings=config)

        class Private(FakePrivate):
            async def place_order(self, payload):
                return await transport.place_order(payload)

        service = OkxDemoService(Private(), FakePublic(), None, settings=config)
        order = request(
            order_type=order_type,
            price=None if order_type == "market" else Decimal(100010),
        )
        callback = (
            (lambda: callback_calls.append("caller-pass"))
            if automation_callback
            else None
        )
        with pytest.raises(OkxPrivateApiError) as caught:
            await service.place_order(order, before_submit=callback)
        assert caught.value.code == DENIED
    assert requests == []
    assert callback_calls == (["caller-pass"] if automation_callback else [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    (
        "cancel_order",
        "close_position",
        "cancel_all_after",
        "order_precheck",
    ),
)
async def test_existing_maintenance_transport_remains_single_attempt_and_simulated(
    operation,
):
    requests = []

    def handler(value):
        requests.append(value)
        assert value.headers["x-simulated-trading"] == "1"
        return httpx.Response(200, json={"code": "0", "data": [{"sCode": "0"}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(http, settings=demo_settings())
        result = await getattr(client, operation)({"instId": "BTC-USDT-SWAP"})
    assert result == [{"sCode": "0"}]
    assert len(requests) == 1
    assert requests[0].method == "POST"


@pytest.mark.asyncio
async def test_manual_demo_leverage_cannot_write_before_qualified_flat_authority():
    requests = []

    def handler(value):
        requests.append(value)
        pytest.fail("unqualified Demo leverage change reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        config = settings()
        transport = OkxDemoPrivateRestClient(http, settings=config)

        class Private(FakePrivate):
            async def set_leverage(self, payload):
                return await transport.set_leverage(payload)

        service = OkxDemoService(Private(), FakePublic(), None, settings=config)
        with pytest.raises(OkxPrivateApiError) as caught:
            await service.set_leverage(
                OkxDemoLeverageRequest(
                    instrument_id="BTC-USDT-SWAP",
                    leverage=3,
                    margin_mode="isolated",
                    direction="long",
                    confirmation="OKX_DEMO_ONLY",
                )
            )
        assert caught.value.code == DENIED
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("write", (False, True))
@pytest.mark.parametrize("malformed_response", (False, True))
async def test_maintenance_cannot_gain_retries_via_read_flag(write, malformed_response):
    requests = []

    def handler(value):
        requests.append(value)
        if malformed_response:
            return httpx.Response(200, json={"code": "0", "data": []})
        raise httpx.ReadTimeout("synthetic ambiguous reply", request=value)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(
            http, settings=demo_settings(okx_demo_read_max_retries=5)
        )
        with pytest.raises(OkxPrivateApiError) as caught:
            await client._request(
                "POST", "/api/v5/trade/close-position", body={}, write=write
            )
        assert caught.value.code == (
            "ambiguous_response" if malformed_response else "transport_error"
        )
    assert len(requests) == 1
