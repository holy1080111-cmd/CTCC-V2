"""Explicit finite Live financial DATA using the unchanged shared risk caps.

The prospective controlled loss counter and measured peak do not describe the
account before its epoch. Historical seven-day cash losses remain separately
required. Decoding these claims never creates source, Arm or order authority.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.source_primitives import canonical
from app.trade_qualification import portfolio as risk
from app.trade_qualification.account_ledger_claims_v2 import ExactRatioV1
from app.trade_qualification.models import Price, QualificationModel


def _need(condition):
    if condition is not True:
        raise ValueError("live_finite_portfolio_claims_invalid") from None


def _utc(value):
    _need(
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )
    return value.astimezone(UTC)


def _ratio(value):
    return (
        None
        if value is None
        else Fraction(int(value.numerator), int(value.denominator))
    )


class LiveFinancialScopeV1(QualificationModel):
    environment: Literal["live"]
    account_id: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    settlement_currency: Literal["USDT"]


class LiveFinitePortfolioSnapshotV1(QualificationModel):
    account_id: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    settlement_currency: Literal["USDT"]
    balance_stamp: risk.LiveEvidenceStamp
    positions_stamp: risk.LiveEvidenceStamp
    history_stamp: risk.LiveEvidenceStamp
    reservations_stamp: risk.LiveEvidenceStamp
    equity: Price
    available_margin: risk.Amount
    positions: Annotated[tuple[risk.PositionExposure, ...], Field(max_length=2048)]
    pending_reservations: Annotated[
        tuple[risk.PendingReservation, ...], Field(max_length=2048)
    ]
    position_count: Annotated[int, Field(ge=0, le=2048)]
    pending_reservation_count: Annotated[int, Field(ge=0, le=2048)]
    history_start: datetime
    history_end: datetime
    original_start_seed: None

    _times = field_validator("history_start", "history_end")(_utc)

    @model_validator(mode="after")
    def scoped(self):
        _need(
            self.history_start <= self.history_end
            and self.position_count == len(self.positions)
            and self.pending_reservation_count == len(self.pending_reservations)
            and len({p.position_id for p in self.positions}) == len(self.positions)
            and len({p.reservation_id for p in self.pending_reservations})
            == len(self.pending_reservations)
        )
        for stamp in (
            self.balance_stamp,
            self.positions_stamp,
            self.history_stamp,
            self.reservations_stamp,
        ):
            _need(
                stamp.environment == "live"
                and stamp.account_id == self.account_id
                and stamp.observed_at <= stamp.received_at
            )
        _need(
            all(
                p.settlement_currency == "USDT"
                for p in (*self.positions, *self.pending_reservations)
            )
        )
        return self


class LiveFinancialOperandsV1(QualificationModel):
    schema_version: Literal["ctcc.live_finite_financial_operands.v1"] = (
        "ctcc.live_finite_financial_operands.v1"
    )
    account_uid: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    main_uid: Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
    credential_session_sha256: risk.Digest
    source_revision_sha256: risk.Digest
    epoch_id: risk.Digest
    epoch_policy_sha256: risk.Digest
    epoch_started_at: datetime
    sample_sequence: Annotated[int, Field(ge=1, le=10**15)]
    source_as_of_at: datetime
    available_at: datetime
    evaluated_at: datetime
    daily_window_started_at: datetime
    weekly_window_started_at: datetime
    daily_net_pnl: ExactRatioV1 | None
    weekly_net_pnl: ExactRatioV1 | None
    prospective_loss_streak: Annotated[int, Field(ge=0, le=4096)] | None
    original_start_seed: None
    measured_peak_equity: ExactRatioV1 | None
    measured_current_equity: ExactRatioV1 | None
    measured_peak_observed_at: datetime | None
    drawdown_basis: Literal["all_durable_original_samples_in_prospective_live_epoch"]
    actual_continuous_peak: None
    position_margin_bucket_usdt: Annotated[Price, Field(le=Decimal(300))]
    original_expires_at: datetime
    source_receipt_sha256: risk.Digest
    missing_obligations: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=128)], ...],
        Field(max_length=128),
    ]
    execution_authority: Literal[False] = False

    _times = field_validator(
        "epoch_started_at",
        "source_as_of_at",
        "available_at",
        "evaluated_at",
        "daily_window_started_at",
        "weekly_window_started_at",
        "original_expires_at",
    )(_utc)

    @field_validator("measured_peak_observed_at")
    @classmethod
    def nullable_time(cls, value):
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def chronology(self):
        _need(
            self.epoch_started_at
            <= self.source_as_of_at
            <= self.available_at
            <= self.evaluated_at
            < self.original_expires_at
            and self.daily_window_started_at
            == self.evaluated_at.replace(hour=0, minute=0, second=0, microsecond=0)
            and self.weekly_window_started_at == self.evaluated_at - timedelta(days=7)
            and self.missing_obligations == tuple(sorted(set(self.missing_obligations)))
            and (self.measured_peak_equity is None)
            == (self.measured_peak_observed_at is None)
        )
        for value in (self.measured_peak_equity, self.measured_current_equity):
            _need(value is None or _ratio(value) > 0)
        if self.measured_peak_observed_at is not None:
            _need(
                self.epoch_started_at
                <= self.measured_peak_observed_at
                <= self.source_as_of_at
            )
        if (
            self.measured_peak_equity is not None
            and self.measured_current_equity is not None
        ):
            _need(
                _ratio(self.measured_peak_equity)
                >= _ratio(self.measured_current_equity)
            )
        return self


class LiveFinitePortfolioClaimsV1(QualificationModel):
    scope: LiveFinancialScopeV1
    account: LiveFinitePortfolioSnapshotV1
    financial: LiveFinancialOperandsV1
    risk_policy: risk.PortfolioRiskPolicy
    authority: risk.LiveRiskAuthority

    @model_validator(mode="after")
    def bindings(self):
        f, a = self.financial, self.account
        _need(
            self.scope.account_id
            == a.account_id
            == f.account_uid
            == self.authority.stamp.account_id
            and self.scope.settlement_currency == a.settlement_currency
            and self.authority.environment == self.authority.stamp.environment == "live"
            and self.authority.credential_session_sha256 == f.credential_session_sha256
            and a.history_end == f.source_as_of_at == a.balance_stamp.observed_at
            and f.epoch_started_at == self.risk_policy.drawdown_window_started_at
            and a.history_start <= f.weekly_window_started_at
            and self.authority.stamp.received_at <= f.evaluated_at
            and self.risk_policy.max_age_seconds <= 5
        )
        for stamp in (
            a.balance_stamp,
            a.positions_stamp,
            a.history_stamp,
            a.reservations_stamp,
        ):
            _need(stamp.received_at <= f.evaluated_at)
        return self


class LiveFinitePortfolioInputsV1(QualificationModel):
    requested_contracts: Price
    requested_leverage: Annotated[int, Field(ge=1, le=125)]
    instrument: risk.ContractRiskSpec | None
    claims: LiveFinitePortfolioClaimsV1


@dataclass(frozen=True, slots=True)
class _LiveFinancialEvaluationV1:
    scope: LiveFinancialScopeV1
    checked_at: datetime
    started_at: datetime
    ended_at: datetime
    daily_net_pnl: Fraction | None
    weekly_net_pnl: Fraction | None
    loss_streak: int | None
    measured_peak: Fraction | None
    measured_current: Fraction | None
    position_margin_bucket: Fraction
    receipt_json: bytes = field(repr=False)
    missing_obligations: tuple[str, ...]


def _exact_tree(value, *, depth=0, budget=None):
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    _need(depth <= 16 and budget[0] >= 0)
    if isinstance(value, BaseModel):
        _need(
            type(value)
            in {
                LiveFinitePortfolioInputsV1,
                LiveFinitePortfolioClaimsV1,
                LiveFinitePortfolioSnapshotV1,
                LiveFinancialOperandsV1,
                LiveFinancialScopeV1,
                risk.LiveRiskAuthority,
                risk.LiveEvidenceStamp,
                risk.PortfolioRiskPolicy,
                risk.ContractRiskSpec,
                risk.PositionExposure,
                risk.PendingReservation,
                ExactRatioV1,
            }
            and set(value.__dict__) == set(type(value).model_fields)
            and not value.__pydantic_extra__
        )
        for item in value.__dict__.values():
            _exact_tree(item, depth=depth + 1, budget=budget)
    elif type(value) is tuple:
        _need(len(value) <= 4096)
        for item in value:
            _exact_tree(item, depth=depth + 1, budget=budget)
    elif type(value) in (str, int, bool, Decimal, datetime) or value is None:
        return
    else:
        _need(False)


def _checked_live_finite_evaluation_v1(claims, *, current_time):
    _need(type(claims) is LiveFinitePortfolioClaimsV1)
    _exact_tree(claims)
    checked = LiveFinitePortfolioClaimsV1.model_validate_json(
        claims.model_dump_json(round_trip=True), strict=True
    )
    _need(checked == claims and checked.financial.evaluated_at == _utc(current_time))
    f, a = checked.financial, checked.account
    missing = set(f.missing_obligations)
    if f.measured_peak_equity is None or f.measured_current_equity is None:
        missing.add("live_prospective_measured_hwm_unknown")
    return _LiveFinancialEvaluationV1(
        checked.scope,
        f.evaluated_at,
        a.history_start,
        a.history_end,
        _ratio(f.daily_net_pnl),
        _ratio(f.weekly_net_pnl),
        f.prospective_loss_streak,
        _ratio(f.measured_peak_equity),
        _ratio(f.measured_current_equity),
        Fraction(f.position_margin_bucket_usdt),
        canonical(f.model_dump(mode="json", round_trip=True)),
        tuple(sorted(missing)),
    )


def evaluate_live_finite_portfolio_v1(
    inputs,
    *,
    report_id,
    instrument_id,
    direction,
    candidate_entry,
    stop_loss,
    round_trip_cost_per_base,
    current_time,
):
    _need(type(inputs) is LiveFinitePortfolioInputsV1)
    _exact_tree(inputs)
    inputs = LiveFinitePortfolioInputsV1.model_validate_json(
        inputs.model_dump_json(round_trip=True), strict=True
    )
    claims = inputs.claims
    financial = _checked_live_finite_evaluation_v1(claims, current_time=current_time)
    return risk._evaluate_portfolio(
        report_id=report_id,
        instrument_id=instrument_id,
        direction=direction,
        candidate_entry=candidate_entry,
        stop_loss=stop_loss,
        round_trip_cost_per_base=round_trip_cost_per_base,
        requested_contracts=inputs.requested_contracts,
        requested_leverage=inputs.requested_leverage,
        instrument=inputs.instrument,
        account=claims.account,
        policy=claims.risk_policy,
        authority=claims.authority,
        current_time=current_time,
        _financial=financial,
        _live_finite=claims,
        _profile=risk.RiskAssessmentProfileV1.LIVE,
    )
