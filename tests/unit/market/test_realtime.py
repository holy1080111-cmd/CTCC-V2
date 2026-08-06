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
    await hub.apply({"channel": "tickers", "symbol": "ETH-USDT-SWAP", "last": Decimal("2000")})
    snapshot = await hub.apply({"channel": "mark-price", "symbol": "ETH-USDT-SWAP", "mark_price": Decimal("1999")})
    assert snapshot.last == Decimal("2000")
    assert snapshot.mark_price == Decimal("1999")
    assert snapshot.sequence == 2
