from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.exchange.okx.live_account_history_page_v1 import (
    HISTORY_INST_TYPES,
    LiveHistoryPageError,
    checked_live_account_history_rows_v1,
    live_account_history_page_receipt_document_v1,
    observe_live_account_history_page_v1,
    reconstruct_live_account_history_page_v1,
    select_live_account_history_page_v1,
    verify_live_account_history_page_readback_v1,
)
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from tests.unit.exchange.test_live_account_config_observation_v1 import (
    _http,
    _raw_response,
    _settings,
)

BEGIN = "1700000000000"
END = "1700003600000"


def test_history_selector_exact_endpoint_cursor_and_partition() -> None:
    assert HISTORY_INST_TYPES == (
        "SPOT",
        "MARGIN",
        "SWAP",
        "FUTURES",
        "OPTION",
        "EVENTS",
    )
    fills = select_live_account_history_page_v1(
        "fills_history", begin_ms=BEGIN, end_ms=END, inst_type="SWAP", after="105"
    )
    assert fills.cursor_type == "billId"
    assert fills.query == (
        ("after", "105"),
        ("begin", BEGIN),
        ("end", END),
        ("instType", "SWAP"),
        ("limit", "100"),
    )
    orders = select_live_account_history_page_v1(
        "orders_archive", begin_ms=BEGIN, end_ms=END, inst_type="EVENTS"
    )
    assert orders.cursor_type == "ordId"
    algo = select_live_account_history_page_v1(
        "algo_history", ord_type="smart_iceberg", state="order_failed"
    )
    assert algo.cursor_type == "algoId"
    assert algo.query == (
        ("limit", "100"),
        ("ordType", "smart_iceberg"),
        ("state", "order_failed"),
    )
    for kwargs in (
        {"kind": "positions_history", "begin_ms": BEGIN, "end_ms": END},
        {"kind": "fills_history", "begin_ms": BEGIN, "end_ms": END},
        {
            "kind": "bills_archive",
            "begin_ms": BEGIN,
            "end_ms": END,
            "inst_type": "SWAP",
        },
        {
            "kind": "orders_recent",
            "begin_ms": BEGIN,
            "end_ms": END,
            "inst_type": "SWAP",
            "after": "0",
        },
        {
            "kind": "algo_history",
            "ord_type": "oco",
            "state": "effective",
            "begin_ms": BEGIN,
        },
    ):
        with pytest.raises(LiveHistoryPageError):
            select_live_account_history_page_v1(**kwargs)


@pytest.mark.asyncio
async def test_history_page_signed_raw_readback_and_private_archive() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _raw_response(
            b'{"code":"0","data":[{"billId":"103","instType":"SWAP"},'
            b'{"billId":"101","instType":"SWAP"}]}'
        )

    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease:
            selector = select_live_account_history_page_v1(
                "fills_history",
                begin_ms=BEGIN,
                end_ms=END,
                inst_type="SWAP",
                after="105",
            )
            page = await observe_live_account_history_page_v1(owner, lease, selector)
            assert page.row_ids == ("103", "101")
            assert page.row_count == 2
            assert page.empty_page is False
            assert page.account_complete is False
            assert page.history_complete is False
            assert page.source_authenticity_verified is False
            assert page.execution_authority is False
            assert (
                verify_live_account_history_page_readback_v1(page, lease=lease)[
                    "receipt_sha256"
                ]
                == page.receipt_sha256
            )
            meta = live_account_history_page_receipt_document_v1(page)
            assert "105" not in str(meta) and "103" not in str(meta)
            replay = reconstruct_live_account_history_page_v1(
                page.raw_body, page.raw_headers, meta, selector
            )
            assert replay.receipt_sha256 == page.receipt_sha256
            with pytest.raises(LiveHistoryPageError):
                reconstruct_live_account_history_page_v1(
                    page.raw_body + b" ", page.raw_headers, meta, selector
                )
            with pytest.raises(LiveHistoryPageError):
                verify_live_account_history_page_readback_v1(
                    replace(page, raw_body=b'{"code":"0","data":[]}'), lease=lease
                )
    assert len(seen) == 1
    assert str(seen[0].url) == (
        "https://openapi.okx.com/api/v5/trade/fills-history"
        "?after=105&begin=1700000000000&end=1700003600000&instType=SWAP&limit=100"
    )
    assert "x-simulated-trading" not in seen[0].headers


@pytest.mark.asyncio
async def test_history_route_has_512k_bound_without_widening_config() -> None:
    body = b'{"code":"0","data":[{"billId":"100","memo":"' + b"x" * 70000 + b'"}]}'
    async with _http(lambda _: _raw_response(body)) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease:
            page = await observe_live_account_history_page_v1(
                owner,
                lease,
                select_live_account_history_page_v1(
                    "bills_archive", begin_ms=BEGIN, end_ms=END
                ),
            )
            assert len(page.raw_body) > 65536
            assert page.row_ids == ("100",)


@pytest.mark.parametrize(
    "raw",
    [
        (
            b'{"code":"0","data":[{"billId":"100","instType":"SWAP"},'
            b'{"billId":"101","instType":"SWAP"}]}'
        ),
        b'{"code":"0","data":[{"billId":"105","instType":"SWAP"}]}',
        b'{"code":"0","data":[{"billId":"100","instType":"SPOT"}]}',
    ],
)
def test_history_rows_fail_closed_on_order_cursor_or_product(raw: bytes) -> None:
    selector = select_live_account_history_page_v1(
        "fills_recent", begin_ms=BEGIN, end_ms=END, inst_type="SWAP", after="105"
    )
    with pytest.raises(LiveHistoryPageError):
        checked_live_account_history_rows_v1(raw, selector)
