import asyncio
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.config.settings import get_settings
from app.domain.market import Candle, InstrumentInfo, OrderBook, SwapTickerV2, Ticker
from app.exchange.okx.errors import OkxPublicApiError
from app.exchange.okx.parsers import (
    parse_candle,
    parse_instrument,
    parse_order_book,
    parse_ticker,
)

settings = get_settings()

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,39})(?:\.[0-9]{1,20})?")
_MILLISECONDS = re.compile(r"[1-9][0-9]{12}")
_COUNT = re.compile(r"[1-9][0-9]{0,18}")
_SWAP_INSTRUMENT = re.compile(r"[A-Z0-9]+-[A-Z0-9]+-SWAP")


class _MalformedPublicResponse(OkxPublicApiError):
    """A successful HTTP response with invalid shape cannot be trusted by retrying."""


def _require_swap_instrument(
    instrument_id: str, *, message: str = "SWAP instrument required"
) -> None:
    if (
        type(instrument_id) is not str
        or _SWAP_INSTRUMENT.fullmatch(instrument_id) is None
    ):
        raise OkxPublicApiError(message)


def _single_row(
    rows: list[Any],
    instrument_id: str,
    endpoint: str,
    *,
    identity_required: bool = True,
) -> dict[str, Any]:
    if len(rows) != 1 or type(rows[0]) is not dict:
        raise OkxPublicApiError(f"{endpoint} returned non-singleton data")
    row = rows[0]
    # REST books do not include instId; their identity is bound by the request.
    if (identity_required or "instId" in row) and row.get("instId") != instrument_id:
        raise OkxPublicApiError(f"{endpoint} instrument mismatch")
    if (identity_required or "instType" in row) and row.get("instType") != "SWAP":
        raise OkxPublicApiError(f"{endpoint} instrument type mismatch")
    return row


def _required_decimal(
    row: dict[str, Any],
    field: str,
    endpoint: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    raw = row.get(field)
    if type(raw) is not str or _DECIMAL.fullmatch(raw) is None:
        raise OkxPublicApiError(f"{endpoint} invalid {field}")
    value = Decimal(raw)
    if (
        not value.is_finite()
        or (positive and value <= 0)
        or (nonnegative and value < 0)
    ):
        raise OkxPublicApiError(f"{endpoint} invalid {field}")
    return value


def _required_time(row: dict[str, Any], field: str, endpoint: str) -> datetime:
    raw = row.get(field)
    if type(raw) is not str or _MILLISECONDS.fullmatch(raw) is None:
        raise OkxPublicApiError(f"{endpoint} invalid {field}")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(raw))
    except (OverflowError, ValueError) as exc:
        raise OkxPublicApiError(f"{endpoint} invalid {field}") from exc


def _validate_book_levels(row: dict[str, Any], side: str, size: int) -> None:
    levels = row.get(side)
    if type(levels) is not list or not 1 <= len(levels) <= size:
        raise OkxPublicApiError(f"order book invalid {side}")
    for level in levels:
        if type(level) is not list or len(level) != 4:
            raise OkxPublicApiError(f"order book invalid {side} level")
        # A zero-size REST snapshot level is not executable depth.
        _required_decimal({"price": level[0]}, "price", "order book", positive=True)
        _required_decimal({"size": level[1]}, "size", "order book", positive=True)
        if (
            level[2] != "0"
            or type(level[3]) is not str
            or _COUNT.fullmatch(level[3]) is None
            or int(level[3]) > 2**63 - 1
        ):
            raise OkxPublicApiError(f"order book invalid {side} level")


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
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise _MalformedPublicResponse(
                            "OKX public API returned invalid JSON"
                        ) from exc
                    if type(payload) is not dict:
                        raise _MalformedPublicResponse(
                            "OKX public API returned non-object body"
                        )
                    code = payload.get("code")
                    if type(code) is not str:
                        raise _MalformedPublicResponse(
                            "OKX public API returned invalid code"
                        )
                    if code != "0":
                        raise OkxPublicApiError(
                            payload.get("msg") or "OKX public API rejected request",
                            code=code,
                        )
                    data = payload.get("data")
                    if not isinstance(data, list):
                        raise _MalformedPublicResponse(
                            "OKX public API returned non-list data"
                        )
                    return data
                except _MalformedPublicResponse:
                    raise
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

    async def ticker(self, instrument_id: str) -> Ticker | SwapTickerV2:
        rows = await self._request("/api/v5/market/ticker", {"instId": instrument_id})
        if len(rows) != 1:
            raise OkxPublicApiError("ticker returned non-singleton data")
        ticker = parse_ticker(rows[0])
        if ticker.instrument_id != instrument_id:
            raise OkxPublicApiError("ticker instrument mismatch")
        return ticker

    async def candles(self, instrument_id: str, bar: str, limit: int) -> list[Candle]:
        _require_swap_instrument(
            instrument_id, message="SWAP candle instrument required"
        )
        rows = await self._request(
            "/api/v5/market/candles",
            {"instId": instrument_id, "bar": bar, "limit": str(limit)},
        )
        candles = [parse_candle(row) for row in rows]
        return sorted(candles, key=lambda candle: candle.timestamp)

    async def order_book(self, instrument_id: str, size: int = 5) -> OrderBook:
        _require_swap_instrument(instrument_id)
        if type(size) is not int or not 1 <= size <= 400:
            raise OkxPublicApiError("order book invalid requested depth")
        rows = await self._request(
            "/api/v5/market/books",
            {"instId": instrument_id, "sz": str(size)},
        )
        row = _single_row(rows, instrument_id, "order book", identity_required=False)
        timestamp = _required_time(row, "ts", "order book")
        _validate_book_levels(row, "bids", size)
        _validate_book_levels(row, "asks", size)
        book = parse_order_book(instrument_id, row)
        return book.model_copy(update={"timestamp": timestamp})

    async def funding_rate(self, instrument_id: str) -> tuple[Any, Any]:
        _require_swap_instrument(instrument_id)
        rows = await self._request(
            "/api/v5/public/funding-rate",
            {"instId": instrument_id},
        )
        row = _single_row(rows, instrument_id, "funding rate")
        _required_time(row, "ts", "funding rate")
        funding_time = _required_time(row, "fundingTime", "funding rate")
        next_time = _required_time(row, "nextFundingTime", "funding rate")
        if next_time <= funding_time:
            raise OkxPublicApiError("funding rate invalid settlement chronology")
        return _required_decimal(row, "fundingRate", "funding rate"), funding_time

    async def open_interest(self, instrument_id: str) -> tuple[Any, Any]:
        _require_swap_instrument(instrument_id)
        rows = await self._request(
            "/api/v5/public/open-interest",
            {"instType": "SWAP", "instId": instrument_id},
        )
        row = _single_row(rows, instrument_id, "open interest")
        _required_time(row, "ts", "open interest")
        return (
            _required_decimal(row, "oi", "open interest", nonnegative=True),
            _required_decimal(row, "oiCcy", "open interest", nonnegative=True),
        )

    async def mark_price(self, instrument_id: str):
        _require_swap_instrument(instrument_id)
        rows = await self._request(
            "/api/v5/public/mark-price",
            {"instType": "SWAP", "instId": instrument_id},
        )
        row = _single_row(rows, instrument_id, "mark price")
        _required_time(row, "ts", "mark price")
        return _required_decimal(row, "markPx", "mark price", positive=True)
