from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Sequence

D = Decimal
EPSILON = D("1e-30")


def clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


def median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / D("2")


def rms(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("RMS requires at least one value")
    return (
        sum((value * value for value in values), D("0")) / D(len(values))
    ).sqrt()


def log_prices(values: Sequence[Decimal]) -> list[Decimal]:
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("prices must be positive and finite")
    with localcontext() as context:
        context.prec = 50
        return [value.ln() for value in values]


def log_returns_from_prices(values: Sequence[Decimal]) -> list[Decimal]:
    logs = log_prices(values)
    return [
        current - previous
        for previous, current in zip(logs[:-1], logs[1:], strict=True)
    ]
