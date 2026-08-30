from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR


@dataclass(frozen=True)
class DemoCapitalBucketPlan:
    """Exact USDT capital-bucket limits for one automation run.

    The bucket is a sizing ceiling, not a minimum order size. Stop-risk,
    notional, contract, exchange-availability, and rounding limits may all
    reduce the final order below this ceiling.
    """

    risk_equity_usdt: Decimal
    available_equity_usdt: Decimal
    configured_bucket_usdt: Decimal
    target_position_margin_usdt: Decimal
    available_position_margin_cap_usdt: Decimal
    capital_slot_count: int
    effective_position_limit: int


def build_demo_capital_bucket_plan(
    *,
    risk_equity_usdt: Decimal,
    available_equity_usdt: Decimal,
    configured_bucket_usdt: Decimal,
    configured_position_limit: int,
) -> DemoCapitalBucketPlan:
    """Build the fail-closed ``< 2000 all capital / otherwise 2000`` plan.

    At or below the configured bucket, the account forms exactly one capital
    slot whose ceiling is the full risk equity. Above it, only complete bucket
    slots count. Residual equity smaller than one bucket cannot create another
    position. Available exchange equity can reduce the current order ceiling
    but can never increase it.
    """

    if risk_equity_usdt <= 0:
        raise ValueError("risk_equity_usdt_must_be_positive")
    if available_equity_usdt < 0:
        raise ValueError("available_equity_usdt_cannot_be_negative")
    if configured_bucket_usdt <= 0:
        raise ValueError("configured_bucket_usdt_must_be_positive")
    if configured_position_limit < 1:
        raise ValueError("configured_position_limit_must_be_positive")

    available = min(risk_equity_usdt, available_equity_usdt)
    if risk_equity_usdt <= configured_bucket_usdt:
        target_margin = risk_equity_usdt
        slots = 1
    else:
        target_margin = configured_bucket_usdt
        slots = int(
            (risk_equity_usdt / configured_bucket_usdt).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )

    return DemoCapitalBucketPlan(
        risk_equity_usdt=risk_equity_usdt,
        available_equity_usdt=available,
        configured_bucket_usdt=configured_bucket_usdt,
        target_position_margin_usdt=target_margin,
        available_position_margin_cap_usdt=min(target_margin, available),
        capital_slot_count=slots,
        effective_position_limit=min(configured_position_limit, slots),
    )


def demo_position_notional_ceiling(
    *,
    plan: DemoCapitalBucketPlan,
    leverage: int,
    global_notional_ceiling_usdt: Decimal,
) -> Decimal:
    """Return the lower of bucket buying power and the global safety cap."""

    if leverage < 1:
        raise ValueError("leverage_must_be_positive")
    if global_notional_ceiling_usdt <= 0:
        raise ValueError("global_notional_ceiling_usdt_must_be_positive")
    return min(
        plan.available_position_margin_cap_usdt * Decimal(leverage),
        global_notional_ceiling_usdt,
    )
