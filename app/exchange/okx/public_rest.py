import asyncio
from typing import Any

import httpx

from app.config.settings import get_settings
from app.domain.market import Candle, InstrumentInfo, OrderBook, Ticker
from app.exchange.okx.errors import OkxPublicApiError
from app.exchange.okx.parsers import (
    decimal_value,
    parse_candle,
    parse_instrument,
    parse_order_book,
    parse_ticker,
    utc_from_ms,
)

settings = get_settings()


class OkxPublicRestClient:
    """Minimal, typed OKX public REST client with bounded retries.

    This client has no API key and cannot place orders.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._external_client = client

    async def _request(self, path: str, params: dict[str, Any]) -> list[Any]:
        own_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient(
            base_url=settings.okx_rest_base_url,
            timeout=httpx.Timeout(settings.okx_public_timeout_seconds),
            headers={"User-Agent": f"CTCC-V2/{settings.app_version}"},
        )
        try:
            last_error: Exception | None = None
            for attempt in range(settings.okx_public_max_retries + 1):
                try:
                    response = await client.get(path, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    if str(payload.get("code")) != "0":
                        raise OkxPublicApiError(
                            payload.get("msg") or "OKX public API rejected request",
                            code=str(payload.get("code")),
                        )
                    data = payload.get("data")
                    if not isinstance(data, list):
                        raise OkxPublicApiError("OKX public API returned non-list data")
                    return data
                except (httpx.HTTPError, ValueError, OkxPublicApiError) as exc:
                    last_error = exc
                    if attempt >= settings.okx_public_max_retries:
                        break
                    await asyncio.sleep(0.25 * (2**attempt))
            if isinstance(last_error, OkxPublicApiError):
                raise last_error
            raise OkxPublicApiError(f"OKX public API unavailable: {last_error}")
        finally:
            if own_client:
                await client.aclose()

    async def instruments(self, instrument_id: str) -> list[InstrumentInfo]:
        rows = await self._request(
            "/api/v5/public/instruments",
            {"instType": "SWAP", "instId": instrument_id},
        )
        return [parse_instrument(row) for row in rows]

    async def ticker(self, instrument_id: str) -> Ticker:
        rows = await self._request("/api/v5/market/ticker", {"instId": instrument_id})
        if not rows:
            raise OkxPublicApiError("ticker returned no data")
        return parse_ticker(rows[0])

    async def candles(self, instrument_id: str, bar: str, limit: int) -> list[Candle]:
        rows = await self._request(
            "/api/v5/market/candles",
            {"instId": instrument_id, "bar": bar, "limit": str(limit)},
        )
        candles = [parse_candle(row) for row in rows]
        return sorted(candles, key=lambda candle: candle.timestamp)

    async def order_book(self, instrument_id: str, size: int = 5) -> OrderBook:
        rows = await self._request(
            "/api/v5/market/books",
            {"instId": instrument_id, "sz": str(size)},
        )
        if not rows:
            raise OkxPublicApiError("order book returned no data")
        return parse_order_book(instrument_id, rows[0])

    async def funding_rate(self, instrument_id: str) -> tuple[Any, Any]:
        rows = await self._request(
            "/api/v5/public/funding-rate",
            {"instId": instrument_id},
        )
        if not rows:
            raise OkxPublicApiError("funding rate returned no data")
        row = rows[0]
        next_time = utc_from_ms(row["nextFundingTime"]) if row.get("nextFundingTime") else None
        return decimal_value(row.get("fundingRate")), next_time

    async def open_interest(self, instrument_id: str) -> tuple[Any, Any]:
        rows = await self._request(
            "/api/v5/public/open-interest",
            {"instType": "SWAP", "instId": instrument_id},
        )
        if not rows:
            raise OkxPublicApiError("open interest returned no data")
        row = rows[0]
        return decimal_value(row.get("oi")), decimal_value(row.get("oiCcy"))

    async def mark_price(self, instrument_id: str):
        rows = await self._request(
            "/api/v5/public/mark-price",
            {"instType": "SWAP", "instId": instrument_id},
        )
        if not rows:
            raise OkxPublicApiError("mark price returned no data")
        return decimal_value(rows[0].get("markPx"))
