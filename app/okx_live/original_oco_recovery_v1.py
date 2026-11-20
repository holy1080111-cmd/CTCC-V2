"""Original Live OCO terminal GET source and DATA replay, never a close order.

One complete, unchanged original mark-price OCO child is supported. Missing
original algo identity, split exits, partials and changed source retain risk.
"""

from dataclasses import dataclass
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
from app.domain.source_primitives import canonical, decode, sha, validate_stamps
from app.exchange.okx.live_account_raw_page_v1 import ALGO_TYPES, LiveAccountRawPageV1
from app.exchange.okx.live_trade_read_page_v1 import (
    LiveTradeReadPageV1,
    observe_live_trade_read_page_v1,
    select_live_trade_read_v1,
    verify_live_trade_read_page_v1,
)
from app.okx_live import reconciliation_facts_v1 as facts
from app.okx_live.postsubmit_capture_v1 import OriginalLiveLifecycleCaptureV1

_CAPTURE_KEY = object()


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class OriginalLiveOcoTerminalCaptureV1(OriginalLiveLifecycleCaptureV1):
    entry_order_after: object
    exit_order: object
    exit_order_after: object
    exit_fills: tuple
    protection_after: object


def _need(condition):
    if condition is not True:
        raise facts.LiveReconciliationFactsError(
            "live_original_oco_incomplete_or_changed"
        ) from None


def expected_original_bindings_v1(journal):
    """Read original authenticated journal DATA, not a caller-supplied ID."""
    result = entry = None
    protected = False
    for raw in journal.proofs:
        record = decode(raw, maximum=65536)
        candidate = record.get("protection_algo_id")
        primary = record.get("entry_order_id")
        for value in (candidate, primary):
            _need(
                value is None
                or type(value) is str
                and value.isascii()
                and value.isdecimal()
                and 0 < int(value) < 10**40
            )
        if candidate is not None:
            _need(result is None or result == candidate)
            result = candidate
        if primary is not None:
            _need(entry is None or entry == primary)
            entry = primary
        if (
            record.get("outcome") == "PROTECTED_HOLD"
            and record.get("protection_verified") is True
        ):
            _need(
                candidate is not None
                and primary is not None
                and record.get("full_account_verified") is True
                and record.get("fills_complete") is True
            )
            protected = True
    return (entry, result) if protected else (None, None)


def terminal_pages_v1(capture):
    _need(type(capture) is OriginalLiveOcoTerminalCaptureV1)
    return (
        capture.order,
        *capture.fills,
        capture.protection,
        capture.exit_order,
        *capture.exit_fills,
        capture.entry_order_after,
        capture.exit_order_after,
        capture.protection_after,
        capture.config_before,
        *capture.current_pages,
        capture.config_after,
    )


def _one(page, context):
    rows = facts._payload(page, **context)
    _need(len(rows) == 1)
    return rows[0]


def _time(row, name):
    value = row.get(name)
    _need(
        type(value) is str
        and value.isascii()
        and value.isdecimal()
        and 0 < int(value) < 10**16
    )
    return int(value)


def _fills(pages, intent, order, context, *, lower_ms):
    rows = facts._chain(
        pages,
        kind="fills_history",
        params=(intent.instrument_id, order["ordId"]),
        getter=lambda selector: (
            selector.kind,
            selector.after_bill_id,
            selector.instrument_id,
            selector.order_id,
        ),
        **context,
    )
    _need(bool(rows))
    trades, bills = set(), set()
    for row in rows:
        _need(
            row.get("instId") == intent.instrument_id
            and row.get("instType") == "SWAP"
            and row.get("ordId") == order["ordId"]
            and row.get("side") == order["side"]
            and row.get("posSide") == intent.position_side
            and type(row.get("clOrdId")) is str
            and row["clOrdId"] == order["clOrdId"]
            and row.get("feeCcy") == "USDT"
        )
        trade = row.get("tradeId")
        _need(
            type(trade) is str
            and trade.isascii()
            and trade.isdecimal()
            and 0 < int(trade) < 10**40
            and trade not in trades
        )
        trades.add(trade)
        bills.add(row["billId"])
        _need(
            lower_ms
            <= _time(row, "fillTime")
            <= _time(row, "ts")
            <= context["validated_at"].utc_ns // 1_000_000
        )
        facts._decimal(row, "fillSz", positive=True)
        facts._decimal(row, "fillPx", positive=True)
        facts._decimal(row, "fee")
    size = sum(
        (facts._decimal(row, "fillSz", positive=True) for row in rows), Decimal(0)
    )
    _need(size == intent.contracts)
    average = (
        sum(
            (
                facts._decimal(row, "fillSz", positive=True)
                * facts._decimal(row, "fillPx", positive=True)
                for row in rows
            ),
            Decimal(0),
        )
        / size
    )
    fees = sum((facts._decimal(row, "fee") for row in rows), Decimal(0))
    return rows, trades, bills, average, fees


def _flat(capture, intent, context):
    before, after = (
        _one(page, context) for page in (capture.config_before, capture.config_after)
    )
    _need(
        before == after
        and before.get("uid") == intent.account_uid
        and before.get("mainUid") == intent.main_uid
        and before.get("acctLv") in ("2", "3")
        and before.get("posMode") == "net_mode"
        and intent.position_side == "net"
    )
    groups = {}
    for page in capture.current_pages:
        _need(type(page) is LiveAccountRawPageV1)
        groups.setdefault(page.selector.stream, []).append(page)
    required = {"positions", "orders_pending", *("algo_" + kind for kind in ALGO_TYPES)}
    _need(required <= set(groups) and len(groups["positions"]) == 1)
    positions = facts._payload(groups["positions"][0], **context)
    _need(all(facts._decimal(row, "pos") == 0 for row in positions))
    for stream in ("orders_pending", *("algo_" + kind for kind in ALGO_TYPES)):
        rows = facts._chain(
            tuple(groups[stream]),
            kind=stream,
            params=(),
            getter=lambda selector: (selector.stream, selector.after),
            **context,
        )
        _need(not rows)


def assess_original_live_oco_terminal_v1(
    intent, capture, *, history, barrier, expected_algo_id, expected_entry_order_id
):
    """Replay only explicit Live raw objects; returned facts have no authority."""
    rid = intent_sha = None
    try:
        with localcontext(
            Context(
                prec=200,
                Emin=-999,
                Emax=999,
                rounding=ROUND_HALF_EVEN,
                traps=[InvalidOperation, DivisionByZero, Overflow],
            )
        ):
            intent = checked_live_intent_data_v1(intent)
            rid, _, intent_sha = exact_live_intent_document_v1(intent)
            _need(
                type(capture) is OriginalLiveOcoTerminalCaptureV1
                and type(history) is facts.LiveReconciliationHistoryV1
                and capture.execution_authority is False
            )
            _need(
                type(expected_algo_id) is str
                and expected_algo_id.isascii()
                and expected_algo_id.isdecimal()
                and 0 < int(expected_algo_id) < 10**40
            )
            _need(
                type(expected_entry_order_id) is str
                and expected_entry_order_id.isascii()
                and expected_entry_order_id.isdecimal()
                and 0 < int(expected_entry_order_id) < 10**40
            )
            _need(
                type(history.intent_recorded_at) is datetime
                and history.intent_recorded_at.tzinfo is UTC
                and history.intent_recorded_at <= intent.deadline
            )
            start = (
                history.submission_started_utc
                if history.submission_started_utc is not None
                else history.intent_recorded_at
            )
            _need(
                type(start) is datetime
                and start.tzinfo is UTC
                and history.intent_recorded_at <= start <= intent.deadline
            )
            delta = start - datetime(1970, 1, 1, tzinfo=UTC)
            lower_ms = (
                delta.days * 86400 + delta.seconds
            ) * 1000 + delta.microseconds // 1000
            delta = intent.deadline - datetime(1970, 1, 1, tzinfo=UTC)
            deadline_ms = (
                delta.days * 86400 + delta.seconds
            ) * 1000 + delta.microseconds // 1000
            _need(
                type(capture.tick_size) is Decimal
                and capture.tick_size.is_finite()
                and capture.tick_size > 0
            )
            _need(
                all(
                    type(value) is Decimal and value.is_finite() and value > 0
                    for value in (capture.contract_value, capture.contract_multiplier)
                )
                and capture.contract_value_currency
                == intent.instrument_id.split("-", 1)[0]
            )
            context = {
                "barrier": barrier,
                "validated_at": capture.validated_at,
                "origin": capture.order.origin,
                "binding": capture.order.lease_binding_id,
            }
            pages = terminal_pages_v1(capture)
            _need(
                all(
                    type(page) is LiveTradeReadPageV1
                    for page in (
                        capture.order,
                        *capture.fills,
                        capture.protection,
                        capture.exit_order,
                        *capture.exit_fills,
                        capture.entry_order_after,
                        capture.exit_order_after,
                        capture.protection_after,
                    )
                )
            )
            # Every GET remains current and in actual collection order, while
            # historical source times stay UTC operands rather than clocks.
            for left, right in pairwise(pages):
                validate_stamps(
                    (left.stamps[-1].model_dump(), right.stamps[0].model_dump())
                )
            entry = _one(capture.order, context)
            _need(
                capture.order.selector
                == select_live_trade_read_v1(
                    "order",
                    instrument_id=intent.instrument_id,
                    order_id=expected_entry_order_id,
                )
                and entry.get("ordId") == expected_entry_order_id
            )
            _need(
                capture.entry_order_after.selector
                == select_live_trade_read_v1(
                    "order",
                    instrument_id=intent.instrument_id,
                    order_id=entry.get("ordId"),
                )
            )
            _need(entry == _one(capture.entry_order_after, context))
            side = "buy" if intent.direction == "long" else "sell"
            opposite = "sell" if side == "buy" else "buy"
            _need(
                entry.get("instId") == intent.instrument_id
                and entry.get("instType") == "SWAP"
                and entry.get("clOrdId") == intent.client_order_id
                and entry.get("side") == side
                and entry.get("posSide") == intent.position_side
                and entry.get("tdMode") == "isolated"
                and entry.get("ordType") == "fok"
                and entry.get("state") == "filled"
            )
            _need(lower_ms <= _time(entry, "cTime") <= deadline_ms)
            _need(
                facts._decimal(entry, "sz", positive=True)
                == facts._decimal(entry, "accFillSz", positive=True)
                == intent.contracts
                and facts._decimal(entry, "lever", positive=True) == intent.leverage
            )
            entry_rows, entry_trades, entry_bills, entry_avg, entry_fees = _fills(
                capture.fills, intent, entry, context, lower_ms=lower_ms
            )
            _need(
                abs(facts._decimal(entry, "avgPx", positive=True) - entry_avg)
                <= capture.tick_size / 2
            )
            _need(
                _time(entry, "cTime")
                <= min(_time(row, "fillTime") for row in entry_rows)
                <= max(_time(row, "fillTime") for row in entry_rows)
                <= _time(entry, "uTime")
            )
            _need(
                (
                    intent.stop_loss < entry_avg < intent.take_profit
                    and entry_avg <= intent.executable_price_bound
                )
                if intent.direction == "long"
                else (
                    intent.take_profit < entry_avg < intent.stop_loss
                    and entry_avg >= intent.executable_price_bound
                )
            )
            algo = _one(capture.protection, context)
            _need(
                capture.protection.selector
                == select_live_trade_read_v1(
                    "algo", instrument_id=intent.instrument_id, algo_id=expected_algo_id
                )
            )
            _need(
                capture.protection_after.selector == capture.protection.selector
                and algo == _one(capture.protection_after, context)
            )
            _need(
                all(
                    algo.get(key) == value
                    for key, value in {
                        "algoId": expected_algo_id,
                        "algoClOrdId": intent.protection_client_id,
                        "instId": intent.instrument_id,
                        "instType": "SWAP",
                        "posSide": intent.position_side,
                        "tdMode": "isolated",
                        "side": opposite,
                        "ordType": "oco",
                        "state": "effective",
                        "tpTriggerPxType": "mark",
                        "slTriggerPxType": "mark",
                        "tpOrdPx": "-1",
                        "slOrdPx": "-1",
                        "reduceOnly": "true",
                        "closeFraction": "",
                    }.items()
                )
            )
            _need(
                algo.get("actualSide") in ("tp", "sl")
                and algo.get("failCode") in ("", "0")
                and algo.get("subAlgoIdList") == []
                and type(algo.get("ordIdList")) is list
                and len(algo["ordIdList"]) == 1
            )
            child_id = algo["ordIdList"][0]
            _need(
                type(child_id) is str
                and child_id.isascii()
                and child_id.isdecimal()
                and 0 < int(child_id) < 10**40
                and child_id != entry["ordId"]
            )
            _need(algo.get("ordId") in ("", child_id))
            _need(
                facts._decimal(algo, "tpTriggerPx", positive=True) == intent.take_profit
                and facts._decimal(algo, "slTriggerPx", positive=True)
                == intent.stop_loss
                and facts._decimal(algo, "sz", positive=True)
                == facts._decimal(algo, "actualSz", positive=True)
                == intent.contracts
                and facts._decimal(algo, "lever", positive=True) == intent.leverage
            )
            child = _one(capture.exit_order, context)
            _need(
                capture.exit_order.selector
                == select_live_trade_read_v1(
                    "order", instrument_id=intent.instrument_id, order_id=child_id
                )
                and capture.exit_order_after.selector == capture.exit_order.selector
                and child == _one(capture.exit_order_after, context)
            )
            _need(
                all(
                    child.get(key) == value
                    for key, value in {
                        "ordId": child_id,
                        "instId": intent.instrument_id,
                        "instType": "SWAP",
                        "posSide": intent.position_side,
                        "tdMode": "isolated",
                        "side": opposite,
                        "ordType": "market",
                        "state": "filled",
                        "algoId": expected_algo_id,
                        "algoClOrdId": intent.protection_client_id,
                        "reduceOnly": "true",
                    }.items()
                )
                and type(child.get("clOrdId")) is str
            )
            _need(
                facts._decimal(child, "sz", positive=True)
                == facts._decimal(child, "accFillSz", positive=True)
                == intent.contracts
                and facts._decimal(child, "lever", positive=True) == intent.leverage
            )
            trigger_ms = _time(algo, "triggerTime")
            _need(
                _time(entry, "cTime")
                <= _time(algo, "cTime")
                <= trigger_ms
                <= _time(algo, "uTime")
                <= capture.validated_at.utc_ns // 1_000_000
            )
            _need(max(_time(row, "fillTime") for row in entry_rows) <= trigger_ms)
            _need(trigger_ms <= _time(child, "cTime"))
            exit_rows, exit_trades, exit_bills, exit_avg, _exit_fees = _fills(
                capture.exit_fills, intent, child, context, lower_ms=trigger_ms
            )
            _need(
                not entry_trades.intersection(exit_trades)
                and not entry_bills.intersection(exit_bills)
            )
            _need(
                _time(child, "cTime")
                <= min(_time(row, "fillTime") for row in exit_rows)
                and _time(child, "uTime")
                >= max(_time(row, "fillTime") for row in exit_rows)
                and abs(facts._decimal(child, "avgPx", positive=True) - exit_avg)
                <= capture.tick_size / 2
                and abs(facts._decimal(algo, "actualPx", positive=True) - exit_avg)
                <= capture.tick_size / 2
            )
            _flat(capture, intent, context)
            bank = sha(canonical([page.receipt_sha256 for page in pages]))
            return facts.LiveReconciliationFactsV1(
                "RECONCILED_FLAT",
                (),
                rid,
                intent_sha,
                entry["ordId"],
                intent.contracts,
                entry_avg,
                entry_fees,
                expected_algo_id,
                bank,
                exit_order_id=child_id,
                exit_filled_contracts=intent.contracts,
                exit_source_sha256=bank,
                exit_kind="original_oco",
                exit_lineage_verified=True,
            )
    except facts.LiveReconciliationFactsError:
        return facts.LiveReconciliationFactsV1(
            "UNCERTAIN", ("live_original_oco_incomplete_or_changed",), rid, intent_sha
        )
    except Exception:  # noqa: BLE001 -- malformed source never becomes a flat population.
        return facts.LiveReconciliationFactsV1(
            "UNCERTAIN", ("live_original_oco_raw_invalid",), rid, intent_sha
        )


async def capture_owned_original_oco_terminal_v1(owner, *, key):
    from app.exchange.okx import live_account_current_chain_v1 as current
    from app.exchange.okx.live_account_config_observation_v1 import _stamp
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import owned_recovery_v1 as recovery
    from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1
    from app.okx_live.postsubmit_capture_v1 import (
        _current_original_pages,
        _metadata,
        _rows,
    )

    _need(key is _CAPTURE_KEY)
    state, _ = recovery._scope(owner)
    _need(state["phase"] == "capturing" and lifecycle._ACTIVE_CAPTURE.get() is owner)
    intent, client, lease = state["intent"], state["client"], state["lease"]
    entry_id, expected = expected_original_bindings_v1(state["journal"])
    _need(expected is not None and entry_id is not None)

    async def read(kind, **kwargs):
        page = await observe_live_trade_read_page_v1(
            client,
            lease,
            select_live_trade_read_v1(
                kind, instrument_id=intent.instrument_id, **kwargs
            ),
        )
        verify_live_trade_read_page_v1(page, lease=lease)
        return page

    async def fills(order_id):
        pages, after, seen = [], None, set()
        for _ in range(64):
            page = await read("fills_history", order_id=order_id, after_bill_id=after)
            pages.append(page)
            incoming = _rows(page)
            if not incoming:
                return tuple(pages)
            ids = [row["billId"] for row in incoming]
            _need(not seen.intersection(ids) and len(ids) == len(set(ids)))
            _need(after is None or all(int(item) < int(after) for item in ids))
            seen.update(ids)
            after = ids[-1]
        _need(False)

    order = await read("order", order_id=entry_id)
    rows = _rows(order)
    _need(len(rows) == 1)
    _need(rows[0]["ordId"] == entry_id)
    entry_fills = await fills(entry_id)
    protection = await read("algo", algo_id=expected)
    rows = _rows(protection)
    _need(
        len(rows) == 1
        and rows[0].get("state") == "effective"
        and type(rows[0].get("ordIdList")) is list
        and len(rows[0]["ordIdList"]) == 1
    )
    child_id = rows[0]["ordIdList"][0]
    child = await read("order", order_id=child_id)
    exits = await fills(child_id)
    entry_after = await read("order", order_id=entry_id)
    child_after = await read("order", order_id=child_id)
    protection_after = await read("algo", algo_id=expected)
    # Existing custody names are exactly32hex; draw a new owned observation ID.
    import secrets

    attempt = secrets.token_hex(16)
    receipt = await current.collect_live_current_account_v1(
        client, lease, private_root=state["source_root"], attempt_id=attempt
    )
    _need(receipt["synthetic_mock"] is False)
    before, after, pages = _current_original_pages(
        state["source_root"], attempt, receipt, lease
    )
    records = state["raw_captures"]
    _need(bool(records))
    for path, digest, body_digest, _, _ in records:
        document = decode(
            read_live_lifecycle_blob_v1(state["evidence_root"], digest, domain="raw"),
            maximum=12 * 1024 * 1024,
        )
        _need(
            path.name == digest + ".json" and document["raw_body_sha256"] == body_digest
        )
    lease.verify_current()
    return OriginalLiveOcoTerminalCaptureV1(
        order,
        entry_fills,
        protection,
        before,
        after,
        pages,
        receipt["custody_sha256"],
        sha(canonical([item[1] for item in records])),
        _stamp(),
        *_metadata(pages, intent.instrument_id),
        entry_order_after=entry_after,
        exit_order=child,
        exit_order_after=child_after,
        exit_fills=exits,
        protection_after=protection_after,
    )


async def capture_recovery_pages_v1(owner, *, key):
    """Classify one original algo, then choose one fresh exact source path."""
    from app.okx_live import owned_recovery_v1 as recovery
    from app.okx_live import postsubmit_capture_v1 as original
    from app.okx_live.postsubmit_capture_v1 import _rows

    _need(key is _CAPTURE_KEY)
    state, _ = recovery._scope(owner)
    entry, algo = expected_original_bindings_v1(state["journal"])
    if entry is not None and algo is not None:
        state["recovery_entry_order_id"] = entry
        page = await observe_live_trade_read_page_v1(
            state["client"],
            state["lease"],
            select_live_trade_read_v1(
                "algo", instrument_id=state["intent"].instrument_id, algo_id=algo
            ),
        )
        verify_live_trade_read_page_v1(page, lease=state["lease"])
        rows = _rows(page)
        _need(
            len(rows) == 1
            and rows[0].get("algoId") == algo
            and rows[0].get("algoClOrdId") == state["intent"].protection_client_id
        )
        if rows[0].get("state") == "effective":
            return await capture_owned_original_oco_terminal_v1(owner, key=_CAPTURE_KEY)
    return await original.capture_owned_live_postsubmit_pages_v1(
        owner, key=original._CAPTURE_KEY_V1
    )


def extra_terminal_pages_v1(capture):
    if type(capture) is not OriginalLiveOcoTerminalCaptureV1:
        return ()
    return (
        capture.entry_order_after,
        capture.exit_order,
        *capture.exit_fills,
        capture.exit_order_after,
        capture.protection_after,
    )


def assess_recovery_capture_v1(state, capture):
    """Pure DATA dispatch; the signer separately requires original custody."""
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    entry, algo = expected_original_bindings_v1(state["journal"])
    if type(capture) is OriginalLiveOcoTerminalCaptureV1:
        return assess_original_live_oco_terminal_v1(
            state["intent"],
            capture,
            history=state["recovery_history"],
            barrier=state["capture_barrier"],
            expected_algo_id=algo,
            expected_entry_order_id=entry,
        )
    _need(type(capture) is OriginalLiveLifecycleCaptureV1)
    result = facts.assess_live_postsubmit_facts_v1(
        state["intent"], **lifecycle._capture_arguments(state, capture)
    )
    if (
        entry is not None
        and result.order_id is not None
        and result.order_id != entry
        or algo is not None
        and result.protection_algo_id is not None
        and result.protection_algo_id != algo
    ):
        return facts.LiveReconciliationFactsV1(
            "UNCERTAIN",
            ("live_original_recovery_binding_changed",),
            result.reservation_id,
            result.intent_sha256,
        )
    return result
