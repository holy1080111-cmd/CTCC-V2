"""Replay exact Live postsubmit raw facts; results are DATA, never resolution.

Original source ownership, TLS custody and signing are separate requirements.
No input flag can mark a chain complete. A short fills/pending page still needs
an explicit successful empty terminal page. Unknown facts retain the hold.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from itertools import pairwise

from app.domain.native_live_intent_v1 import (
    checked_live_intent_data_v1,
    exact_live_intent_document_v1,
)
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    validate_stamps,
)
from app.exchange.okx.live_account_config_observation_v1 import (
    LiveAccountConfigObservationV1,
    _receipt_document,
)
from app.exchange.okx.live_account_raw_page_v1 import (
    ALGO_TYPES,
    LiveAccountRawPageV1,
    live_account_raw_page_receipt_document_v1,
)
from app.exchange.okx.live_trade_read_page_v1 import (
    LiveTradeReadPageV1,
    live_trade_read_receipt_v1,
)

MAX_CURRENT_AGE_NS = 5_000_000_000


class LiveReconciliationFactsError(ValueError):
    pass


def _need(condition, code):
    if condition is not True:
        raise LiveReconciliationFactsError(code) from None


def _decimal(row, field, *, nonnegative=False, positive=False):
    value = row.get(field)
    _need(type(value) is str and 0 < len(value) <= 80, "live_reconcile_number_unknown")
    try:
        number = Decimal(value)
    except InvalidOperation:
        _need(False, "live_reconcile_number_invalid")
    _need(number.is_finite(), "live_reconcile_number_invalid")
    _need(
        abs(number.as_tuple().exponent) <= 80 and len(number.as_tuple().digits) <= 80,
        "live_reconcile_number_bound",
    )
    _need(not nonnegative or number >= 0, "live_reconcile_number_invalid")
    _need(not positive or number > 0, "live_reconcile_number_invalid")
    return number


def _payload(page, *, barrier, validated_at, origin, binding):
    _need(
        type(page)
        in (LiveTradeReadPageV1, LiveAccountRawPageV1, LiveAccountConfigObservationV1),
        "live_reconcile_original_live_page_required",
    )
    _need(
        page.execution_authority
        is page.account_complete
        is page.source_authenticity_verified
        is False,
        "live_reconcile_page_authority_invalid",
    )
    _need(
        page.origin == origin and page.lease_binding_id == binding,
        "live_reconcile_source_binding_changed",
    )
    _need(len(page.stamps) == 6, "live_reconcile_clock_invalid")
    validate_stamps(
        (
            barrier.model_dump(),
            *(stamp.model_dump() for stamp in page.stamps),
            validated_at.model_dump(),
        )
    )
    _need(
        page.stamps[0].monotonic_ns > barrier.monotonic_ns
        and page.stamps[0].utc_ns > barrier.utc_ns,
        "live_reconcile_requests_must_start_after_submit",
    )
    _need(
        0
        <= validated_at.monotonic_ns - page.stamps[-1].monotonic_ns
        <= MAX_CURRENT_AGE_NS,
        "live_reconcile_source_stale",
    )
    _need(sha(page.raw_body) == page.raw_sha256, "live_reconcile_raw_changed")
    receipt = (
        live_trade_read_receipt_v1(page)
        if type(page) is LiveTradeReadPageV1
        else live_account_raw_page_receipt_document_v1(page)
        if type(page) is LiveAccountRawPageV1
        else _receipt_document(page)
    )
    _need(
        sha(canonical(receipt)) == page.receipt_sha256, "live_reconcile_receipt_changed"
    )
    document = decode(page.raw_body, maximum=524288)
    _need(
        type(document) is dict
        and document.get("code") == "0"
        and type(document.get("data")) is list,
        "live_reconcile_provider_error",
    )
    _need(
        all(type(row) is dict for row in document["data"]),
        "live_reconcile_raw_rows_invalid",
    )
    for row in document["data"]:
        for name in ("cTime", "uTime", "ts", "fillTime"):
            if name in row:
                value = row[name]
                _need(
                    type(value) is str
                    and value.isascii()
                    and value.isdecimal()
                    and 0 < int(value) <= page.stamps[3].utc_ns // 1_000_000,
                    "live_reconcile_future_or_unknown_source_time",
                )
        for first, last in (("cTime", "uTime"), ("fillTime", "ts")):
            if first in row and last in row:
                _need(
                    int(row[first]) <= int(row[last]),
                    "live_reconcile_source_chronology_invalid",
                )
    _need(
        sha(canonical(document)) == page.canonical_sha256,
        "live_reconcile_canonical_changed",
    )
    return document["data"]


def _chain(pages, *, kind, params, getter, barrier, validated_at, origin, binding):
    _need(
        type(pages) is tuple and 1 <= len(pages) <= 64,
        "live_reconcile_chain_incomplete",
    )
    rows, seen, prior = [], set(), None
    for index, page in enumerate(pages):
        _need(
            type(page)
            is (LiveTradeReadPageV1 if kind == "fills" else LiveAccountRawPageV1),
            "live_reconcile_chain_invalid",
        )
        selector = page.selector
        _need(
            getter(selector) == (kind, prior, *params),
            "live_reconcile_cursor_binding_changed",
        )
        incoming = _payload(
            page,
            barrier=barrier,
            validated_at=validated_at,
            origin=origin,
            binding=binding,
        )
        _need(len(incoming) <= 100, "live_reconcile_chain_bound")
        if not incoming:
            _need(index == len(pages) - 1, "live_reconcile_data_after_chain_end")
            return tuple(rows)
        _need(index < len(pages) - 1, "live_reconcile_terminal_page_missing")
        field_name = (
            "billId"
            if kind == "fills"
            else "ordId"
            if kind == "orders_pending"
            else "algoId"
        )
        ids = [item.get(field_name) for item in incoming]
        _need(
            all(
                type(item) is str
                and item.isascii()
                and item.isdecimal()
                and 0 < int(item) < 10**40
                for item in ids
            ),
            "live_reconcile_cursor_invalid",
        )
        _need(
            all(int(left) > int(right) for left, right in pairwise(ids)),
            "live_reconcile_cursor_not_ordered",
        )
        _need(
            prior is None or all(int(item) < int(prior) for item in ids),
            "live_reconcile_cursor_not_advancing",
        )
        _need(
            not seen.intersection(ids) and len(set(ids)) == len(ids),
            "live_reconcile_duplicate_source",
        )
        seen.update(ids)
        prior = ids[-1]
        rows.extend(incoming)
    _need(False, "live_reconcile_chain_incomplete")


@dataclass(frozen=True, slots=True)
class LiveReconciliationFactsV1:
    outcome: str
    codes: tuple[str, ...]
    reservation_id: str | None = None
    intent_sha256: str | None = None
    order_id: str | None = None
    filled_contracts: Decimal | None = None
    average_price: Decimal | None = None
    entry_fees_usdt: Decimal | None = None
    protection_algo_id: str | None = None
    raw_bank_sha256: str | None = None
    execution_authority: bool = field(default=False, init=False)
    resolution_authority: bool = field(default=False, init=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    economic_qualification_verified: bool = field(default=False, init=False)


def _assess_live_postsubmit_facts_v1(
    intent,
    *,
    order_page,
    fill_pages,
    protection_page,
    config_before,
    config_after,
    current_pages,
    submission_started,
    submission_barrier,
    validated_at,
    tick_size,
):
    """Pure raw replay; caller-created inputs cannot sign or release anything."""
    reservation_id = intent_sha = None
    try:
        intent = checked_live_intent_data_v1(intent)
        reservation_id, _, intent_sha = exact_live_intent_document_v1(intent)
        _need(
            type(submission_started)
            is type(submission_barrier)
            is type(validated_at)
            is ClockStamp,
            "live_reconcile_clock_invalid",
        )
        validate_stamps(
            (
                submission_started.model_dump(),
                submission_barrier.model_dump(),
                validated_at.model_dump(),
            )
        )
        _need(
            type(tick_size) is Decimal and tick_size.is_finite() and tick_size > 0,
            "live_reconcile_tick_unknown",
        )
        _need(
            type(order_page) is LiveTradeReadPageV1
            and order_page.selector.kind == "order",
            "live_reconcile_exact_order_required",
        )
        origin, binding = order_page.origin, order_page.lease_binding_id
        context = {
            "barrier": submission_barrier,
            "validated_at": validated_at,
            "origin": origin,
            "binding": binding,
        }
        configs = [_payload(page, **context) for page in (config_before, config_after)]
        _need(
            all(
                type(page) is LiveAccountConfigObservationV1
                for page in (config_before, config_after)
            ),
            "live_reconcile_config_required",
        )
        _need(
            all(
                len(rows) == 1
                and rows[0].get("uid") == intent.account_uid
                and rows[0].get("mainUid") == intent.main_uid
                for rows in configs
            ),
            "live_reconcile_account_identity_changed",
        )
        _need(
            configs[0][0].get("acctLv") == configs[1][0].get("acctLv")
            and configs[0][0].get("acctLv") in ("2", "3"),
            "live_reconcile_account_mode_changed",
        )
        _need(
            configs[0][0].get("posMode") == configs[1][0].get("posMode") == "net_mode"
            and intent.position_side == "net",
            "live_reconcile_micro_net_mode_required",
        )
        order_rows = _payload(order_page, **context)
        _need(len(order_rows) == 1, "live_reconcile_order_unknown")
        order = order_rows[0]
        _need(
            order.get("instId") == intent.instrument_id
            and order.get("clOrdId") == intent.client_order_id
            and order.get("side") == ("buy" if intent.direction == "long" else "sell")
            and order.get("posSide") == intent.position_side
            and order.get("tdMode") == "isolated"
            and order.get("ordType") == "fok",
            "live_reconcile_order_binding_changed",
        )
        order_id = order.get("ordId")
        _need(
            type(order_id) is str and order_id.isdecimal() and int(order_id) > 0,
            "live_reconcile_order_identity_unknown",
        )
        _need(
            type(order.get("cTime")) is str
            and order["cTime"].isascii()
            and order["cTime"].isdecimal()
            and int(order["cTime"]) >= submission_started.utc_ns // 1_000_000,
            "live_reconcile_order_predates_original_submission",
        )
        deadline_delta = intent.deadline - datetime(1970, 1, 1, tzinfo=UTC)
        deadline_ms = (
            deadline_delta.days * 86400 + deadline_delta.seconds
        ) * 1000 + deadline_delta.microseconds // 1000
        _need(
            int(order["cTime"]) <= deadline_ms,
            "live_reconcile_order_outside_original_expiry",
        )
        _need(
            _decimal(order, "sz", positive=True) == intent.contracts
            and _decimal(order, "lever", positive=True) == intent.leverage,
            "live_reconcile_size_or_leverage_changed",
        )
        fills = _chain(
            fill_pages,
            kind="fills",
            params=(intent.instrument_id, order_id),
            getter=lambda value: (
                value.kind,
                value.after_bill_id,
                value.instrument_id,
                value.order_id,
            ),
            **context,
        )
        seen_trade = set()
        for row in fills:
            _need(
                row.get("ordId") == order_id
                and row.get("clOrdId") == intent.client_order_id
                and row.get("instId") == intent.instrument_id
                and row.get("posSide") == intent.position_side
                and row.get("side") == order["side"],
                "live_reconcile_fill_binding_changed",
            )
            trade = row.get("tradeId")
            _need(
                type(trade) is str
                and trade.isascii()
                and trade.isdecimal()
                and int(trade) > 0
                and trade not in seen_trade,
                "live_reconcile_fill_identity_conflict",
            )
            seen_trade.add(trade)
            _need(row.get("feeCcy") == "USDT", "live_reconcile_fee_currency_unknown")
            _decimal(row, "fillSz", positive=True)
            _decimal(row, "fillPx", positive=True)
            _decimal(row, "fee")
            for name in ("fillTime", "ts"):
                _need(
                    type(row.get(name)) is str
                    and row[name].isascii()
                    and row[name].isdecimal(),
                    "live_reconcile_fill_time_unknown",
                )
            _need(
                submission_started.utc_ns // 1_000_000
                <= int(row["fillTime"])
                <= int(row["ts"])
                <= validated_at.utc_ns // 1_000_000,
                "live_reconcile_fill_chronology_invalid",
            )
        _need(type(current_pages) is tuple, "live_reconcile_current_account_incomplete")
        grouped = {}
        for page in current_pages:
            _need(
                type(page) is LiveAccountRawPageV1,
                "live_reconcile_current_account_incomplete",
            )
            grouped.setdefault(page.selector.stream, []).append(page)
        required = {
            "positions",
            "orders_pending",
            *("algo_" + kind for kind in ALGO_TYPES),
        }
        _need(required <= set(grouped), "live_reconcile_current_account_incomplete")
        _need(len(grouped["positions"]) == 1, "live_reconcile_positions_incomplete")
        positions = _payload(grouped["positions"][0], **context)
        nonzero = [row for row in positions if _decimal(row, "pos") != 0]
        normal = _chain(
            tuple(grouped["orders_pending"]),
            kind="orders_pending",
            params=(),
            getter=lambda value: (value.stream, value.after),
            **context,
        )
        _need(not normal, "live_reconcile_pending_order_unknown")
        active = []
        for kind in ALGO_TYPES:
            stream = "algo_" + kind
            active.extend(
                _chain(
                    tuple(grouped[stream]),
                    kind=stream,
                    params=(),
                    getter=lambda value: (value.stream, value.after),
                    **context,
                )
            )
        filled = _decimal(order, "accFillSz", nonnegative=True)
        total = sum(
            (_decimal(row, "fillSz", positive=True) for row in fills), Decimal(0)
        )
        _need(total == filled, "live_reconcile_fill_size_conflict")
        bank = sha(
            canonical(
                [
                    page.receipt_sha256
                    for page in (
                        config_before,
                        order_page,
                        *fill_pages,
                        *current_pages,
                        *(() if protection_page is None else (protection_page,)),
                        config_after,
                    )
                ]
            )
        )
        if order.get("state") == "canceled" and filled == 0:
            _need(
                not fills and not nonzero and not active and order.get("avgPx") == "",
                "live_reconcile_zero_fill_not_flat",
            )
            return LiveReconciliationFactsV1(
                "ZERO_FILL_FLAT",
                (),
                reservation_id,
                intent_sha,
                order_id,
                filled,
                None,
                Decimal(0),
                None,
                bank,
            )
        _need(
            order.get("state") == "filled" and filled == intent.contracts,
            "live_reconcile_partial_or_ambiguous_fill",
        )
        average = _decimal(order, "avgPx", positive=True)
        weighted = (
            sum(
                (
                    _decimal(row, "fillPx", positive=True)
                    * _decimal(row, "fillSz", positive=True)
                    for row in fills
                ),
                Decimal(0),
            )
            / filled
        )
        _need(
            abs(average - weighted) <= tick_size / 2,
            "live_reconcile_average_fill_conflict",
        )
        _need(
            (
                intent.stop_loss < average < intent.take_profit
                and average <= intent.executable_price_bound
            )
            if intent.direction == "long"
            else (
                intent.take_profit < average < intent.stop_loss
                and average >= intent.executable_price_bound
            ),
            "live_reconcile_adverse_price_boundary",
        )
        _need(len(nonzero) == 1, "live_reconcile_position_unknown")
        position = nonzero[0]
        _need(
            position.get("instId") == intent.instrument_id
            and position.get("posSide") == "net"
            and position.get("mgnMode") == "isolated"
            and _decimal(position, "lever", positive=True) == intent.leverage
            and _decimal(position, "pos")
            == filled * (1 if intent.direction == "long" else -1),
            "live_reconcile_position_mismatch",
        )
        _need(
            type(protection_page) is LiveTradeReadPageV1
            and protection_page.selector.kind == "algo",
            "live_reconcile_protection_unknown",
        )
        protection_rows = _payload(protection_page, **context)
        _need(
            len(protection_rows) == 1 and len(active) == 1,
            "live_reconcile_protection_not_unique",
        )
        protection = protection_rows[0]
        protection_fields = (
            "algoId",
            "algoClOrdId",
            "instId",
            "posSide",
            "tdMode",
            "side",
            "ordType",
            "state",
            "sz",
            "tpTriggerPx",
            "slTriggerPx",
            "tpTriggerPxType",
            "slTriggerPxType",
            "tpOrdPx",
            "slOrdPx",
            "closeFraction",
            "actualSz",
            "reduceOnly",
        )
        _need(
            all(
                type(protection.get(name)) is str
                and protection.get(name) == active[0].get(name)
                for name in protection_fields
            ),
            "live_reconcile_active_protection_conflict",
        )
        _need(
            protection.get("instId") == intent.instrument_id
            and protection.get("algoClOrdId") == intent.protection_client_id
            and protection.get("posSide") == "net"
            and protection.get("tdMode") == "isolated"
            and protection.get("side")
            == ("sell" if intent.direction == "long" else "buy")
            and protection.get("ordType") == "oco"
            and protection.get("state") == "live"
            and protection.get("tpTriggerPxType")
            == protection.get("slTriggerPxType")
            == "mark"
            and protection.get("tpOrdPx") == protection.get("slOrdPx") == "-1"
            and protection.get("closeFraction") == ""
            and protection.get("reduceOnly") == "true"
            and _decimal(protection, "sz", positive=True) == filled
            and _decimal(protection, "tpTriggerPx", positive=True) == intent.take_profit
            and _decimal(protection, "slTriggerPx", positive=True) == intent.stop_loss
            and _decimal(protection, "actualSz", nonnegative=True) == 0,
            "live_reconcile_protection_mismatch",
        )
        fee = sum((_decimal(row, "fee") for row in fills), Decimal(0))
        return LiveReconciliationFactsV1(
            "FULL_FILL_PROTECTED",
            (),
            reservation_id,
            intent_sha,
            order_id,
            filled,
            average,
            fee,
            protection.get("algoId"),
            bank,
        )
    except LiveReconciliationFactsError as error:
        return LiveReconciliationFactsV1(
            "UNCERTAIN", (str(error),), reservation_id, intent_sha
        )
    except Exception:  # noqa: BLE001 -- malformed source never produces a safe empty set.
        return LiveReconciliationFactsV1(
            "UNCERTAIN", ("live_reconcile_raw_invalid",), reservation_id, intent_sha
        )


def assess_live_postsubmit_facts_v1(
    intent,
    *,
    order_page,
    fill_pages,
    protection_page,
    config_before,
    config_after,
    current_pages,
    submission_started,
    submission_barrier,
    validated_at,
    tick_size,
):
    # Source-number bounds and a fixed context make the result independent of
    # another task/caller's Decimal precision, rounding mode or disabled traps.
    context = Context(
        prec=200,
        Emin=-999,
        Emax=999,
        rounding=ROUND_HALF_EVEN,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )
    with localcontext(context):
        return _assess_live_postsubmit_facts_v1(
            intent,
            order_page=order_page,
            fill_pages=fill_pages,
            protection_page=protection_page,
            config_before=config_before,
            config_after=config_after,
            current_pages=current_pages,
            submission_started=submission_started,
            submission_barrier=submission_barrier,
            validated_at=validated_at,
            tick_size=tick_size,
        )
