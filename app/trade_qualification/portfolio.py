"""Pure, fail-closed portfolio qualification; never execution authority.

Inputs are complete, source-pinned *claims* supplied by a future trusted collector.
Neither a digest nor a completeness flag proves actual account reconciliation.
Amounts and realized PnL share the explicitly named settlement/risk currency.
The engineering loss windows are the current UTC day and rolling seven days;
this does not imply that the user has selected a business reporting timezone.
No settings, wall clock, exchange client, score tier, or continuous-mode bypass
is consulted. Requested contracts, leverage and price geometry are unchanged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, DecimalException, localcontext
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from app.trade_qualification.models import Price, QualificationModel, ReportId, Text

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Amount = Annotated[Decimal, Field(ge=0, max_digits=40, decimal_places=20)]
SignedAmount = Annotated[Decimal, Field(max_digits=40, decimal_places=20)]
Rate = Annotated[Decimal, Field(gt=0, le=1, max_digits=30, decimal_places=20)]
Count = Annotated[int, Field(ge=0, le=2048)]
LimitCount = Annotated[int, Field(ge=1, le=2048)]
Leverage = Annotated[int, Field(ge=1, le=125)]
_PRICE = TypeAdapter(Price)
_AMOUNT = TypeAdapter(Amount)
_LEVERAGE = TypeAdapter(Leverage)
_REPORT = TypeAdapter(ReportId)
_TEXT = TypeAdapter(Text)
_INVALID = (ValueError, TypeError, AttributeError, OverflowError, DecimalException)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an exact aware datetime is required")
    return value.astimezone(UTC)


class ObservedSource(QualificationModel):
    """Actual source observation and receipt times, not fabricated freshness."""

    source_sha256: Digest
    observed_at: datetime
    received_at: datetime

    _utc_times = field_validator("observed_at", "received_at")(_utc)


class EvidenceStamp(ObservedSource):
    # Demo and Live can share an account identifier; a complete Live receipt
    # must never become Demo account evidence merely because the guard is Demo.
    environment: Literal["demo"]
    account_id: Text
    complete: bool


class ContractRiskSpec(ObservedSource):
    instrument_id: Text
    base_currency: Text
    settlement_currency: Text
    contract_kind: Text
    contract_value_currency: Text
    contract_value: Price
    lot_size: Price
    min_contracts: Price
    max_contracts: Price
    max_leverage: Leverage
    # A reviewed collector must supply a known classification for every record.
    correlation_group: Text


class PositionExposure(QualificationModel):
    position_id: Text
    instrument_id: Text
    direction: Literal["long", "short"]
    settlement_currency: Text
    notional: Price
    margin: Amount
    risk_amount: Amount
    correlation_group: Text


class PendingReservation(QualificationModel):
    """Union of exchange pending and local in-flight risk, already deduplicated.

    An uncertain or nominally expired reservation cannot silently disappear from
    this complete ledger. Synchronization and atomic reservation remain runtime
    requirements; this function does not create or consume a reservation.
    """

    reservation_id: Text
    instrument_id: Text
    direction: Literal["long", "short"]
    settlement_currency: Text
    notional: Price
    margin: Amount
    risk_amount: Amount
    correlation_group: Text


class RealizedOutcome(QualificationModel):
    outcome_id: Text
    sequence: Annotated[int, Field(ge=0, le=10**15)]
    instrument_id: Text
    closed_at: datetime
    realized_pnl: SignedAmount

    _utc_time = field_validator("closed_at")(_utc)


class PortfolioRiskSnapshot(QualificationModel):
    """Explicit account-wide evidence in one settlement/risk currency.

    ``available_margin`` is exchange availability before local pending holds.
    Every supplied pending margin is reserved again conservatively if the
    collector cannot separate an exchange hold already included in availability.
    Empty tuples mean known empty *only* alongside complete scoped source stamps;
    they are never substituted for missing balances, positions or history.
    """

    account_id: Text
    settlement_currency: Text
    balance_stamp: EvidenceStamp
    positions_stamp: EvidenceStamp
    history_stamp: EvidenceStamp
    reservations_stamp: EvidenceStamp
    equity: Price
    available_margin: Amount
    peak_equity: Price
    peak_observed_at: datetime
    peak_window_started_at: datetime
    history_start: datetime
    history_end: datetime
    # Persistent ledger state immediately before history_start, not default zero.
    loss_streak_at_history_start: Count
    positions: Annotated[tuple[PositionExposure, ...], Field(max_length=2048)]
    pending_reservations: Annotated[
        tuple[PendingReservation, ...], Field(max_length=2048)
    ]
    loss_history: Annotated[tuple[RealizedOutcome, ...], Field(max_length=2048)]
    position_count: Count
    pending_reservation_count: Count

    _utc_times = field_validator(
        "peak_observed_at", "peak_window_started_at", "history_start", "history_end"
    )(_utc)


class PortfolioRiskPolicy(QualificationModel):
    """Explicit reviewed caps; no defaults are inherited from legacy risk engines."""

    max_age_seconds: Annotated[int, Field(ge=1, le=86400)]
    drawdown_window_started_at: datetime
    risk_per_trade_pct: Rate
    max_daily_loss_pct: Rate
    max_weekly_loss_pct: Rate
    max_drawdown_pct: Rate
    max_consecutive_losses: LimitCount
    max_open_positions: LimitCount
    max_same_direction_positions: LimitCount
    max_correlated_positions: LimitCount
    max_order_notional: Price
    max_order_contracts: Price
    max_portfolio_notional: Price
    max_same_direction_notional: Price
    max_correlated_notional: Price
    max_portfolio_risk_pct: Rate
    max_portfolio_margin_pct: Rate
    max_leverage: Leverage

    _utc_drawdown = field_validator("drawdown_window_started_at")(_utc)


class DemoRiskAuthority(QualificationModel):
    """Snapshot of independent Demo guards, never a request to enable them."""

    stamp: EvidenceStamp
    environment: Text
    demo_enabled: bool
    armed: bool
    order_writes_allowed: bool
    simulated_trading_header: Text
    emergency_stop: bool
    live_trading: bool
    live_order_writes: bool
    live_auto_execution: bool


class PortfolioRiskResult(QualificationModel):
    report_id: ReportId
    instrument_id: Text
    direction: Literal["long", "short"] | None
    passed: bool
    code: Literal["passed", "risk_authority_denied"]
    causes: tuple[Text, ...]
    reason: Text
    requested_contracts: Price | None = None
    requested_leverage: Leverage | None = None
    base_quantity: Decimal | None = Field(default=None, ge=0)
    notional: Decimal | None = Field(default=None, ge=0)
    required_margin: Decimal | None = Field(default=None, ge=0)
    max_loss_amount: Decimal | None = Field(default=None, ge=0)
    risk_pct: Decimal | None = Field(default=None, ge=0)
    daily_loss_pct: Decimal | None = Field(default=None, ge=0)
    weekly_loss_pct: Decimal | None = Field(default=None, ge=0)
    drawdown_pct: Decimal | None = Field(default=None, ge=0)
    evidence_sha256: Digest | None = None
    execution_authority: Literal[False] = False

    @field_validator("execution_authority", mode="before")
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("portfolio qualification cannot grant execution authority")
        return value

    @model_validator(mode="after")
    def consistent_result(self):
        if self.causes != tuple(sorted(set(self.causes))):
            raise ValueError("risk causes must be unique and canonically ordered")
        if self.passed != (self.code == "passed") or self.passed == bool(self.causes):
            raise ValueError("risk status must agree with its causes")
        if self.passed and any(
            value is None
            for value in (
                self.direction,
                self.requested_contracts,
                self.requested_leverage,
                self.base_quantity,
                self.notional,
                self.required_margin,
                self.max_loss_amount,
                self.risk_pct,
                self.daily_loss_pct,
                self.weekly_loss_pct,
                self.drawdown_pct,
                self.evidence_sha256,
            )
        ):
            raise ValueError("passing risk checks require complete computed evidence")
        return self


def _check_tree(value, depth=0):
    if depth > 8:
        raise ValueError("risk evidence nesting exceeds the supported model")
    if isinstance(value, BaseModel):
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("risk evidence includes removed or undeclared fields")
        for item in value.__dict__.values():
            _check_tree(item, depth + 1)
    elif isinstance(value, (tuple, list)):
        if len(value) > 2048:
            raise ValueError("risk evidence exceeds the bounded ledger size")
        for item in value:
            _check_tree(item, depth + 1)


def _revalidate[Model: BaseModel](value: Model, expected: type[Model]) -> Model:
    if type(value) is not expected:
        raise ValueError(f"an exact {expected.__name__} is required")
    _check_tree(value)
    return expected.model_validate(
        value.model_dump(mode="python", round_trip=True, serialize_as_any=True),
        strict=True,
    )


def _canonical(value):
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python", round_trip=True))
    if isinstance(value, Decimal):
        if value.is_zero():
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _fingerprint(payload) -> str:
    raw = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("risk evidence exceeds its canonical byte budget")
    return hashlib.sha256(raw).hexdigest()


def _display(value: Fraction) -> Decimal:
    with localcontext(Context(prec=100)):
        return Decimal(value.numerator) / Decimal(value.denominator)


def evaluate_portfolio(
    *,
    report_id: str,
    instrument_id: str,
    direction: Literal["long", "short"],
    candidate_entry: Decimal,
    stop_loss: Decimal,
    round_trip_cost_per_base: Decimal,
    requested_contracts: Decimal,
    requested_leverage: int,
    instrument: ContractRiskSpec | None,
    account: PortfolioRiskSnapshot | None,
    policy: PortfolioRiskPolicy | None,
    authority: DemoRiskAuthority | None,
    current_time: datetime,
) -> PortfolioRiskResult:
    """Assess fixed linear-contract risk against fresh complete scoped evidence.

    All gates compare exact rational values; Context(100) Decimal numbers are
    reporting representations only, so recurring divisions cannot round a fail
    into a pass. Daily/weekly realized loss is net realized PnL, clipped at zero;
    its rate uses current settlement equity. Drawdown uses the recorded peak as
    denominator and independently covers equity deterioration. Profitable
    outcomes reset loss streaks, while flat outcomes do not erase them.

    History covers [history_start, history_end], with a complete receipt ending
    at its actual observed_at. Daily is [UTC midnight, history_end]; rolling-week
    is (now - 7 days, history_end]. Freshness bounds the unobserved tail to now.
    A later runtime must recheck/reconcile and reserve risk atomically; passing
    here does not freeze account state or prove that any supplied claim is true.
    """
    report_id = _REPORT.validate_python(report_id, strict=True)
    instrument_id = _TEXT.validate_python(instrument_id, strict=True)
    causes: list[str] = []
    outputs = {}
    valid_direction = (
        direction if type(direction) is str and direction in {"long", "short"} else None
    )

    def finish() -> PortfolioRiskResult:
        codes = tuple(sorted(set(causes)))
        return PortfolioRiskResult(
            report_id=report_id,
            instrument_id=instrument_id,
            direction=valid_direction,
            passed=not codes,
            code="risk_authority_denied" if codes else "passed",
            causes=codes,
            reason="Portfolio risk authority denied."
            if codes
            else "Fixed proposed risk is within every supplied portfolio limit.",
            **outputs,
        )

    if valid_direction is None:
        causes.append("direction_invalid")
    try:
        now = _utc(current_time)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)
    except _INVALID:
        causes.append("current_time_invalid")
        return finish()
    try:
        entry = _PRICE.validate_python(candidate_entry, strict=True)
        stop = _PRICE.validate_python(stop_loss, strict=True)
        cost = _AMOUNT.validate_python(round_trip_cost_per_base, strict=True)
        contracts = _PRICE.validate_python(requested_contracts, strict=True)
        leverage = _LEVERAGE.validate_python(requested_leverage, strict=True)
        outputs.update(requested_contracts=contracts, requested_leverage=leverage)
    except _INVALID:
        causes.append("candidate_invalid")
    checked = {}
    for name, value, expected in (
        ("instrument", instrument, ContractRiskSpec),
        ("account", account, PortfolioRiskSnapshot),
        ("policy", policy, PortfolioRiskPolicy),
        ("authority", authority, DemoRiskAuthority),
    ):
        if value is None:
            causes.append(f"{name}_missing")
        else:
            try:
                checked[name] = _revalidate(value, expected)
            except _INVALID:
                causes.append(f"{name}_invalid")
    if causes:
        return finish()
    spec, state, limits, guard = (
        checked[name] for name in ("instrument", "account", "policy", "authority")
    )
    stamps = (
        state.balance_stamp,
        state.positions_stamp,
        state.history_stamp,
        state.reservations_stamp,
        guard.stamp,
    )
    if any(stamp.account_id != state.account_id for stamp in stamps):
        causes.append("scope_mismatch")
    if any(not stamp.complete for stamp in stamps):
        causes.append("evidence_incomplete")
    max_age = timedelta(seconds=limits.max_age_seconds)
    for stamp in (*stamps, spec):
        if stamp.observed_at > stamp.received_at or stamp.received_at > now:
            causes.append("evidence_timestamp_invalid")
        if now - stamp.observed_at > max_age or now - stamp.received_at > max_age:
            causes.append("evidence_stale")
    if spec.instrument_id != instrument_id:
        causes.append("instrument_mismatch")
    if state.settlement_currency != spec.settlement_currency:
        causes.append("settlement_currency_mismatch")
    if (
        spec.contract_kind != "linear_base"
        or spec.contract_value_currency != spec.base_currency
        or spec.base_currency == spec.settlement_currency
    ):
        causes.append("instrument_contract_unsupported")
    if spec.min_contracts > spec.max_contracts:
        causes.append("instrument_limits_invalid")
    if (
        guard.environment != "demo"
        or not guard.demo_enabled
        or not guard.armed
        or not guard.order_writes_allowed
        or guard.simulated_trading_header != "1"
        or guard.live_trading
        or guard.live_order_writes
        or guard.live_auto_execution
    ):
        causes.append("demo_authority_denied")
    if guard.emergency_stop:
        causes.append("emergency_stop_active")
    if state.position_count != len(state.positions):
        causes.append("position_count_mismatch")
    if state.pending_reservation_count != len(state.pending_reservations):
        causes.append("reservation_count_mismatch")
    if len({item.position_id for item in state.positions}) != len(
        state.positions
    ) or len({item.reservation_id for item in state.pending_reservations}) != len(
        state.pending_reservations
    ):
        causes.append("duplicate_exposure")
    exposures = (*state.positions, *state.pending_reservations)
    if any(item.settlement_currency != state.settlement_currency for item in exposures):
        causes.append("settlement_currency_mismatch")
    if any(
        item.correlation_group.casefold() in {"unknown", "unavailable", "unclassified"}
        for item in (*exposures, spec)
    ):
        causes.append("correlation_unknown")
    groups_by_instrument: dict[str, set[str]] = {}
    for item in (*exposures, spec):
        groups_by_instrument.setdefault(item.instrument_id, set()).add(
            item.correlation_group
        )
    if any(len(groups) != 1 for groups in groups_by_instrument.values()):
        causes.append("correlation_identity_mismatch")
    if (
        state.available_margin > state.equity
        or state.peak_equity < state.equity
        or not state.peak_window_started_at
        <= state.peak_observed_at
        <= state.balance_stamp.observed_at
    ):
        causes.append("peak_equity_invalid")
    if state.peak_window_started_at != limits.drawdown_window_started_at:
        causes.append("drawdown_window_mismatch")
    if (
        state.history_start > week_start
        or state.history_end != state.history_stamp.observed_at
        or state.history_end < day_start
        or state.history_end > now
        or state.history_start > state.history_end
    ):
        causes.append("history_coverage_incomplete")
    rows = state.loss_history
    if len({row.outcome_id for row in rows}) != len(rows):
        causes.append("history_invalid")
    for index, row in enumerate(rows):
        if not state.history_start <= row.closed_at <= state.history_end:
            causes.append("history_invalid")
        if index and (
            row.sequence != rows[index - 1].sequence + 1
            or row.closed_at < rows[index - 1].closed_at
        ):
            causes.append("history_invalid")
    if causes:
        return finish()
    try:
        outputs["evidence_sha256"] = _fingerprint(
            {
                "report_id": report_id,
                "instrument_id": instrument_id,
                "direction": direction,
                "candidate_entry": entry,
                "stop_loss": stop,
                "round_trip_cost_per_base": cost,
                "requested_contracts": contracts,
                "requested_leverage": leverage,
                "instrument": spec,
                "account": state,
                "policy": limits,
                "authority": guard,
                "current_time": now,
            }
        )
        quantity = Fraction(contracts) * Fraction(spec.contract_value)
        notional = quantity * Fraction(entry)
        margin = notional / leverage
        max_loss = quantity * (abs(Fraction(entry) - Fraction(stop)) + Fraction(cost))
        equity = Fraction(state.equity)
        daily_pnl = sum(
            (Fraction(row.realized_pnl) for row in rows if row.closed_at >= day_start),
            Fraction(0),
        )
        weekly_pnl = sum(
            (Fraction(row.realized_pnl) for row in rows if row.closed_at > week_start),
            Fraction(0),
        )
        daily_loss = max(Fraction(0), -daily_pnl)
        weekly_loss = max(Fraction(0), -weekly_pnl)
        drawdown = (Fraction(state.peak_equity) - equity) / Fraction(state.peak_equity)
        outputs.update(
            {
                name: _display(value)
                for name, value in {
                    "base_quantity": quantity,
                    "notional": notional,
                    "required_margin": margin,
                    "max_loss_amount": max_loss,
                    "risk_pct": max_loss / equity,
                    "daily_loss_pct": daily_loss / equity,
                    "weekly_loss_pct": weekly_loss / equity,
                    "drawdown_pct": drawdown,
                }.items()
            }
        )
        if (direction == "long" and stop >= entry) or (
            direction == "short" and stop <= entry
        ):
            causes.append("protection_geometry_invalid")
        if (Fraction(contracts) / Fraction(spec.lot_size)).denominator != 1:
            causes.append("quantity_not_on_lot")
        if contracts < spec.min_contracts:
            causes.append("quantity_below_minimum")
        if contracts > min(spec.max_contracts, limits.max_order_contracts):
            causes.append("quantity_limit_exceeded")
        if leverage > min(spec.max_leverage, limits.max_leverage):
            causes.append("leverage_limit_exceeded")
        streak = state.loss_streak_at_history_start
        for row in rows:
            if row.realized_pnl < 0:
                streak += 1
            elif row.realized_pnl > 0:
                streak = 0
        if streak >= limits.max_consecutive_losses:
            causes.append("consecutive_loss_limit_reached")
        if len(exposures) + 1 > limits.max_open_positions:
            causes.append("open_position_limit_exceeded")
        if (
            sum(item.direction == direction for item in exposures) + 1
            > limits.max_same_direction_positions
        ):
            causes.append("same_direction_limit_exceeded")
        if (
            sum(item.correlation_group == spec.correlation_group for item in exposures)
            + 1
            > limits.max_correlated_positions
        ):
            causes.append("correlation_limit_exceeded")
        existing_notional = sum(
            (Fraction(item.notional) for item in exposures), Fraction(0)
        )
        existing_risk = sum(
            (Fraction(item.risk_amount) for item in exposures), Fraction(0)
        )
        existing_margin = sum(
            (Fraction(item.margin) for item in exposures), Fraction(0)
        )
        same_direction_notional = sum(
            (
                Fraction(item.notional)
                for item in exposures
                if item.direction == direction
            ),
            Fraction(0),
        )
        correlated_notional = sum(
            (
                Fraction(item.notional)
                for item in exposures
                if item.correlation_group == spec.correlation_group
            ),
            Fraction(0),
        )
        # available_margin is exchange availability before local pending holds;
        # reserve all supplied pending margin conservatively, never default zero.
        pending_margin = sum(
            (Fraction(item.margin) for item in state.pending_reservations), Fraction(0)
        )
        for violation, code in (
            (
                daily_loss / equity >= Fraction(limits.max_daily_loss_pct),
                "daily_loss_limit_reached",
            ),
            (
                weekly_loss / equity >= Fraction(limits.max_weekly_loss_pct),
                "weekly_loss_limit_reached",
            ),
            (drawdown >= Fraction(limits.max_drawdown_pct), "drawdown_limit_reached"),
            (
                max_loss / equity > Fraction(limits.risk_per_trade_pct),
                "trade_risk_limit_exceeded",
            ),
            (
                notional > Fraction(limits.max_order_notional),
                "order_notional_limit_exceeded",
            ),
            (
                existing_notional + notional > Fraction(limits.max_portfolio_notional),
                "portfolio_notional_limit_exceeded",
            ),
            (
                same_direction_notional + notional
                > Fraction(limits.max_same_direction_notional),
                "same_direction_notional_limit_exceeded",
            ),
            (
                correlated_notional + notional
                > Fraction(limits.max_correlated_notional),
                "correlated_notional_limit_exceeded",
            ),
            (
                (existing_risk + max_loss) / equity
                > Fraction(limits.max_portfolio_risk_pct),
                "portfolio_risk_limit_exceeded",
            ),
            (
                (existing_margin + margin) / equity
                > Fraction(limits.max_portfolio_margin_pct),
                "portfolio_margin_limit_exceeded",
            ),
            (
                pending_margin + margin > Fraction(state.available_margin),
                "available_margin_exceeded",
            ),
        ):
            if violation:
                causes.append(code)
    except _INVALID:
        causes.append("risk_computation_invalid")
    return finish()
