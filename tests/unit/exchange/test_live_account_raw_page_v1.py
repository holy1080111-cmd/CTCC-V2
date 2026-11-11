from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.exchange.okx.live_account_raw_page_v1 import (
    LiveRawPageError,
    observe_live_account_raw_page_v1,
    select_live_account_raw_page_v1,
    verify_live_account_raw_page_readback_v1,
)
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from tests.unit.exchange.test_live_account_config_observation_v1 import (
    _http,
    _raw_response,
    _settings,
)


def test_live_page_selector_is_closed_and_canonical() -> None:
    pending = select_live_account_raw_page_v1("orders_pending", after="123")
    assert pending.cursor_type == "ordId"
    assert pending.query == (("after", "123"), ("limit", "100"))
    algo = select_live_account_raw_page_v1("algo_smart_iceberg")
    assert algo.cursor_type == "algoId"
    assert algo.query == (("limit", "100"), ("ordType", "smart_iceberg"))
    cross = select_live_account_raw_page_v1(
        "leverage_cross", leverage_instrument_ids=("BTC-USDT-SWAP",)
    )
    assert cross.query == (("instId", "BTC-USDT-SWAP"), ("mgnMode", "cross"))
    for kwargs in (
        {"stream": "orders_history"},
        {"stream": "positions", "after": "123"},
        {"stream": "orders_pending", "after": "0"},
        {"stream": "leverage_cross"},
        {
            "stream": "leverage_cross",
            "leverage_instrument_ids": ("BTC-USDT-SWAP", "BTC-USDT-SWAP"),
        },
    ):
        with pytest.raises(LiveRawPageError):
            select_live_account_raw_page_v1(**kwargs)


@pytest.mark.asyncio
async def test_live_page_one_owned_get_and_raw_readback() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _raw_response(b'{"code":"0","data":[{"ordId":"103"},{"ordId":"101"}]}')

    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease:
            selector = select_live_account_raw_page_v1("orders_pending", after="105")
            page = await observe_live_account_raw_page_v1(owner, lease, selector)
            result = verify_live_account_raw_page_readback_v1(page, lease=lease)
            assert page.row_ids == ("103", "101")
            assert page.empty_page is False
            assert page.row_count == 2
            assert page.account_complete is False
            assert page.source_authenticity_verified is False
            assert page.execution_authority is False
            assert result["receipt_sha256"] == page.receipt_sha256
            with pytest.raises(LiveRawPageError):
                verify_live_account_raw_page_readback_v1(
                    replace(page, raw_body=b'{"code":"0","data":[]}'), lease=lease
                )
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://openapi.okx.com/api/v5/trade/orders-pending?after=105&limit=100"
    )
    assert "x-simulated-trading" not in requests[0].headers


@pytest.mark.asyncio
async def test_live_page_empty_is_not_account_or_chain_completion() -> None:
    async with _http(lambda _: _raw_response(b'{"code":"0","data":[]}')) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease:
            page = await observe_live_account_raw_page_v1(
                owner, lease, select_live_account_raw_page_v1("algo_conditional")
            )
            assert page.empty_page is True
            assert page.account_complete is False
            assert page.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"code":"0","data":[{"ordId":"101"},{"ordId":"103"}]}',
        b'{"code":"0","data":[{"ordId":"105"}]}',
        b'{"code":"1","data":[]}',
    ],
)
async def test_live_page_rejects_source_order_cursor_and_provider_code(
    body: bytes,
) -> None:
    async with _http(lambda _: _raw_response(body)) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease, pytest.raises(LiveRawPageError):
            await observe_live_account_raw_page_v1(
                owner,
                lease,
                select_live_account_raw_page_v1("orders_pending", after="105"),
            )
