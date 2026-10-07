"""Offline V5 B1 source fact for one acknowledged demo order.

This replays supplied private bytes only. It has no database, transport, or
execution entry point and cannot release a reservation or establish account
completeness. A matching recorded quantity is not proof of source authenticity
or of future history finality.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.trade_evidence import post_submit, submission_reporting
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as history

VERSION = "ctcc.recorded_terminal_order_fill_fact.v1"
_ORDER_STREAMS = frozenset(
    {"orders_history_recent_swap", "orders_history_archive_swap"}
)
_FILL_STREAMS = frozenset({"fills_recent", "fills_history_swap"})
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")


class RecordedOrderFillFactError(ValueError):
    """Fixed safe codes; no raw account or order values escape."""


def _deny(code):
    raise RecordedOrderFillFactError(code)


@dataclass(frozen=True, slots=True, repr=False)
class RecordedOrderFillFact:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def reservation_releasable(self):
        return False

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _deny("recorded_fact_duplicate_json_key")
        result[key] = value
    return result


def _document(raw, *, limit):
    if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= limit:
        _deny("recorded_fact_document_invalid")
    value = json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_float=lambda _: _deny("recorded_fact_document_invalid"),
        parse_constant=lambda _: _deny("recorded_fact_document_invalid"),
    )
    if type(value) is not dict or submission_reporting.wire(value) != raw:
        _deny("recorded_fact_document_invalid")
    return value


def _number(value):
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        _deny("recorded_fact_quantity_invalid")
    try:
        result = Decimal(value)
    except InvalidOperation:
        _deny("recorded_fact_quantity_invalid")
    if not result.is_finite():
        _deny("recorded_fact_quantity_invalid")
    return result


def _row_number(row, field):
    return _number(row.get(field))


def _raw_sequences(chain, packet):
    result = []
    for item in chain:
        record = journal.checked_event(item.event)
        if record["kind"] != "raw_finalized":
            continue
        index = len(result)
        if (
            index >= len(packet.observations)
            or record["data"].get("request_index") != index
            or record["data"].get("receipt_sha256")
            != packet.observations[index].receipt_sha256
            or record["raw_sha256"] != packet.observations[index].body_sha256
        ):
            _deny("recorded_fact_raw_locator_mismatch")
        result.append(record["sequence"])
    if len(result) != len(packet.observations):
        _deny("recorded_fact_raw_locator_missing")
    return tuple(result)


def _source_rows(packet, *, order_id, order_body, report, raw_sequences=None):
    """Read verified rows without replacing page order or raw source values."""
    orders, fills = [], {}
    for request_index, observation in enumerate(packet.observations):
        stream = observation.request.stream
        family = capture.stream_family(stream)
        if family not in {
            "orders_history_recent",
            "orders_history_archive",
            "orders_pending",
            "fills_recent",
            "fills_history",
        }:
            continue
        for ordinal, item in enumerate(observation.rows):
            raw = json.loads(item.canonical_json)
            if raw.get("ordId") != order_id:
                continue
            if stream == "orders_pending":
                _deny("recorded_fact_pending_order_conflict")
            if stream not in _ORDER_STREAMS | _FILL_STREAMS:
                _deny("recorded_fact_product_scope_conflict")
            locator = {
                "stream": stream,
                "request_index": request_index,
                "page_index": observation.page_index,
                "row_ordinal": ordinal,
                "raw_event_sequence": None
                if raw_sequences is None
                else raw_sequences[request_index],
                "page_receipt_sha256": observation.receipt_sha256,
                "raw_sha256": observation.body_sha256,
                "row_sha256": journal.digest(item.canonical_json.encode("utf-8")),
            }
            if stream in _ORDER_STREAMS:
                orders.append((raw, item.canonical_json, locator))
            else:
                bill_id = raw.get("billId")
                if type(bill_id) is not str or bill_id != item.row_id:
                    _deny("recorded_fact_fill_identity_invalid")
                prior = fills.get(bill_id)
                if prior is not None and prior[0] != item.canonical_json:
                    _deny("recorded_fact_fill_conflict")
                if prior is None:
                    fills[bill_id] = (item.canonical_json, raw, item.source_times, [])
                fills[bill_id][3].append(locator)
    if not orders:
        _deny("recorded_fact_terminal_order_missing")
    first, first_canonical, _ = orders[0]
    if any(canonical != first_canonical for _, canonical, _ in orders[1:]):
        _deny("recorded_fact_terminal_order_conflict")
    expected = {
        "ordId": order_id,
        "clOrdId": report.client_order_id,
        "instId": order_body["instId"],
        "instType": "SWAP",
        "tdMode": order_body["tdMode"],
        "posSide": order_body["posSide"],
        "side": order_body["side"],
        "ordType": order_body["ordType"],
    }
    if any(first.get(name) != value for name, value in expected.items()):
        _deny("recorded_fact_order_binding_mismatch")
    if first.get("state") not in {"filled", "canceled"}:
        _deny("recorded_fact_terminal_state_unsupported")
    size, accumulated = _row_number(first, "sz"), _row_number(first, "accFillSz")
    if size <= 0 or size != _number(order_body["sz"]) or accumulated > size:
        _deny("recorded_fact_order_quantity_mismatch")
    limit = _row_number(first, "px")
    if limit <= 0 or limit != _number(order_body["px"]):
        _deny("recorded_fact_order_price_mismatch")
    if (first["state"] == "filled" and accumulated != size) or (
        first["state"] == "canceled" and accumulated != 0
    ):
        _deny("recorded_fact_fok_state_quantity_conflict")
    # Source milliseconds were parsed by V5; compare their verified typed values.
    order_item = next(
        item
        for observation in packet.observations
        if observation.request.stream in _ORDER_STREAMS
        for item in observation.rows
        if item.row_id == order_id
    )
    order_times = {item.path: item.value for item in order_item.source_times}
    created, updated = order_times.get("cTime"), order_times.get("uTime")
    if (
        created is None
        or updated is None
        or not packet.plan.history_start <= report.submit_started_at <= created
        or created > report.completed_at
        or updated > packet.plan.history_end
    ):
        _deny("recorded_fact_order_clock_mismatch")
    total = Decimal(0)
    fill_locators = []
    for _, raw, source_times, locators in fills.values():
        if any(
            raw.get(field) != first[field]
            for field in ("instId", "instType", "side", "posSide")
        ):
            _deny("recorded_fact_fill_binding_mismatch")
        amount = _row_number(raw, "fillSz")
        fill_price = _row_number(raw, "fillPx")
        fill_times = {item.path: item.value for item in source_times}
        if amount <= 0 or fill_price <= 0:
            _deny("recorded_fact_fill_quantity_invalid")
        if (first["side"] == "buy" and fill_price > limit) or (
            first["side"] == "sell" and fill_price < limit
        ):
            _deny("recorded_fact_fill_price_outside_limit")
        if (
            fill_times.get("fillTime") is None
            or fill_times.get("ts") is None
            or not created <= fill_times["fillTime"] <= updated
            or fill_times["ts"] > packet.plan.history_end
        ):
            _deny("recorded_fact_fill_clock_invalid")
        total += amount
        fill_locators.extend(locators)
    if total != accumulated:
        _deny("recorded_fact_fill_quantity_mismatch")
    return first["state"], size, accumulated, total, orders, len(fills), fill_locators


def _outcome(raw, expected_id, prepared, report):
    if (
        type(expected_id) is not str
        or re.fullmatch(r"[a-f0-9]{64}", expected_id) is None
        or type(raw) is not str
        or submission_reporting.sha(raw.encode("utf-8")) != expected_id
    ):
        _deny("recorded_fact_outcome_pin_mismatch")
    value = _document(raw, limit=16384)
    try:
        recorded_at = datetime.fromisoformat(value["recorded_at"])
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            _deny("recorded_fact_outcome_clock_invalid")
        if recorded_at < report.completed_at:
            _deny("recorded_fact_outcome_clock_invalid")
        transition_id = value["intent_transition_id"]
        revision = value["ledger_revision"]
        if (
            type(transition_id) is not int
            or transition_id <= 0
            or type(revision) is not int
            or revision <= 0
        ):
            _deny("recorded_fact_outcome_invalid")
    except (KeyError, TypeError, ValueError):
        _deny("recorded_fact_outcome_invalid")
    expected = {
        "version": "ctcc.private.submission-outcome.v1",
        "reservation_id": prepared.consumed.reservation_id,
        "intent_transition_id": transition_id,
        "intent_sha256": prepared.intent.sha256,
        "sequence": 1,
        "previous_sha256": None,
        "observation_kind": "initial",
        "status": prepared.status,
        "reason": prepared.reason,
        "capture_sha256": prepared.capture_sha256,
        "report_sha256": prepared.report_sha256,
        "ledger_revision": revision,
        "recorded_at": recorded_at.isoformat(),
        "binding": prepared.binding,
        "execution_authority": False,
        "order_retry_authority": False,
        "transport_authenticity_verified": False,
        "fill_verified": False,
        "protection_verified": False,
    }
    if value != expected or raw != submission_reporting.wire(expected):
        _deny("recorded_fact_outcome_mismatch")


def verify_submission_recorded_order_fill_fact(
    chain,
    *,
    history_pins,
    submission_request,
    intent_raw,
    intent_sha256,
    submission_capture,
    outcome_json,
    expected_outcome_id,
):
    """Reverify original B1 and submission lineage; return a non-authority fact.

    `history_pins` are the exact keyword pins of verify_history_query_chain.
    No new request is made. A missing or contradictory fact raises a static code.
    """
    try:
        if type(history_pins) is not dict or set(history_pins) != {
            "expected_head_sha256",
            "expected_plan_sha256",
            "expected_packet_sha256",
            "expected_account_id",
            "expected_settlement_currency",
        }:
            _deny("recorded_fact_history_pins_invalid")
        query = history.verify_history_query_chain(chain, **history_pins)
        saved = [
            item.event.packet_payload
            for item in chain
            if item.event.packet_payload is not None
        ]
        if len(saved) != 1:
            _deny("recorded_fact_packet_missing")
        packet = capture.verify_demo_account_packet(
            saved[0],
            expected_sha256=history_pins["expected_packet_sha256"],
            expected_plan_sha256=history_pins["expected_plan_sha256"],
        )
        if type(packet.plan) is not capture.AllProductDemoAccountCapturePlan:
            _deny("recorded_fact_v5_required")
        prepared = submission_reporting.prepare_submission(
            submission_request,
            intent_raw,
            intent_sha256=intent_sha256,
            capture=submission_capture,
        )
        report = post_submit.verify_submission_report(
            prepared.report_bytes, prepared.report_sha256
        )
        if prepared.status != "acknowledged" or report.exchange_order_id is None:
            _deny("recorded_fact_ack_required")
        if (
            report.candidate.account_id != packet.plan.expected_uid
            or report.candidate.settlement_currency != packet.plan.settlement_currency
            or report.candidate.instrument_id not in packet.plan.leverage_instrument_ids
        ):
            _deny("recorded_fact_scope_mismatch")
        intent = _document(prepared.intent.canonical_json, limit=8 * 1024 * 1024)
        if "intent_json" in intent:
            intent = _document(intent["intent_json"], limit=8 * 1024 * 1024)
        if intent.get("version") != "ctcc-demo-submit-intent-v2":
            _deny("recorded_fact_intent_invalid")
        order_body = intent["exchange_request"]["body"]
        if (
            order_body.get("ordType") != "fok"
            or order_body.get("tdMode") != "isolated"
            or order_body.get("clOrdId") != report.client_order_id
        ):
            _deny("recorded_fact_intent_invalid")
        _outcome(outcome_json, expected_outcome_id, prepared, report)
        raw_sequences = _raw_sequences(chain, packet)
        state, size, accumulated, fill_sum, orders, fill_count, fills = _source_rows(
            packet,
            order_id=report.exchange_order_id,
            order_body=order_body,
            report=report,
            raw_sequences=raw_sequences,
        )
        source_receipt = json.loads(query.receipt_json)
        value = {
            "schema_version": VERSION,
            "state": "recorded_terminal_order_with_matching_recorded_fill_rows",
            "journal_head_sha256": history_pins["expected_head_sha256"],
            "packet_sha256": history_pins["expected_packet_sha256"],
            "plan_sha256": history_pins["expected_plan_sha256"],
            "history_query_receipt_sha256": query.receipt_sha256,
            "capture_id": source_receipt["capture_id"],
            "scope_sha256": source_receipt["scope_sha256"],
            "outcome_id": expected_outcome_id,
            "intent_sha256": prepared.intent.sha256,
            "submission_report_sha256": prepared.report_sha256,
            "order_id_sha256": journal.digest(report.exchange_order_id.encode("ascii")),
            "terminal_state": state,
            "order_size": format(size, "f"),
            "recorded_accumulated_fill_size": format(accumulated, "f"),
            "recorded_fill_rows_sum": format(fill_sum, "f"),
            "distinct_fill_count": fill_count,
            "order_locators": [locator for _, _, locator in orders],
            "fill_locators": fills,
            "recorded_terminal_order_observed": True,
            "recorded_fill_quantity_matches_acc_fill": True,
            "reservation_releasable": False,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "order_retry_authority": False,
            "admission": "DENY",
        }
        return RecordedOrderFillFact(journal.canonical(value))
    except RecordedOrderFillFactError:
        raise
    except Exception:  # noqa: BLE001 -- Never expose raw account or submission bytes.
        raise RecordedOrderFillFactError("recorded_fact_verification_invalid") from None
