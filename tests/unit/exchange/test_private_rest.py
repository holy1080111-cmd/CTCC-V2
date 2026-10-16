import logging
from datetime import UTC, datetime

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx import private_rest
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
    OkxLivePrivateRestClient,
    _OkxPrivateRestClientBase,
    _private_response_rejection,
    _project_successful_write_data,
    build_signature,
    utc_iso_timestamp,
)
from app.okx_demo.service import OkxDemoService


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


class _SyntheticWriteClient(_OkxPrivateRestClientBase):
    """Network-free transport fixture; production authority remains unchanged."""

    def _credentials(self) -> tuple[str, str, str]:
        return "fixture-key", "fixture-secret", "fixture-passphrase"

    def _rest_base_url(self) -> str:
        return "https://www.okx.com"

    def _timeout_seconds(self) -> float:
        return 1.0

    def _read_max_retries(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_successful_write_exposes_only_validated_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "sensitive-remote-payload-must-never-leak"
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": marker,
                "data": [
                    {
                        "sCode": "0",
                        "sMsg": marker,
                        "ordId": "12345",
                        "clOrdId": "CTCCsynthetic1",
                        "uid": marker,
                        "exchange_data": {"secret": marker},
                    }
                ],
            },
        )

    # Exercise the successful response path with a MockTransport. This fixture
    # is never an execution authority and cannot reach an exchange.
    monkeypatch.setattr(
        private_rest,
        "require_owned_private_write_authority",
        lambda *args, **kwargs: None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _SyntheticWriteClient(http, settings=demo_settings())._request(
            "POST",
            "/api/v5/trade/order",
            body={"clOrdId": "CTCCsynthetic1", "instId": "BTC-USDT-SWAP"},
            write=True,
        )

    assert len(calls) == 1
    assert result == [{"sCode": "0", "ordId": "12345", "clOrdId": "CTCCsynthetic1"}]
    assert OkxDemoService._ack(result).exchange_message == ""
    assert marker not in repr(result)


@pytest.mark.asyncio
async def test_malformed_successful_write_is_ambiguous_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "private-token-marker"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": marker,
                "data": [
                    {
                        "sCode": "0",
                        "sMsg": marker,
                        "ordId": marker,
                        "clOrdId": "CTCCsynthetic1",
                    }
                ],
            },
        )

    monkeypatch.setattr(
        private_rest,
        "require_owned_private_write_authority",
        lambda *args, **kwargs: None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OkxPrivateApiError) as error:
            await _SyntheticWriteClient(http, settings=demo_settings())._request(
                "POST",
                "/api/v5/trade/order",
                body={"clOrdId": "CTCCsynthetic1", "instId": "BTC-USDT-SWAP"},
                write=True,
            )

    assert calls == 1
    assert error.value.code == "ambiguous_response"
    assert error.value.data is None
    assert marker not in repr(error.value)


def test_demo_ack_never_exposes_untrusted_response_message() -> None:
    acknowledgement = OkxDemoService._ack(
        [
            {
                "ordId": "123",
                "clOrdId": "CTCCsynthetic1",
                "sCode": "0",
                "sMsg": "private-token-marker",
            }
        ]
    )
    assert acknowledgement.exchange_message == ""
    assert "private-token-marker" not in acknowledgement.model_dump_json()


@pytest.mark.parametrize(
    "path,body,row,expected",
    [
        (
            "/api/v5/trade/order",
            '{"clOrdId":"CTCCsynthetic1"}',
            {
                "sCode": "0",
                "sMsg": "private-marker",
                "ordId": "1",
                "clOrdId": "CTCCsynthetic1",
                "uid": "private-marker",
            },
            [{"sCode": "0", "ordId": "1", "clOrdId": "CTCCsynthetic1"}],
        ),
        (
            "/api/v5/trade/cancel-order",
            '{"ordId":"1"}',
            {
                "sCode": "0",
                "ordId": "1",
                "sMsg": "private-marker",
                "uid": "private-marker",
                "clOrdId": "private-marker",
            },
            [{"sCode": "0", "ordId": "1"}],
        ),
        (
            "/api/v5/account/set-leverage",
            '{"instId":"BTC-USDT-SWAP","mgnMode":"isolated","lever":"5"}',
            {
                "sCode": "0",
                "instId": "BTC-USDT-SWAP",
                "mgnMode": "isolated",
                "posSide": "net",
                "lever": "5.0",
                "sMsg": "private-marker",
            },
            [
                {
                    "sCode": "0",
                    "instId": "BTC-USDT-SWAP",
                    "mgnMode": "isolated",
                    "posSide": "net",
                    "lever": "5.0",
                }
            ],
        ),
        (
            "/api/v5/trade/close-position",
            '{"instId":"BTC-USDT-SWAP","mgnMode":"cross","posSide":"long"}',
            {
                "instId": "BTC-USDT-SWAP",
                "posSide": "long",
                "clOrdId": "",
                "tag": "",
                "sMsg": "private-marker",
                "uid": "private-marker",
            },
            [{"instId": "BTC-USDT-SWAP", "posSide": "long"}],
        ),
        (
            "/api/v5/trade/order-precheck",
            '{"instId":"BTC-USDT-SWAP","tdMode":"cross","side":"buy","ordType":"limit","sz":"1"}',
            {
                "adjEq": "41.94347460746277",
                "adjEqChg": "-226.05616481626",
                "imr": "0",
                "imrChg": "57.74709688430927",
                "mmr": "0",
                "mmrChg": "0",
                "mgnRatio": "0",
                "mgnRatioChg": "0",
                "sMsg": "private-marker",
                "uid": "private-marker",
            },
            [
                {
                    "adjEqChg": "-226.05616481626",
                    "imrChg": "57.74709688430927",
                    "mmrChg": "0",
                }
            ],
        ),
        (
            "/api/v5/trade/cancel-all-after",
            '{"timeOut":"30","tag":"CTCCV11"}',
            {
                "triggerTime": "1587971430",
                "tag": "CTCCV11",
                "ts": "1587971400",
                "sMsg": "private-marker",
                "uid": "private-marker",
            },
            [{"triggerTime": "1587971430", "ts": "1587971400", "tag": "CTCCV11"}],
        ),
    ],
)
def test_write_route_projections_drop_untrusted_exchange_text(
    path: str, body: str, row: dict[str, object], expected: list[dict[str, str]]
) -> None:
    projected = _project_successful_write_data(
        path=path, signed_body_text=body, data=[row]
    )
    assert projected == expected
    assert "private-marker" not in repr(projected)


@pytest.mark.parametrize(
    "path,body,row",
    [
        (
            "/api/v5/trade/order",
            '{"clOrdId":"CTCCsynthetic1"}',
            {"sCode": "0", "ordId": "secret\nmarker", "clOrdId": "CTCCsynthetic1"},
        ),
        (
            "/api/v5/trade/order",
            '{"clOrdId":"CTCCsynthetic1"}',
            {"sCode": "0", "ordId": "1", "clOrdId": "secret-marker"},
        ),
        (
            "/api/v5/trade/cancel-order",
            '{"ordId":"1"}',
            {"sCode": "0", "ordId": "2"},
        ),
        (
            "/api/v5/trade/cancel-order",
            '{"clOrdId":"CTCCsynthetic1"}',
            {"sCode": "0", "ordId": "1", "clOrdId": "secret-marker"},
        ),
        (
            "/api/v5/account/set-leverage",
            '{"instId":"BTC-USDT-SWAP","mgnMode":"isolated","lever":"5"}',
            {
                "sCode": "0",
                "instId": "secret-marker",
                "mgnMode": "isolated",
                "lever": "5",
                "posSide": "net",
            },
        ),
        (
            "/api/v5/trade/close-position",
            '{"instId":"BTC-USDT-SWAP","mgnMode":"cross","posSide":"net"}',
            {"sCode": "0", "instId": "ETH-USDT-SWAP"},
        ),
    ],
)
def test_invalid_successful_write_field_is_ambiguous_without_remote_text(
    path: str, body: str, row: dict[str, object]
) -> None:
    with pytest.raises(OkxPrivateApiError) as error:
        _project_successful_write_data(path=path, signed_body_text=body, data=[row])
    assert error.value.code == "ambiguous_response"
    assert "secret" not in repr(error.value)


@pytest.mark.parametrize(
    "path,body,row",
    [
        (
            "/api/v5/trade/order-precheck",
            '{"instId":"BTC-USDT-SWAP","tdMode":"cross","side":"buy","ordType":"limit","sz":"1"}',
            {"sCode": "0", "junk": "private-marker"},
        ),
        (
            "/api/v5/trade/order-precheck",
            '{"instId":"BTC-USDT-SWAP","tdMode":"cross","side":"buy","ordType":"limit","sz":"1"}',
            {"adjEq": "0", "imr": "0", "junk": "private-marker"},
        ),
        (
            "/api/v5/trade/close-position",
            '{"instId":"BTC-USDT-SWAP","mgnMode":"cross","posSide":"long"}',
            {"sCode": "0", "junk": "private-marker"},
        ),
        (
            "/api/v5/trade/close-position",
            '{"instId":"BTC-USDT-SWAP","mgnMode":"cross","posSide":"long"}',
            {"instId": "BTC-USDT-SWAP", "posSide": "short", "clOrdId": "", "tag": ""},
        ),
        (
            "/api/v5/trade/cancel-all-after",
            '{"timeOut":"30","tag":"CTCCV11"}',
            {"sCode": "0", "junk": "private-marker"},
        ),
        (
            "/api/v5/trade/cancel-all-after",
            '{"timeOut":"30","tag":"CTCCV11"}',
            {"triggerTime": "1587971400", "ts": "1587971400", "tag": "CTCCV11"},
        ),
        (
            "/api/v5/trade/cancel-all-after",
            '{"timeOut":"30","tag":"CTCCV11"}',
            {"triggerTime": "1587971440", "ts": "1587971400", "tag": "CTCCV11"},
        ),
        (
            "/api/v5/trade/cancel-all-after",
            '{"timeOut":"30","tag":"CTCCV11"}',
            {"triggerTime": "1587971430", "ts": "1587971400", "tag": "WRONG"},
        ),
    ],
)
def test_documented_non_order_write_shape_and_echo_fail_closed(
    path: str, body: str, row: dict[str, object]
) -> None:
    with pytest.raises(OkxPrivateApiError) as error:
        _project_successful_write_data(path=path, signed_body_text=body, data=[row])
    assert error.value.code == "ambiguous_response"
    assert "private-marker" not in repr(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/api/v5/trade/order-precheck",
            {
                "instId": "BTC-USDT-SWAP",
                "tdMode": "cross",
                "side": "buy",
                "ordType": "limit",
                "sz": "1",
            },
        ),
        (
            "/api/v5/trade/close-position",
            {"instId": "BTC-USDT-SWAP", "mgnMode": "cross", "posSide": "long"},
        ),
        (
            "/api/v5/trade/cancel-all-after",
            {"timeOut": "30", "tag": "CTCCV11"},
        ),
    ],
)
async def test_junk_only_successful_non_order_write_is_ambiguous_without_retry(
    path: str, body: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "private-response-marker"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"code": "0", "msg": marker, "data": [{"junk": marker}]}
        )

    monkeypatch.setattr(
        private_rest,
        "require_owned_private_write_authority",
        lambda *args, **kwargs: None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OkxPrivateApiError) as error:
            await _SyntheticWriteClient(http, settings=demo_settings())._request(
                "POST", path, body=body, write=True
            )

    assert calls == 1
    assert error.value.code == "ambiguous_response"
    assert marker not in repr(error.value)


@pytest.mark.parametrize(
    "path,rows",
    [
        (
            "/api/v5/trade/order",
            [
                {"sCode": "0", "ordId": "1", "clOrdId": "CTCCsynthetic1"},
                {"sCode": "0", "ordId": "2", "clOrdId": "CTCCsynthetic1"},
            ],
        ),
        (
            "/api/v5/account/unknown-write",
            [{"sCode": "0", "secret": "private-marker"}],
        ),
    ],
)
def test_unrecognized_or_multiple_write_results_require_reconciliation(
    path: str, rows: list[dict[str, object]]
) -> None:
    with pytest.raises(OkxPrivateApiError) as error:
        _project_successful_write_data(
            path=path, signed_body_text='{"clOrdId":"CTCCsynthetic1"}', data=rows
        )
    assert error.value.code == "ambiguous_response"
    assert "private-marker" not in repr(error.value)


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
@pytest.mark.parametrize("code", ["50110", "50119"])
@pytest.mark.parametrize("environment", ["demo", "live"])
async def test_private_get_classifies_only_bounded_401_auth_code_without_remote_text(
    code: str, environment: str
) -> None:
    calls: list[httpx.Request] = []
    remote_text = "confidential-remote-error-marker"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v5/account/config"
        assert (request.headers.get("x-simulated-trading") == "1") is (
            environment == "demo"
        )
        return httpx.Response(
            401,
            json={"code": code, "msg": remote_text, "data": [remote_text]},
        )

    settings = (
        demo_settings(okx_demo_allow_order_writes=False, okx_demo_read_max_retries=2)
        if environment == "demo"
        else Settings(
            _env_file=None,
            okx_live_api_key="synthetic-live-key",
            okx_live_api_secret="synthetic-live-secret",
            okx_live_api_passphrase="synthetic-live-passphrase",
            okx_live_read_max_retries=2,
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = (
            OkxDemoPrivateRestClient(http, settings=settings)
            if environment == "demo"
            else OkxLivePrivateRestClient(http, settings=settings)
        )
        with pytest.raises(OkxPrivateApiError) as error:
            await client.account_config()

    assert error.value.code == code
    assert error.value.data is None
    assert str(error.value) == "OKX private read authentication rejected"
    assert remote_text not in repr(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            401, content=b"not-json", headers={"content-type": "application/json"}
        ),
        httpx.Response(
            401, json={"code": "50113", "msg": "confidential-remote-error-marker"}
        ),
        httpx.Response(
            401, json={"code": 50110, "msg": "confidential-remote-error-marker"}
        ),
        httpx.Response(
            401,
            content=b'{"code":"50110"}',
            headers={"content-type": "text/plain"},
        ),
        httpx.Response(
            400, json={"code": "50110", "msg": "confidential-remote-error-marker"}
        ),
        httpx.Response(
            401,
            json={"code": "50110", "msg": "confidential-remote-error-marker" * 200},
        ),
    ],
    ids=[
        "malformed",
        "unknown",
        "integer",
        "wrong-media-type",
        "wrong-status",
        "oversized",
    ],
)
async def test_private_get_other_http_errors_remain_generic_and_redacted(
    response: httpx.Response,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OkxPrivateApiError) as error:
            await OkxDemoPrivateRestClient(
                http,
                settings=demo_settings(
                    okx_demo_allow_order_writes=False, okx_demo_read_max_retries=0
                ),
            ).account_config()

    assert calls == 1
    assert error.value.code == "transport_error"
    assert error.value.data is None
    assert "confidential-remote-error-marker" not in str(error.value)
    assert "confidential-remote-error-marker" not in repr(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_code", "expected_code"),
    [("50110", "50110"), ("50110:private-token", "private_response_rejected")],
)
async def test_200_read_error_cannot_leak_remote_values_to_demo_status_api_or_log(
    remote_code: str, expected_code: str, caplog: pytest.LogCaptureFixture
) -> None:
    from app.api.routers.okx_demo import _http_error
    from app.exchange.okx.public_rest import OkxPublicRestClient
    from app.okx_demo.service import OkxDemoService

    marker = "private-ip-uid-token-marker"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        assert request.url.path == "/api/v5/account/config"
        return httpx.Response(
            200,
            json={
                "code": remote_code,
                "msg": marker,
                "data": [{"uid": marker, "ip": marker, "token": marker}],
            },
        )

    settings = demo_settings(
        okx_demo_allow_order_writes=False, okx_demo_read_max_retries=0
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = OkxDemoService(
            OkxDemoPrivateRestClient(http, settings=settings),
            OkxPublicRestClient(),
            None,
            settings=settings,
        )
        with pytest.raises(OkxPrivateApiError) as error:
            await service.connectivity_check()
        status = await service.status()

    assert calls == 1
    assert error.value.code == expected_code
    assert error.value.data is None
    assert str(error.value) == "OKX private read rejected"
    assert marker not in str(error.value)
    assert marker not in str(status.last_error)
    assert marker not in repr(_http_error(error.value).detail)
    with caplog.at_level(logging.ERROR):
        try:
            raise error.value
        except OkxPrivateApiError:
            logging.getLogger("app.demo_automation.service").exception(
                "demo_automation_symbol_failed"
            )
    assert marker not in caplog.text


@pytest.mark.parametrize("remote_code", ["51000", "51000:private-token", 51000])
def test_write_response_error_is_redacted_and_always_ambiguous(
    remote_code: object,
) -> None:
    error = _private_response_rejection(remote_code, write=True)
    assert error.code == "ambiguous_response"
    assert error.data is None
    assert str(error) == "OKX private write response requires reconciliation"
    assert "private-token" not in repr(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_location", ["top-level", "per-item"])
async def test_synthetic_write_error_cannot_expose_remote_message_or_data(
    failure_location: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The production write authorities are only bypassed inside this exact
    # MockTransport test adapter to exercise the otherwise unreachable parser.
    monkeypatch.setattr(
        private_rest, "enforce_demo_submission_boundary", lambda *_: None
    )
    monkeypatch.setattr(private_rest, "enforce_demo_final_dispatch", lambda *_: None)
    monkeypatch.setattr(
        private_rest, "require_owned_private_write_authority", lambda *_, **__: None
    )
    marker = "private-ip-uid-token-marker"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        assert request.url.path == "/api/v5/trade/order"
        assert request.headers["x-simulated-trading"] == "1"
        return httpx.Response(
            200,
            json=(
                {"code": "51000", "msg": marker, "data": [{"uid": marker}]}
                if failure_location == "top-level"
                else {
                    "code": "0",
                    "data": [{"sCode": "51000", "sMsg": marker, "uid": marker}],
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(OkxPrivateApiError) as error:
            await OkxDemoPrivateRestClient(
                http,
                settings=demo_settings(
                    okx_demo_allow_order_writes=False, okx_demo_read_max_retries=2
                ),
            )._request("POST", "/api/v5/trade/order", body={"instId": "BTC-USDT-SWAP"})

    assert calls == 1
    assert error.value.code == "ambiguous_response"
    assert error.value.data is None
    assert str(error.value) == "OKX private write response requires reconciliation"
    assert marker not in repr(error.value)


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
async def test_order_history_short_pages_continue_to_explicit_empty_terminal() -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/trade/orders-history"
        assert request.url.params["instType"] == "SWAP"
        assert request.url.params["instId"] == "BTC-USDT-SWAP"
        assert request.url.params["limit"] == "3"
        assert request.headers["x-simulated-trading"] == "1"
        after = request.url.params.get("after")
        observed.append(after)
        rows = {
            None: [{"ordId": "200"}, {"ordId": "199"}],
            "199": [{"ordId": "198"}],
            "198": [],
        }[after]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as client:
        rows = await OkxDemoPrivateRestClient(
            client, settings=demo_settings()
        ).order_history("BTC-USDT-SWAP", limit=3)

    assert [row["ordId"] for row in rows] == ["200", "199", "198"]
    assert observed == [None, "199", "198"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["duplicate_on_page", "cursor_not_advanced"])
async def test_order_history_rejects_duplicate_or_nonprogressing_cursor(
    fault: str,
) -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        observed.append(after)
        rows = (
            [{"ordId": "200"}, {"ordId": "200"}]
            if fault == "duplicate_on_page"
            else [{"ordId": "200"}, {"ordId": "199"}]
            if after is None
            else [{"ordId": "199"}]
        )
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as client:
        with pytest.raises(OkxPrivateApiError) as error:
            await OkxDemoPrivateRestClient(
                client, settings=demo_settings()
            ).order_history(limit=3)

    assert error.value.code == "pagination_incomplete"
    assert observed == ([None] if fault == "duplicate_on_page" else [None, "199"])


@pytest.mark.asyncio
async def test_order_history_page_cap_without_terminal_fails_closed() -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.params.get("after"))
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [{"ordId": str(200 - len(observed))}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as client:
        with pytest.raises(OkxPrivateApiError) as error:
            await OkxDemoPrivateRestClient(
                client, settings=demo_settings()
            ).order_history()

    assert error.value.code == "pagination_incomplete"
    assert len(observed) == 16


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, -1, 101, "100", 1.0])
async def test_order_history_rejects_invalid_page_limit_before_request(limit) -> None:
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as client:
        with pytest.raises(OkxPrivateApiError) as error:
            await OkxDemoPrivateRestClient(
                client, settings=demo_settings()
            ).order_history(limit=limit)

    assert error.value.code == "pagination_limit_invalid"
    assert observed == []


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
        assert order_type in {
            "conditional",
            "oco",
            "chase",
            "trigger",
            "move_order_stop",
            "iceberg",
            "twap",
            "smart_iceberg",
        }
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
            ("chase", None): [{"algoId": "70"}],
            ("chase", "70"): [],
            ("trigger", None): [],
            ("move_order_stop", None): [{"algoId": "60"}],
            ("move_order_stop", "60"): [],
            ("iceberg", None): [{"algoId": "55"}],
            ("iceberg", "55"): [],
            ("twap", None): [{"algoId": "50"}],
            ("twap", "50"): [],
            ("smart_iceberg", None): [{"algoId": "40"}],
            ("smart_iceberg", "40"): [],
        }[(order_type, after)]
        rows = [{**row, "ordType": order_type} for row in rows]
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

    assert [row["algoId"] for row in result] == [
        "100",
        "99",
        "80",
        "70",
        "60",
        "55",
        "50",
        "40",
    ]
    assert observed == [
        ("conditional", None),
        ("conditional", "100"),
        ("conditional", "99"),
        ("oco", None),
        ("oco", "80"),
        ("chase", None),
        ("chase", "70"),
        ("trigger", None),
        ("move_order_stop", None),
        ("move_order_stop", "60"),
        ("iceberg", None),
        ("iceberg", "55"),
        ("twap", None),
        ("twap", "50"),
        ("smart_iceberg", None),
        ("smart_iceberg", "40"),
    ]


@pytest.mark.asyncio
async def test_unsupported_extra_algo_type_rejects_legacy_reconciliation_read() -> None:
    settings = demo_settings()
    fixed = datetime(2026, 8, 4, 13, 1, 2, 345000, tzinfo=UTC)
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        order_type = request.url.params["ordType"]
        observed.append(order_type)
        if order_type == "chase":
            return httpx.Response(400, json={"code": "unsupported", "data": []})
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        reader = OkxDemoPrivateRestClient(http, settings=settings, clock=lambda: fixed)
        with pytest.raises(OkxPrivateApiError):
            await reader.pending_algo_orders("BTC-USDT-SWAP")

    assert observed[:3] == ["conditional", "oco", "chase"]
    assert set(observed) == {"conditional", "oco", "chase"}


@pytest.mark.asyncio
async def test_pending_algo_row_type_conflict_rejects_inventory() -> None:
    settings = demo_settings()
    fixed = datetime(2026, 8, 4, 13, 1, 2, 345000, tzinfo=UTC)
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.params["ordType"])
        rows = (
            [{"algoId": "100", "ordType": "oco"}]
            if request.url.params.get("after") is None
            else []
        )
        return httpx.Response(200, json={"code": "0", "data": rows})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.okx.com"
    ) as http:
        reader = OkxDemoPrivateRestClient(http, settings=settings, clock=lambda: fixed)
        with pytest.raises(OkxPrivateApiError) as error:
            await reader.pending_algo_orders("BTC-USDT-SWAP")

    assert error.value.code == "pagination_incomplete"
    assert observed == ["conditional", "conditional"]


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
