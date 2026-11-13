"""Replay closed-trade cash-PnL concentration sensitivity offline."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.economic_metrics import (
    ClosedTradeOutcome,
    TradeProfitSummary,
    _summary,
    summarize_closed_trade_outcomes,
)
from app.mie.validation.metrics import _bounded_decimal_tuple

_MAX_ROWS = 4096
_MAX_GROUPS = 32
_MAX_TOP_K = 64
_MAX_PLAN = 16 * 1024
_MAX_COHORT = 4 * 1024 * 1024
_MAX_REPORT = 256 * 1024
_Label = Annotated[str, Field(min_length=1, max_length=96)]
_Groups = Annotated[tuple[_Label, ...], Field(min_length=1, max_length=_MAX_GROUPS)]


class ConcentrationPlanV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.cash_pnl_concentration.plan.v1"] = (
        "ctcc.mie.cash_pnl_concentration.plan.v1"
    )
    candidate_sha256: Sha256
    preregistered_at: datetime
    window_start: datetime
    window_end: datetime
    as_of: datetime
    currency: _Label
    symbols: _Groups
    regimes: _Groups
    top_k: int = Field(ge=1, le=_MAX_TOP_K)

    @field_validator("preregistered_at", "window_start", "window_end", "as_of")
    @classmethod
    def validate_time(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_design(self) -> ConcentrationPlanV1:
        if (
            not self.preregistered_at
            < self.window_start
            < self.window_end
            <= self.as_of
        ):
            raise ValueError("concentration plan chronology differs")
        if any(
            group != tuple(sorted(set(group))) for group in (self.symbols, self.regimes)
        ):
            raise ValueError("concentration group roster is not canonical")
        return self


class ConcentrationTradeV1(Gate3Contract):
    trade_id: _Label
    closed_at: datetime
    available_at: datetime
    net_pnl: Decimal
    currency: _Label
    direction: Literal["long", "short"]
    symbol: _Label
    regime: _Label

    @field_validator("net_pnl")
    @classmethod
    def validate_pnl(cls, value: Decimal) -> Decimal:
        _bounded_decimal_tuple(value, "cash PnL")
        return value


class ConcentrationCohortV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.cash_pnl_concentration.cohort.v1"] = (
        "ctcc.mie.cash_pnl_concentration.cohort.v1"
    )
    plan_sha256: Sha256
    candidate_sha256: Sha256
    source_replay_sha256: Sha256
    trades: tuple[ConcentrationTradeV1, ...] = Field(min_length=1, max_length=_MAX_ROWS)


class ConcentrationSummaryV1(Gate3Contract):
    sample_count: int
    win_count: int
    loss_count: int
    flat_count: int
    gross_profit: Decimal
    gross_loss_magnitude: Decimal
    net_profit: Decimal
    net_expectancy_per_trade: Decimal | None
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss_magnitude: Decimal | None


class ConcentrationSliceV1(Gate3Contract):
    label: _Label
    removed_count: int | None = Field(ge=0)
    summary: ConcentrationSummaryV1 | None


class ConcentrationReportV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.cash_pnl_concentration.report.v1"] = (
        "ctcc.mie.cash_pnl_concentration.report.v1"
    )
    plan_sha256: Sha256
    cohort_sha256: Sha256
    candidate_sha256: Sha256
    source_replay_sha256: Sha256
    as_of: datetime
    currency: _Label
    all_trades: ConcentrationSummaryV1
    by_direction: tuple[ConcentrationSliceV1, ...]
    by_symbol: tuple[ConcentrationSliceV1, ...]
    by_regime: tuple[ConcentrationSliceV1, ...]
    requested_top_k: int
    removed_winner_ids: tuple[_Label, ...]
    without_top_winners: ConcentrationSummaryV1 | None
    without_each_symbol: tuple[ConcentrationSliceV1, ...]
    without_each_regime: tuple[ConcentrationSliceV1, ...]
    scope: Literal["declared_closed_trade_cash_pnl_sensitivity"] = (
        "declared_closed_trade_cash_pnl_sensitivity"
    )
    independent_preregistration_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    economic_oos_accepted: Literal[False] = False
    execution_authority: Literal[False] = False


def _read[T: Gate3Contract](raw: bytes, pin: str, kind: type[T], maximum: int) -> T:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise ArtifactVerificationError("concentration artifact size differs")
    return _verify(raw, expected_sha256=pin, contract_type=kind)


def _wire(summary: TradeProfitSummary) -> ConcentrationSummaryV1:
    return ConcentrationSummaryV1(**asdict(summary))


def _summarize_slice(
    rows: tuple[ClosedTradeOutcome, ...],
) -> ConcentrationSummaryV1 | None:
    return _wire(_summary(rows)) if rows else None


def _by_group(
    items: tuple[tuple[str, TradeProfitSummary], ...], labels: tuple[str, ...]
):
    by_label = dict(items)
    return tuple(
        ConcentrationSliceV1(
            label=label,
            removed_count=None,
            summary=_wire(by_label[label]) if label in by_label else None,
        )
        for label in labels
    )


def _without_group(
    rows: tuple[ClosedTradeOutcome, ...],
    field: Literal["symbol", "regime"],
    groups: tuple[str, ...],
) -> tuple[ConcentrationSliceV1, ...]:
    slices = []
    for group in groups:
        remaining = tuple(row for row in rows if getattr(row, field) != group)
        slices.append(
            ConcentrationSliceV1(
                label=group,
                removed_count=len(rows) - len(remaining),
                summary=_summarize_slice(remaining)
                if len(remaining) != len(rows)
                else None,
            )
        )
    return tuple(slices)


def evaluate_cash_pnl_concentration_v1(
    plan_bytes: bytes,
    cohort_bytes: bytes,
    *,
    expected_plan_sha256: str,
    expected_cohort_sha256: str,
) -> FrozenGate3Artifact[ConcentrationReportV1]:
    """Measure sensitivity on one declared, complete input cohort.

    Costs remain embedded in supplied cash PnL. No position is simulated and no
    earlier disclosure or authenticity claim is inferred from input timestamps.
    """
    plan = _read(plan_bytes, expected_plan_sha256, ConcentrationPlanV1, _MAX_PLAN)
    cohort = _read(
        cohort_bytes, expected_cohort_sha256, ConcentrationCohortV1, _MAX_COHORT
    )
    if (
        cohort.plan_sha256 != expected_plan_sha256
        or cohort.candidate_sha256 != plan.candidate_sha256
    ):
        raise ValueError("concentration plan or candidate pin differs")
    rows = tuple(ClosedTradeOutcome(**row.model_dump()) for row in cohort.trades)
    base = summarize_closed_trade_outcomes(rows, as_of=plan.as_of)
    if (
        base.currency != plan.currency
        or any(not plan.window_start <= row.closed_at < plan.window_end for row in rows)
        or not {row.symbol for row in rows}.issubset(plan.symbols)
        or not {row.regime for row in rows}.issubset(plan.regimes)
    ):
        raise ValueError("concentration trade window, currency or group differs")
    # Descending exact Decimal cash PnL, with the canonical trade order as tie-break.
    # copy_negate() is exact even when the caller changes the Decimal context.
    winners = tuple(
        row.trade_id
        for row in sorted(
            (row for row in rows if row.net_pnl > 0),
            key=lambda row: (row.net_pnl.copy_negate(), row.closed_at, row.trade_id),
        )[: plan.top_k]
    )
    removed = set(winners)
    report = ConcentrationReportV1(
        plan_sha256=expected_plan_sha256,
        cohort_sha256=expected_cohort_sha256,
        candidate_sha256=plan.candidate_sha256,
        source_replay_sha256=cohort.source_replay_sha256,
        as_of=plan.as_of,
        currency=base.currency,
        all_trades=_wire(base.all_trades),
        by_direction=_by_group(base.by_direction, ("long", "short")),
        by_symbol=_by_group(base.by_symbol, plan.symbols),
        by_regime=_by_group(base.by_regime, plan.regimes),
        requested_top_k=plan.top_k,
        removed_winner_ids=winners,
        without_top_winners=_summarize_slice(
            tuple(row for row in rows if row.trade_id not in removed)
        ),
        without_each_symbol=_without_group(rows, "symbol", plan.symbols),
        without_each_regime=_without_group(rows, "regime", plan.regimes),
    )
    result = _freeze(report, contract_type=ConcentrationReportV1)
    if len(result.payload) > _MAX_REPORT:
        raise ArtifactVerificationError("concentration report size differs")
    return result


def verify_cash_pnl_concentration_v1(
    report_bytes: bytes,
    plan_bytes: bytes,
    cohort_bytes: bytes,
    *,
    expected_report_sha256: str,
    expected_plan_sha256: str,
    expected_cohort_sha256: str,
) -> FrozenGate3Artifact[ConcentrationReportV1]:
    """Verify exact retained bytes and reproduce the diagnostic from the cohort."""
    _read(report_bytes, expected_report_sha256, ConcentrationReportV1, _MAX_REPORT)
    replay = evaluate_cash_pnl_concentration_v1(
        plan_bytes,
        cohort_bytes,
        expected_plan_sha256=expected_plan_sha256,
        expected_cohort_sha256=expected_cohort_sha256,
    )
    if replay.payload != report_bytes:
        raise ArtifactVerificationError("concentration report differs from replay")
    return replay
