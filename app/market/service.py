import asyncio
from datetime import datetime, timezone

from app.domain.market import MarketSnapshot
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.exchange.okx.symbols import to_canonical_symbol, to_instrument_id
from app.market.quality.candles import inspect_candles

SUPPORTED_BARS = ("4H", "1H", "15m", "5m")


class MarketDataService:
    def __init__(self, client: OkxPublicRestClient | None = None) -> None:
        self.client = client or OkxPublicRestClient()

    async def snapshot(self, symbol: str, candle_limit: int = 100) -> MarketSnapshot:
        instrument_id = to_instrument_id(symbol)

        candle_tasks = {
            bar: asyncio.create_task(self.client.candles(instrument_id, bar, candle_limit))
            for bar in SUPPORTED_BARS
        }
        ticker_task = asyncio.create_task(self.client.ticker(instrument_id))
        book_task = asyncio.create_task(self.client.order_book(instrument_id, 5))
        funding_task = asyncio.create_task(self.client.funding_rate(instrument_id))
        oi_task = asyncio.create_task(self.client.open_interest(instrument_id))
        mark_task = asyncio.create_task(self.client.mark_price(instrument_id))

        candles = {bar: await task for bar, task in candle_tasks.items()}
        ticker, order_book, funding, open_interest, mark_price = await asyncio.gather(
            ticker_task,
            book_task,
            funding_task,
            oi_task,
            mark_task,
        )
        funding_rate, next_funding_time = funding
        oi_contracts, oi_currency = open_interest
        quality = {bar: inspect_candles(series, bar) for bar, series in candles.items()}

        return MarketSnapshot(
            symbol=to_canonical_symbol(instrument_id),
            instrument_id=instrument_id,
            ticker=ticker,
            mark_price=mark_price,
            funding_rate=funding_rate,
            next_funding_time=next_funding_time,
            open_interest_contracts=oi_contracts,
            open_interest_currency=oi_currency,
            order_book=order_book,
            candles=candles,
            quality=quality,
            received_at=datetime.now(timezone.utc),
        )
