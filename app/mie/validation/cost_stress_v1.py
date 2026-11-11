"""Replay preregistered cost sensitivity on an independently pinned return path."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import CostModel, Gate3Contract, Sha256, require_utc
from app.mie.validation.costs import ReturnPathMetrics, evaluate_costed_return_path
from app.mie.validation.metrics import _ARITHMETIC_CONTEXT, _bounded_decimal_tuple

_COST_FIELDS = ("fee_bps", "funding_bps", "spread_bps", "slippage_bps")
_MAX_PATH_BYTES = 64 * 1024 * 1024
_MAX_PLAN_BYTES = 64 * 1024
_MAX_REPORT_BYTES = 1024 * 1024
_ONE = Decimal(1)
_Scenario = Literal[
    "cost_1x",
    "cost_1_5x",
    "cost_2x",
    "spread_widening",
    "slippage_stress",
    "funding_stress",
]


class CostStressPlanV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.cost_stress.plan.v1"] = (
        "ctcc.mie.cost_stress.plan.v1"
    )
    candidate_sha256: Sha256
    preregistered_at: datetime
    cost_model: CostModel
    observation_interval_seconds: int = Field(gt=0)
    cvar_confidence_level: Decimal = Field(gt=0, lt=1)
    spread_multiplier: Decimal = Field(gt=1)
    slippage_multiplier: Decimal = Field(gt=1)
    funding_multiplier: Decimal = Field(gt=1)

    @field_validator("preregistered_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return require_utc(value, "preregistered_at")

    @field_validator(
        "cvar_confidence_level",
        "spread_multiplier",
        "slippage_multiplier",
        "funding_multiplier",
    )
    @classmethod
    def validate_decimal(cls, value: Decimal, info) -> Decimal:
        _bounded_decimal_tuple(value, info.field_name)
        return value

    @field_validator("cost_model")
    @classmethod
    def validate_cost(cls, value: CostModel) -> CostModel:
        for name in _COST_FIELDS:
            _bounded_decimal_tuple(getattr(value, name), name)
        return value


class CostStressObservationV1(Gate3Contract):
    closed_at: datetime
    decision_at: datetime
    return_available_at: datetime
    asset_return: Decimal = Field(gt=-1)
    exposure: Decimal = Field(ge=-1, le=1)

    @field_validator("closed_at", "decision_at", "return_available_at")
    @classmethod
    def validate_times(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("asset_return", "exposure")
    @classmethod
    def validate_decimal(cls, value: Decimal, info) -> Decimal:
        _bounded_decimal_tuple(value, info.field_name)
        return value


class CostStressPathV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.cost_stress.path.v1"] = (
        "ctcc.mie.cost_stress.path.v1"
    )
    plan_sha256: Sha256
    candidate_sha256: Sha256
    source_replay_sha256: Sha256
    started_at: datetime
    ended_at: datetime
    evaluated_at: datetime
    observations: tuple[CostStressObservationV1, ...] = Field(min_length=1)

    @field_validator("started_at", "ended_at", "evaluated_at")
    @classmethod
    def validate_times(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)


class CostStressScenarioV1(Gate3Contract):
    scenario: _Scenario
    cost_model: CostModel
    gross_compound_return: Decimal
    net_compound_return: Decimal
    maximum_drawdown: Decimal
    cvar_loss: Decimal
    annualized_sharpe_ratio: Decimal | None
    annualized_sortino_ratio: Decimal | None
    turnover: Decimal
    fee_cost: Decimal
    funding_cost: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    total_cost: Decimal


class CostStressReportV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.cost_stress.report.v1"] = (
        "ctcc.mie.cost_stress.report.v1"
    )
    plan_sha256: Sha256
    path_sha256: Sha256
    candidate_sha256: Sha256
    source_replay_sha256: Sha256
    observation_count: int = Field(gt=0)
    observation_interval_seconds: int = Field(gt=0)
    cvar_confidence_level: Decimal = Field(gt=0, lt=1)
    scenarios: tuple[CostStressScenarioV1, ...]
    scope: Literal["declared_normalized_cost_sensitivity"] = (
        "declared_normalized_cost_sensitivity"
    )
    source_authenticity_verified: Literal[False] = False
    economic_oos_accepted: Literal[False] = False
    execution_authority: Literal[False] = False


def _checked_artifact[T: Gate3Contract](
    payload: bytes, expected_sha256: str, contract_type: type[T], limit: int
) -> T:
    if type(payload) is not bytes or not 0 < len(payload) <= limit:
        raise ArtifactVerificationError("cost stress artifact size is invalid")
    return _verify(
        payload, expected_sha256=expected_sha256, contract_type=contract_type
    )


def _validate_path(
    plan: CostStressPlanV1, path: CostStressPathV1, plan_sha256: str
) -> None:
    if (
        path.plan_sha256 != plan_sha256
        or path.candidate_sha256 != plan.candidate_sha256
        or not plan.preregistered_at
        < path.started_at
        < path.ended_at
        <= path.evaluated_at
    ):
        raise ValueError("cost stress plan, candidate, or window mismatch")
    period = timedelta(seconds=plan.observation_interval_seconds)
    opened_at = path.started_at
    for row in path.observations:
        if (
            row.closed_at - opened_at != period
            or row.decision_at > opened_at
            or not row.closed_at <= row.return_available_at <= path.evaluated_at
        ):
            raise ValueError(
                "cost stress observation is missing, late, or out of order"
            )
        opened_at = row.closed_at
    if opened_at != path.ended_at:
        raise ValueError("cost stress path does not cover the declared window")


def _scenario(
    name: _Scenario, model: CostModel, metrics: ReturnPathMetrics
) -> CostStressScenarioV1:
    return CostStressScenarioV1(
        scenario=name,
        cost_model=model,
        gross_compound_return=metrics.gross_compound_return,
        net_compound_return=metrics.net_compound_return,
        maximum_drawdown=metrics.maximum_drawdown,
        cvar_loss=metrics.cvar_loss,
        annualized_sharpe_ratio=metrics.annualized_sharpe_ratio,
        annualized_sortino_ratio=metrics.annualized_sortino_ratio,
        turnover=metrics.turnover,
        fee_cost=metrics.fee_cost,
        funding_cost=metrics.funding_cost,
        spread_cost=metrics.spread_cost,
        slippage_cost=metrics.slippage_cost,
        total_cost=metrics.total_cost,
    )


def evaluate_cost_stress_v1(
    plan_bytes: bytes,
    path_bytes: bytes,
    *,
    expected_plan_sha256: str,
    expected_path_sha256: str,
) -> FrozenGate3Artifact[CostStressReportV1]:
    """Apply all six declared cost scenarios to the same fixed decisions.

    Both digests must be retained outside the supplied artifacts. This function
    performs arithmetic on a normalized return series without opening any data
    source or authorizing Gate 4, economic OOS, or an order.
    """
    plan = _checked_artifact(
        plan_bytes, expected_plan_sha256, CostStressPlanV1, _MAX_PLAN_BYTES
    )
    path = _checked_artifact(
        path_bytes, expected_path_sha256, CostStressPathV1, _MAX_PATH_BYTES
    )
    _validate_path(plan, path, expected_plan_sha256)
    returns = tuple(row.asset_return for row in path.observations)
    exposures = tuple(row.exposure for row in path.observations)
    specifications: tuple[tuple[_Scenario, tuple[str, ...], Decimal], ...] = (
        ("cost_1x", (), _ONE),
        ("cost_1_5x", _COST_FIELDS, Decimal("1.5")),
        ("cost_2x", _COST_FIELDS, Decimal(2)),
        ("spread_widening", ("spread_bps",), plan.spread_multiplier),
        ("slippage_stress", ("slippage_bps",), plan.slippage_multiplier),
        ("funding_stress", ("funding_bps",), plan.funding_multiplier),
    )
    scenarios = []
    for name, fields, multiplier in specifications:
        with localcontext(_ARITHMETIC_CONTEXT):
            updates = {
                field: getattr(plan.cost_model, field) * multiplier for field in fields
            }
        model = CostModel.model_validate(plan.cost_model.model_dump() | updates)
        metrics = evaluate_costed_return_path(
            returns,
            exposures,
            cost_model=model,
            observation_interval_seconds=plan.observation_interval_seconds,
            cvar_confidence_level=plan.cvar_confidence_level,
        )
        scenarios.append(_scenario(name, model, metrics))
    report = CostStressReportV1(
        plan_sha256=expected_plan_sha256,
        path_sha256=expected_path_sha256,
        candidate_sha256=plan.candidate_sha256,
        source_replay_sha256=path.source_replay_sha256,
        observation_count=len(path.observations),
        observation_interval_seconds=plan.observation_interval_seconds,
        cvar_confidence_level=plan.cvar_confidence_level,
        scenarios=tuple(scenarios),
    )
    return _freeze(report, contract_type=CostStressReportV1)


def verify_cost_stress_v1(
    report_bytes: bytes,
    plan_bytes: bytes,
    path_bytes: bytes,
    *,
    expected_report_sha256: str,
    expected_plan_sha256: str,
    expected_path_sha256: str,
) -> FrozenGate3Artifact[CostStressReportV1]:
    """Recompute the report and refuse changed claims or figures."""
    if (
        type(report_bytes) is not bytes
        or not 0 < len(report_bytes) <= _MAX_REPORT_BYTES
    ):
        raise ArtifactVerificationError("cost stress artifact size is invalid")
    if (
        type(expected_report_sha256) is not str
        or sha256(report_bytes).hexdigest() != expected_report_sha256
    ):
        raise ArtifactVerificationError("cost stress report SHA256 mismatch")
    replay = evaluate_cost_stress_v1(
        plan_bytes,
        path_bytes,
        expected_plan_sha256=expected_plan_sha256,
        expected_path_sha256=expected_path_sha256,
    )
    if replay.payload != report_bytes:
        raise ArtifactVerificationError("cost stress report does not match replay")
    return replay
