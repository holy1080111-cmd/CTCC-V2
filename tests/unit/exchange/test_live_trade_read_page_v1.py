"""Declared mock HTTP Live DATA, never a venue/protection/submit acceptance."""

from dataclasses import replace

import pytest

from app.domain.source_primitives import canonical
from app.exchange.okx import live_trade_read_page_v1 as subject
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from tests.unit.exchange.test_live_account_config_observation_v1 import (
    _http,
    _raw_response,
    _settings,
)

INSTRUMENT = "BTC-USDT-SWAP"


def _select(kind="order", **kwargs):
    return subject.select_live_trade_read_v1(kind, instrument_id=INSTRUMENT, **kwargs)


def test_closed_selectors_never_mix_order_algo_and_bill_cursor_identities():
    assert _select(order_id="123").query == (("instId", INSTRUMENT), ("ordId", "123"))
    assert _select(client_order_id="CTCCLone").query == (
        ("clOrdId", "CTCCLone"),
        ("instId", INSTRUMENT),
    )
    assert _select("algo", algo_client_id="CTCCAone").query == (
        ("algoClOrdId", "CTCCAone"),
    )
    assert _select("fills", order_id="123", after_bill_id="456").query == (
        ("after", "456"),
        ("instId", INSTRUMENT),
        ("instType", "SWAP"),
        ("limit", "100"),
        ("ordId", "123"),
    )


@pytest.mark.parametrize(
    "kind,arguments",
    (
        ("order", {}),
        ("order", {"order_id": "123", "client_order_id": "CTCCLone"}),
        ("order", {"order_id": "123", "after_bill_id": "456"}),
        ("order", {"order_id": 123}),
        ("order", {"order_id": "0"}),
        ("order", {"client_order_id": "CTCCL_one"}),
        ("algo", {"algo_id": "123", "algo_client_id": "CTCCAone"}),
        ("algo", {"algo_client_id": "CTCCAone", "order_id": "456"}),
        ("fills", {"client_order_id": "CTCCLone"}),
        ("fills", {"order_id": "123", "after_bill_id": True}),
        ("order?evil=1", {"order_id": "123"}),
        ("positions", {"order_id": "123"}),
    ),
)
def test_unknown_or_conflicting_selector_is_refused_before_any_http(kind, arguments):
    with pytest.raises(subject.LiveTradeReadPageError):
        _select(kind, **arguments)


@pytest.mark.parametrize(
    "instrument",
    ("BTC-USDT", "BTC-USD-SWAP", "../BTC-USDT-SWAP", "BTC-USDT-SWAP?ordId=2", None),
)
def test_only_exact_linear_usdt_instrument_is_accepted(instrument):
    with pytest.raises(subject.LiveTradeReadPageError):
        subject.select_live_trade_read_v1(
            "order", instrument_id=instrument, order_id="123"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,arguments,row",
    (
        (
            "order",
            {"order_id": "123"},
            {
                "instId": INSTRUMENT,
                "ordId": "123",
                "clOrdId": "CTCCLone",
                "state": "filled",
                "accFillSz": "1",
            },
        ),
        (
            "order",
            {"client_order_id": "CTCCLone"},
            {
                "instId": INSTRUMENT,
                "ordId": "123",
                "clOrdId": "CTCCLone",
                "state": "canceled",
                "accFillSz": "0",
            },
        ),
        (
            "algo",
            {"algo_client_id": "CTCCAone"},
            {
                "instId": INSTRUMENT,
                "algoId": "456",
                "algoClOrdId": "CTCCAone",
                "state": "live",
            },
        ),
        (
            "fills",
            {"order_id": "123"},
            {"instId": INSTRUMENT, "ordId": "123", "billId": "789", "tradeId": "10"},
        ),
    ),
)
async def test_exact_request_retains_original_raw_and_never_claims_protection(
    kind, arguments, row
):
    seen = []
    body = canonical({"code": "0", "msg": "", "data": [row]})

    def handler(request):
        seen.append(request)
        return _raw_response(body)

    async with _http(handler) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        with client.pinned_get_lease() as lease:
            selector = _select(kind, **arguments)
            page = await subject.observe_live_trade_read_page_v1(
                client, lease, selector
            )
            assert page.raw_body == body and page.synthetic_mock is True
            assert (
                page.execution_authority
                is page.protection_verified
                is page.account_complete
                is False
            )
            result = subject.verify_live_trade_read_page_v1(page, lease=lease)
            assert result["raw_sha256"] == subject.sha(body)
            with pytest.raises(subject.LiveTradeReadPageError):
                subject.verify_live_trade_read_page_v1(
                    replace(page, raw_body=b'{"code":"0","msg":"","data":[]}'),
                    lease=lease,
                )
    assert len(seen) == 1 and seen[0].method == "GET"
    assert str(seen[0].url) == "https://openapi.okx.com" + subject._request_path(
        selector
    )
    assert "x-simulated-trading" not in seen[0].headers


@pytest.mark.asyncio
async def test_explicit_order_not_found_response_is_retained_error_data_not_zero_fill():
    body = canonical({"code": "51603", "msg": "Order does not exist", "data": []})
    async with _http(lambda _: _raw_response(body)) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        with client.pinned_get_lease() as lease:
            page = await subject.observe_live_trade_read_page_v1(
                client, lease, _select(client_order_id="CTCCLone")
            )
            assert page.raw_body == body and page.protection_verified is False
            assert (
                subject.verify_live_trade_read_page_v1(page, lease=lease)["identities"]
                == ()
            )


@pytest.mark.parametrize(
    "rows",
    (
        [
            {"billId": "5", "ordId": "123", "instId": INSTRUMENT},
            {"billId": "6", "ordId": "123", "instId": INSTRUMENT},
        ],
        [
            {"billId": "5", "ordId": "123", "instId": INSTRUMENT},
            {"billId": "5", "ordId": "123", "instId": INSTRUMENT},
        ],
        [{"billId": "5", "ordId": "456", "instId": INSTRUMENT}],
        [{"tradeId": "5", "ordId": "123", "instId": INSTRUMENT}],
        [{"billId": "7", "ordId": "123", "instId": INSTRUMENT}],
    ),
)
def test_fill_bill_cursor_order_duplicate_conflict_or_nonadvance_is_incomplete(rows):
    with pytest.raises(subject.LiveTradeReadPageError):
        subject._body(
            canonical({"code": "0", "msg": "", "data": rows}),
            _select("fills", order_id="123", after_bill_id="7"),
            private_tokens=(),
        )


def test_one_short_fill_page_never_contains_a_completeness_claim():
    raw = canonical(
        {
            "code": "0",
            "msg": "",
            "data": [
                {"billId": "5", "tradeId": "999", "ordId": "123", "instId": INSTRUMENT}
            ],
        }
    )
    _, identities = subject._body(
        raw, _select("fills", order_id="123"), private_tokens=()
    )
    assert identities == ("5",)
    assert "complete" not in subject.LiveTradeReadPageV1.__dataclass_fields__


def test_modified_path_and_query_are_not_authorized_by_a_selector_constructor():
    value = _select(order_id="123")
    for modified in (
        replace(value, path="/api/v5/trade/cancel-order"),
        replace(value, query=(("ordId", "456"),)),
        replace(value, kind="fills"),
    ):
        with pytest.raises(subject.LiveTradeReadPageError):
            subject._checked_selector(modified)
