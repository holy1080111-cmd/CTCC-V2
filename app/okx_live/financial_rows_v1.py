"""Live source-row arithmetic. Public results are DATA, never risk authority.

Fills use fillTime. Funding payments use their actual balance-update timestamp,
explicitly not an invented accrual timestamp. Trade bills corroborate fills and
are never added twice. Closed-position aggregates retain actual source funding.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.exchange.okx import live_account_history_page_v1 as history
from app.okx_live import trusted_source_bridge_v1 as bridge
from app.trade_qualification.account_materializer import _balance
from app.trade_qualification.account_positions_history_v1 import _row as _position_row

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,39})(?:\.[0-9]{1,20})?\Z")
_MS = re.compile(r"[1-9][0-9]{12}\Z")
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_INSTRUMENT = re.compile(r"[A-Z0-9]{1,20}-USDT-SWAP\Z")


class LiveFinancialRowsError(ValueError):
    pass


def _need(condition, code="live_financial_rows_unavailable"):
    if condition is not True:
        raise LiveFinancialRowsError(code) from None


def _number(value):
    _need(type(value) is str and _DECIMAL.fullmatch(value) is not None)
    return Fraction(Decimal(value))


def _milliseconds(value):
    _need(type(value) is str and _MS.fullmatch(value) is not None)
    return int(value)


def _identifier(value):
    _need(type(value) is str and _ID.fullmatch(value) is not None)
    return value


def _product(row):
    _need(
        row.get("instType") == "SWAP"
        and type(row.get("instId")) is str
        and _INSTRUMENT.fullmatch(row["instId"]) is not None,
        "live_financial_product_or_currency_unsupported",
    )


def _locators(raw, locators):
    _need(type(locators) in (tuple, list) and 0 < len(locators) <= 128)
    result = []
    for item in locators:
        _need(type(item) in (tuple, list) and len(item) == 5)
        _need(
            type(item[0]) is int
            and 0 <= item[0] < 1024
            and type(item[1]) is int
            and 0 <= item[1] < 256
        )
        _need(
            all(
                type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None
                for value in item[2:]
            )
            and item[4] == sha(raw)
        )
        result.append(tuple(item))
    return tuple(result)


@dataclass(frozen=True, slots=True, repr=False)
class LiveFillCashflowDataV1:
    bill_id: str
    trade_id: str
    order_id: str
    instrument_id: str
    position_side: str
    subtype: str
    execution_ms: int
    generated_ms: int
    contracts: Fraction
    gross_pnl: Fraction
    signed_fee: Fraction
    source_row_json: bytes
    source_locators: tuple
    bill_row_json: bytes
    bill_locators: tuple


@dataclass(frozen=True, slots=True, repr=False)
class LiveFundingPaymentDataV1:
    bill_id: str
    instrument_id: str
    balance_updated_ms: int
    signed_payment: Fraction
    source_row_json: bytes
    source_locators: tuple


@dataclass(frozen=True, slots=True, repr=False)
class LivePositionClosureDataV1:
    identity_sha256: str
    instrument_id: str
    position_side: str
    direction: str
    created_ms: int
    updated_ms: int
    gross_pnl: Fraction
    signed_fee: Fraction
    settled_funding: Fraction
    net_pnl: Fraction
    source_row_json: bytes
    source_locators: tuple


@dataclass(frozen=True, slots=True, repr=False)
class LiveFinancialRowsDataV1:
    source_revision_sha256: str
    account_uid: str
    main_uid: str
    credential_session_sha256: str
    observed_at: datetime
    available_at: datetime
    history_start: datetime
    cutoff: datetime
    equity: Decimal
    available_margin: Decimal
    fills: tuple[LiveFillCashflowDataV1, ...]
    funding_payments: tuple[LiveFundingPaymentDataV1, ...]
    closed_positions: tuple[LivePositionClosureDataV1, ...]
    principal_transfers: tuple
    receipt_json: bytes


def _partition(selector):
    return (
        selector.kind,
        selector.inst_type,
        selector.begin_ms,
        selector.end_ms,
        selector.ord_type,
        selector.state,
    )


def _original_rows(view):
    """Retain overlaps and their locators; conflicts never disappear by dedup."""
    tail = decode(view["financial_tail_json"], maximum=16384)
    expected = (
        {_partition(item) for item in bridge._selectors(view["plan"])}
        | {
            _partition(
                history.select_live_account_history_page_v1(
                    "fills_recent",
                    begin_ms=tail["begin_ms"],
                    end_ms=tail["end_ms"],
                    inst_type=product,
                )
            )
            for product in history.HISTORY_INST_TYPES
        }
        | {
            _partition(
                history.select_live_account_history_page_v1(
                    "bills_recent", begin_ms=tail["begin_ms"], end_ms=tail["end_ms"]
                )
            )
        }
    )
    if "history_selectors" in view:
        selectors = view["history_selectors"]
        _need(type(selectors) is tuple and 1 <= len(selectors) <= 128)
        expected = {_partition(selector) for selector in selectors}
        _need(len(expected) == len(selectors))
    groups, families = {}, {"fills": {}, "bills": {}, "positions": {}}
    for page_index, page in enumerate(view["history_pages"]):
        _need(type(page) is bridge.LiveSourcePageDataV1)
        selector = page.selector
        partition = _partition(selector)
        _need(partition in expected)
        checked, identities, _, terminal = history.checked_live_account_history_rows_v1(
            page.raw_body, selector
        )
        _need(
            sha(page.raw_body) == page.raw_sha256
            and sha(checked) == page.canonical_sha256
        )
        previous = groups.get(partition)
        _need(
            (previous is None and selector.after is None)
            or (
                previous is not None
                and previous[0] is False
                and selector.after == previous[1]
            )
        )
        groups[partition] = (terminal, identities[-1] if identities else None)
        kind = (
            "fills"
            if selector.kind.startswith("fills_")
            else "bills"
            if selector.kind.startswith("bills_")
            else "positions"
            if selector.kind == "positions_history"
            else None
        )
        if kind is None:
            continue
        for ordinal, row in enumerate(decode(page.raw_body, maximum=1048576)["data"]):
            identity = (
                row.get("billId")
                if kind != "positions"
                else (row.get("posId"), row.get("uTime"))
            )
            raw = canonical(row)
            locator = (
                page_index,
                ordinal,
                page.page_sha256,
                page.custody_sha256,
                sha(raw),
            )
            old = families[kind].get(identity)
            _need(old is None or old[0] == raw, "live_financial_overlap_conflict")
            if old is None:
                families[kind][identity] = (raw, [locator])
            else:
                old[1].append(locator)
    _need(
        set(groups) == expected and all(value[0] is True for value in groups.values()),
        "live_financial_partition_incomplete",
    )
    return families


def _replay_fill_pair_v1(raw, locators, bill_raw, bill_locators, *, cutoff_ms):
    row, mirror = decode(raw, maximum=65536), decode(bill_raw, maximum=65536)
    _need(canonical(row) == raw and canonical(mirror) == bill_raw)
    _product(row)
    _need(row.get("feeCcy") == "USDT" and row.get("subType") in ("3", "4", "5", "6"))
    side = row.get("posSide")
    _need(side in ("net", "long", "short"))
    executed, generated = (
        _milliseconds(row.get("fillTime")),
        _milliseconds(row.get("ts")),
    )
    _need(executed <= generated <= cutoff_ms)
    quantity, gross, fee = (
        _number(row.get("fillSz")),
        _number(row.get("fillPnl")),
        _number(row.get("fee")),
    )
    _need(quantity > 0 and (row["subType"] not in ("3", "4") or gross == 0))
    _need(mirror.get("type") == "2" and mirror.get("ccy") == "USDT")
    for name in (
        "billId",
        "instId",
        "instType",
        "ordId",
        "tradeId",
        "fillTime",
        "subType",
    ):
        _need(mirror.get(name) == row.get(name), "live_financial_fill_bill_conflict")
    _need(
        _number(mirror.get("pnl")) == gross
        and _number(mirror.get("fee")) == fee
        and _number(mirror.get("sz")) == quantity
    )
    _need(_milliseconds(mirror.get("ts")) <= cutoff_ms)
    return LiveFillCashflowDataV1(
        _identifier(row.get("billId")),
        _identifier(row.get("tradeId")),
        _identifier(row.get("ordId")),
        row["instId"],
        side,
        row["subType"],
        executed,
        generated,
        quantity,
        gross,
        fee,
        raw,
        _locators(raw, locators),
        bill_raw,
        _locators(bill_raw, bill_locators),
    )


def _replay_funding_payment_v1(raw, locators, *, cutoff_ms):
    row = decode(raw, maximum=65536)
    _need(canonical(row) == raw and row.get("ccy") == "USDT")
    _need(
        row.get("type") == "8" and row.get("subType") in ("173", "174"),
        "live_financial_bill_semantics_unsupported",
    )
    _product(row)
    at = _milliseconds(row.get("ts"))
    pnl, fee, change = (_number(row.get(name)) for name in ("pnl", "fee", "balChg"))
    _need(
        at <= cutoff_ms
        and fee == 0
        and change == pnl
        and (pnl < 0 if row["subType"] == "173" else pnl > 0)
    )
    return LiveFundingPaymentDataV1(
        _identifier(row.get("billId")),
        row["instId"],
        at,
        pnl,
        raw,
        _locators(raw, locators),
    )


def _replay_closed_position_v1(raw, locators, *, observed_at, cutoff_ms):
    row = decode(raw, maximum=65536)
    _need(canonical(row) == raw)
    _product(row)
    parsed = _position_row(row, observed_at)
    _need(parsed.u_time_ms <= cutoff_ms)
    _need(
        row.get("ccy") == "USDT"
        and row.get("mgnMode") == "isolated"
        and row.get("type") == "2"
        and row.get("direction") in ("long", "short")
        and not parsed.missing_financial_fields
    )
    gross, fee, paid, penalty, net = (
        _number(row.get(name))
        for name in ("pnl", "fee", "fundingFee", "liqPenalty", "realizedPnl")
    )
    _need(penalty == 0 and net == gross + fee + paid)
    return LivePositionClosureDataV1(
        parsed.identity_sha256,
        row["instId"],
        row["posSide"],
        row["direction"],
        parsed.c_time_ms,
        parsed.u_time_ms,
        gross,
        fee,
        paid,
        net,
        raw,
        _locators(raw, locators),
    )


def _derive_original_financial_rows_v1(view):
    """Pure arithmetic on replayed original pages, not a callable issuer."""
    _need(type(view["financial_tail_json"]) is bytes)
    tail = decode(view["financial_tail_json"], maximum=16384)
    cutoff_ms = _milliseconds(tail["end_ms"])
    _need(tail["environment"] == "live" and tail["partition_count"] == 7)
    bank = _original_rows(view)
    balance_pages = [
        page for page in view["current_pages"] if page.selector.stream == "balance"
    ]
    _need(len(balance_pages) == 1)
    balance = bridge.current._rows(balance_pages[0].raw_body)
    _need(len(balance) == 1)
    equity, available, gap = _balance(balance[0], "USDT")
    _need(gap is None and equity is not None and available is not None)
    _need(
        view["current_readback"].receipt["no_open_positions_or_pending_orders_observed"]
        is True
    )
    detail = balance[0]["details"][0]
    equity_decimal, available_decimal = (
        Decimal(detail["eq"]),
        Decimal(detail["availEq"]),
    )
    fills, funding, transfers = [], [], []
    remaining_bills = dict(bank["bills"])
    for identity, (raw, locators) in bank["fills"].items():
        bill = remaining_bills.pop(identity, None)
        _need(bill is not None, "live_financial_fill_bill_missing")
        fills.append(
            _replay_fill_pair_v1(raw, locators, bill[0], bill[1], cutoff_ms=cutoff_ms)
        )
    for identity, (raw, locators) in remaining_bills.items():
        row = decode(raw, maximum=65536)
        _need(
            row.get("ccy") == "USDT", "live_financial_product_or_currency_unsupported"
        )
        at = _milliseconds(row.get("ts"))
        _need(at <= cutoff_ms)
        if row.get("type") == "1":
            # Principal movements are retained separately, never called PnL.
            transfers.append((raw, tuple(locators)))
            continue
        funding.append(_replay_funding_payment_v1(raw, locators, cutoff_ms=cutoff_ms))
    closed = []
    for raw, locators in bank["positions"].values():
        row = decode(raw, maximum=65536)
        parsed = _position_row(row, utc_from_ns(view["current_completed"].utc_ns))
        if parsed.u_time_ms < bridge._utc_ns(view["plan"].history_start) // 1_000_000:
            continue
        closed.append(
            _replay_closed_position_v1(
                raw,
                locators,
                observed_at=utc_from_ns(view["current_completed"].utc_ns),
                cutoff_ms=cutoff_ms,
            )
        )
    observed = utc_from_ns(balance_pages[0].stamps[3].utc_ns)
    available_at = utc_from_ns(view["current_completed"].utc_ns)
    cutoff = utc_from_ns(cutoff_ms * 1_000_000)
    _need(
        view["plan"].history_start <= observed - timedelta(days=7)
        and cutoff <= observed <= available_at
    )
    receipt = canonical(
        {
            "schema": "ctcc.native_live.financial_rows_data.v1",
            "environment": "live",
            "source_revision_sha256": view["source_revision_sha256"],
            "raw_bank_sha256": view["raw_bank_sha256"],
            "financial_tail_sha256": sha(view["financial_tail_json"]),
            "observed_at": observed.isoformat(),
            "available_at": available_at.isoformat(),
            "history_start": view["plan"].history_start.isoformat(),
            "cutoff": cutoff.isoformat(),
            "fills": [sha(item.source_row_json) for item in fills],
            "trade_bill_mirrors": [sha(item.bill_row_json) for item in fills],
            "funding_payments": [sha(item.source_row_json) for item in funding],
            "closed_positions": [sha(item.source_row_json) for item in closed],
            "principal_transfers": [sha(item[0]) for item in transfers],
            "funding_time_basis": "actual_booked_payment_not_accrual",
            "peak_scope": "prospective_sampled_epoch_required",
            "loss_streak_seed": None,
            "account_complete": False,
            "qualification_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return LiveFinancialRowsDataV1(
        view["source_revision_sha256"],
        view["uid"],
        view["main_uid"],
        view["credential_session_sha256"],
        observed,
        available_at,
        view["plan"].history_start,
        cutoff,
        equity_decimal,
        available_decimal,
        tuple(fills),
        tuple(funding),
        tuple(closed),
        tuple(transfers),
        receipt,
    )


def read_owned_live_financial_rows_data_v1(owner):
    """Original same-task Live source only; the returned DTO has no authority."""
    view = bridge._replay_owned_live_source_v1(owner, key=bridge._SOURCE_KEY)
    value = _derive_original_financial_rows_v1(view)
    bridge._replay_owned_live_source_v1(owner, key=bridge._SOURCE_KEY)
    return value
