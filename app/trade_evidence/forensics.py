"""Pure, replayable trade forensics for explicitly attributed linear Demo fills.

No exchange, database, filesystem, clock, order or calibration side effects.
Inputs are source claims, not authenticated exchange evidence. An upstream
report store must retain the original candidate hash; hashes alone cannot stop
a caller replacing a report and all its evidence together.

FIFO matches partial closes. Cash flows are signed credits (+) / debits (-),
never inferred from order aggregates. PnL excludes unconverted currencies.
MFE/MAE are *gross trade cash excursions*: realized FIFO PnL plus the remaining
inventory marked within each fully covered interval. They are not price-only
excursions or an assertion about intrabar order. Intervals crossing any fill
cannot establish these extrema. R always uses the original planned cash risk.
Exact rational operands accompany every Decimal presentation, including
nonterminating VWAP/R ratios. No result grants authority or proves an edge.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timezone
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import TzInfo

MAX_BYTES = 4 * 1024 * 1024
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,16}$")]
Number = Annotated[Decimal, Field(max_digits=40, decimal_places=20)]
Positive = Annotated[Decimal, Field(gt=0, max_digits=40, decimal_places=20)]
Text = Annotated[str, Field(min_length=1, max_length=512)]
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
Category = Literal[
    "Wrong Direction",
    "Early Entry",
    "Late Entry",
    "Chasing Entry",
    "Stop Too Tight",
    "Liquidity Sweep",
    "Target Unrealistic",
    "Regime Mismatch",
    "Execution Error",
    "Strategy Edge Failure",
    "Good Execution",
]


class ForensicsError(ValueError):
    """Bounded local error codes; never interpolate untrusted evidence."""


def _utc(value):
    if type(value) is not datetime:
        raise ForensicsError("exact_datetime_required")
    if (
        type(value.tzinfo) is not timezone
        and type(value.tzinfo) is not ZoneInfo
        and type(value.tzinfo) is not TzInfo
    ):
        raise ForensicsError("known_aware_timezone_required")
    return value.astimezone(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
        str_strip_whitespace=False,
    )


class TradeCandidate(_Model):
    report_id: Identifier
    instrument_id: Annotated[
        str, Field(pattern=r"^[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP$")
    ]
    account_id: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    environment: Literal["demo"] = "demo"
    direction: Literal["long", "short"]
    settlement_currency: Currency
    contract_kind: Literal["linear_quote_settled"] = "linear_quote_settled"
    contract_value_base: Positive
    planned_contracts: Positive
    entry: Positive
    stop_loss: Positive
    take_profit: Positive
    recorded_at: datetime
    original_evidence_sha256: Digest
    original_source_sha256: Digest
    entry_window_start: datetime | None = None
    entry_deadline: datetime | None = None
    entry_zone_low: Positive | None = None
    entry_zone_high: Positive | None = None
    max_adverse_slippage_bps: Number | None = None

    _times = field_validator("recorded_at", "entry_window_start", "entry_deadline")(
        lambda value: None if value is None else _utc(value)
    )

    @model_validator(mode="after")
    def geometry(self):
        ordered = self.stop_loss < self.entry < self.take_profit
        if self.direction == "short":
            ordered = self.take_profit < self.entry < self.stop_loss
        if not ordered or self.instrument_id.split("-")[1] != self.settlement_currency:
            raise ForensicsError("candidate_geometry_or_settlement_invalid")
        if (self.entry_zone_low is None) != (self.entry_zone_high is None):
            raise ForensicsError("incomplete_entry_zone")
        if self.entry_zone_low is not None and not (
            self.entry_zone_low <= self.entry <= self.entry_zone_high
        ):
            raise ForensicsError("candidate_outside_original_zone")
        if (
            self.max_adverse_slippage_bps is not None
            and self.max_adverse_slippage_bps < 0
        ):
            raise ForensicsError("invalid_slippage_policy")
        start = self.entry_window_start or self.recorded_at
        if start < self.recorded_at or (
            self.entry_deadline and self.entry_deadline <= start
        ):
            raise ForensicsError("invalid_entry_window")
        return self


class _BoundEvidence(_Model):
    evidence_id: Identifier
    report_id: Identifier
    candidate_sha256: Digest
    instrument_id: Annotated[str, Field(min_length=1, max_length=64)]
    account_id: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    environment: Literal["demo"] = "demo"
    source_sha256: Digest
    recorded_at: datetime

    _time = field_validator("recorded_at")(_utc)


class FillEvent(_BoundEvidence):
    """One exchange trade/fill ID, NOT accumulated order quantity or order PnL."""

    order_id: Identifier
    occurred_at: datetime
    role: Literal["entry", "exit"]
    side: Literal["buy", "sell"]
    contracts: Positive
    price: Positive
    exit_reason: (
        Literal["stop_loss", "take_profit", "manual", "liquidation", "other", "unknown"]
        | None
    ) = None
    reference_price: Positive | None = None
    reference_source_sha256: Digest | None = None

    _occurred = field_validator("occurred_at")(_utc)

    @model_validator(mode="after")
    def shape(self):
        if self.occurred_at > self.recorded_at:
            raise ForensicsError("fill_recorded_before_occurrence")
        if (self.role == "entry") != (self.exit_reason is None):
            raise ForensicsError("exit_reason_must_be_explicit")
        if (self.reference_price is None) != (self.reference_source_sha256 is None):
            raise ForensicsError("reference_price_requires_source")
        return self


class CashFlowEvent(_BoundEvidence):
    """Funding occurred_at is its effective accrual, not later bill receipt."""

    occurred_at: datetime
    kind: Literal["fee", "funding"]
    amount: Number
    currency: Currency
    fill_id: Identifier | None = None

    _occurred = field_validator("occurred_at")(_utc)

    @model_validator(mode="after")
    def shape(self):
        if self.occurred_at > self.recorded_at:
            raise ForensicsError("cashflow_recorded_before_occurrence")
        if (self.kind == "fee") != (self.fill_id is not None):
            raise ForensicsError("fee_requires_fill_funding_must_not_alias_fill")
        return self


class StreamCoverage(_BoundEvidence):
    """Explicit upstream pagination/reconciliation claim, not proof of absence."""

    stream: Literal["fills", "fees", "funding", "path"]
    status: Literal["complete", "partial", "unknown"]
    started_at: datetime
    ended_at: datetime
    reason: Text | None = None

    _times = field_validator("started_at", "ended_at")(_utc)

    @model_validator(mode="after")
    def interval(self):
        if not self.started_at <= self.ended_at <= self.recorded_at:
            raise ForensicsError("invalid_coverage_time")
        if self.status != "complete" and self.reason is None:
            raise ForensicsError("incomplete_coverage_requires_reason")
        return self


class PriceInterval(_BoundEvidence):
    """Complete [start, end) OHLC; terminal fill marks are evaluated separately.

    No interpolation/clipping at fills. A fill exactly at end can be the next
    observed trade price, outside the preceding half-open interval's extrema.
    """

    started_at: datetime
    ended_at: datetime
    open: Positive
    high: Positive
    low: Positive
    close: Positive
    price_basis: Literal["last_trade"] = "last_trade"

    _times = field_validator("started_at", "ended_at")(_utc)

    @model_validator(mode="after")
    def geometry(self):
        if not self.started_at < self.ended_at <= self.recorded_at:
            raise ForensicsError("invalid_path_time")
        if self.low > min(self.open, self.close) or self.high < max(
            self.open, self.close
        ):
            raise ForensicsError("invalid_path_ohlc")
        return self


class AttributionFact(_BoundEvidence):
    """An attributed source assessment, never an authenticated root cause.

    References must resolve to packet fill/cashflow/path/coverage evidence.
    Free text is retained as a claim and is never executed or parsed as a rule.
    """

    category: Category
    statement: Text
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=16)


class ForensicsInput(_Model):
    candidate: TradeCandidate
    fills: tuple[FillEvent, ...] = Field(max_length=512)
    cashflows: tuple[CashFlowEvent, ...] = Field(max_length=1024)
    path: tuple[PriceInterval, ...] = Field(max_length=4096)
    coverage: tuple[StreamCoverage, ...] = Field(min_length=4, max_length=4)
    facts: tuple[AttributionFact, ...] = Field(default=(), max_length=32)
    observed_at: datetime
    purpose: Literal["synthetic_test", "observed"]

    _time = field_validator("observed_at")(_utc)


class ExactMetric(_Model):
    """Numerator/denominator are authoritative; value is a 160-digit rendering."""

    value: Annotated[Decimal, Field(max_digits=700, decimal_places=400)] | None
    numerator: Annotated[str, Field(pattern=r"^-?[0-9]{1,300}$")] | None
    denominator: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,299}$")] | None
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    unknown_reason: Code | None

    @model_validator(mode="after")
    def complete_value(self):
        if self.value is None:
            if (
                self.numerator is not None
                or self.denominator is not None
                or self.unknown_reason is None
            ):
                raise ForensicsError("invalid_unknown_metric")
        elif (
            self.numerator is None
            or self.denominator is None
            or self.unknown_reason is not None
        ):
            raise ForensicsError("incomplete_exact_metric")
        elif (
            _decimal(Fraction(int(self.numerator), int(self.denominator))) != self.value
        ):
            raise ForensicsError("metric_rational_mismatch")
        return self


class ForensicObservation(_Model):
    category: Category
    status: Literal[
        "computed_fact", "source_claim", "insufficient_single_trade_evidence"
    ]
    code: Code
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=512)
    statement: Text
    causality_established: Literal[False] = False

    @field_validator("causality_established", mode="before")
    @classmethod
    def no_cause(cls, value):
        if type(value) is not bool or value is not False:
            raise ForensicsError("causality_not_established")
        return value


class TradeForensicsResult(_Model):
    schema_version: Literal["ctcc_trade_forensics_v1"] = "ctcc_trade_forensics_v1"
    inputs: ForensicsInput
    candidate_sha256: Digest
    input_sha256: Digest
    result_sha256: Digest
    position_status: Literal["flat", "partially_open", "unfilled", "unknown"]
    metrics_complete: bool
    actual_fill: ExactMetric
    exit_price: ExactMetric
    exit_reason: Literal[
        "stop_loss", "take_profit", "manual", "liquidation", "other", "mixed", "unknown"
    ]
    entry_contracts: ExactMetric
    exit_contracts: ExactMetric
    remaining_contracts: ExactMetric
    entry_slippage_bps: ExactMetric
    exit_slippage_bps: ExactMetric
    fees: ExactMetric
    funding: ExactMetric
    gross_realized_pnl: ExactMetric
    net_cash_pnl_to_date: ExactMetric
    pnl: ExactMetric
    mfe: ExactMetric
    mae: ExactMetric
    max_favorable_r: ExactMetric
    max_adverse_r: ExactMetric
    original_risk: ExactMetric
    observations: tuple[ForensicObservation, ...] = Field(max_length=40)
    limitations: tuple[Code, ...] = Field(max_length=24)
    primary_cause: Literal["unknown"] = "unknown"
    source_authenticity_verified: Literal[False] = False
    account_evidence_authenticated: Literal[False] = False
    execution_authority: Literal[False] = False
    strategy_edge_established: Literal[False] = False
    calibration_sample_sufficient: Literal[False] = False

    @field_validator(
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "execution_authority",
        "strategy_edge_established",
        "calibration_sample_sufficient",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ForensicsError("forensics_cannot_grant_authority")
        return value


_NESTED = {
    ForensicsInput: {
        "candidate": TradeCandidate,
        "fills": (FillEvent,),
        "cashflows": (CashFlowEvent,),
        "path": (PriceInterval,),
        "coverage": (StreamCoverage,),
        "facts": (AttributionFact,),
    },
    TradeForensicsResult: {
        "inputs": ForensicsInput,
        "observations": (ForensicObservation,),
    },
}
for _name in (
    "actual_fill",
    "exit_price",
    "entry_contracts",
    "exit_contracts",
    "remaining_contracts",
    "entry_slippage_bps",
    "exit_slippage_bps",
    "fees",
    "funding",
    "gross_realized_pnl",
    "net_cash_pnl_to_date",
    "pnl",
    "mfe",
    "mae",
    "max_favorable_r",
    "max_adverse_r",
    "original_risk",
):
    _NESTED[TradeForensicsResult][_name] = ExactMetric
_TYPES = (
    TradeCandidate,
    FillEvent,
    CashFlowEvent,
    StreamCoverage,
    PriceInterval,
    AttributionFact,
    ForensicsInput,
    ExactMetric,
    ForensicObservation,
    TradeForensicsResult,
)


def _guard(value, expected=None, depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > 12 or budget[0] > 180000:
        raise ForensicsError("forensics_input_bound_exceeded")
    kind = type(value)
    if expected is not None and kind is not expected:
        raise ForensicsError("exact_forensics_model_required")
    if any(kind is item for item in _TYPES):
        raw = object.__getattribute__(value, "__dict__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        supplied = object.__getattribute__(value, "__pydantic_fields_set__")
        if (
            type(raw) is not dict
            or type(supplied) is not set
            or extra is not None
            or private is not None
        ):
            raise ForensicsError("dirty_forensics_model")
        if len(raw) != len(kind.model_fields) or len(supplied) > len(raw):
            raise ForensicsError("dirty_forensics_model")
        if any(type(key) is not str for key in (*raw, *supplied)):
            raise ForensicsError("dirty_forensics_model")
        if set(raw) != set(kind.model_fields) or not supplied <= set(raw):
            raise ForensicsError("dirty_forensics_model")
        for key, part in raw.items():
            target = _NESTED.get(kind, {}).get(key)
            if type(target) is tuple:
                if type(part) is not tuple or len(part) > 4096:
                    raise ForensicsError("bounded_exact_tuple_required")
                for child in part:
                    _guard(child, target[0], depth + 1, budget)
            else:
                _guard(part, target, depth + 1, budget)
    elif kind is tuple:
        if len(value) > 4096:
            raise ForensicsError("bounded_exact_tuple_required")
        for part in value:
            _guard(part, depth=depth + 1, budget=budget)
    elif kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 400
            or abs(value.as_tuple().exponent) > 400
        ):
            raise ForensicsError("invalid_forensics_decimal")
    elif kind is datetime:
        _utc(value)
    elif kind is str:
        if len(value) > 512:
            raise ForensicsError("forensics_text_bound_exceeded")
    elif kind is not bool and value is not None:
        raise ForensicsError("unsupported_forensics_value")


def _plain(value):
    if any(type(value) is item for item in _TYPES):
        return {key: _plain(part) for key, part in value.__dict__.items()}
    if type(value) is tuple:
        return tuple(_plain(part) for part in value)
    return value


def _copy(value, kind):
    _guard(value, kind)
    try:
        return kind.model_validate(_plain(value), strict=True)
    except (ValueError, TypeError, OverflowError):
        raise ForensicsError("invalid_forensics_contract") from None


def _wire(value, *, omit=None):
    data = value.model_dump(mode="json", round_trip=True)
    if omit:
        data.pop(omit)
    raw = json.dumps(
        data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(raw) > MAX_BYTES:
        raise ForensicsError("forensics_bytes_bound_exceeded")
    return raw


def _hash(value):
    return hashlib.sha256(_wire(value)).hexdigest()


def candidate_sha256(value: TradeCandidate) -> str:
    return _hash(_copy(value, TradeCandidate))


def _pin(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ForensicsError("invalid_expected_candidate_hash")
    return value


def _decimal(value):
    with localcontext(Context(prec=160)):
        return Decimal(value.numerator) / Decimal(value.denominator)


def _metric(value, unit, reason=None):
    if value is None:
        return ExactMetric(
            value=None,
            numerator=None,
            denominator=None,
            unit=unit,
            unknown_reason=reason,
        )
    value = Fraction(value)
    return ExactMetric(
        value=_decimal(value),
        numerator=str(value.numerator),
        denominator=str(value.denominator),
        unit=unit,
        unknown_reason=None,
    )


def _inputs(packet, expected):
    packet = _copy(packet, ForensicsInput)
    candidate = packet.candidate
    if candidate_sha256(candidate) != _pin(expected):
        raise ForensicsError("original_candidate_hash_mismatch")
    if packet.observed_at < candidate.recorded_at:
        raise ForensicsError("observation_before_candidate")
    if tuple(item.stream for item in packet.coverage) != (
        "fills",
        "fees",
        "funding",
        "path",
    ):
        raise ForensicsError("ordered_four_stream_coverage_required")
    identifiers = set()
    evidence = (
        *packet.fills,
        *packet.cashflows,
        *packet.path,
        *packet.coverage,
        *packet.facts,
    )
    for item in evidence:
        if (
            item.report_id,
            item.candidate_sha256,
            item.instrument_id,
            item.account_id,
            item.environment,
        ) != (
            candidate.report_id,
            expected,
            candidate.instrument_id,
            candidate.account_id,
            candidate.environment,
        ):
            raise ForensicsError("evidence_candidate_scope_mismatch")
        if item.evidence_id in identifiers:
            raise ForensicsError("duplicate_or_conflicting_evidence_id")
        identifiers.add(item.evidence_id)
        if item.recorded_at > packet.observed_at:
            raise ForensicsError("future_evidence")
    for rows in (packet.fills, packet.cashflows):
        if any(
            rows[index].occurred_at < rows[index - 1].occurred_at
            for index in range(1, len(rows))
        ):
            raise ForensicsError("event_time_order_invalid")
        if any(item.occurred_at < candidate.recorded_at for item in rows):
            raise ForensicsError("event_precedes_candidate")
    for previous, current in zip(packet.fills, packet.fills[1:], strict=False):
        if current.occurred_at == previous.occurred_at:
            if current.role != previous.role:
                raise ForensicsError("equal_time_mixed_fill_roles_ambiguous")
            # Accounting convention only, not inferred exchange chronology.
            # The caller must supply this order; the evaluator never repairs it.
            if current.evidence_id <= previous.evidence_id:
                raise ForensicsError("equal_time_fill_id_order_required")
    by_id = {item.evidence_id: item for item in packet.fills}
    for item in packet.cashflows:
        if item.kind == "fee" and (
            item.fill_id not in by_id
            or item.occurred_at < by_id[item.fill_id].occurred_at
        ):
            raise ForensicsError("fee_fill_reference_invalid")
    for index, item in enumerate(packet.path):
        if index and item.started_at < packet.path[index - 1].ended_at:
            raise ForensicsError("path_overlap_or_reordering")
    base_ids = identifiers - {item.evidence_id for item in packet.facts}
    recorded_times = {item.evidence_id: item.recorded_at for item in evidence}
    for fact in packet.facts:
        if (
            len(set(fact.evidence_ids)) != len(fact.evidence_ids)
            or not set(fact.evidence_ids) <= base_ids
        ):
            raise ForensicsError("fact_references_unknown_or_duplicate_evidence")
        if any(recorded_times[key] > fact.recorded_at for key in fact.evidence_ids):
            raise ForensicsError("fact_precedes_referenced_evidence")
    _wire(packet)
    return packet


def _inventory(packet):
    candidate = packet.candidate
    sign = 1 if candidate.direction == "long" else -1
    unit = Fraction(candidate.contract_value_base)
    lots = []
    states = []
    realized = Fraction(0)
    entries = exits = entry_value = exit_value = Fraction(0)
    closed = False
    for fill in packet.fills:
        expected_side = (
            "buy"
            if (candidate.direction == "long") == (fill.role == "entry")
            else "sell"
        )
        if fill.side != expected_side:
            raise ForensicsError("fill_side_role_direction_conflict")
        quantity, price = Fraction(fill.contracts), Fraction(fill.price)
        if fill.role == "entry":
            if closed:
                raise ForensicsError("same_report_position_reopened")
            lots.append([quantity, price])
            entries += quantity
            entry_value += quantity * price
        else:
            if quantity > entries - exits:
                raise ForensicsError("exit_exceeds_known_inventory")
            exits += quantity
            exit_value += quantity * price
            while quantity:
                matched = min(quantity, lots[0][0])
                realized += sign * matched * unit * (price - lots[0][1])
                quantity -= matched
                lots[0][0] -= matched
                if not lots[0][0]:
                    lots.pop(0)
            closed = not lots
        states.append((fill.occurred_at, realized, tuple((q, p) for q, p in lots)))
    for flow in packet.cashflows:
        if flow.kind != "funding":
            continue
        if any(flow.occurred_at == at for at, _, _ in states):
            raise ForensicsError("funding_at_fill_time_is_ambiguous")
        prior = [state for state in states if state[0] < flow.occurred_at]
        if not prior or not prior[-1][2]:
            raise ForensicsError("funding_outside_known_holding_period")
    return entries, exits, entry_value, exit_value, realized, states


def _path_excursions(packet, states, fill_complete):
    if not fill_complete:
        return None, None, "fill_coverage_incomplete"
    if not states:
        return None, None, "no_entry_fill"
    if len({state[0] for state in states}) != len(states):
        return None, None, "equal_time_fills_have_unknown_path_order"
    start = states[0][0]
    end = states[-1][0] if not states[-1][2] else packet.observed_at
    cover = packet.coverage[3]
    if cover.status != "complete" or cover.started_at > start or cover.ended_at < end:
        return None, None, "path_coverage_incomplete"
    rows = packet.path
    if not rows or rows[0].started_at != start or rows[-1].ended_at != end:
        return None, None, "path_does_not_match_holding_period"
    if any(rows[i].started_at != rows[i - 1].ended_at for i in range(1, len(rows))):
        return None, None, "path_gap"
    times = {at for at, _, _ in states}
    boundaries = {row.started_at for row in rows} | {rows[-1].ended_at}
    if not times <= boundaries:
        return None, None, "path_crosses_fill_unknown_extrema_order"
    sign = 1 if packet.candidate.direction == "long" else -1
    unit = Fraction(packet.candidate.contract_value_base)
    favorable = adverse = Fraction(0)
    index = -1
    fills_at = {fill.occurred_at: fill.price for fill in packet.fills}
    for row in rows:
        while index + 1 < len(states) and states[index + 1][0] <= row.started_at:
            index += 1
        if index < 0 or not states[index][2]:
            return None, None, "path_outside_open_inventory"
        if (
            row.started_at in fills_at
            and not row.low <= fills_at[row.started_at] <= row.high
        ):
            raise ForensicsError("path_conflicts_with_boundary_fill_price")
        _, realized, lots = states[index]
        for mark in (row.low, row.high):
            pnl = realized + sum(
                (sign * q * unit * (Fraction(mark) - p) for q, p in lots), Fraction(0)
            )
            favorable, adverse = max(favorable, pnl), max(adverse, -pnl)
    # A terminal fill is an observed price point after the last half-open bar.
    # Partial exits (or scale-ins) still leave inventory: mark the entire state,
    # not just realized cash, and never discard that known terminal excursion.
    if states[-1][0] == end:
        _, realized, lots = states[-1]
        mark = Fraction(packet.fills[-1].price)
        terminal = realized + sum(
            (sign * q * unit * (mark - price) for q, price in lots), Fraction(0)
        )
        favorable = max(favorable, terminal)
        adverse = max(adverse, -terminal)
    return favorable, adverse, None


def _observations(packet):
    found = []
    for fact in packet.facts:
        found.append(
            ForensicObservation(
                category=fact.category,
                status="insufficient_single_trade_evidence"
                if fact.category == "Strategy Edge Failure"
                else "source_claim",
                code="attributed_source_assessment",
                evidence_ids=(fact.evidence_id, *fact.evidence_ids),
                statement=fact.statement,
            )
        )
    candidate = packet.candidate
    entries = tuple(item for item in packet.fills if item.role == "entry")
    predicates = (
        (
            "Early Entry",
            "fill_before_original_entry_window",
            lambda item: (
                candidate.entry_window_start is not None
                and item.occurred_at < candidate.entry_window_start
            ),
        ),
        (
            "Late Entry",
            "fill_at_or_after_original_deadline",
            lambda item: (
                candidate.entry_deadline is not None
                and item.occurred_at >= candidate.entry_deadline
            ),
        ),
        (
            "Chasing Entry",
            "adverse_fill_outside_original_zone",
            lambda item: (
                candidate.entry_zone_low is not None
                and (
                    item.price > candidate.entry_zone_high
                    if candidate.direction == "long"
                    else item.price < candidate.entry_zone_low
                )
            ),
        ),
        (
            "Execution Error",
            "entry_exceeds_original_slippage_limit",
            lambda item: (
                candidate.max_adverse_slippage_bps is not None
                and Fraction(1 if candidate.direction == "long" else -1)
                * (Fraction(item.price) - Fraction(candidate.entry))
                / Fraction(candidate.entry)
                * 10000
                > Fraction(candidate.max_adverse_slippage_bps)
            ),
        ),
    )
    for category, code, predicate in predicates:
        ids = tuple(item.evidence_id for item in entries if predicate(item))
        if ids:
            found.append(
                ForensicObservation(
                    category=category,
                    status="computed_fact",
                    code=code,
                    evidence_ids=ids,
                    statement="Observed fill compared with the immutable original plan; root cause is not established.",
                )
            )
    return tuple(found)


def analyze_trade(
    packet: ForensicsInput, *, expected_candidate_sha256: str
) -> TradeForensicsResult:
    """Recompute source-bound metrics; no caller PASS or input outcome is used."""
    packet = _inputs(packet, expected_candidate_sha256)
    candidate = packet.candidate
    currency = candidate.settlement_currency
    covers = {item.stream: item for item in packet.coverage}

    def complete(stream):
        item = covers[stream]
        return (
            item.status == "complete"
            and item.started_at <= candidate.recorded_at
            and item.ended_at >= packet.observed_at
        )

    fill_complete = complete("fills")
    entries, exits, entry_value, exit_value, realized, states = _inventory(packet)
    remaining = entries - exits
    fully_closed = fill_complete and entries > 0 and remaining == 0
    known_fills = None if fill_complete else "fill_coverage_incomplete"
    status = (
        "unknown"
        if not fill_complete
        else "unfilled"
        if not entries
        else "flat"
        if fully_closed
        else "partially_open"
    )
    metrics = {}
    for key, value in (
        ("entry_contracts", entries),
        ("exit_contracts", exits),
        ("remaining_contracts", remaining),
    ):
        metrics[key] = _metric(
            value if fill_complete else None, "contracts", known_fills
        )
    actual_fill = entry_value / entries if entries and fill_complete else None
    exit_price = exit_value / exits if exits and fill_complete else None
    metrics["actual_fill"] = _metric(
        actual_fill, f"{currency}_per_base", known_fills or "no_entry_fill"
    )
    metrics["exit_price"] = _metric(
        exit_price, f"{currency}_per_base", known_fills or "no_exit_fill"
    )
    sign = 1 if candidate.direction == "long" else -1
    slippage = (
        sign
        * (actual_fill - Fraction(candidate.entry))
        / Fraction(candidate.entry)
        * 10000
        if actual_fill is not None
        else None
    )
    metrics["entry_slippage_bps"] = _metric(
        slippage, "bps_adverse_positive", known_fills or "no_entry_fill"
    )
    closing = tuple(fill for fill in packet.fills if fill.role == "exit")
    exit_slip = None
    if (
        fill_complete
        and closing
        and all(fill.reference_price is not None for fill in closing)
    ):
        exit_slip = (
            sum(
                (
                    -sign
                    * (Fraction(fill.price) - Fraction(fill.reference_price))
                    / Fraction(fill.reference_price)
                    * 10000
                    * Fraction(fill.contracts)
                    for fill in closing
                ),
                Fraction(0),
            )
            / exits
        )
    metrics["exit_slippage_bps"] = _metric(
        exit_slip, "bps_adverse_positive", "exit_reference_or_fill_coverage_missing"
    )
    for stream, kind in (("fees", "fee"), ("funding", "funding")):
        flows = tuple(flow for flow in packet.cashflows if flow.kind == kind)
        reason = None
        if not complete(stream):
            reason = f"{stream}_coverage_incomplete"
        elif any(flow.currency != currency for flow in flows):
            reason = f"{stream}_currency_unconverted"
        metrics[stream] = _metric(
            sum((Fraction(flow.amount) for flow in flows), Fraction(0))
            if reason is None
            else None,
            currency,
            reason,
        )
    metrics["gross_realized_pnl"] = _metric(
        realized if fill_complete else None,
        currency,
        known_fills,
    )
    net = None
    if fill_complete and all(
        metrics[key].value is not None for key in ("fees", "funding")
    ):
        net = realized + sum(
            (Fraction(flow.amount) for flow in packet.cashflows), Fraction(0)
        )
    metrics["net_cash_pnl_to_date"] = _metric(
        net, currency, "fills_or_costs_incomplete"
    )
    metrics["pnl"] = _metric(
        net if fully_closed else None,
        currency,
        "position_not_closed" if not fully_closed else "cost_coverage_incomplete",
    )
    favorable, adverse, path_reason = _path_excursions(packet, states, fill_complete)
    risk = (
        Fraction(candidate.planned_contracts)
        * Fraction(candidate.contract_value_base)
        * abs(Fraction(candidate.entry) - Fraction(candidate.stop_loss))
    )
    metrics["original_risk"] = _metric(risk, currency)
    metrics["mfe"], metrics["mae"] = (
        _metric(favorable, currency, path_reason),
        _metric(adverse, currency, path_reason),
    )
    metrics["max_favorable_r"] = _metric(
        favorable / risk if favorable is not None else None, "original_R", path_reason
    )
    metrics["max_adverse_r"] = _metric(
        adverse / risk if adverse is not None else None, "original_R", path_reason
    )
    reasons = {fill.exit_reason for fill in closing}
    exit_reason = (
        "unknown"
        if not reasons or not fill_complete
        else next(iter(reasons))
        if len(reasons) == 1
        else "mixed"
    )
    limitations = [
        "upstream_evidence_and_coverage_are_unauthenticated_claims",
        "single_trade_does_not_establish_causality_or_strategy_edge",
        "path_extrema_do_not_prove_intrabar_event_order",
        "fifo_linear_quote_settled_only",
        "equal_time_same_role_fifo_uses_fill_id_not_exchange_chronology",
        "cashflows_signed_credit_positive_debit_negative",
    ]
    limitations.extend(
        sorted(
            {
                item.unknown_reason
                for item in metrics.values()
                if item.unknown_reason is not None
            }
        )
    )
    if not closing or "unknown" in reasons:
        limitations.append("exit_reason_unconfirmed")
    values = dict(
        inputs=_plain(packet),
        candidate_sha256=expected_candidate_sha256,
        input_sha256=_hash(packet),
        result_sha256="0" * 64,
        position_status=status,
        metrics_complete=fully_closed
        and exit_reason != "unknown"
        and "unknown" not in reasons
        and all(value.value is not None for value in metrics.values()),
        exit_reason=exit_reason,
        observations=tuple(_plain(item) for item in _observations(packet)),
        limitations=tuple(limitations),
        **{key: _plain(value) for key, value in metrics.items()},
    )
    result = TradeForensicsResult.model_validate(values, strict=True)
    values["result_sha256"] = hashlib.sha256(
        _wire(result, omit="result_sha256")
    ).hexdigest()
    return TradeForensicsResult.model_validate(values, strict=True)


def validate_forensics(
    value: TradeForensicsResult, *, expected_candidate_sha256: str
) -> TradeForensicsResult:
    copied = _copy(value, TradeForensicsResult)
    rebuilt = analyze_trade(
        copied.inputs, expected_candidate_sha256=expected_candidate_sha256
    )
    if _wire(copied) != _wire(rebuilt):
        raise ForensicsError("forensics_replay_mismatch")
    return rebuilt


def freeze_forensics(
    value: TradeForensicsResult, *, expected_candidate_sha256: str
) -> bytes:
    return _wire(
        validate_forensics(value, expected_candidate_sha256=expected_candidate_sha256)
    )


def verify_forensics(
    payload: bytes, sha256: str, *, expected_candidate_sha256: str
) -> TradeForensicsResult:
    """Bytes-only canonical replay. Hashes authenticate neither source nor fills."""
    if type(payload) is not bytes or len(payload) > MAX_BYTES or not payload:
        raise ForensicsError("bounded_forensics_bytes_required")
    if hashlib.sha256(payload).hexdigest() != _pin(sha256):
        raise ForensicsError("forensics_payload_hash_mismatch")

    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ForensicsError("duplicate_forensics_json_key")
            output[key] = value
        return output

    try:
        parsed = json.loads(
            payload,
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ForensicsError("nonfinite_forensics_json")
            ),
        )
        canonical = json.dumps(
            parsed,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if canonical != payload:
            raise ForensicsError("noncanonical_forensics_json")
        result = TradeForensicsResult.model_validate_json(payload, strict=True)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ForensicsError("invalid_forensics_json") from None
    return validate_forensics(
        result, expected_candidate_sha256=expected_candidate_sha256
    )
