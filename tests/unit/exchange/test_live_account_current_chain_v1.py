from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.domain.source_primitives import MAX_CLOCK_BATCH_NS, ClockStamp, sha
from app.exchange.okx import live_account_current_chain_v1 as chain
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from tests.unit.exchange.test_live_account_config_custody_v1 import (
    _private_root_path,
)
from tests.unit.exchange.test_live_account_config_observation_v1 import (
    _http,
    _raw_response,
    _settings,
)

ATTEMPT = "a" * 32
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
_BALANCE_UPDATE_MS = "1785000000000"


@pytest.fixture
def private_root(tmp_path):
    return _private_root_path(tmp_path)


def _response(rows):
    return _raw_response(json.dumps({"code": "0", "data": rows}).encode())


class CurrentAccount:
    def __init__(
        self,
        *,
        orders=False,
        repeat=False,
        config_drift=False,
        bad_position=False,
        missing_leverage=False,
        balance_details=(),
    ):
        self.orders = orders
        self.repeat = repeat
        self.config_drift = config_drift
        self.bad_position = bad_position
        self.missing_leverage = missing_leverage
        self.balance_details = balance_details
        self.configs = 0
        self.requests = []

    def __call__(self, request):
        assert request.method == "GET"
        assert request.url.host == "openapi.okx.com"
        assert "x-simulated-trading" not in request.headers
        path, query = request.url.path, dict(request.url.params)
        self.requests.append((path, query))
        match path:
            case "/api/v5/account/config":
                self.configs += 1
                mode = (
                    "long_short_mode"
                    if self.config_drift and self.configs == 2
                    else "net_mode"
                )
                return _response(
                    [
                        {
                            "uid": "1234567",
                            "mainUid": "1234568",
                            "acctLv": "2",
                            "posMode": mode,
                            "perm": "read_only,trade",
                            "ip": "192.0.2.1",
                        }
                    ]
                )
            case "/api/v5/account/account-position-risk":
                return _response([{"balData": [], "posData": []}])
            case "/api/v5/account/balance":
                return _response(
                    [{"totalEq": "10.20", "details": self.balance_details}]
                )
            case "/api/v5/account/positions":
                return _response(
                    [{"instId": SYMBOLS[0], "posId": "1", "pos": ""}]
                    if self.bad_position
                    else []
                )
            case "/api/v5/trade/orders-pending":
                assert query["limit"] == "100"
                return _response(
                    [{"ordId": "10"}, {"ordId": "8"}]
                    if self.orders and ("after" not in query or self.repeat)
                    else []
                )
            case "/api/v5/trade/orders-algo-pending":
                assert query["limit"] == "100"
                return _response([])
            case "/api/v5/account/instruments":
                instrument = query["instId"]
                assert query == {"instType": "SWAP", "instId": instrument}
                assert instrument in SYMBOLS
                return _response(
                    [
                        {
                            "instType": "SWAP",
                            "instId": instrument,
                            "settleCcy": "USDT",
                            "ctType": "linear",
                            "ctValCcy": instrument.split("-")[0],
                            "state": "live",
                            "ctVal": "0.01",
                            "ctMult": "1",
                            "tickSz": "0.1",
                            "lotSz": "1",
                            "minSz": "1",
                            "lever": "20",
                        }
                    ]
                )
            case "/api/v5/account/leverage-info":
                assert query["instId"] == ",".join(SYMBOLS)
                return _response(
                    [
                        {
                            "instId": value,
                            "posSide": "net",
                            "mgnMode": query["mgnMode"],
                            "lever": "1",
                        }
                        for value in (SYMBOLS[:1] if self.missing_leverage else SYMBOLS)
                    ]
                )
            case _:
                pytest.fail("unexpected GET")


async def _collect(handler, private_root):
    async with _http(handler) as http:
        client = OkxLivePinnedGetClientV1(
            http,
            settings=_settings(okx_live_allowed_symbols=",".join(reversed(SYMBOLS))),
        )
        with client.pinned_get_lease() as lease:
            return await chain.collect_live_current_account_v1(
                client, lease, private_root=private_root, attempt_id=ATTEMPT
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("orders", [False, True])
async def test_live_current_account_retains_and_replays_every_current_page(
    private_root, orders
):
    handler = CurrentAccount(orders=orders)
    receipt = await _collect(handler, private_root)
    assert receipt["page_count"] == 16 + int(orders)
    assert receipt["no_open_positions_or_pending_orders_observed"] is (not orders)
    assert receipt["synthetic_mock"] is True
    assert receipt["account_complete"] is False
    assert receipt["source_authenticity_verified"] is False
    assert receipt["execution_authority"] is False
    assert len(handler.requests) == receipt["page_count"] + 2
    assert handler.requests[0][0] == handler.requests[-1][0] == "/api/v5/account/config"
    pending = [
        query for path, query in handler.requests if path.endswith("/orders-pending")
    ]
    assert pending == (
        [{"limit": "100"}, {"limit": "100", "after": "8"}]
        if orders
        else [{"limit": "100"}]
    )
    assert [
        query["ordType"]
        for path, query in handler.requests
        if path.endswith("/orders-algo-pending")
    ] == [name.removeprefix("algo_") for name in chain._ALGO_STREAMS]
    assert [
        query["instId"]
        for path, query in handler.requests
        if path.endswith("/account/instruments")
    ] == list(SYMBOLS)
    assert (
        chain.read_live_current_account_v1(
            private_root,
            attempt_id=ATTEMPT,
            expected_custody_sha256=receipt["custody_sha256"],
        )
        == receipt
    )
    assert "1234567" not in str(receipt) and "192.0.2.1" not in str(receipt)
    balance = private_root / ATTEMPT / "p-000001" / "body.raw"
    balance.write_bytes(balance.read_bytes().replace(b"10.20", b"20.10"))
    with pytest.raises(chain.LiveCurrentAccountChainError):
        chain.read_live_current_account_with_balance_v1(
            private_root,
            attempt_id=ATTEMPT,
            expected_custody_sha256=receipt["custody_sha256"],
        )


@pytest.mark.asyncio
async def test_retained_usdt_equity_is_exact_and_bound_to_the_balance_page(
    private_root,
):
    currency = {
        "ccy": "USDT",
        "availEq": "0.000",
        "availBal": "19",
        "uTime": _BALANCE_UPDATE_MS,
    }
    receipt = await _collect(
        CurrentAccount(balance_details=[{"ccy": "USDC"}, currency]), private_root
    )
    readback = chain.read_live_current_account_with_balance_v1(
        private_root,
        attempt_id=ATTEMPT,
        expected_custody_sha256=receipt["custody_sha256"],
    )
    assert readback.receipt == receipt
    equity = readback.usdt_available_equity
    assert equity is not None
    assert equity.available_equity.as_tuple() == Decimal("0.000").as_tuple()
    assert equity.exchange_update_ms == int(_BALANCE_UPDATE_MS)
    balance_page = private_root / ATTEMPT / "p-000001"
    metadata = (balance_page / "observation.json").read_bytes()
    assert equity.raw_body_sha256 == sha((balance_page / "body.raw").read_bytes())
    assert equity.page_metadata_sha256 == sha(metadata)
    assert (
        equity.body_completed.model_dump()
        == json.loads(metadata)["source"]["stamps"][3]
    )


@pytest.mark.parametrize(
    ("details", "balance_fields", "expected"),
    [
        ([], {}, None),
        ([{"ccy": "USDC", "availEq": "1", "uTime": _BALANCE_UPDATE_MS}], {}, None),
        ([{"ccy": "USDT", "availBal": "1", "uTime": _BALANCE_UPDATE_MS}], {}, None),
        ([{"ccy": "USDT", "availEq": 1, "uTime": _BALANCE_UPDATE_MS}], {}, None),
        ([{"ccy": "USDT", "availEq": "1"}], {}, None),
        ([{"ccy": "USDT", "availEq": "1", "uTime": "1000"}], {}, None),
        ([{"ccy": "USDT", "availEq": "1", "uTime": "1785000000001"}], {}, None),
        (
            [
                {"ccy": "USDT", "availEq": "1", "uTime": _BALANCE_UPDATE_MS},
                {"ccy": "USDT"},
            ],
            {},
            None,
        ),
        (
            [
                {"ccy": "USDT", "availEq": "1", "uTime": _BALANCE_UPDATE_MS},
                {"ccy": " usdt"},
            ],
            {},
            None,
        ),
        (
            [{"ccy": "USDT", "availEq": "1", "uTime": _BALANCE_UPDATE_MS}],
            {"uTime": "1785000000001"},
            None,
        ),
        (
            [{"ccy": "USDT", "availEq": "-0.01", "uTime": _BALANCE_UPDATE_MS}],
            {"uTime": _BALANCE_UPDATE_MS},
            Decimal("-0.01"),
        ),
    ],
)
def test_retained_balance_projection_validates_currency_and_timestamps(
    details, balance_fields, expected
):
    from types import SimpleNamespace

    raw = json.dumps(
        {
            "code": "0",
            "data": [{"totalEq": "10.2", "details": details, **balance_fields}],
        }
    ).encode()
    page = SimpleNamespace(
        raw_body=raw,
        raw_sha256=sha(raw),
        stamps=(ClockStamp(utc_ns=1_785_000_000_000_000_000, monotonic_ns=1),) * 6,
    )
    equity = chain._usdt_available_equity(page, sha(b"synthetic metadata"))
    assert (equity.available_equity if equity is not None else None) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition",
    [
        {"orders": True, "repeat": True},
        {"config_drift": True},
        {"bad_position": True},
        {"missing_leverage": True},
    ],
)
async def test_live_current_account_partial_attempt_is_retained_and_cannot_resume(
    private_root, condition
):
    handler = CurrentAccount(**condition)
    with pytest.raises(chain.LiveCurrentAccountChainError):
        await _collect(handler, private_root)
    attempt = private_root / ATTEMPT
    assert (attempt / "p-000000" / "body.raw").is_file()
    assert not (attempt / "custody.json").exists()
    count = len(handler.requests)
    with pytest.raises(chain.LiveCurrentAccountChainError):
        await _collect(handler, private_root)
    assert len(handler.requests) == count


@pytest.mark.asyncio
async def test_live_current_account_expired_batch_cannot_start_another_get(
    private_root, monkeypatch
):
    handler = CurrentAccount()
    stamp = chain._stamp

    def expired():
        sample = stamp()
        return ClockStamp(
            utc_ns=sample.utc_ns + MAX_CLOCK_BATCH_NS + 1,
            monotonic_ns=sample.monotonic_ns + MAX_CLOCK_BATCH_NS + 1,
        )

    monkeypatch.setattr(chain, "_stamp", expired)
    with pytest.raises(chain.LiveCurrentAccountChainError):
        await _collect(handler, private_root)
    assert [path for path, _ in handler.requests] == ["/api/v5/account/config"]
    assert not (private_root / ATTEMPT / "custody.json").exists()
