from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.paper import PaperOrderRequest
from app.market.realtime import RealtimeMarketHub
from app.paper.engine import PaperBroker
from app.paper.execution_service import PaperExecutionService


@pytest.mark.asyncio
async def test_hub_updates_snapshot_and_paper_take_profit() -> None:
    paper_broker = PaperBroker()
    paper_service = PaperExecutionService(paper_broker)
    await paper_service.reset()
    await paper_service.submit(PaperOrderRequest(
        symbol="BTC-USDT-SWAP", side="long", quantity=Decimal("1"),
        reference_price=Decimal("100"), stop_loss=Decimal("95"), take_profit=Decimal("110"),
        order_type="market", risk_decision="approved", strategy="test", score=80,
    ))
    hub = RealtimeMarketHub(paper_auto_ticks=True, paper_execution=paper_service)
    snapshot = await hub.apply({"channel": "tickers", "symbol": "BTC-USDT-SWAP", "last": Decimal("110")})
    assert snapshot.last == Decimal("110")
    assert snapshot.sequence == 1
    assert paper_broker.account().closed_trades == 1


@pytest.mark.asyncio
async def test_hub_merges_channels() -> None:
    hub = RealtimeMarketHub(
        paper_auto_ticks=False,
        paper_execution=PaperExecutionService(PaperBroker()),
    )
    first = await hub.apply(
        {
            "channel": "tickers",
            "symbol": "ETH-USDT-SWAP",
            "last": Decimal("2000"),
            "bid": Decimal("1999.9"),
            "ask": Decimal("2000.1"),
        }
    )
    snapshot = await hub.apply({"channel": "mark-price", "symbol": "ETH-USDT-SWAP", "mark_price": Decimal("1999")})
    assert snapshot.last == Decimal("2000")
    assert snapshot.mark_price == Decimal("1999")
    assert snapshot.sequence == 2
    assert snapshot.last_received_at == first.last_received_at
    assert snapshot.quote_received_at == first.quote_received_at
    assert snapshot.mark_price_received_at is not None
    assert snapshot.mark_price_received_at >= first.received_at


@pytest.mark.asyncio
async def test_non_price_channel_cannot_refresh_stale_execution_fields() -> None:
    hub = RealtimeMarketHub(
        paper_auto_ticks=False,
        paper_execution=PaperExecutionService(PaperBroker()),
    )
    price = await hub.apply(
        {
            "channel": "tickers",
            "symbol": "BTC-USDT-SWAP",
            "last": Decimal("100"),
            "bid": Decimal("99.9"),
            "ask": Decimal("100.1"),
        }
    )
    refreshed = await hub.apply(
        {
            "channel": "funding-rate",
            "symbol": "BTC-USDT-SWAP",
            "funding_rate": Decimal("0.0001"),
        }
    )

    assert isinstance(price.last_received_at, datetime)
    assert price.last_received_at.tzinfo == timezone.utc
    assert refreshed.received_at >= price.received_at
    assert refreshed.last_received_at == price.last_received_at
    assert refreshed.quote_received_at == price.quote_received_at


@pytest.mark.asyncio
async def test_one_sided_quote_update_cannot_refresh_merged_quote() -> None:
    hub = RealtimeMarketHub(
        paper_auto_ticks=False,
        paper_execution=PaperExecutionService(PaperBroker()),
    )
    complete = await hub.apply(
        {
            "channel": "tickers",
            "symbol": "BTC-USDT-SWAP",
            "bid": Decimal("99.9"),
            "ask": Decimal("100.1"),
        }
    )
    partial = await hub.apply(
        {
            "channel": "tickers",
            "symbol": "BTC-USDT-SWAP",
            "bid": Decimal("100"),
        }
    )

    assert partial.bid == Decimal("100")
    assert partial.ask == Decimal("100.1")
    assert partial.quote_received_at == complete.quote_received_at
