"""Projected fixed-candidate and executable-reference costs from raw V2 public data.

The original G12 entry, stop, target and cost assumptions remain fixed. Public
funding is the next-settlement forecast, not realized account funding or a rate
generation timestamp. This calculation cannot reserve risk or authorize an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import data, data_v2
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.current_conditions_v2 import BASE_STRATEGIES
from app.trade_qualification.data_v2 import DataQualificationResultV2
from app.trade_qualification.economics import (
    _INVALID,
    EconomicsPolicy,
    _evaluate_economics_numeric_tail,
)
from app.trade_qualification.engine import PreEvidenceRun
from app.trade_qualification.executable_economics import (
    _compare_economics_numeric_tail,
)
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.recheck_models import copy_recheck_origin


@dataclass(frozen=True, slots=True)
class ProjectedCostScenarioV2:
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    code: str
    measurements: tuple[tuple[str, Decimal], ...]

    @property
    def math_checks_passed(self) -> bool:
        return self.code == "passed"

    def measured(self, name: str) -> Decimal | None:
        return dict(self.measurements).get(name)


@dataclass(frozen=True, slots=True)
class CurrentProjectedEconomicsDiagnosticV2:
    code: str
    failure_stage: Literal["quote", "candidate", "execution"] | None
    candidate: ProjectedCostScenarioV2 | None
    execution: ProjectedCostScenarioV2 | None
    original_entry: Decimal
    original_stop_loss: Decimal
    original_take_profit: Decimal
    executable_reference: Decimal
    comparison: tuple[tuple[str, Decimal | None], ...]
    origin_sha256: str
    original_candidate_sha256: str
    original_event_key: str
    original_policy_sha256: str
    economics_policy_sha256: str
    funding_periods: int
    public_bundle_sha256: str
    quote_bundle_sha256: str
    current_g1_sha256: str
    current_source_sha256: str
    quote_inspection_sha256: str
    funding_pair_sha256: str
    observed_at: datetime
    record_kind: str = field(default="post_g12_projected_economics_v2", init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    # These cannot be derived from public bytes or the projected old policy.
    actual_account_fee_bps: None = field(default=None, init=False)
    actual_account_funding: None = field(default=None, init=False)
    actual_margin_cost: None = field(default=None, init=False)
    fixed_protection_rechecked: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def projected_math_passed(self) -> bool:
        return (
            self.candidate is not None
            and self.candidate.math_checks_passed
            and self.execution is not None
            and self.execution.math_checks_passed
        )


def _scenario(*, entry, stop, target, direction, quote, policy):
    if not (stop < entry < target if direction == "long" else target < entry < stop):
        return ProjectedCostScenarioV2(
            entry, stop, target, "economics_geometry_invalid", ()
        )
    measurements = {}
    try:
        with localcontext(Context(prec=100)):
            code, _ = _evaluate_economics_numeric_tail(
                entry=entry,
                direction=direction,
                stop=stop,
                target=target,
                bid=quote.ticker.bid,
                ask=quote.ticker.ask,
                funding_rate=quote.funding.forecast.rate,
                checked_policy=policy,
                evidence=measurements,
            )
    except _INVALID:
        measurements.clear()
        code = "economics_arithmetic_invalid"
    return ProjectedCostScenarioV2(
        entry,
        stop,
        target,
        code,
        tuple(sorted(measurements.items())),
    )


def _comparison(candidate, execution, entry, reference, direction, stop):
    values = {
        "entry_delta_per_base": None,
        "adverse_entry_delta_per_base": None,
        "adverse_entry_delta_bps": None,
        "candidate_cost_adjusted_risk_per_base": None,
        "execution_cost_adjusted_risk_per_base": None,
        "worst_cost_adjusted_risk_per_base": None,
        "net_rr_delta": None,
        "worst_net_rr": None,
    }
    if candidate is None or execution is None:
        return tuple(sorted(values.items()))
    with localcontext(Context(prec=100)):
        values.update(
            _compare_economics_numeric_tail(
                "entry", reference=reference, entry=entry, direction=direction
            )
        )
        for label, scenario in (("candidate", candidate), ("execution", execution)):
            cost = scenario.measured("cost_per_base")
            if cost is not None:
                values[f"{label}_cost_adjusted_risk_per_base"] = (
                    _compare_economics_numeric_tail(
                        "cost", entry=scenario.entry, stop=stop, cost=cost
                    )
                )
        candidate_risk = values["candidate_cost_adjusted_risk_per_base"]
        execution_risk = values["execution_cost_adjusted_risk_per_base"]
        if candidate_risk is not None and execution_risk is not None:
            values["worst_cost_adjusted_risk_per_base"] = (
                _compare_economics_numeric_tail(
                    "risk", candidate_risk=candidate_risk, execution_risk=execution_risk
                )
            )
        candidate_net, execution_net = (
            scenario.measured("net_rr") for scenario in (candidate, execution)
        )
        if candidate_net is not None and execution_net is not None:
            values.update(
                _compare_economics_numeric_tail(
                    "rr", candidate_net=candidate_net, execution_net=execution_net
                )
            )
    return tuple(sorted(values.items()))


def evaluate_current_projected_economics_v2(packet, *, origin, current_g1, observed_at):
    """Recompute both fixed-geometry scenarios from the same fresh V2 quote.

    Candidate failure has priority even when the current reference improves.
    A cost-policy age stricter than the V2 quote profile remains binding, also
    for the funding exchange-return timestamp. No source field is synthesized.
    """
    if type(current_g1) is not DataQualificationResultV2:
        raise ValueError("economics_v2_exact_g1_required")
    original = copy_recheck_origin(origin)
    pre = original.evidence.pre_evidence
    if (
        type(pre) is not PreEvidenceRun
        or pre.prefix.intent.strategy not in BASE_STRATEGIES
    ):
        raise ValueError("economics_v2_base_original_required")
    now = data._utc(observed_at)
    if (
        not original.publication_completed_at
        < current_g1.evaluated_at
        <= now
        < original.deadline
    ):
        raise ValueError("economics_v2_outside_original_window")
    intent = pre.prefix.intent
    checked, _ = public_v2._parts(packet)
    document = decode(checked.packet_json, public_v2.MAX_PACKET_BYTES)
    if (
        document["stage"] != "post_publication"
        or document["barrier_completed_at"]
        != original.publication_completed_at.isoformat()
        or document["report_id"] != intent.report_id
        or document["instrument_id"] != intent.instrument_id
    ):
        raise ValueError("economics_v2_post_g12_scope_mismatch")
    g1 = data_v2.verify_public_market_data_v2(
        current_g1,
        checked,
        expected_bundle_sha256=checked.bundle_sha256,
        policy=pre.policy.prefix.data,
        evaluated_at=current_g1.evaluated_at,
    )
    if (
        not g1.passed
        or g1.report_id != intent.report_id
        or g1.instrument_id != intent.instrument_id
        or g1.source_sha256 is None
    ):
        raise ValueError("economics_v2_current_g1_rejected")
    context = public_market_context_v2(
        checked, expected_bundle_sha256=checked.bundle_sha256, evaluated_at=now
    )
    quote = context.quote
    if (quote.report_id, quote.instrument_id) != (
        intent.report_id,
        intent.instrument_id,
    ):
        raise ValueError("economics_v2_quote_scope_mismatch")
    policy = pre.policy.economics
    if type(policy) is not EconomicsPolicy:
        raise ValueError("economics_v2_original_cost_policy_required")
    entry, stop, target = (
        intent.candidate_entry,
        pre.result.stop_loss,
        pre.result.take_profit,
    )
    if (
        type(entry) is not Decimal
        or type(stop) is not Decimal
        or type(target) is not Decimal
    ):
        raise ValueError("economics_v2_fixed_geometry_required")
    reference = quote.ticker.ask if intent.direction == "long" else quote.ticker.bid
    values = {
        "original_entry": entry,
        "original_stop_loss": stop,
        "original_take_profit": target,
        "executable_reference": reference,
        "origin_sha256": original.evaluation_sha256,
        "original_candidate_sha256": sha(
            canonical(original.candidate.model_dump(mode="json", round_trip=True))
        ),
        "original_event_key": original.original_event_key,
        "original_policy_sha256": original.original_policy_sha256,
        "economics_policy_sha256": sha(
            policy.model_dump_json(round_trip=True).encode()
        ),
        "funding_periods": policy.funding_periods,
        "public_bundle_sha256": checked.bundle_sha256,
        "quote_bundle_sha256": g1.quote_bundle_sha256,
        "current_g1_sha256": g1.evaluation_sha256,
        "current_source_sha256": g1.source_sha256,
        "quote_inspection_sha256": sha(context.quote_inspection_json),
        "funding_pair_sha256": g1.funding_pair_sha256,
        "observed_at": now,
    }
    maximum_age = timedelta(seconds=policy.maximum_quote_age_seconds)
    if any(
        now - stamp > maximum_age
        for stamp in (
            quote.ticker.source_generated_at,
            quote.mark.exchange_data_return_at,
            quote.funding.exchange_data_return_at,
            quote.capture_completed_at,
        )
    ):
        return CurrentProjectedEconomicsDiagnosticV2(
            **values,
            code="projected_quote_source_stale",
            failure_stage="quote",
            candidate=None,
            execution=None,
            comparison=_comparison(
                None, None, entry, reference, intent.direction, stop
            ),
        )
    candidate = _scenario(
        entry=entry,
        stop=stop,
        target=target,
        direction=intent.direction,
        quote=quote,
        policy=policy,
    )
    execution = _scenario(
        entry=reference,
        stop=stop,
        target=target,
        direction=intent.direction,
        quote=quote,
        policy=policy,
    )
    stage = (
        "candidate"
        if not candidate.math_checks_passed
        else "execution"
        if not execution.math_checks_passed
        else None
    )
    return CurrentProjectedEconomicsDiagnosticV2(
        **values,
        code=(
            candidate.code
            if stage == "candidate"
            else execution.code
            if stage == "execution"
            else "passed"
        ),
        failure_stage=stage,
        candidate=candidate,
        execution=execution,
        comparison=_comparison(
            candidate, execution, entry, reference, intent.direction, stop
        ),
    )
