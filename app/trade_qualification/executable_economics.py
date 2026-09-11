"""Compare unchanged candidate geometry with a sampled executable reference.

This is a pure *current-quote* cost comparison, not an Execution Recheck gate.
It does not authenticate sources, verify an earlier gate run, check the entry
zone, prove a post-render fetch, or guarantee a market fill. A future coordinator
must independently require the original gate run to have passed. No candidate
price, structural stop/target, cost policy, or earlier report is updated here.
"""

from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from app.trade_qualification.economics import (
    EconomicsPolicy,
    EconomicsResult,
    evaluate_economics,
)
from app.trade_qualification.location import (
    ExecutableQuote,
    _revalidate,
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
_PRICE = TypeAdapter(Price)


def _original_price(value):
    # Preserve independently valid original prices even when the existing
    # evaluator stops earlier at a missing policy. Invalid raw values are not
    # coerced into plausible prices or injected into the strict audit envelope.
    try:
        return _PRICE.validate_python(value, strict=True)
    except ValueError:
        return None


def _measurements(candidate, execution, reference):
    """Compose existing cost results; never duplicate fees/funding/RR formulas."""
    measurements = {
        "entry_delta_per_base": None,
        "adverse_entry_delta_per_base": None,
        "adverse_entry_delta_bps": None,
        "candidate_cost_adjusted_risk_per_base": None,
        "execution_cost_adjusted_risk_per_base": None,
        "worst_cost_adjusted_risk_per_base": None,
        "net_rr_delta": None,
        "worst_net_rr": None,
    }
    with localcontext(Context(prec=100)):
        if reference is not None and candidate.candidate_entry is not None:
            delta = reference - candidate.candidate_entry
            adverse = max(D(0), delta if candidate.direction == "long" else -delta)
            measurements.update(
                entry_delta_per_base=delta,
                adverse_entry_delta_per_base=adverse,
                adverse_entry_delta_bps=adverse / candidate.candidate_entry * D(10000),
            )
        for label, result in (("candidate", candidate), ("execution", execution)):
            if result is not None and result.cost_per_base is not None:
                measurements[f"{label}_cost_adjusted_risk_per_base"] = (
                    abs(result.candidate_entry - result.stop_loss)
                    + result.cost_per_base
                )
        candidate_risk = measurements["candidate_cost_adjusted_risk_per_base"]
        execution_risk = measurements["execution_cost_adjusted_risk_per_base"]
        if candidate_risk is not None and execution_risk is not None:
            measurements["worst_cost_adjusted_risk_per_base"] = max(
                candidate_risk, execution_risk
            )
        if (
            execution is not None
            and candidate.net_rr is not None
            and execution.net_rr is not None
        ):
            measurements["net_rr_delta"] = execution.net_rr - candidate.net_rr
            measurements["worst_net_rr"] = min(candidate.net_rr, execution.net_rr)
    return measurements


class ExecutableEconomicsResult(QualificationModel):
    """Immutable audit; ``execution_result.candidate_entry`` is only a scenario.

    The original entry remains ``original_candidate_entry`` and
    ``candidate_result.candidate_entry``. Worst risk/RR span the two evaluated
    scenarios, not all possible fills, slippage, or market outcomes.
    """

    report_id: ReportId
    instrument_id: Text
    direction: Literal["long", "short"]
    evaluated_at: datetime
    original_candidate_entry: Price | None
    stop_loss: Price | None
    take_profit: Price | None
    executable_reference: Price | None
    candidate_result: EconomicsResult
    execution_result: EconomicsResult | None
    passed: bool
    code: Text
    reason: Text
    failure_stage: Literal["candidate", "execution"] | None
    entry_delta_per_base: Decimal | None = None
    adverse_entry_delta_per_base: Decimal | None = Field(default=None, ge=0)
    adverse_entry_delta_bps: Decimal | None = Field(default=None, ge=0)
    candidate_cost_adjusted_risk_per_base: Decimal | None = Field(default=None, gt=0)
    execution_cost_adjusted_risk_per_base: Decimal | None = Field(default=None, gt=0)
    worst_cost_adjusted_risk_per_base: Decimal | None = Field(default=None, gt=0)
    net_rr_delta: Decimal | None = None
    worst_net_rr: Decimal | None = None
    execution_authority: Literal[False] = False
    market_fill_guaranteed: Literal[False] = False
    historical_gates_verified: Literal[False] = False
    post_render_barrier_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    _aware = field_validator("evaluated_at")(require_aware)

    @field_validator(
        "execution_authority",
        "market_fill_guaranteed",
        "historical_gates_verified",
        "post_render_barrier_verified",
        "source_authenticity_verified",
        mode="before",
    )
    @classmethod
    def exact_false(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError(
                "executable economics cannot grant authority or guarantees"
            )
        return value

    @model_validator(mode="after")
    def consistent_comparison(self):
        candidate, execution = self.candidate_result, self.execution_result
        identity = (
            self.report_id,
            self.instrument_id,
            self.direction,
            self.evaluated_at,
        )
        for result in (candidate, execution):
            if (
                result is not None
                and (
                    result.report_id,
                    result.instrument_id,
                    result.direction,
                    result.evaluated_at,
                )
                != identity
            ):
                raise ValueError("economics comparison identities differ")
        for original, evaluated in zip(
            (self.original_candidate_entry, self.stop_loss, self.take_profit),
            (candidate.candidate_entry, candidate.stop_loss, candidate.take_profit),
            strict=True,
        ):
            if evaluated is not None and original != evaluated:
                raise ValueError("original candidate geometry changed")
        if execution is None:
            if self.executable_reference is not None or candidate.passed:
                raise ValueError("execution evidence is missing")
        else:
            if self.executable_reference is None or (
                execution.candidate_entry,
                execution.stop_loss,
                execution.take_profit,
            ) != (self.executable_reference, self.stop_loss, self.take_profit):
                raise ValueError("execution scenario changed structural geometry")
            if (
                candidate.policy != execution.policy
                or candidate.policy_sha256 != execution.policy_sha256
            ):
                raise ValueError("cost scenarios use different policies")
            if candidate.quote_sha256 is None or (
                execution.quote_sha256 is not None
                and execution.quote_sha256 != candidate.quote_sha256
            ):
                raise ValueError("cost scenarios use different source quotes")
        failed = candidate if not candidate.passed else execution
        passed = candidate.passed and execution is not None and execution.passed
        expected_stage = (
            None if passed else "candidate" if not candidate.passed else "execution"
        )
        if (
            self.passed != passed
            or self.code != ("passed" if passed else failed.code)
            or self.failure_stage != expected_stage
        ):
            raise ValueError(
                "candidate failure cannot be repaired by execution scenario"
            )
        for field, expected in _measurements(
            candidate, execution, self.executable_reference
        ).items():
            if getattr(self, field) != expected:
                raise ValueError("economics comparison measurement mismatch")
        return self


def evaluate_executable_economics(
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
) -> ExecutableEconomicsResult:
    """Require both original-entry and sampled-reference economics to pass.

    Both evaluations reuse the existing economics engine and its explicit cost,
    identity, source timestamp, and freshness checks. Candidate failures retain
    first-failure priority, including when a favorable reference would pass.
    Invalid identity/evaluation clocks raise as in ``evaluate_economics``; other
    input failures produce that engine's deterministic codes. Missing complete
    source/geometry prevents inventing an execution result. No earlier gate or
    entry zone is inspected, and no future-fill or execution authority is issued.
    """
    if type(current_time) is not datetime:
        raise ValueError("an exact aware evaluation datetime is required")
    candidate = evaluate_economics(
        report_id=report_id,
        instrument_id=instrument_id,
        direction=direction,
        candidate_entry=candidate_entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        quote=quote,
        policy=policy,
        current_time=current_time,
    )
    execution = None
    reference = None
    # The original evaluator emits a quote digest only after strict quote,
    # freshness, geometry and identity validation. Rebuild that exact same quote
    # for the second scenario; never accept a caller-authored EconomicsResult.
    if candidate.quote_sha256 is not None:
        checked_quote = _revalidate(quote, ExecutableQuote)
        if quote_fingerprint(checked_quote) != candidate.quote_sha256:
            raise ValueError("quote changed during economics comparison")
        reference = checked_quote.ask if direction == "long" else checked_quote.bid
        execution = evaluate_economics(
            report_id=report_id,
            instrument_id=instrument_id,
            direction=direction,
            candidate_entry=reference,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            quote=checked_quote,
            policy=candidate.policy,
            current_time=candidate.evaluated_at,
        )
    passed = candidate.passed and execution is not None and execution.passed
    failed = candidate if not candidate.passed else execution
    stage = None if passed else "candidate" if not candidate.passed else "execution"
    return ExecutableEconomicsResult(
        report_id=candidate.report_id,
        instrument_id=candidate.instrument_id,
        direction=candidate.direction,
        evaluated_at=candidate.evaluated_at,
        original_candidate_entry=_original_price(candidate_entry),
        stop_loss=_original_price(stop_loss),
        take_profit=_original_price(take_profit),
        executable_reference=reference,
        candidate_result=candidate,
        execution_result=execution,
        passed=passed,
        code="passed" if passed else failed.code,
        reason=(
            "Both unchanged-candidate and sampled-reference cost scenarios pass; not a fill guarantee or recheck gate."
            if passed
            else f"{stage.capitalize()} economics failed: {failed.code}; no other gate is repaired."
        ),
        failure_stage=stage,
        **_measurements(candidate, execution, reference),
    )
