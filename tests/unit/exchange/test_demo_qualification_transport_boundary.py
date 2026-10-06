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
        "/api/v5/trade/order-precheck",
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
async def test_direct_demo_order_precheck_has_zero_http_io():
    requests = []

    def handler(value):
        requests.append(value)
        pytest.fail("unqualified Demo precheck reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(http, settings=demo_settings())
        with pytest.raises(OkxPrivateApiError) as caught:
            await client.order_precheck({"instId": "BTC-USDT-SWAP", "sz": "1"})
    assert caught.value.code == DENIED
    assert requests == []


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
        payload = (
            {"timeOut": "30", "tag": "CTCCV11"}
            if operation == "cancel_all_after"
            else {"instId": "BTC-USDT-SWAP"}
        )
        result = await getattr(client, operation)(payload)
    assert result == [{"sCode": "0"}]
    assert len(requests) == 1
    assert requests[0].method == "POST"


@pytest.mark.asyncio
async def test_demo_caa_positive_timeout_remains_available_with_order_writes_off():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": "0", "data": [{"sCode": "0"}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(
            http, settings=demo_settings(okx_demo_allow_order_writes=False)
        )
        await client.cancel_all_after({"timeOut": "30", "tag": "CTCCV11"})
    assert len(requests) == 1
    assert requests[0].content == b'{"timeOut":"30","tag":"CTCCV11"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "params"),
    (
        ({"timeOut": "0"}, None),
        ({"timeOut": 0}, None),
        ({"timeOut": "09"}, None),
        ({"timeOut": "121"}, None),
        ({"timeOut": "30", "side": "buy"}, None),
        ({"timeOut": "30", "tag": "bad!"}, None),
        ({}, None),
        ({"timeOut": "30"}, {"timeOut": "0"}),
    ),
)
async def test_demo_caa_invalid_or_query_overridden_body_has_zero_http(payload, params):
    requests = []

    def handler(request):
        requests.append(request)
        pytest.fail("invalid CAA reached private HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(http, settings=demo_settings())
        with pytest.raises(OkxPrivateApiError) as caught:
            await client._request(
                "POST", "/api/v5/trade/cancel-all-after", body=payload, params=params
            )
    assert caught.value.code == "cancel_all_after_payload_rejected"
    assert requests == []


@pytest.mark.asyncio
async def test_demo_caa_checks_serialized_body_after_mutable_payload_changes(
    monkeypatch,
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": "0", "data": [{"sCode": "0"}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        client = OkxDemoPrivateRestClient(http, settings=demo_settings())
        payload = {"timeOut": "30"}
        original_headers = client._headers

        def mutate_after_serialization(**kwargs):
            result = original_headers(**kwargs)
            payload["timeOut"] = "0"
            return result

        monkeypatch.setattr(client, "_headers", mutate_after_serialization)
        await client.cancel_all_after(payload)
    assert payload == {"timeOut": "0"}
    assert len(requests) == 1
    assert requests[0].content == b'{"timeOut":"30"}'


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
