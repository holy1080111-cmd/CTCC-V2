from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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


def _required_decimal(row: dict[str, Any], field: str) -> Decimal:
    value = row.get(field)
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError(f"okx_live_numeric_field_missing_or_invalid:{field}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"okx_live_numeric_field_missing_or_invalid:{field}") from exc
    if not number.is_finite():
        raise ValueError(f"okx_live_numeric_field_missing_or_invalid:{field}")
    return number


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TypeError("okx_live_optional_numeric_field_invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("okx_live_optional_numeric_field_invalid") from exc
    if not number.is_finite():
        raise ValueError("okx_live_optional_numeric_field_invalid")
    return number


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError("okx_live_boolean_field_missing_or_invalid")


def _bool_or_none(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    return _bool_value(value)


def _datetime_from_ms(value: Any) -> datetime | None:
    if value in (None, "", "0", 0):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _required_datetime_from_ms(row: dict[str, Any], field: str) -> datetime:
    value = row.get(field)
    if value is None or isinstance(value, bool) or str(value).strip() in {"", "0"}:
        raise ValueError(f"okx_live_timestamp_missing_or_invalid:{field}")
    try:
        timestamp = int(str(value))
        if timestamp <= 0:
            raise ValueError
        return datetime.fromtimestamp(timestamp / 1000, tz=UTC)
    except (OverflowError, ValueError, TypeError) as exc:
        raise ValueError(f"okx_live_timestamp_missing_or_invalid:{field}") from exc


def _permission_tokens(value: Any) -> list[str]:
    if value is None:
        raise ValueError("okx_live_permission_field_missing_or_invalid")
    if not isinstance(value, str):
        raise TypeError("okx_live_permission_field_missing_or_invalid")
    if value == "":
        return []
    return sorted(
        {item.strip().lower() for item in str(value).split(",") if item.strip()}
    )


def parse_live_account_config(row: dict[str, Any]) -> OkxLiveAccountConfig:
    permissions = _permission_tokens(row.get("perm"))
    ip = row.get("ip")
    if ip is None:
        raise ValueError("okx_live_ip_binding_field_missing_or_invalid")
    if not isinstance(ip, str):
        raise TypeError("okx_live_ip_binding_field_missing_or_invalid")
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
            ip_bound=bool(ip.strip()),
        ),
    )


def parse_live_balance(row: dict[str, Any]) -> OkxLiveBalanceSnapshot:
    if type(row) is not dict:
        raise ValueError("okx_live_balance_row_invalid")
    raw_details = row.get("details")
    if type(raw_details) is not list or not raw_details:
        raise ValueError("okx_live_balance_details_missing_or_invalid")
    details: list[OkxLiveBalanceDetail] = []
    seen_currencies: set[str] = set()
    for item in raw_details:
        if type(item) is not dict:
            raise ValueError("okx_live_balance_detail_invalid")
        currency = item.get("ccy")
        if (
            type(currency) is not str
            or not currency
            or currency != currency.strip()
            or currency in seen_currencies
        ):
            raise ValueError("okx_live_balance_currency_missing_or_conflicting")
        seen_currencies.add(currency)
        details.append(
            OkxLiveBalanceDetail(
                currency=currency,
                equity=_required_decimal(item, "eq"),
                cash_balance=_required_decimal(item, "cashBal"),
                available_balance=_required_decimal(item, "availBal"),
                frozen_balance=_required_decimal(item, "frozenBal"),
                unrealized_pnl=_required_decimal(item, "upl"),
            )
        )
    if "USDT" not in seen_currencies:
        raise ValueError("okx_live_usdt_settlement_detail_missing")
    captured_at = _required_datetime_from_ms(row, "uTime")
    return OkxLiveBalanceSnapshot(
        total_equity=_required_decimal(row, "totalEq"),
        isolated_equity=_required_decimal(row, "isoEq"),
        adjusted_equity=_required_decimal(row, "adjEq"),
        available_equity=_required_decimal(row, "availEq"),
        details=details,
        captured_at=captured_at,
        raw=dict(row),
    )


def parse_live_position(row: dict[str, Any]) -> OkxLivePositionView:
    return OkxLivePositionView(
        position_id=str(row.get("posId") or ""),
        instrument_id=str(row.get("instId") or ""),
        position_side=str(row.get("posSide") or ""),
        size=_required_decimal(row, "pos"),
        available_size=_required_decimal(row, "availPos"),
        average_price=_decimal_or_none(row.get("avgPx")),
        mark_price=_decimal_or_none(row.get("markPx")),
        unrealized_pnl=_required_decimal(row, "upl"),
        leverage=_decimal_or_none(row.get("lever")),
        margin_mode=row.get("mgnMode") or None,
        liquidation_price=_decimal_or_none(row.get("liqPx")),
        created_at=_datetime_from_ms(row.get("cTime")),
        updated_at=_datetime_from_ms(row.get("uTime")),
        raw=dict(row),
    )


def parse_live_order(row: dict[str, Any]) -> OkxLiveOrderView:
    attached = row.get("attachAlgoOrds")
    if type(attached) is not list or any(type(item) is not dict for item in attached):
        raise ValueError("okx_live_order_attached_algo_evidence_missing_or_invalid")
    return OkxLiveOrderView(
        order_id=str(row.get("ordId") or ""),
        client_order_id=row.get("clOrdId") or None,
        instrument_id=str(row.get("instId") or ""),
        side=str(row.get("side") or ""),
        position_side=row.get("posSide") or None,
        order_type=str(row.get("ordType") or ""),
        state=str(row.get("state") or ""),
        size=_required_decimal(row, "sz"),
        accumulated_fill_size=_required_decimal(row, "accFillSz"),
        price=_decimal_or_none(row.get("px")),
        average_fill_price=_decimal_or_none(row.get("avgPx")),
        reduce_only=_bool_value(row.get("reduceOnly")),
        created_at=_datetime_from_ms(row.get("cTime")),
        updated_at=_datetime_from_ms(row.get("uTime")),
        attached_algo_orders=[dict(item) for item in attached],
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
        size=_required_decimal(row, "sz"),
        actual_size=_required_decimal(row, "actualSz"),
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
