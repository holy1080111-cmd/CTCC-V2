"""Deterministic order-permutation stress on a locked costed return path."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field, field_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
)
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.cost_stress_v1 import (
    CostStressPathV1,
    CostStressPlanV1,
    _checked_artifact,
    _validate_path,
)
from app.mie.validation.costs import _maximum_drawdown, evaluate_costed_return_path
from app.mie.validation.metrics import _ARITHMETIC_CONTEXT, _bounded_decimal_tuple
from app.mie.validation.moving_block_net_outcomes_v1 import _locked_net_hash, _quantile

_DOMAIN = b"CTCC.MIE.ORDER_RESHUFFLE.NET_OUTCOMES.V1\x00"
_MAX_PLAN_BYTES = 64 * 1024
_MAX_PATH_BYTES = 64 * 1024 * 1024
_MAX_REPORT_BYTES = 1024 * 1024
_MAX_ROWS = 2048
_MAX_REPLICATIONS = 128
_MAX_DRAWS = 262144
_U64 = 1 << 64


class OrderReshufflePlanV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.order_reshuffle.plan.v1"] = (
        "ctcc.mie.order_reshuffle.plan.v1"
    )
    cost_plan_sha256: Sha256
    candidate_sha256: Sha256
    preregistered_at: datetime
    source_window_start: datetime
    source_window_end: datetime
    observation_count: int = Field(ge=16, le=_MAX_ROWS)
    seed_sha256: Sha256
    replications: int = Field(ge=2, le=_MAX_REPLICATIONS)
    upper_drawdown_quantile: Decimal = Field(ge=Decimal("0.75"), lt=1)
    maximum_drawdown_limit: Decimal = Field(ge=0, le=1)

    @field_validator("preregistered_at", "source_window_start", "source_window_end")
    @classmethod
    def validate_time(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("upper_drawdown_quantile", "maximum_drawdown_limit")
    @classmethod
    def validate_decimal(cls, value: Decimal, info) -> Decimal:
        _bounded_decimal_tuple(value, info.field_name)
        return value


class OrderReshuffleReplicationV1(Gate3Contract):
    index: int = Field(ge=0, lt=_MAX_REPLICATIONS)
    permutation_sha256: Sha256
    maximum_drawdown: Decimal = Field(ge=0, le=1)


class OrderReshuffleReportV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.order_reshuffle.report.v1"] = (
        "ctcc.mie.order_reshuffle.report.v1"
    )
    plan_sha256: Sha256
    cost_plan_sha256: Sha256
    cost_path_sha256: Sha256
    candidate_sha256: Sha256
    source_replay_sha256: Sha256
    locked_net_outcomes_sha256: Sha256
    observation_count: int = Field(ge=16, le=_MAX_ROWS)
    replications: tuple[OrderReshuffleReplicationV1, ...] = Field(
        min_length=2, max_length=_MAX_REPLICATIONS
    )
    original_maximum_drawdown: Decimal = Field(ge=0, le=1)
    maximum_drawdown_limit: Decimal = Field(ge=0, le=1)
    upper_drawdown_quantile: Decimal = Field(ge=Decimal("0.75"), lt=1)
    empirical_upper_drawdown: Decimal = Field(ge=0, le=1)
    drawdown_limit_breach_count: int = Field(ge=0, le=_MAX_REPLICATIONS)
    scope: Literal["locked_normalized_net_path_order_sensitivity"] = (
        "locked_normalized_net_path_order_sensitivity"
    )
    independent_preregistration_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    economic_oos_accepted: Literal[False] = False
    execution_authority: Literal[False] = False


def _permutation(seed: bytes, replication: int, size: int) -> tuple[int, ...]:
    """Fisher–Yates with fixed SHA256 framing and unbiased bounded draws."""
    indices = list(range(size))
    key = _DOMAIN + seed + replication.to_bytes(4, "big")
    for end in range(size - 1, 0, -1):
        choices = end + 1
        limit = _U64 - _U64 % choices
        counter = key + end.to_bytes(4, "big")
        for attempt in range(32):
            digest = hashlib.sha256(counter + attempt.to_bytes(4, "big")).digest()
            value = int.from_bytes(digest[:8], "big")
            if value < limit:
                swap = value % choices
                indices[end], indices[swap] = indices[swap], indices[end]
                break
        else:
            raise ValueError("order reshuffle sampler exhausted")
    return tuple(indices)


def evaluate_order_reshuffle_v1(
    plan_bytes: bytes,
    cost_plan_bytes: bytes,
    cost_path_bytes: bytes,
    *,
    expected_plan_sha256: str,
    expected_cost_plan_sha256: str,
    expected_cost_path_sha256: str,
) -> FrozenGate3Artifact[OrderReshuffleReportV1]:
    """Permute whole baseline net observations without replacement or repricing.

    This isolates drawdown sensitivity to ordering in the supplied arithmetic
    path. It does not preserve serial dependence or model alternate executions.
    The moving-block report handles dependence as a separate diagnostic.
    """
    plan = _checked_artifact(
        plan_bytes, expected_plan_sha256, OrderReshufflePlanV1, _MAX_PLAN_BYTES
    )
    cost_plan = _checked_artifact(
        cost_plan_bytes, expected_cost_plan_sha256, CostStressPlanV1, _MAX_PLAN_BYTES
    )
    path = _checked_artifact(
        cost_path_bytes, expected_cost_path_sha256, CostStressPathV1, _MAX_PATH_BYTES
    )
    count = len(path.observations)
    if (
        plan.cost_plan_sha256 != expected_cost_plan_sha256
        or plan.candidate_sha256 != cost_plan.candidate_sha256
        or plan.candidate_sha256 != path.candidate_sha256
        or not cost_plan.preregistered_at <= plan.preregistered_at < path.started_at
        or path.started_at != plan.source_window_start
        or path.ended_at != plan.source_window_end
        or plan.observation_count != count
        or plan.replications * count > _MAX_DRAWS
    ):
        raise ValueError("order reshuffle plan, population or source pin differs")
    _validate_path(cost_plan, path, expected_cost_plan_sha256)
    costed = evaluate_costed_return_path(
        tuple(row.asset_return for row in path.observations),
        tuple(row.exposure for row in path.observations),
        cost_model=cost_plan.cost_model,
        observation_interval_seconds=cost_plan.observation_interval_seconds,
        cvar_confidence_level=cost_plan.cvar_confidence_level,
    )
    net = tuple(row.net_return for row in costed.observations)
    seed = bytes.fromhex(plan.seed_sha256)
    replications = []
    with localcontext(_ARITHMETIC_CONTEXT):
        for replication in range(plan.replications):
            indices = _permutation(seed, replication, count)
            indices_digest = hashlib.sha256(
                b"".join(index.to_bytes(4, "big") for index in indices)
            ).hexdigest()
            replications.append(
                OrderReshuffleReplicationV1(
                    index=replication,
                    permutation_sha256=indices_digest,
                    maximum_drawdown=_maximum_drawdown(tuple(net[i] for i in indices)),
                )
            )
        drawdowns = tuple(sorted(item.maximum_drawdown for item in replications))
        report = OrderReshuffleReportV1(
            plan_sha256=expected_plan_sha256,
            cost_plan_sha256=expected_cost_plan_sha256,
            cost_path_sha256=expected_cost_path_sha256,
            candidate_sha256=path.candidate_sha256,
            source_replay_sha256=path.source_replay_sha256,
            locked_net_outcomes_sha256=_locked_net_hash(net),
            observation_count=count,
            replications=tuple(replications),
            original_maximum_drawdown=costed.maximum_drawdown,
            maximum_drawdown_limit=plan.maximum_drawdown_limit,
            upper_drawdown_quantile=plan.upper_drawdown_quantile,
            empirical_upper_drawdown=_quantile(drawdowns, plan.upper_drawdown_quantile),
            drawdown_limit_breach_count=sum(
                drawdown > plan.maximum_drawdown_limit for drawdown in drawdowns
            ),
        )
    return _freeze(report, contract_type=OrderReshuffleReportV1)


def verify_order_reshuffle_v1(
    report_bytes: bytes,
    plan_bytes: bytes,
    cost_plan_bytes: bytes,
    cost_path_bytes: bytes,
    *,
    expected_report_sha256: str,
    expected_plan_sha256: str,
    expected_cost_plan_sha256: str,
    expected_cost_path_sha256: str,
) -> FrozenGate3Artifact[OrderReshuffleReportV1]:
    """Recompute the entire artifact against separately retained exact pins."""
    _checked_artifact(
        report_bytes, expected_report_sha256, OrderReshuffleReportV1, _MAX_REPORT_BYTES
    )
    replay = evaluate_order_reshuffle_v1(
        plan_bytes,
        cost_plan_bytes,
        cost_path_bytes,
        expected_plan_sha256=expected_plan_sha256,
        expected_cost_plan_sha256=expected_cost_plan_sha256,
        expected_cost_path_sha256=expected_cost_path_sha256,
    )
    if replay.payload != report_bytes:
        raise ArtifactVerificationError("order reshuffle report does not match replay")
    return replay
