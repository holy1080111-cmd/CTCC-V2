from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


class OkxWsParseError(ValueError):
    pass


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise OkxWsParseError(f"invalid decimal: {value!r}") from exc


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise OkxWsParseError(f"invalid timestamp: {value!r}") from exc


def parse_public_message(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one OKX public WS payload.

    Event acknowledgements return an empty list. Data messages return one
    normalized event per item in ``data``.
    """
    if payload.get("event"):
        if payload.get("event") == "error":
            raise OkxWsParseError(str(payload.get("msg") or payload))
        return []

    arg = payload.get("arg") or {}
    channel = str(arg.get("channel") or "")
    symbol = str(arg.get("instId") or "")
    data = payload.get("data")
    if not channel or not symbol or not isinstance(data, list):
        return []

    events: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        event: dict[str, Any] = {
            "channel": channel,
            "symbol": symbol,
            "exchange_timestamp": _timestamp(item.get("ts")),
        }
        if channel == "tickers":
            event.update(last=_decimal(item.get("last")), bid=_decimal(item.get("bidPx")), ask=_decimal(item.get("askPx")))
        elif channel == "mark-price":
            event.update(mark_price=_decimal(item.get("markPx")))
        elif channel == "funding-rate":
            event.update(funding_rate=_decimal(item.get("fundingRate")), next_funding_time=_timestamp(item.get("nextFundingTime")))
        elif channel == "open-interest":
            event.update(open_interest=_decimal(item.get("oi")), open_interest_currency=_decimal(item.get("oiCcy")))
        elif channel == "trades":
            event.update(last=_decimal(item.get("px")), last_trade_size=_decimal(item.get("sz")), last_trade_side=item.get("side"))
        elif channel in {"books5", "bbo-tbt"}:
            event.update(bids=_levels(item.get("bids")), asks=_levels(item.get("asks")))
        else:
            continue
        events.append(event)
    return events


def _levels(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for level in raw[:5]:
        if not isinstance(level, list) or len(level) < 2:
            continue
        result.append({
            "price": _decimal(level[0]),
            "size": _decimal(level[1]),
            "order_count": int(level[3]) if len(level) > 3 and str(level[3]).isdigit() else None,
        })
    return result
