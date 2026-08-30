from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.okx_demo import OkxDemoAccountConfig, OkxDemoBalanceSnapshot


D = Decimal
DEFAULT_DEMO_SETTLEMENT_CURRENCY = "USDT"


@dataclass(frozen=True)
class DemoRiskCapital:
    risk_equity: Decimal
    available_equity: Decimal
    currency: str
    basis: str


def resolve_demo_risk_capital(
    account_config: OkxDemoAccountConfig,
    balance: OkxDemoBalanceSnapshot,
    *,
    settlement_currency: str = DEFAULT_DEMO_SETTLEMENT_CURRENCY,
) -> tuple[DemoRiskCapital | None, str]:
    """Resolve the equity basis used for both Demo risk and performance.

    OKX ``totalEq`` is the USD value of every asset in the account. It is not a
    valid strategy-equity series for single-currency USDT derivatives because
    unrelated spot assets move it. Account levels 3 and 4 intentionally use
    OKX adjusted account equity because that is also the pooled margin basis.
    """

    account_level = account_config.account_level or ""
    currency = settlement_currency.upper()
    if account_level == "2":
        matches = [
            item for item in balance.details if item.currency.upper() == currency
        ]
        if len(matches) != 1:
            return None, "demo_settlement_currency_balance_unavailable"
        detail = matches[0]
        if detail.equity <= 0:
            return None, "demo_settlement_currency_equity_exhausted"
        return (
            DemoRiskCapital(
                risk_equity=detail.equity,
                available_equity=min(
                    detail.equity,
                    max(D("0"), detail.available_equity),
                ),
                currency=currency,
                basis=f"single_currency:{currency}",
            ),
            "",
        )
    if account_level in {"3", "4"}:
        if balance.adjusted_equity <= 0:
            return None, "demo_account_adjusted_equity_unavailable"
        return (
            DemoRiskCapital(
                risk_equity=balance.adjusted_equity,
                available_equity=min(
                    balance.adjusted_equity,
                    max(D("0"), balance.available_equity),
                ),
                currency="USD",
                basis="account_adjusted:USD",
            ),
            "",
        )
    return None, "unsupported_okx_demo_account_level"
