"""Conservative cost arithmetic after entry/structure, without trading authority.

Fees/slippage/holding assumptions must be explicit. Observed spread is charged
once as an additional conservative allowance, even with an executable entry.
Adverse funding is projected over the stated holding periods; favorable funding
is never treated as a credit. This scenario is not a forecast or calibration.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import ROUND_CEILING, Context, Decimal, DecimalException, localcontext
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from app.trade_qualification.location import (
    ExecutableQuote,
    _revalidate,
    inspect_executable_quote,
    quote_fingerprint,
)
from app.trade_qualification.models import (
    Price,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)

D = Decimal
Bps = Annotated[Decimal, Field(ge=0, le=1000, max_digits=30, decimal_places=20)]
Cost = Annotated[Decimal, Field(ge=0, max_digits=40, decimal_places=20)]
_PRICE = TypeAdapter(Price)
_INVALID = (ValueError, TypeError, AttributeError, OverflowError, DecimalException)


class EconomicsPolicy(QualificationModel):
    policy_id: Text
    round_trip_fee_bps: Bps
    round_trip_slippage_bps: Bps
    funding_buffer_bps: Bps
    funding_periods: int = Field(ge=1, le=30)
    minimum_net_rr: Decimal = Field(gt=0, le=1000, max_digits=30, decimal_places=20)
    maximum_spread_bps: Bps
    maximum_funding_bps: Bps
    maximum_quote_age_seconds: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def funding_budget_within_limit(self):
        if self.funding_buffer_bps > self.maximum_funding_bps:
            raise ValueError("funding buffer cannot exceed the total funding cap")
        return self


class EconomicsResult(QualificationModel):
    report_id: ReportId
    instrument_id: Text
    direction: Literal["long", "short"]
    evaluated_at: datetime
    passed: bool
    code: Text
    reason: Text
    candidate_entry: Price | None = None
    stop_loss: Price | None = None
    take_profit: Price | None = None
    policy: EconomicsPolicy | None = None
    policy_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    quote_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    spread_bps: Decimal | None = None
    funding_bps: Decimal | None = None
    total_cost_bps: Decimal | None = None
    cost_per_base: Cost | None = None
    gross_rr: Decimal | None = None
    net_rr: Decimal | None = None
    execution_authority: Literal[False] = False

    _aware = field_validator("evaluated_at")(require_aware)

    @field_validator("execution_authority", mode="before")
    @classmethod
    def exact_false(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("economics cannot grant execution authority")
        return value

    @model_validator(mode="after")
    def passing_cost_evidence(self):
        if self.passed != (self.code == "passed"):
            raise ValueError("only passing economics uses passed")
        if self.passed:
            if any(
                value is None
                for value in (
                    self.candidate_entry,
                    self.stop_loss,
                    self.take_profit,
                    self.policy,
                    self.policy_sha256,
                    self.quote_sha256,
                    self.spread_bps,
                    self.funding_bps,
                    self.total_cost_bps,
                    self.cost_per_base,
                    self.gross_rr,
                    self.net_rr,
                )
            ):
                raise ValueError("passing economics requires complete cost evidence")
            if not 0 < self.policy.minimum_net_rr <= self.net_rr <= self.gross_rr:
                raise ValueError("net RR must pass the explicit minimum")
        return self


def evaluate_economics(
    *,
    report_id: str,
    instrument_id: str,
    direction: Literal["long", "short"],
    candidate_entry: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal,
    quote: ExecutableQuote | None,
    policy: EconomicsPolicy | None,
    current_time: datetime,
) -> EconomicsResult:
    """Check unchanged geometry; a pass never repairs an earlier failed gate.

    Numeric/quote/policy defects return a deterministic failure. Invalid result
    identity or an invalid evaluation clock raises, because it cannot identify
    a valid report. Pure inputs do not authenticate fee rates or market sources.
    Cost per base is rounded UP to 20 decimal places before net RR/risk use.
    """
    identity = {
        "report_id": TypeAdapter(ReportId).validate_python(report_id, strict=True),
        "instrument_id": TypeAdapter(Text).validate_python(instrument_id, strict=True),
        "direction": TypeAdapter(Literal["long", "short"]).validate_python(
            direction, strict=True
        ),
        "evaluated_at": require_aware(current_time),
    }
    evidence = {}

    def result(code, reason):
        return EconomicsResult(
            **identity, **evidence, passed=code == "passed", code=code, reason=reason
        )

    if policy is None:
        return result(
            "cost_policy_missing",
            "Explicit costs and holding assumptions are required.",
        )
    try:
        checked_policy = _revalidate(policy, EconomicsPolicy)
        evidence.update(
            policy=checked_policy,
            policy_sha256=hashlib.sha256(
                checked_policy.model_dump_json(round_trip=True).encode()
            ).hexdigest(),
        )
    except _INVALID:
        return result(
            "cost_policy_invalid", "Cost policy failed strict reconstruction."
        )
    try:
        entry, stop, target = (
            _PRICE.validate_python(value, strict=True)
            for value in (candidate_entry, stop_loss, take_profit)
        )
    except _INVALID:
        return result(
            "economics_geometry_invalid",
            "Entry and structural prices must be finite positive Decimals.",
        )
    evidence.update(candidate_entry=entry, stop_loss=stop, take_profit=target)
    if not (stop < entry < target if direction == "long" else target < entry < stop):
        return result(
            "economics_geometry_invalid",
            "Unchanged structural geometry has the wrong direction.",
        )
    checked_quote, code = inspect_executable_quote(
        quote,
        current_time=identity["evaluated_at"],
        max_quote_age_seconds=checked_policy.maximum_quote_age_seconds,
    )
    if checked_quote is None:
        return result(
            code, "Costs require a valid quote with independent fresh components."
        )
    if (
        checked_quote.report_id != report_id
        or checked_quote.instrument_id != instrument_id
    ):
        return result("identity_mismatch", "Quote and cost report identities differ.")
    evidence["quote_sha256"] = quote_fingerprint(checked_quote)
    try:
        with localcontext(Context(prec=100)):
            spread = (checked_quote.ask - checked_quote.bid) / entry * D(10000)
            evidence["spread_bps"] = spread
            if spread > checked_policy.maximum_spread_bps:
                return result(
                    "spread_above_limit",
                    "Observed spread exceeds the configured ceiling.",
                )
            adverse_rate = max(
                D(0), checked_quote.funding_rate * (1 if direction == "long" else -1)
            )
            funding = max(
                checked_policy.funding_buffer_bps,
                adverse_rate * D(10000) * checked_policy.funding_periods,
            )
            evidence["funding_bps"] = funding
            if funding > checked_policy.maximum_funding_bps:
                return result(
                    "funding_above_limit",
                    "Projected adverse funding exceeds the total holding-period cap.",
                )
            non_spread_cost_bps = (
                checked_policy.round_trip_fee_bps
                + checked_policy.round_trip_slippage_bps
                + funding
            )
            cost_bps = non_spread_cost_bps + spread
            # Spread is already an exact money amount. Dividing by entry for
            # audit bps and multiplying back can turn a repeating decimal's
            # rounding residue into a whole extra cost quantum at CEILING.
            # All operands are bounded finite decimal inputs; this direct
            # money calculation is exact inside the 100-digit local context.
            cost_per_base = (
                checked_quote.ask
                - checked_quote.bid
                + entry * non_spread_cost_bps / D(10000)
            ).quantize(D("1e-20"), rounding=ROUND_CEILING)
            risk, reward = abs(entry - stop), abs(target - entry)
            gross = reward / risk
            net = (reward - cost_per_base) / (risk + cost_per_base)
            evidence.update(
                total_cost_bps=cost_bps,
                cost_per_base=cost_per_base,
                gross_rr=gross,
                net_rr=net,
            )
            if net < checked_policy.minimum_net_rr:
                return result(
                    "net_rr_below_minimum",
                    "Costs leave insufficient reward for the unchanged structural risk.",
                )
    except _INVALID:
        # Keep a valid envelope even if an arithmetic edge cannot fit contracts.
        evidence.pop("cost_per_base", None)
        evidence.pop("gross_rr", None)
        evidence.pop("net_rr", None)
        return result(
            "economics_arithmetic_invalid",
            "Bounded cost arithmetic could not be validated.",
        )
    return result(
        "passed",
        "Explicit conservative costs pass the minimum net RR; other gates remain independent.",
    )
