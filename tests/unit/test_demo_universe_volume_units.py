"""Exercise the Python probe embedded in the read-only PowerShell verifier."""

from __future__ import annotations

import ast
import asyncio
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.market import SwapTickerV2, Ticker
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "verify_demo_multi_symbol_universe.ps1"
)


def _inspect_symbol():
    match = re.search(r"(?ms)^\$probe = @'\r?\n(.*?)\r?\n'@", _SCRIPT.read_text())
    assert match is not None
    module = ast.parse(match.group(1), filename=str(_SCRIPT))
    selected = [
        node
        for node in module.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id
                in {
                    "CANDIDATES",
                    "MAX_SPREAD_BPS",
                    "MIN_ESTIMATED_NOTIONAL_24H_USDT",
                    "MAX_MINIMUM_ORDER_NOTIONAL_USDT",
                    "MIN_CONFIRMED_4H_CANDLES",
                    "MAX_CONFIRMED_4H_AGE_HOURS",
                }
                for target in node.targets
            )
        )
        or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"decimal_text", "inspect_symbol"}
    ]
    scope = {
        "asyncio": asyncio,
        "datetime": datetime,
        "timezone": SimpleNamespace(utc=UTC),
        "Decimal": Decimal,
        "REVIEWED_DEMO_INSTRUMENT_IDS": REVIEWED_DEMO_INSTRUMENT_IDS,
        "SwapTickerV2": SwapTickerV2,
    }
    tree = ast.fix_missing_locations(
        ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__", names=[ast.alias(name="annotations")], level=0
                ),
                *selected,
            ],
            type_ignores=[],
        )
    )
    # The test extracts only constants and these two functions from this repo's
    # checked-in script; it never executes the embedded main or any network IO.
    exec(compile(tree, str(_SCRIPT), "exec"), scope)  # noqa: S102
    return scope["inspect_symbol"]


def _ticker(*, legacy: bool = False):
    fields = {
        "instrument_id": "BTC-USDT-SWAP",
        "last": Decimal(50000),
        "bid": Decimal(49999),
        "ask": Decimal(50001),
        "bid_size": Decimal(2),
        "ask_size": Decimal(2),
        "open_24h": Decimal(49000),
        "high_24h": Decimal(51000),
        "low_24h": Decimal(48000),
        "timestamp": datetime.now(UTC),
    }
    if legacy:
        return Ticker(
            **fields,
            volume_24h=Decimal(50000),
            volume_quote_24h=Decimal(500),
        )
    return SwapTickerV2(
        **fields,
        schema_version="okx-swap-ticker-v2",
        volume_contracts_24h=Decimal(50000),
        volume_currency_24h=Decimal(500),
    )


class _OfflineClient:
    def __init__(self, ticker, *, settlement_currency="USDT", contract_currency="BTC"):
        self._ticker = ticker
        self._settlement_currency = settlement_currency
        self._contract_currency = contract_currency

    async def instruments(self, instrument_id):
        return [
            SimpleNamespace(
                instrument_id=instrument_id,
                instrument_type="SWAP",
                state="live",
                settlement_currency=self._settlement_currency,
                contract_currency=self._contract_currency,
                contract_value=Decimal("0.01"),
                minimum_size=Decimal("0.01"),
            )
        ]

    async def ticker(self, instrument_id):
        return self._ticker

    async def candles(self, instrument_id, bar, limit):
        now = datetime.now(UTC)
        return [
            SimpleNamespace(
                confirmed=True,
                timestamp=now - timedelta(hours=1 + 4 * (199 - index)),
            )
            for index in range(200)
        ]

    async def order_book(self, instrument_id, depth):
        return SimpleNamespace(bids=[object()], asks=[object()])


def test_swap_base_volume_produces_only_an_estimated_usdt_notional():
    result = asyncio.run(_inspect_symbol()(_OfflineClient(_ticker()), "BTC-USDT-SWAP"))
    assert result["qualified"] is True
    assert result["estimated_usdt_notional_24h"] == "25000000"
    assert "quote_notional_24h_usdt" not in result


@pytest.mark.parametrize(
    ("client", "blocker"),
    [
        (_OfflineClient(_ticker(legacy=True)), "swap_ticker_v2_required"),
        (
            _OfflineClient(_ticker(), settlement_currency="USD"),
            "settlement_currency_not_usdt",
        ),
        (
            _OfflineClient(_ticker(), contract_currency="USD"),
            "contract_value_currency_not_base",
        ),
    ],
)
def test_untrusted_swap_volume_identity_fails_closed(client, blocker):
    result = asyncio.run(_inspect_symbol()(client, "BTC-USDT-SWAP"))
    assert result["qualified"] is False
    assert blocker in result["blockers"]
    assert result["estimated_usdt_notional_24h"] is None
