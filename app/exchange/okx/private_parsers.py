from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.okx_demo import (
    OkxDemoAccountConfig,
    OkxDemoAlgoOrderView,
    OkxDemoBalanceDetail,
    OkxDemoBalanceSnapshot,
    OkxDemoOrderView,
    OkxDemoPositionView,
)


def decimal_required(value: Any) -> Decimal:
    """Preserve a genuine source zero; never turn an absent value into zero."""
    if value is None or type(value) is bool or str(value).strip() == "":
        raise ValueError("okx_required_decimal_missing")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("okx_required_decimal_invalid") from None
    if not number.is_finite():
        raise ValueError("okx_required_decimal_invalid")
    return number


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if type(value) is bool:
        raise ValueError("okx_optional_decimal_invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("okx_optional_decimal_invalid") from None
    if not number.is_finite():
        raise ValueError("okx_optional_decimal_invalid")
    return number


def bool_value(value: Any) -> bool:
    """A missing or unrecognized reduction flag is unknown, never false."""
    if type(value) is bool:
        return value
    if type(value) is str and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError("okx_required_boolean_missing_or_invalid")


def datetime_from_ms(value: Any) -> datetime | None:
    if type(value) is bool:
        raise ValueError("okx_timestamp_invalid")
    if value in (None, "", "0", 0):
        return None
    if not str(value).isdigit():
        raise ValueError("okx_timestamp_invalid")
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise ValueError("okx_timestamp_invalid") from None


def parse_account_config(row: dict[str, Any]) -> OkxDemoAccountConfig:
    if type(row) is not dict:
        raise ValueError("okx_account_config_row_invalid")
    position_mode = row.get("posMode")
    if type(position_mode) is not str or position_mode not in {
        "net_mode",
        "long_short_mode",
    }:
        raise ValueError("okx_account_position_mode_invalid")
    return OkxDemoAccountConfig(
        uid=_required_text(row.get("uid")),
        account_level=_required_text(row.get("acctLv")),
        position_mode=position_mode,
        account_stp_mode=row.get("acctStpMode") or None,
        raw=dict(row),
    )


def parse_balance(row: dict[str, Any]) -> OkxDemoBalanceSnapshot:
    if type(row) is not dict:
        raise ValueError("okx_balance_row_invalid")
    source_time = datetime_from_ms(row.get("uTime"))
    if source_time is None:
        raise ValueError("okx_balance_update_time_missing")
    raw_details = row.get("details")
    if type(raw_details) is not list or not raw_details:
        raise ValueError("okx_balance_details_missing")
    details: list[OkxDemoBalanceDetail] = []
    for item in raw_details:
        if type(item) is not dict:
            raise ValueError("okx_balance_detail_invalid")
        details.append(
            OkxDemoBalanceDetail(
                currency=_required_text(item.get("ccy")),
                equity=decimal_required(item.get("eq")),
                equity_usd=decimal_or_none(item.get("eqUsd")),
                available_equity=decimal_required(item.get("availEq")),
                cash_balance=decimal_required(item.get("cashBal")),
                available_balance=decimal_required(item.get("availBal")),
                frozen_balance=decimal_required(item.get("frozenBal")),
                unrealized_pnl=decimal_required(item.get("upl")),
            )
        )
    return OkxDemoBalanceSnapshot(
        total_equity=decimal_required(row.get("totalEq")),
        isolated_equity=decimal_required(row.get("isoEq")),
        adjusted_equity=decimal_required(row.get("adjEq")),
        available_equity=decimal_required(row.get("availEq")),
        details=details,
        captured_at=source_time,
        raw=dict(row),
    )


def parse_position(row: dict[str, Any]) -> OkxDemoPositionView:
    if type(row) is not dict:
        raise ValueError("okx_position_row_invalid")
    instrument_id = _required_text(row.get("instId"))
    position_side = _required_text(row.get("posSide"))
    margin_mode = _required_text(row.get("mgnMode"))
    if position_side not in {"net", "long", "short"} or margin_mode not in {
        "cross",
        "isolated",
    }:
        raise ValueError("okx_position_identity_invalid")
    return OkxDemoPositionView(
        instrument_id=instrument_id,
        position_side=position_side,
        size=decimal_required(row.get("pos")),
        available_size=decimal_required(row.get("availPos")),
        average_price=decimal_or_none(row.get("avgPx")),
        mark_price=decimal_or_none(row.get("markPx")),
        unrealized_pnl=decimal_required(row.get("upl")),
        leverage=decimal_or_none(row.get("lever")),
        margin_mode=margin_mode,
        liquidation_price=decimal_or_none(row.get("liqPx")),
        created_at=datetime_from_ms(row.get("cTime")),
        updated_at=datetime_from_ms(row.get("uTime")),
        raw=dict(row),
    )


def _required_text(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("okx_required_text_missing")
    return value


def parse_order(row: dict[str, Any]) -> OkxDemoOrderView:
    if type(row) is not dict:
        raise ValueError("okx_order_row_invalid")
    attached = row.get("attachAlgoOrds")
    if type(attached) is not list or any(type(item) is not dict for item in attached):
        raise ValueError("okx_order_attached_algo_evidence_missing_or_invalid")
    return OkxDemoOrderView(
        order_id=_required_text(row.get("ordId")),
        client_order_id=row.get("clOrdId") or None,
        instrument_id=_required_text(row.get("instId")),
        side=_required_text(row.get("side")),
        position_side=row.get("posSide") or None,
        order_type=_required_text(row.get("ordType")),
        state=_required_text(row.get("state")),
        size=decimal_required(row.get("sz")),
        accumulated_fill_size=decimal_required(row.get("accFillSz")),
        price=decimal_or_none(row.get("px")),
        average_fill_price=decimal_or_none(row.get("avgPx")),
        reduce_only=bool_value(row.get("reduceOnly")),
        created_at=datetime_from_ms(row.get("cTime")),
        updated_at=datetime_from_ms(row.get("uTime")),
        attached_algo_orders=[dict(item) for item in attached],
        raw=dict(row),
    )


def parse_algo_order(row: dict[str, Any]) -> OkxDemoAlgoOrderView:
    if type(row) is not dict:
        raise ValueError("okx_algo_order_row_invalid")
    return OkxDemoAlgoOrderView(
        algo_order_id=_required_text(row.get("algoId")),
        client_algo_order_id=row.get("algoClOrdId") or None,
        instrument_id=_required_text(row.get("instId")),
        order_type=_required_text(row.get("ordType")),
        state=_required_text(row.get("state")),
        side=row.get("side") or None,
        position_side=row.get("posSide") or None,
        size=decimal_required(row.get("sz")),
        take_profit_trigger_price=decimal_or_none(row.get("tpTriggerPx")),
        stop_loss_trigger_price=decimal_or_none(row.get("slTriggerPx")),
        created_at=datetime_from_ms(row.get("cTime")),
        updated_at=datetime_from_ms(row.get("uTime")),
        raw=dict(row),
    )
