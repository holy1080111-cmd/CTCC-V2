"""Offline raw post-trade source replay; no transport, writes, clock or authority.

The existing account packet and submission report are replayed from externally
pinned canonical bytes. A separately pinned attribution record identifies orders,
not successful fills or complete streams. All matching supplied fills are mapped;
their fees are counted once, without adding mirrored bill fees or fillPnl. Raw
funding bills are retained as claims: bill generation time is NOT silently changed
into effective accrual time. Unknown ingestion/retention never becomes complete.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from app.trade_evidence import forensics, post_submit
from app.trade_qualification import account_capture

MAX_ATTRIBUTION_BYTES = 65536
MAX_PATH_BYTES = 1048576
MAX_RECEIPT_BYTES = 4 * 1048576
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?")
_IDENTIFIER = re.compile(r"[1-9][0-9]{0,39}")
_CLIENT = re.compile(r"[A-Za-z0-9]{1,32}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class RawForensicsError(ValueError):
    """Only static redacted public errors; raw account values never escape."""


@dataclass(frozen=True)
class RawForensicsReplay:
    """A computed receipt, not authentication, ingestion proof or a trade sample."""

    analysis: forensics.TradeForensicsResult = field(repr=False)
    canonical_receipt: bytes = field(repr=False)
    receipt_sha256: str
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    funding_accrual_verified: Literal[False] = field(default=False, init=False)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _wire(value):
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("nonfinite")


def _tree(value, depth=0, budget=None):
    if budget is None:
        budget = [50000]
    budget[0] -= 1
    if depth > 8 or budget[0] < 0:
        raise ValueError("bound")
    kind = type(value)
    if kind is dict:
        if len(value) > 32:
            raise ValueError("bound")
        for key, part in value.items():
            if type(key) is not str or len(key) > 96:
                raise ValueError("key")
            _tree(part, depth + 1, budget)
    elif kind is list:
        if len(value) > 4096:
            raise ValueError("bound")
        for part in value:
            _tree(part, depth + 1, budget)
    elif kind is str:
        if len(value) > 512:
            raise ValueError("bound")
    elif value is not None:
        # JSON numbers/bools are forbidden, including 1 standing for True/PASS.
        raise ValueError("scalar")


def _pinned_json(raw, pin, bound):
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= bound
        or type(pin) is not str
        or _DIGEST.fullmatch(pin) is None
        or _sha(raw) != pin
    ):
        raise ValueError("pin")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    _tree(parsed)
    if _wire(parsed) != raw:
        raise ValueError("canonical")
    return parsed


def _fields(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError("fields")


def _identifier(value):
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("identifier")
    return value


def _number(value, *, positive=False):
    if type(value) is not str or _NUMBER.fullmatch(value) is None:
        raise ValueError("number")
    result = Decimal(value)
    if positive and result <= 0:
        raise ValueError("positive")
    return result


def _millisecond(value):
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,14}", value) is None:
        raise ValueError("timestamp")
    return _EPOCH + timedelta(milliseconds=int(value))


def _base(candidate, pin, *, evidence_id, source_sha256, recorded_at):
    return {
        "evidence_id": evidence_id,
        "report_id": candidate.report_id,
        "candidate_sha256": pin,
        "instrument_id": candidate.instrument_id,
        "account_id": candidate.account_id,
        "environment": candidate.environment,
        "source_sha256": source_sha256,
        "recorded_at": recorded_at,
    }


def _attribution(raw, pin, report, submission_pin, account_pin):
    value = _pinned_json(raw, pin, MAX_ATTRIBUTION_BYTES)
    _fields(
        value,
        (
            "schema_version",
            "report_id",
            "candidate_sha256",
            "submission_report_sha256",
            "account_packet_sha256",
            "orders",
            "funding_bill_ids",
            "holding_path_sha256",
            "purpose",
        ),
    )
    if (
        value["schema_version"] != "ctcc.raw_trade_attribution.v1"
        or value["report_id"] != report.report_id
        or value["candidate_sha256"] != report.candidate_sha256
        or value["submission_report_sha256"] != submission_pin
        or value["account_packet_sha256"] != account_pin
        or value["purpose"] not in {"synthetic_test", "observed"}
    ):
        raise ValueError("scope")
    orders = value["orders"]
    if type(orders) is not list or not 1 <= len(orders) <= 32:
        raise ValueError("orders")
    by_order, clients, entries = {}, set(), []
    for item in orders:
        _fields(item, ("order_id", "client_order_id", "role"))
        identifier = _identifier(item["order_id"])
        client = item["client_order_id"]
        if (
            type(client) is not str
            or _CLIENT.fullmatch(client) is None
            or item["role"] not in {"entry", "exit"}
            or identifier in by_order
            or client in clients
        ):
            raise ValueError("order_binding")
        by_order[identifier] = item
        clients.add(client)
        if item["role"] == "entry":
            entries.append(item)
    if len(entries) != 1 or (entries[0]["order_id"], entries[0]["client_order_id"]) != (
        report.exchange_order_id,
        report.client_order_id,
    ):
        raise ValueError("entry_ack_binding")
    funding = value["funding_bill_ids"]
    if type(funding) is not list or len(funding) > 1024:
        raise ValueError("funding")
    for identifier in funding:
        _identifier(identifier)
    if len(set(funding)) != len(funding):
        raise ValueError("funding_duplicate")
    path_pin = value["holding_path_sha256"]
    if path_pin is not None and (
        type(path_pin) is not str or _DIGEST.fullmatch(path_pin) is None
    ):
        raise ValueError("path_pin")
    return value, by_order


def _path(raw, pin, candidate, observed, cutoff):
    if raw is None and pin is None:
        return ()
    value = _pinned_json(raw, pin, MAX_PATH_BYTES)
    _fields(
        value,
        (
            "schema_version",
            "instrument_id",
            "price_basis",
            "received_at_ms",
            "intervals",
        ),
    )
    if (
        value["schema_version"] != "ctcc.recorded_holding_path.v1"
        or value["instrument_id"] != candidate.instrument_id
        or value["price_basis"] != "last_trade"
    ):
        raise ValueError("path_scope")
    received = _millisecond(value["received_at_ms"])
    if received > observed or received < candidate.recorded_at:
        raise ValueError("path_clock")
    rows = value["intervals"]
    if type(rows) is not list or len(rows) > 4096:
        raise ValueError("path_bound")
    result = []
    candidate_pin = forensics.candidate_sha256(candidate)
    for index, row in enumerate(rows):
        if type(row) is not list or len(row) != 6:
            raise ValueError("path_row")
        started, ended = _millisecond(row[0]), _millisecond(row[1])
        if not candidate.recorded_at <= started < ended <= cutoff:
            raise ValueError("path_cutoff")
        result.append(
            forensics.PriceInterval(
                **_base(
                    candidate,
                    candidate_pin,
                    evidence_id=f"path_{index}",
                    source_sha256=pin,
                    recorded_at=received,
                ),
                started_at=started,
                ended_at=ended,
                open=_number(row[2], positive=True),
                high=_number(row[3], positive=True),
                low=_number(row[4], positive=True),
                close=_number(row[5], positive=True),
            )
        )
    return tuple(result)


def _reconstruct(
    submission_bytes,
    *,
    expected_submission_sha256,
    account_bytes,
    expected_account_sha256,
    expected_account_plan_sha256,
    attribution_bytes,
    expected_attribution_sha256,
    holding_path_bytes,
):
    report = post_submit.verify_submission_report(
        submission_bytes, expected_submission_sha256
    )
    if report.status != "acknowledged":
        raise ValueError("ack_required")
    account = account_capture.verify_demo_account_packet(
        account_bytes,
        expected_sha256=expected_account_sha256,
        expected_plan_sha256=expected_account_plan_sha256,
    )
    candidate, pin = report.candidate, report.candidate_sha256
    if (
        account.plan.expected_uid != candidate.account_id
        or account.plan.settlement_currency != candidate.settlement_currency
        or not account.plan.history_start
        <= candidate.recorded_at
        <= report.completed_at
        <= account.plan.history_end
    ):
        raise ValueError("account_scope_or_cutoff")
    attribution, orders = _attribution(
        attribution_bytes,
        expected_attribution_sha256,
        report,
        expected_submission_sha256,
        expected_account_sha256,
    )
    fills, flows, source_links, unbound_fills, bills, trades = [], [], [], [], {}, set()
    for observation in account.observations:
        stream = account_capture.stream_family(observation.request.stream)
        if stream not in {"fills_history", "bills_archive"}:
            continue
        for record in observation.rows:
            row = json.loads(record.canonical_json)
            if stream == "bills_archive":
                bills[record.row_id] = (row, observation)
                continue
            binding = orders.get(row["ordId"])
            if binding is None:
                if row["instId"] == candidate.instrument_id:
                    unbound_fills.append(record.row_id)
                continue
            if (
                row["instId"] != candidate.instrument_id
                or row.get("clOrdId") != binding["client_order_id"]
                or row["posSide"] not in {"net", candidate.direction}
            ):
                raise ValueError("fill_scope")
            trade_id = row["tradeId"]
            if trade_id in trades:
                raise ValueError("duplicate_trade")
            trades.add(trade_id)
            matched, generated = (
                _millisecond(row.get("fillTime")),
                _millisecond(row.get("ts")),
            )
            if (
                not report.submit_started_at
                <= matched
                <= generated
                <= account.plan.history_end
            ):
                raise ValueError("fill_clock")
            base = _base(
                candidate,
                pin,
                evidence_id="fill_" + record.row_id,
                source_sha256=observation.body_sha256,
                recorded_at=observation.body_completed_at,
            )
            fill = forensics.FillEvent(
                **base,
                order_id=row["ordId"],
                occurred_at=matched,
                role=binding["role"],
                side=row["side"],
                contracts=_number(row.get("fillSz"), positive=True),
                price=_number(row.get("fillPx"), positive=True),
                exit_reason="unknown" if binding["role"] == "exit" else None,
            )
            fills.append(fill)
            flows.append(
                forensics.CashFlowEvent(
                    **(base | {"evidence_id": "fee_" + record.row_id}),
                    occurred_at=matched,
                    kind="fee",
                    amount=_number(row.get("fee")),
                    currency=row["feeCcy"],
                    fill_id=fill.evidence_id,
                )
            )
            source_links.append(
                {
                    "evidence_id": fill.evidence_id,
                    "bill_id": record.row_id,
                    "trade_id": trade_id,
                    "body_sha256": observation.body_sha256,
                    "observation_sha256": observation.receipt_sha256,
                }
            )
    # Account history pages are descending. Convert to a documented FIFO order,
    # never change event times. Equal-time mixed roles remain rejected downstream.
    fills.sort(key=lambda item: (item.occurred_at, item.evidence_id))
    flows.sort(key=lambda item: (item.occurred_at, item.evidence_id))
    claims = []
    for identifier in attribution["funding_bill_ids"]:
        row, observation = bills[identifier]
        if (
            row.get("instId") != candidate.instrument_id
            or row.get("type") != "8"
            or row.get("subType") not in {"173", "174"}
        ):
            raise ValueError("funding_bill_kind")
        amount = _number(row.get("balChg"))
        currency = row.get("ccy")
        if (
            type(currency) is not str
            or re.fullmatch(r"[A-Z0-9]{1,16}", currency) is None
        ):
            raise ValueError("funding_currency")
        generated = _millisecond(row.get("ts"))
        if not report.submit_started_at <= generated <= account.plan.history_end:
            raise ValueError("funding_bill_clock")
        claims.append(
            {
                "bill_id": identifier,
                "signed_balance_change": str(amount),
                "currency": currency,
                "source_timestamp": generated.isoformat(),
                "recorded_at": observation.body_completed_at.isoformat(),
                "body_sha256": observation.body_sha256,
                "observation_sha256": observation.receipt_sha256,
                "effective_accrual_at": None,
            }
        )
    path = _path(
        holding_path_bytes,
        attribution["holding_path_sha256"],
        candidate,
        account.completed_at,
        account.plan.history_end,
    )
    coverage = tuple(
        forensics.StreamCoverage(
            **_base(
                candidate,
                pin,
                evidence_id="coverage_" + stream,
                source_sha256=expected_attribution_sha256,
                recorded_at=account.completed_at,
            ),
            stream=stream,
            status="partial",
            started_at=candidate.recorded_at,
            ended_at=account.plan.history_end,
            reason=(
                "funding_effective_accrual_and_ingestion_unverified"
                if stream == "funding"
                else "raw_recorded_sources_do_not_prove_complete_ingestion"
            ),
        )
        for stream in ("fills", "fees", "funding", "path")
    )
    analysis = forensics.analyze_trade(
        forensics.ForensicsInput(
            candidate=candidate,
            fills=tuple(fills),
            cashflows=tuple(flows),
            path=path,
            coverage=coverage,
            observed_at=account.completed_at,
            purpose=attribution["purpose"],
        ),
        expected_candidate_sha256=pin,
    )
    result = {
        "schema_version": "ctcc.raw_trade_forensics.v1",
        "submission_report_sha256": expected_submission_sha256,
        "account_packet_sha256": expected_account_sha256,
        "account_plan_sha256": expected_account_plan_sha256,
        "attribution_sha256": expected_attribution_sha256,
        "holding_path_sha256": attribution["holding_path_sha256"],
        "source_links": sorted(source_links, key=lambda item: item["evidence_id"]),
        "funding_bill_claims": sorted(claims, key=lambda item: item["bill_id"]),
        "unattributed_same_instrument_fill_ids": sorted(unbound_fills),
        "unattributed_same_instrument_funding_bill_ids": sorted(
            key
            for key, (row, _) in bills.items()
            if row.get("instId") == candidate.instrument_id
            and row.get("type") == "8"
            and key not in attribution["funding_bill_ids"]
        ),
        "analysis": analysis.model_dump(mode="json", round_trip=True),
        "source_authenticity_verified": False,
        "execution_authority": False,
        "funding_accrual_verified": False,
        "ingestion_completeness_verified": False,
        "protection_verified": False,
        "real_trade_sample_verified": False,
    }
    raw = _wire(result)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt_bound")
    return RawForensicsReplay(
        analysis=analysis, canonical_receipt=raw, receipt_sha256=_sha(raw)
    )


def reconstruct_trade_forensics(
    submission_bytes: bytes,
    *,
    expected_submission_sha256: str,
    account_bytes: bytes,
    expected_account_sha256: str,
    expected_account_plan_sha256: str,
    attribution_bytes: bytes,
    expected_attribution_sha256: str,
    holding_path_bytes: bytes | None = None,
) -> RawForensicsReplay:
    """Recompute from retained raw bytes. No caller-supplied result is accepted.

    The generated canonical receipt is returned in memory for the existing owner
    ledger; this function performs no persistence or remote reporting. Missing
    required source fields fail closed instead of inventing zero or timestamps.
    """
    try:
        return _reconstruct(
            submission_bytes,
            expected_submission_sha256=expected_submission_sha256,
            account_bytes=account_bytes,
            expected_account_sha256=expected_account_sha256,
            expected_account_plan_sha256=expected_account_plan_sha256,
            attribution_bytes=attribution_bytes,
            expected_attribution_sha256=expected_attribution_sha256,
            holding_path_bytes=holding_path_bytes,
        )
    except Exception:  # noqa: BLE001 -- do not expose raw private source errors
        raise RawForensicsError("raw_forensics_reconstruction_invalid") from None


def verify_raw_forensics_receipt(
    receipt_bytes: bytes,
    expected_receipt_sha256: str,
    submission_bytes: bytes,
    **source_arguments,
) -> RawForensicsReplay:
    """Replay all original sources, not just hash a caller's computed metrics."""
    try:
        if (
            type(receipt_bytes) is not bytes
            or not 0 < len(receipt_bytes) <= MAX_RECEIPT_BYTES
            or type(expected_receipt_sha256) is not str
            or _DIGEST.fullmatch(expected_receipt_sha256) is None
            or _sha(receipt_bytes) != expected_receipt_sha256
        ):
            raise ValueError("receipt_pin")
        result = reconstruct_trade_forensics(submission_bytes, **source_arguments)
        if result.canonical_receipt != receipt_bytes:
            raise ValueError("receipt_replay")
        return result
    except Exception:  # noqa: BLE001 -- even self-consistently rehashed claims replay
        raise RawForensicsError("raw_forensics_receipt_invalid") from None
