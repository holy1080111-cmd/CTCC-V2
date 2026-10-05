from copy import deepcopy
from decimal import Decimal

import httpx
import pytest

from app.exchange.okx.errors import OkxPublicApiError
from app.exchange.okx.public_rest import OkxPublicRestClient

INSTRUMENT = "BTC-USDT-SWAP"
MILLISECONDS = "1750000000000"
VALID_AUX_ROWS = {
    "mark_price": {
        "instId": INSTRUMENT,
        "instType": "SWAP",
        "markPx": "100.00000000000000000001",
        "ts": MILLISECONDS,
    },
    "funding_rate": {
        "instId": INSTRUMENT,
        "instType": "SWAP",
        "fundingRate": "0",
        "fundingTime": "1750000000001",
        "nextFundingTime": "1750028800001",
        "ts": MILLISECONDS,
    },
    "open_interest": {
        "instId": INSTRUMENT,
        "instType": "SWAP",
        "oi": "0",
        "oiCcy": "0.00000000000000000001",
        "ts": MILLISECONDS,
    },
    "order_book": {
        "asks": [["101", "2", "0", "1"]],
        "bids": [["99", "3", "0", "2"]],
        "ts": "1750000000001",
        "seqId": 1,
    },
}
PATHS = {
    "mark_price": "/api/v5/public/mark-price",
    "funding_rate": "/api/v5/public/funding-rate",
    "open_interest": "/api/v5/public/open-interest",
    "order_book": "/api/v5/market/books",
}


async def _aux_request(method: str, rows: list[object]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == PATHS[method]
        assert request.url.params["instId"] == INSTRUMENT
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        return await getattr(OkxPublicRestClient(client), method)(INSTRUMENT)


@pytest.mark.asyncio
async def test_ticker_request_and_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/market/ticker"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "instType": "SWAP",
                        "instId": "BTC-USDT-SWAP",
                        "last": "100",
                        "bidPx": "99",
                        "askPx": "101",
                        "bidSz": "2",
                        "askSz": "3",
                        "open24h": "90",
                        "high24h": "110",
                        "low24h": "80",
                        "vol24h": "1000",
                        "volCcy24h": "100000",
                        "ts": "1750000000000",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://example.test"
    ) as http_client:
        ticker = await OkxPublicRestClient(http_client).ticker("BTC-USDT-SWAP")
    assert str(ticker.last) == "100"


@pytest.mark.asyncio
async def test_ticker_rejects_other_instrument_volume() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["instId"] == "BTC-USDT-SWAP"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "instType": "SWAP",
                        "instId": "ETH-USDT-SWAP",
                        "last": "100",
                        "bidPx": "99",
                        "askPx": "101",
                        "bidSz": "2",
                        "askSz": "3",
                        "open24h": "90",
                        "high24h": "110",
                        "low24h": "80",
                        "vol24h": "1000",
                        "volCcy24h": "100000",
                        "ts": "1750000000000",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="ticker instrument mismatch"):
            await OkxPublicRestClient(client).ticker("BTC-USDT-SWAP")


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [[], [{}, {}]])
async def test_ticker_requires_exactly_one_response_row(data) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": data})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="non-singleton"):
            await OkxPublicRestClient(client).ticker("BTC-USDT-SWAP")


@pytest.mark.asyncio
async def test_candles_reject_spot_before_request() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("no SPOT candle may enter SWAP parser")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="SWAP candle instrument required"):
            await OkxPublicRestClient(client).candles("BTC-USDT", "1m", 1)
    assert not calls


@pytest.mark.asyncio
async def test_auxiliary_reads_preserve_decimal_precision_and_real_zero() -> None:
    assert await _aux_request("mark_price", [VALID_AUX_ROWS["mark_price"]]) == Decimal(
        "100.00000000000000000001"
    )
    funding, next_time = await _aux_request(
        "funding_rate", [VALID_AUX_ROWS["funding_rate"]]
    )
    assert funding == Decimal(0)
    assert next_time is not None
    assert next_time.microsecond == 1000
    assert await _aux_request("open_interest", [VALID_AUX_ROWS["open_interest"]]) == (
        Decimal(0),
        Decimal("0.00000000000000000001"),
    )
    book = await _aux_request("order_book", [VALID_AUX_ROWS["order_book"]])
    assert book.instrument_id == INSTRUMENT
    assert book.timestamp.microsecond == 1000
    assert book.bids[0].price == Decimal(99)
    assert book.asks[0].price == Decimal(101)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(VALID_AUX_ROWS))
@pytest.mark.parametrize(
    "instrument_id", ["BTC-USDT", "BTC-USDT-SWAP-extra", "-SWAP", "btc-usdt-swap"]
)
async def test_auxiliary_reads_reject_non_swap_request_before_network(
    method: str, instrument_id: str
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid instrument must not start a request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="SWAP instrument required"):
            await getattr(OkxPublicRestClient(client), method)(instrument_id)
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, -1, 401, True])
async def test_order_book_rejects_invalid_depth_before_network(size: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid depth must not start a request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="invalid requested depth"):
            await OkxPublicRestClient(client).order_book(INSTRUMENT, size)
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(VALID_AUX_ROWS))
@pytest.mark.parametrize("kind", ["empty", "duplicate", "non_object"])
async def test_auxiliary_reads_require_single_object_row(
    method: str, kind: str
) -> None:
    row = deepcopy(VALID_AUX_ROWS[method])
    rows = {"empty": [], "duplicate": [row, row], "non_object": [None]}[kind]
    with pytest.raises(OkxPublicApiError, match="non-singleton"):
        await _aux_request(method, rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["mark_price", "funding_rate", "open_interest"])
@pytest.mark.parametrize("identity", [None, "ETH-USDT-SWAP"])
async def test_auxiliary_reads_require_exact_response_instrument(
    method: str, identity: str | None
) -> None:
    row = deepcopy(VALID_AUX_ROWS[method])
    if identity is None:
        row.pop("instId")
    else:
        row["instId"] = identity
    with pytest.raises(OkxPublicApiError, match="instrument mismatch"):
        await _aux_request(method, [row])


@pytest.mark.asyncio
async def test_order_book_rejects_conflicting_optional_response_instrument() -> None:
    row = deepcopy(VALID_AUX_ROWS["order_book"])
    row["instId"] = "ETH-USDT-SWAP"
    with pytest.raises(OkxPublicApiError, match="instrument mismatch"):
        await _aux_request("order_book", [row])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["mark_price", "funding_rate", "open_interest"])
@pytest.mark.parametrize("instrument_type", [None, "SPOT"])
async def test_auxiliary_reads_require_swap_response_type(
    method: str, instrument_type: str | None
) -> None:
    row = deepcopy(VALID_AUX_ROWS[method])
    if instrument_type is None:
        row.pop("instType")
    else:
        row["instType"] = instrument_type
    with pytest.raises(OkxPublicApiError, match="instrument type mismatch"):
        await _aux_request(method, [row])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "field"),
    [
        ("mark_price", "markPx"),
        ("funding_rate", "fundingRate"),
        ("open_interest", "oi"),
        ("open_interest", "oiCcy"),
    ],
)
@pytest.mark.parametrize("bad", [None, "", "NaN", "Infinity", "-Infinity", "1e2", 1])
async def test_auxiliary_reads_reject_missing_or_invalid_numbers(
    method: str, field: str, bad: object
) -> None:
    row = deepcopy(VALID_AUX_ROWS[method])
    row[field] = bad
    with pytest.raises(OkxPublicApiError, match=f"invalid {field}"):
        await _aux_request(method, [row])


@pytest.mark.asyncio
async def test_auxiliary_reads_allow_negative_funding_but_not_negative_interest_or_mark() -> (
    None
):
    funding_row = deepcopy(VALID_AUX_ROWS["funding_rate"])
    funding_row["fundingRate"] = "-0.00000000000000000001"
    funding, _ = await _aux_request("funding_rate", [funding_row])
    assert funding == Decimal("-0.00000000000000000001")

    for method, field in (("mark_price", "markPx"), ("open_interest", "oi")):
        row = deepcopy(VALID_AUX_ROWS[method])
        row[field] = "-1"
        with pytest.raises(OkxPublicApiError, match=f"invalid {field}"):
            await _aux_request(method, [row])

    mark_row = deepcopy(VALID_AUX_ROWS["mark_price"])
    mark_row["markPx"] = "0"
    with pytest.raises(OkxPublicApiError, match="invalid markPx"):
        await _aux_request("mark_price", [mark_row])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(VALID_AUX_ROWS))
@pytest.mark.parametrize("bad", [None, "", "NaN", "175000000000", 1750000000000])
async def test_auxiliary_reads_reject_invalid_exchange_time(
    method: str, bad: object
) -> None:
    row = deepcopy(VALID_AUX_ROWS[method])
    row["ts"] = bad
    with pytest.raises(OkxPublicApiError, match="invalid ts"):
        await _aux_request(method, [row])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "", "NaN", "175000000000", 1750000000000])
@pytest.mark.parametrize("field", ["fundingTime", "nextFundingTime"])
async def test_funding_requires_valid_settlement_times(field: str, bad: object) -> None:
    row = deepcopy(VALID_AUX_ROWS["funding_rate"])
    row[field] = bad
    with pytest.raises(OkxPublicApiError, match=f"invalid {field}"):
        await _aux_request("funding_rate", [row])


@pytest.mark.asyncio
@pytest.mark.parametrize("next_time", ["1750000000001", "1749999999999"])
async def test_funding_rejects_nonadvancing_next_settlement(next_time: str) -> None:
    row = deepcopy(VALID_AUX_ROWS["funding_rate"])
    row["nextFundingTime"] = next_time
    with pytest.raises(OkxPublicApiError, match="invalid settlement chronology"):
        await _aux_request("funding_rate", [row])


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["bids", "asks"])
@pytest.mark.parametrize(
    "bad", [None, [], [["100", "1", "0"]], [["100", "1", "1", "1"]]]
)
async def test_order_book_requires_complete_depth(side: str, bad: object) -> None:
    row = deepcopy(VALID_AUX_ROWS["order_book"])
    row[side] = bad
    with pytest.raises(OkxPublicApiError, match="order book invalid"):
        await _aux_request("order_book", [row])


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("bad", [None, "", "NaN", "Infinity", "0", "-1"])
async def test_order_book_rejects_invalid_level_price_or_size(
    index: int, bad: object
) -> None:
    row = deepcopy(VALID_AUX_ROWS["order_book"])
    row["bids"][0][index] = bad
    with pytest.raises(OkxPublicApiError, match="order book invalid"):
        await _aux_request("order_book", [row])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "field"),
    [
        ("mark_price", "markPx"),
        ("funding_rate", "fundingRate"),
        ("open_interest", "oi"),
    ],
)
@pytest.mark.parametrize("bad", ["1" * 41, "0." + "1" * 21])
async def test_auxiliary_reads_bound_numeric_field_length(
    method: str, field: str, bad: str
) -> None:
    row = deepcopy(VALID_AUX_ROWS[method])
    row[field] = bad
    with pytest.raises(OkxPublicApiError, match=f"invalid {field}"):
        await _aux_request(method, [row])


@pytest.mark.asyncio
async def test_order_book_rejects_unbounded_order_count() -> None:
    row = deepcopy(VALID_AUX_ROWS["order_book"])
    row["bids"][0][3] = "9" * 40
    with pytest.raises(OkxPublicApiError, match="order book invalid"):
        await _aux_request("order_book", [row])


@pytest.mark.asyncio
async def test_order_book_rejects_zero_order_count() -> None:
    row = deepcopy(VALID_AUX_ROWS["order_book"])
    row["bids"][0][3] = "0"
    with pytest.raises(OkxPublicApiError, match="order book invalid"):
        await _aux_request("order_book", [row])


@pytest.mark.asyncio
async def test_bad_auxiliary_row_does_not_retry_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [{**VALID_AUX_ROWS["mark_price"], "markPx": ""}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="invalid markPx"):
            await OkxPublicRestClient(client).mark_price(INSTRUMENT)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"code": "0", "data": {}}, []])
async def test_malformed_success_envelope_does_not_retry_request(body: object) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match="non-list data|non-object body"):
            await OkxPublicRestClient(client).mark_price(INSTRUMENT)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"{not-json", "invalid JSON"),
        (b'{"code":0,"data":[]}', "invalid code"),
    ],
)
async def test_malformed_success_body_does_not_retry_request(
    content: bytes, error: str
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as client:
        with pytest.raises(OkxPublicApiError, match=error):
            await OkxPublicRestClient(client).mark_price(INSTRUMENT)
    assert calls == 1
