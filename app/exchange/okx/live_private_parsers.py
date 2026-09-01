from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.okx_live import (
    OkxLiveAccountConfig,
    OkxLiveAlgoOrderView,
    OkxLiveApiKeyCapability,
    OkxLiveBalanceDetail,
    OkxLiveBalanceSnapshot,
    OkxLiveOrderView,
    OkxLivePositionView,
)


KNOWN_API_KEY_PERMISSIONS = frozenset({"read_only", "trade", "withdraw"})


def _decimal_or_zero(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def _bool_or_none(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    return _bool_value(value)


def _datetime_from_ms(value: Any) -> datetime | None:
    if value in (None, "", "0", 0):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _permission_tokens(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    return sorted(
        {
            item.strip().lower()
            for item in str(value).split(",")
            if item.strip()
        }
    )


def parse_live_account_config(row: dict[str, Any]) -> OkxLiveAccountConfig:
    permissions = _permission_tokens(row.get("perm"))
    permission_set = set(permissions)
    uid = row.get("uid") or None
    main_uid = row.get("mainUid") or None
    is_sub_account = None if uid is None or main_uid is None else uid != main_uid

    return OkxLiveAccountConfig(
        uid=uid,
        main_uid=main_uid,
        is_sub_account=is_sub_account,
        account_level=row.get("acctLv") or None,
        position_mode=str(row.get("posMode") or ""),
        account_stp_mode=row.get("acctStpMode") or None,
        account_type=row.get("type") or None,
        capability=OkxLiveApiKeyCapability(
            permissions=permissions,
            unknown_permissions=sorted(permission_set - KNOWN_API_KEY_PERMISSIONS),
            read_permission="read_only" in permission_set,
            trade_permission="trade" in permission_set,
            withdraw_permission="withdraw" in permission_set,
            ip_bound=bool(str(row.get("ip") or "").strip()),
        ),
    )


def parse_live_balance(row: dict[str, Any]) -> OkxLiveBalanceSnapshot:
    details: list[OkxLiveBalanceDetail] = []
    for item in row.get("details", []) or []:
        details.append(
            OkxLiveBalanceDetail(
                currency=str(item.get("ccy") or ""),
                equity=_decimal_or_zero(item.get("eq")),
                cash_balance=_decimal_or_zero(item.get("cashBal")),
                available_balance=_decimal_or_zero(item.get("availBal")),
                frozen_balance=_decimal_or_zero(item.get("frozenBal")),
                unrealized_pnl=_decimal_or_zero(item.get("upl")),
            )
        )
    captured_at = _datetime_from_ms(row.get("uTime")) or datetime.now(timezone.utc)
    return OkxLiveBalanceSnapshot(
        total_equity=_decimal_or_zero(row.get("totalEq")),
        isolated_equity=_decimal_or_zero(row.get("isoEq")),
        adjusted_equity=_decimal_or_zero(row.get("adjEq")),
        available_equity=_decimal_or_zero(row.get("availEq")),
        details=details,
        captured_at=captured_at,
        raw=dict(row),
    )


def parse_live_position(row: dict[str, Any]) -> OkxLivePositionView:
    return OkxLivePositionView(
        position_id=str(row.get("posId") or ""),
        instrument_id=str(row.get("instId") or ""),
        position_side=str(row.get("posSide") or ""),
        size=_decimal_or_zero(row.get("pos")),
        available_size=_decimal_or_zero(row.get("availPos")),
        average_price=_decimal_or_none(row.get("avgPx")),
        mark_price=_decimal_or_none(row.get("markPx")),
        unrealized_pnl=_decimal_or_zero(row.get("upl")),
        leverage=_decimal_or_none(row.get("lever")),
        margin_mode=row.get("mgnMode") or None,
        liquidation_price=_decimal_or_none(row.get("liqPx")),
        created_at=_datetime_from_ms(row.get("cTime")),
        updated_at=_datetime_from_ms(row.get("uTime")),
        raw=dict(row),
    )


def parse_live_order(row: dict[str, Any]) -> OkxLiveOrderView:
    return OkxLiveOrderView(
        order_id=str(row.get("ordId") or ""),
        client_order_id=row.get("clOrdId") or None,
        instrument_id=str(row.get("instId") or ""),
        side=str(row.get("side") or ""),
        position_side=row.get("posSide") or None,
        order_type=str(row.get("ordType") or ""),
        state=str(row.get("state") or ""),
        size=_decimal_or_zero(row.get("sz")),
        accumulated_fill_size=_decimal_or_zero(row.get("accFillSz")),
        price=_decimal_or_none(row.get("px")),
        average_fill_price=_decimal_or_none(row.get("avgPx")),
        reduce_only=_bool_value(row.get("reduceOnly")),
        created_at=_datetime_from_ms(row.get("cTime")),
        updated_at=_datetime_from_ms(row.get("uTime")),
        attached_algo_orders=list(row.get("attachAlgoOrds") or []),
        raw=dict(row),
    )


def parse_live_algo_order(row: dict[str, Any]) -> OkxLiveAlgoOrderView:
    return OkxLiveAlgoOrderView(
        algo_order_id=str(row.get("algoId") or ""),
        client_algo_order_id=row.get("algoClOrdId") or None,
        instrument_type=row.get("instType") or None,
        instrument_id=str(row.get("instId") or ""),
        order_type=str(row.get("ordType") or ""),
        state=str(row.get("state") or ""),
        side=row.get("side") or None,
        position_side=row.get("posSide") or None,
        margin_mode=row.get("tdMode") or None,
        reduce_only=_bool_or_none(row.get("reduceOnly")),
        close_fraction=_decimal_or_none(row.get("closeFraction")),
        size=_decimal_or_zero(row.get("sz")),
        actual_size=_decimal_or_zero(row.get("actualSz")),
        take_profit_trigger_price=_decimal_or_none(row.get("tpTriggerPx")),
        take_profit_trigger_price_type=row.get("tpTriggerPxType") or None,
        take_profit_order_price=_decimal_or_none(row.get("tpOrdPx")),
        stop_loss_trigger_price=_decimal_or_none(row.get("slTriggerPx")),
        stop_loss_trigger_price_type=row.get("slTriggerPxType") or None,
        stop_loss_order_price=_decimal_or_none(row.get("slOrdPx")),
        amend_price_on_trigger_type=row.get("amendPxOnTriggerType") or None,
        failure_code=row.get("failCode") or None,
        trigger_time=_datetime_from_ms(row.get("triggerTime")),
        created_at=_datetime_from_ms(row.get("cTime")),
        updated_at=_datetime_from_ms(row.get("uTime")),
        raw=dict(row),
    )
