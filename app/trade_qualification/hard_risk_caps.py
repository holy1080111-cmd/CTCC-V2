"""Non-waivable Demo arithmetic ceilings; these never authenticate a source.

The caller's reviewed policy may be tighter. These bounds only prevent a
caller-supplied loose policy from widening the qualification/ledger arithmetic.
Amounts are compared after the same upward 1e-20 rounding as a durable hold.
The USDT bucket cannot be translated to another currency without a verified FX
source, so non-USDT settlement is denied by its callers.
"""

from fractions import Fraction

MAX_TRADE_RISK_PCT = Fraction(1, 200)  # 0.5% of settlement equity
MAX_PORTFOLIO_STOP_RISK_PCT = Fraction(1, 100)  # 1%
MAX_SINGLE_POSITION_MARGIN_USDT = Fraction(300)
MAX_PORTFOLIO_MARGIN_PCT = Fraction(3, 5)  # 60%
BUCKET_SETTLEMENT_CURRENCY = "USDT"
AMOUNT_SCALE = 10**20


def rounded_up_amount(value: Fraction) -> Fraction:
    """Exact upward monetary rounding used for pure risk-cap comparisons."""
    if type(value) is not Fraction or value < 0:
        raise ValueError("exact_nonnegative_risk_amount_required")
    scaled = value * AMOUNT_SCALE
    units = -(-scaled.numerator // scaled.denominator)
    return Fraction(units, AMOUNT_SCALE)
