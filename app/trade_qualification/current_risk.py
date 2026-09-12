"""Recompute costs and current account claims for two unchanged-size scenarios.

This is an offline calculation, not G13, a portfolio reservation, a fill promise
or authentication of an account/publication. Original G1--G12 facts must still
be independently replayed and a runtime must collect/reconcile under its lock.
The maximum displays span only original entry and the sampled executable quote;
they MUST NOT be used as exact atomic-ledger reservation amounts.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.trade_evidence.gates import _digest
from app.trade_qualification.economics import EconomicsResult
from app.trade_qualification.engine import PortfolioInputs, _copy, _preflight
from app.trade_qualification.event_models import Digest
from app.trade_qualification.executable_economics import (
    ExecutableEconomicsResult,
    evaluate_executable_economics,
)
from app.trade_qualification.location import ExecutableQuote, _revalidate
from app.trade_qualification.models import (
    Price,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)
from app.trade_qualification.portfolio import PortfolioRiskResult, evaluate_portfolio
from app.trade_qualification.recheck_models import (
    RecheckOrigin,
    copy_recheck_origin,
)
from app.trade_qualification.service import _plain

_FALSE_FLAGS = (
    "execution_authority",
    "account_evidence_authenticated",
    "atomic_risk_reserved",
    "execution_recheck_performed",
    "publication_observed_here",
    "market_fill_guaranteed",
    "original_sources_replayed",
)


def _guard(value):
    """No serializers on unknown model types or removed/hidden raw fields."""
    if isinstance(value, BaseModel):
        if type(value) not in {CurrentRiskResult, ExecutableEconomicsResult}:
            _preflight(value)
            return
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("dirty_current_risk_record")
        for item in value.__dict__.values():
            _guard(item)
    else:
        _preflight(value)


def _status(economics, candidate, execution):
    if not economics.passed:
        if candidate is not None or execution is not None:
            raise ValueError("risk_cannot_run_after_economics_failure")
        return "economics", economics.code
    if candidate is None:
        raise ValueError("current_candidate_risk_missing")
    if not candidate.passed:
        if execution is not None:
            raise ValueError("execution_scenario_cannot_repair_candidate_risk")
        return "candidate_risk", candidate.code
    if execution is None:
        raise ValueError("current_execution_risk_missing")
    return (None, "passed") if execution.passed else ("execution_risk", execution.code)


def _maxima(candidate, execution):
    fields = {
        "maximum_displayed_risk_amount": "max_loss_amount",
        "maximum_displayed_notional": "notional",
        "maximum_displayed_margin": "required_margin",
    }
    if (
        candidate is None
        or execution is None
        or not candidate.passed
        or not execution.passed
    ):
        return dict.fromkeys(fields)
    return {
        name: max(getattr(candidate, field), getattr(execution, field))
        for name, field in fields.items()
    }


class CurrentRiskResult(QualificationModel):
    report_id: ReportId
    instrument_id: Text
    direction: Literal["long", "short"]
    observed_at: datetime
    origin_sha256: Digest
    original_event_key: Digest
    original_entry: Price
    original_stop_loss: Price
    original_take_profit: Price
    original_requested_contracts: Price
    original_requested_leverage: int = Field(ge=1, le=125)
    current_risk_inputs_sha256: Digest
    economics: ExecutableEconomicsResult
    candidate_risk: PortfolioRiskResult | None
    execution_risk: PortfolioRiskResult | None
    passed: bool
    code: Text
    failure_stage: Literal["economics", "candidate_risk", "execution_risk"] | None
    maximum_displayed_risk_amount: Decimal | None = Field(default=None, gt=0)
    maximum_displayed_notional: Decimal | None = Field(default=None, gt=0)
    maximum_displayed_margin: Decimal | None = Field(default=None, gt=0)
    execution_authority: Literal[False] = False
    account_evidence_authenticated: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    execution_recheck_performed: Literal[False] = False
    publication_observed_here: Literal[False] = False
    market_fill_guaranteed: Literal[False] = False
    original_sources_replayed: Literal[False] = False

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if type(value) is not datetime:
            raise ValueError("exact_current_risk_clock_required")
        return require_aware(value)

    @field_validator(*_FALSE_FLAGS, mode="before")
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("current_risk_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistent(self):
        _guard(self)
        eco = self.economics
        if (eco.report_id, eco.instrument_id, eco.direction, eco.evaluated_at) != (
            self.report_id,
            self.instrument_id,
            self.direction,
            self.observed_at,
        ) or (eco.original_candidate_entry, eco.stop_loss, eco.take_profit) != (
            self.original_entry,
            self.original_stop_loss,
            self.original_take_profit,
        ):
            raise ValueError("current_risk_changed_original_geometry")
        for result in (self.candidate_risk, self.execution_risk):
            if result is not None and (
                result.report_id,
                result.instrument_id,
                result.direction,
                result.requested_contracts,
                result.requested_leverage,
            ) != (
                self.report_id,
                self.instrument_id,
                self.direction,
                self.original_requested_contracts,
                self.original_requested_leverage,
            ):
                raise ValueError("current_risk_changed_size_or_identity")
        stage, code = _status(eco, self.candidate_risk, self.execution_risk)
        if (self.failure_stage, self.code, self.passed) != (stage, code, stage is None):
            raise ValueError("current_risk_first_failure_mismatch")
        if any(
            getattr(self, name) != expected
            for name, expected in _maxima(
                self.candidate_risk, self.execution_risk
            ).items()
        ):
            raise ValueError("current_risk_display_mismatch")
        return self

    @property
    def evaluation_sha256(self):
        return _digest(copy_current_risk(self))


def copy_current_risk(result: CurrentRiskResult) -> CurrentRiskResult:
    if type(result) is not CurrentRiskResult:
        raise ValueError("exact_current_risk_result_required")
    _guard(result)
    return CurrentRiskResult.model_validate(_plain(result), strict=True)


def evaluate_current_risk(
    origin: RecheckOrigin,
    *,
    quote: ExecutableQuote | None,
    current_risk_inputs: PortfolioInputs,
    observed_at: datetime,
) -> CurrentRiskResult:
    """Require both scenario costs and fresh same-account portfolio checks.

    The original account identity, contract economics/classification, size,
    leverage and policies cannot change to repair a failure. Instrument source
    stamps may refresh; a changed contract specification cancels this candidate.
    Quote request/barrier provenance and actual IO remain the caller's job.
    Invalid identity/record admission raises; valid economic/risk denials return
    an auditable first failure and do not run later checks.
    """
    original = copy_recheck_origin(origin)
    if type(observed_at) is not datetime:
        raise ValueError("exact_current_risk_clock_required")
    now = require_aware(observed_at)
    if not original.publication_completed_at < now < original.deadline:
        raise ValueError("current_risk_outside_original_window")
    risk = _copy(current_risk_inputs, PortfolioInputs)
    pre = original.evidence.pre_evidence
    old = pre.risk_inputs
    if (risk.requested_contracts, risk.requested_leverage) != (
        old.requested_contracts,
        old.requested_leverage,
    ):
        raise ValueError("current_risk_cannot_resize_or_releverage")
    # Missing data is returned by the existing evaluator as an explicit denial.
    # A different identity/contract is not the same candidate at all.
    if risk.account is not None and risk.account.account_id != old.account.account_id:
        raise ValueError("current_risk_account_changed")
    if risk.instrument is not None:
        for name in type(risk.instrument).model_fields:
            if name not in {"source_sha256", "observed_at", "received_at"} and getattr(
                risk.instrument, name
            ) != getattr(old.instrument, name):
                raise ValueError("current_risk_contract_changed")
    checked_quote = None if quote is None else _revalidate(quote, ExecutableQuote)
    intent = pre.prefix.intent
    candidate = pre.result
    economics = evaluate_executable_economics(
        report_id=intent.report_id,
        instrument_id=intent.instrument_id,
        direction=intent.direction,
        candidate_entry=intent.candidate_entry,
        stop_loss=candidate.stop_loss,
        take_profit=candidate.take_profit,
        quote=checked_quote,
        policy=pre.policy.economics,
        current_time=now,
    )

    def portfolio(scenario: EconomicsResult):
        return evaluate_portfolio(
            report_id=intent.report_id,
            instrument_id=intent.instrument_id,
            direction=intent.direction,
            candidate_entry=scenario.candidate_entry,
            stop_loss=candidate.stop_loss,
            round_trip_cost_per_base=scenario.cost_per_base,
            requested_contracts=old.requested_contracts,
            requested_leverage=old.requested_leverage,
            instrument=risk.instrument,
            account=risk.account,
            policy=pre.policy.portfolio,
            authority=risk.authority,
            current_time=now,
        )

    candidate_risk = portfolio(economics.candidate_result) if economics.passed else None
    execution_risk = (
        portfolio(economics.execution_result)
        if candidate_risk is not None and candidate_risk.passed
        else None
    )
    stage, code = _status(economics, candidate_risk, execution_risk)
    return CurrentRiskResult.model_validate(
        _plain(
            dict(
                report_id=intent.report_id,
                instrument_id=intent.instrument_id,
                direction=intent.direction,
                observed_at=now,
                origin_sha256=original.evaluation_sha256,
                original_event_key=original.original_event_key,
                original_entry=intent.candidate_entry,
                original_stop_loss=candidate.stop_loss,
                original_take_profit=candidate.take_profit,
                original_requested_contracts=old.requested_contracts,
                original_requested_leverage=old.requested_leverage,
                current_risk_inputs_sha256=_digest(risk),
                economics=economics,
                candidate_risk=candidate_risk,
                execution_risk=execution_risk,
                passed=stage is None,
                code=code,
                failure_stage=stage,
                **_maxima(candidate_risk, execution_risk),
            )
        ),
        strict=True,
    )


def verify_current_risk(
    result: CurrentRiskResult, origin: RecheckOrigin, **inputs
) -> CurrentRiskResult:
    """Recompute, not accept caller-written PASS fields or cached old account IO."""
    checked = copy_current_risk(result)
    actual = evaluate_current_risk(origin, **inputs)
    if checked != actual:
        raise ValueError("current_risk_replay_mismatch")
    return actual
