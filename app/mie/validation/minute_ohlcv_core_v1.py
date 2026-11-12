"""Pure OHLCV arithmetic shared by exact D/V and holdout minute grids.

This helper carries no partition selection, source provenance, or authority.
Callers must verify those before aggregating; this only refuses malformed
contiguous one-minute groups and pins Decimal arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)

from app.mie.features import FeatureBar
from app.mie.validation.replay import PointInTimeBar

_ARITHMETIC = Context(
    prec=256,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)


def aggregate_ohlcv_group(
    rows: tuple[PointInTimeBar, ...],
) -> tuple[FeatureBar, datetime]:
    """Return the exact aggregate and latest constituent availability."""
    if type(rows) is not tuple or len(rows) not in (15, 60, 240):
        raise ValueError("minute_ohlcv_group_count_invalid")
    first = rows[0]
    if type(first) is not PointInTimeBar:
        raise ValueError("minute_ohlcv_source_invalid")
    instrument = first.instrument_id
    for index, row in enumerate(rows):
        if (
            type(row) is not PointInTimeBar
            or row.instrument_id != instrument
            or row.bar.closed_at != first.bar.closed_at + timedelta(minutes=index)
            or row.available_at < row.bar.closed_at
        ):
            raise ValueError("minute_ohlcv_group_noncausal_or_changed")
    with localcontext(_ARITHMETIC):
        volume = sum((row.bar.volume for row in rows), Decimal(0))
    return (
        FeatureBar(
            closed_at=rows[-1].bar.closed_at,
            open=first.bar.open,
            high=max(row.bar.high for row in rows),
            low=min(row.bar.low for row in rows),
            close=rows[-1].bar.close,
            volume=volume,
        ),
        max(row.available_at for row in rows),
    )
