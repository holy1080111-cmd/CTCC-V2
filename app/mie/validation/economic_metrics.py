"""Pure descriptive arithmetic for caller-supplied, closed trade outcomes.

This module cannot establish that a trade existed, that its PnL includes every
fee and funding bill, or that a holdout was unseen. A trusted forensics ledger
must establish those facts separately before these figures can support review.
It has no Gate 3 promotion, runtime, risk, or order authority.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)
from typing import Literal

D = Decimal
DECIMAL_PRECISION = 50


@dataclass(frozen=True, slots=True)
class ClosedTradeOutcome:
    trade_id: str
    closed_at: datetime
    available_at: datetime
    net_pnl: Decimal
    currency: str
    direction: Literal["long", "short"]
    symbol: str
    regime: str


@dataclass(frozen=True, slots=True)
class TradeProfitSummary:
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


@dataclass(frozen=True, slots=True)
class DescriptiveTradeMetrics:
    as_of: datetime
    currency: str
    all_trades: TradeProfitSummary
    by_direction: tuple[tuple[str, TradeProfitSummary], ...]
    by_symbol: tuple[tuple[str, TradeProfitSummary], ...]
    by_regime: tuple[tuple[str, TradeProfitSummary], ...]
    input_scope: Literal["caller_supplied_closed_trades_only"] = (
        "caller_supplied_closed_trades_only"
    )
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False


def _utc(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")


def _label(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonblank canonical string")


def _summary(rows: Sequence[ClosedTradeOutcome]) -> TradeProfitSummary:
    # Pin every field: localcontext() otherwise inherits caller rounding, exponent
    # bounds, and traps, so identical closed trades can yield different metrics.
    arithmetic = Context(
        prec=DECIMAL_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )
    with localcontext(arithmetic):
        wins = tuple(row.net_pnl for row in rows if row.net_pnl > 0)
        losses = tuple(-row.net_pnl for row in rows if row.net_pnl < 0)
        gross_profit = sum(wins, D(0))
        gross_loss = sum(losses, D(0))
        count = len(rows)
        return TradeProfitSummary(
            sample_count=count,
            win_count=len(wins),
            loss_count=len(losses),
            flat_count=count - len(wins) - len(losses),
            gross_profit=gross_profit,
            gross_loss_magnitude=gross_loss,
            net_profit=gross_profit - gross_loss,
            net_expectancy_per_trade=(
                (gross_profit - gross_loss) / D(count) if count else None
            ),
            profit_factor=(gross_profit / gross_loss if gross_loss else None),
            average_win=(gross_profit / D(len(wins)) if wins else None),
            average_loss_magnitude=(gross_loss / D(len(losses)) if losses else None),
        )


def _breakdown(
    rows: Sequence[ClosedTradeOutcome], field: Literal["direction", "symbol", "regime"]
) -> tuple[tuple[str, TradeProfitSummary], ...]:
    keys = sorted({getattr(row, field) for row in rows})
    return tuple(
        (key, _summary(tuple(row for row in rows if getattr(row, field) == key)))
        for key in keys
    )


def summarize_closed_trade_outcomes(
    outcomes: Sequence[ClosedTradeOutcome], *, as_of: datetime
) -> DescriptiveTradeMetrics:
    """Summarize an exact as-of prefix; reject unrevealed or ambiguous rows.

    ``net_pnl`` must already be fully reconciled in one currency. This function
    never estimates unknown fees, fills, funding, marks, or missing trades.
    Input order is canonical by ``(closed_at, trade_id)`` and is not repaired.
    """

    _utc(as_of, "as_of")
    rows = tuple(outcomes)
    if not rows:
        raise ValueError("closed trade outcomes cannot be empty")
    seen_ids: set[str] = set()
    previous_key: tuple[datetime, str] | None = None
    if not isinstance(rows[0], ClosedTradeOutcome):
        raise ValueError("outcomes must be closed trade rows")  # noqa: TRY004
    currency = rows[0].currency
    for row in rows:
        if not isinstance(row, ClosedTradeOutcome):
            raise ValueError("outcomes must be closed trade rows")  # noqa: TRY004
        for name in ("trade_id", "currency", "symbol", "regime"):
            _label(getattr(row, name), name)
        _utc(row.closed_at, "closed_at")
        _utc(row.available_at, "available_at")
        if row.closed_at > row.available_at or row.available_at > as_of:
            raise ValueError("trade outcome is unavailable at the declared as_of")
        if row.trade_id in seen_ids:
            raise ValueError("trade IDs must be unique")
        seen_ids.add(row.trade_id)
        if row.currency != currency:
            raise ValueError("net PnL currency must be identical for every trade")
        if row.direction not in ("long", "short"):
            raise ValueError("direction must be long or short")
        if isinstance(row.net_pnl, bool) or not isinstance(row.net_pnl, (D, int)):
            raise ValueError("net PnL must be a Decimal or integer")  # noqa: TRY004
        if not D(row.net_pnl).is_finite():
            raise ValueError("net PnL must be finite")
        key = (row.closed_at, row.trade_id)
        if previous_key is not None and key <= previous_key:
            raise ValueError("trade rows must be in canonical close-time order")
        previous_key = key

    return DescriptiveTradeMetrics(
        as_of=as_of,
        currency=currency,
        all_trades=_summary(rows),
        by_direction=_breakdown(rows, "direction"),
        by_symbol=_breakdown(rows, "symbol"),
        by_regime=_breakdown(rows, "regime"),
    )
