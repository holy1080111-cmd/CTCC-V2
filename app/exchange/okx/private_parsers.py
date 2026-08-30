from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoAlgoOrderView,
    OkxDemoBalanceDetail,
    OkxDemoBalanceSnapshot,
    OkxDemoOrderView,
    OkxDemoPositionView,
)


def decimal_or_zero(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def datetime_from_ms(value: Any) -> datetime | None:
    if value in (None, "", "0", 0):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def parse_account_config(row: dict[str, Any]) -> OkxDemoAccountConfig:
    return OkxDemoAccountConfig(
        uid=row.get("uid") or None,
        account_level=row.get("acctLv") or None,
        position_mode=row.get("posMode") or "net_mode",
        account_stp_mode=row.get("acctStpMode") or None,
        raw=dict(row),
    )


def parse_balance(row: dict[str, Any]) -> OkxDemoBalanceSnapshot:
    details: list[OkxDemoBalanceDetail] = []
    for item in row.get("details", []) or []:
        details.append(
            OkxDemoBalanceDetail(
                currency=str(item.get("ccy") or ""),
                equity=decimal_or_zero(item.get("eq")),
                equity_usd=decimal_or_none(item.get("eqUsd")),
                available_equity=decimal_or_zero(item.get("availEq")),
                cash_balance=decimal_or_zero(item.get("cashBal")),
                available_balance=decimal_or_zero(item.get("availBal")),
                frozen_balance=decimal_or_zero(item.get("frozenBal")),
                unrealized_pnl=decimal_or_zero(item.get("upl")),
            )
        )
    captured_at = datetime_from_ms(row.get("uTime")) or datetime.now(timezone.utc)
    return OkxDemoBalanceSnapshot(
        total_equity=decimal_or_zero(row.get("totalEq")),
        isolated_equity=decimal_or_zero(row.get("isoEq")),
        adjusted_equity=decimal_or_zero(row.get("adjEq")),
        available_equity=decimal_or_zero(row.get("availEq")),
        details=details,
        captured_at=captured_at,
        raw=dict(row),
    )


def parse_position(row: dict[str, Any]) -> OkxDemoPositionView:
    return OkxDemoPositionView(
        instrument_id=str(row.get("instId") or ""),
        position_side=str(row.get("posSide") or "net"),
        size=decimal_or_zero(row.get("pos")),
        available_size=decimal_or_zero(row.get("availPos")),
        average_price=decimal_or_none(row.get("avgPx")),
        mark_price=decimal_or_none(row.get("markPx")),
        unrealized_pnl=decimal_or_zero(row.get("upl")),
        leverage=decimal_or_none(row.get("lever")),
        margin_mode=row.get("mgnMode") or None,
        liquidation_price=decimal_or_none(row.get("liqPx")),
        created_at=datetime_from_ms(row.get("cTime")),
        updated_at=datetime_from_ms(row.get("uTime")),
        raw=dict(row),
    )


def parse_order(row: dict[str, Any]) -> OkxDemoOrderView:
    return OkxDemoOrderView(
        order_id=str(row.get("ordId") or ""),
        client_order_id=row.get("clOrdId") or None,
        instrument_id=str(row.get("instId") or ""),
        side=str(row.get("side") or ""),
        position_side=row.get("posSide") or None,
        order_type=str(row.get("ordType") or ""),
        state=str(row.get("state") or ""),
        size=decimal_or_zero(row.get("sz")),
        accumulated_fill_size=decimal_or_zero(row.get("accFillSz")),
        price=decimal_or_none(row.get("px")),
        average_fill_price=decimal_or_none(row.get("avgPx")),
        reduce_only=bool_value(row.get("reduceOnly")),
        created_at=datetime_from_ms(row.get("cTime")),
        updated_at=datetime_from_ms(row.get("uTime")),
        attached_algo_orders=list(row.get("attachAlgoOrds") or []),
        raw=dict(row),
    )


def parse_algo_order(row: dict[str, Any]) -> OkxDemoAlgoOrderView:
    return OkxDemoAlgoOrderView(
        algo_order_id=str(row.get("algoId") or ""),
        client_algo_order_id=row.get("algoClOrdId") or None,
        instrument_id=str(row.get("instId") or ""),
        order_type=str(row.get("ordType") or ""),
        state=str(row.get("state") or ""),
        side=row.get("side") or None,
        position_side=row.get("posSide") or None,
        size=decimal_or_zero(row.get("sz")),
        take_profit_trigger_price=decimal_or_none(row.get("tpTriggerPx")),
        stop_loss_trigger_price=decimal_or_none(row.get("slTriggerPx")),
        created_at=datetime_from_ms(row.get("cTime")),
        updated_at=datetime_from_ms(row.get("uTime")),
        raw=dict(row),
    )
