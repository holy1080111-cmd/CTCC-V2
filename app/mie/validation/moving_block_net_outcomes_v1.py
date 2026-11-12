"""Inert moving-block uncertainty diagnostic for a locked, costed return path.

The path is a normalized shadow series, not exchange fills or realized trades.
The plan's pre-window date is a structural check; independent publication of
that plan and source authenticity are outside this computational boundary.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Literal

from pydantic import Field, field_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.cost_stress_v1 import (
    CostStressPathV1,
    CostStressPlanV1,
    _validate_path,
)
from app.mie.validation.costs import (
    _compound,
    _maximum_drawdown,
    evaluate_costed_return_path,
)
from app.mie.validation.metrics import _ARITHMETIC_CONTEXT

_DOMAIN = b"CTCC.MIE.MOVING_BLOCK.NET_OUTCOMES.V1\x00"
_MAX_PLAN_BYTES = 64 * 1024
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_REPORT_BYTES = 1024 * 1024
_MAX_ROWS = 2048
_MAX_DRAWS = 262144
_U64 = 1 << 64
_ZERO = Decimal(0)
_ONE = Decimal(1)


class MovingBlockPlanV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.moving_block.plan.v1"] = (
        "ctcc.mie.moving_block.plan.v1"
    )
    cost_plan_sha256: Sha256
    candidate_sha256: Sha256
    preregistered_at: datetime
    source_window_start: datetime
    source_window_end: datetime
    observation_count: int = Field(ge=16, le=_MAX_ROWS)
    seed_sha256: Sha256
    replications: int = Field(ge=2, le=128)
    block_length: int = Field(ge=2, le=512)
    lower_tail_probability: Decimal = Field(gt=0, le=Decimal("0.25"))
    maximum_drawdown_limit: Decimal = Field(ge=0, le=1)

    @field_validator("preregistered_at", "source_window_start", "source_window_end")
    @classmethod
    def validate_time(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("lower_tail_probability", "maximum_drawdown_limit")
    @classmethod
    def validate_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("moving-block plan decimal must be finite")
        return value


class MovingBlockReplicationV1(Gate3Contract):
    index: int = Field(ge=0)
    sampled_indices_sha256: Sha256
    mean_net_return: Decimal
    compound_net_return: Decimal
    maximum_drawdown: Decimal = Field(ge=0, le=1)


class MovingBlockReportV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.moving_block.report.v1"] = (
        "ctcc.mie.moving_block.report.v1"
    )
    plan_sha256: Sha256
    cost_plan_sha256: Sha256
    cost_path_sha256: Sha256
    candidate_sha256: Sha256
    source_replay_sha256: Sha256
    locked_net_outcomes_sha256: Sha256
    observation_count: int = Field(ge=16, le=_MAX_ROWS)
    block_length: int = Field(ge=2, le=512)
    replications: tuple[MovingBlockReplicationV1, ...]
    original_mean_net_return: Decimal
    original_compound_net_return: Decimal
    original_maximum_drawdown: Decimal = Field(ge=0, le=1)
    empirical_compound_lower: Decimal
    empirical_compound_median: Decimal
    empirical_compound_upper: Decimal
    empirical_drawdown_upper: Decimal = Field(ge=0, le=1)
    drawdown_limit_breach_count: int = Field(ge=0)
    scope: Literal["locked_normalized_net_path_descriptive_uncertainty"] = (
        "locked_normalized_net_path_descriptive_uncertainty"
    )
    independent_preregistration_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    economic_oos_accepted: Literal[False] = False
    execution_authority: Literal[False] = False


def _read[T: Gate3Contract](
    payload: bytes, digest: str, contract: type[T], limit: int
) -> T:
    if type(payload) is not bytes or not 0 < len(payload) <= limit:
        raise ArtifactVerificationError("moving-block artifact size invalid")
    return _verify(payload, expected_sha256=digest, contract_type=contract)


def _sample_indices(
    *, seed_sha256: str, replication: int, length: int, block_length: int
) -> tuple[int, ...]:
    """Fixed counter SHA256; each block is contiguous and clipped only at N.

    Boundary convention: sample starts lie in [0, N-L]; concatenate full
    blocks with replacement, then keep the first N observations. A block never
    wraps; costs are not recalculated across boundaries between sampled blocks.
    """
    choices = length - block_length + 1
    if (
        type(seed_sha256) is not str
        or len(seed_sha256) != 64
        or any(c not in "0123456789abcdef" for c in seed_sha256)
        or type(replication) is not int
        or not 0 <= replication < 128
        or type(length) is not int
        or not 16 <= length <= _MAX_ROWS
        or type(block_length) is not int
        or not 2 <= block_length <= min(512, length // 2)
    ):
        raise ValueError("moving-block sampling input invalid")
    seed = bytes.fromhex(seed_sha256)
    limit = _U64 - (_U64 % choices)
    sampled: list[int] = []
    block_number = 0
    while len(sampled) < length:
        # Rejection sampling avoids modulo bias. Thirty-two independent
        # counter candidates are a bounded refusal, never a fallback draw.
        for attempt in range(32):
            digest = hashlib.sha256(
                _DOMAIN
                + seed
                + replication.to_bytes(4, "big")
                + block_number.to_bytes(4, "big")
                + attempt.to_bytes(4, "big")
            ).digest()
            value = int.from_bytes(digest[:8], "big")
            if value < limit:
                start = value % choices
                sampled.extend(range(start, start + block_length))
                break
        else:
            raise ValueError("moving-block sampler exhausted")
        block_number += 1
    return tuple(sampled[:length])


def _quantile(sorted_values: tuple[Decimal, ...], probability: Decimal) -> Decimal:
    with localcontext(_ARITHMETIC_CONTEXT):
        rank = int(
            (Decimal(len(sorted_values)) * probability).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
    return sorted_values[max(0, rank - 1)]


def _locked_net_hash(values: tuple[Decimal, ...]) -> str:
    # The cost evaluator has a pinned arithmetic context; canonical Decimal
    # text from the existing contract prevents caller context from altering it.
    from app.mie.validation.contracts import _canonical_decimal

    payload = json.dumps(
        [_canonical_decimal(value) for value in values], separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def evaluate_moving_block_v1(
    bootstrap_plan_bytes: bytes,
    cost_plan_bytes: bytes,
    cost_path_bytes: bytes,
    *,
    expected_bootstrap_plan_sha256: str,
    expected_cost_plan_sha256: str,
    expected_cost_path_sha256: str,
) -> FrozenGate3Artifact[MovingBlockReportV1]:
    """Compute deterministic uncertainty only; no source or order access."""
    plan = _read(
        bootstrap_plan_bytes,
        expected_bootstrap_plan_sha256,
        MovingBlockPlanV1,
        _MAX_PLAN_BYTES,
    )
    cost_plan = _read(
        cost_plan_bytes, expected_cost_plan_sha256, CostStressPlanV1, _MAX_PLAN_BYTES
    )
    path = _read(
        cost_path_bytes, expected_cost_path_sha256, CostStressPathV1, _MAX_SOURCE_BYTES
    )
    if (
        plan.cost_plan_sha256 != expected_cost_plan_sha256
        or plan.candidate_sha256 != cost_plan.candidate_sha256
        or plan.candidate_sha256 != path.candidate_sha256
        or plan.preregistered_at < cost_plan.preregistered_at
        or plan.preregistered_at >= plan.source_window_start
        or path.started_at != plan.source_window_start
        or path.ended_at != plan.source_window_end
        or len(path.observations) != plan.observation_count
    ):
        raise ValueError("moving-block declared plan or source pin invalid")
    _validate_path(cost_plan, path, expected_cost_plan_sha256)
    count = len(path.observations)
    if (
        not 16 <= count <= _MAX_ROWS
        or plan.block_length > count // 2
        or plan.replications * count > _MAX_DRAWS
    ):
        raise ValueError("moving-block population or work bound invalid")
    costed = evaluate_costed_return_path(
        tuple(row.asset_return for row in path.observations),
        tuple(row.exposure for row in path.observations),
        cost_model=cost_plan.cost_model,
        observation_interval_seconds=cost_plan.observation_interval_seconds,
        cvar_confidence_level=cost_plan.cvar_confidence_level,
    )
    net = tuple(row.net_return for row in costed.observations)
    replicas: list[MovingBlockReplicationV1] = []
    with localcontext(_ARITHMETIC_CONTEXT):
        for index in range(plan.replications):
            indices = _sample_indices(
                seed_sha256=plan.seed_sha256,
                replication=index,
                length=count,
                block_length=plan.block_length,
            )
            sampled = tuple(net[position] for position in indices)
            replicas.append(
                MovingBlockReplicationV1(
                    index=index,
                    sampled_indices_sha256=hashlib.sha256(
                        b"".join(position.to_bytes(4, "big") for position in indices)
                    ).hexdigest(),
                    mean_net_return=sum(sampled, _ZERO) / Decimal(count),
                    compound_net_return=_compound(sampled),
                    maximum_drawdown=_maximum_drawdown(sampled),
                )
            )
        compounds = tuple(sorted(row.compound_net_return for row in replicas))
        drawdowns = tuple(sorted(row.maximum_drawdown for row in replicas))
        report = MovingBlockReportV1(
            plan_sha256=expected_bootstrap_plan_sha256,
            cost_plan_sha256=expected_cost_plan_sha256,
            cost_path_sha256=expected_cost_path_sha256,
            candidate_sha256=path.candidate_sha256,
            source_replay_sha256=path.source_replay_sha256,
            locked_net_outcomes_sha256=_locked_net_hash(net),
            observation_count=count,
            block_length=plan.block_length,
            replications=tuple(replicas),
            original_mean_net_return=costed.mean_net_return_per_observation,
            original_compound_net_return=costed.net_compound_return,
            original_maximum_drawdown=costed.maximum_drawdown,
            empirical_compound_lower=_quantile(compounds, plan.lower_tail_probability),
            empirical_compound_median=_quantile(compounds, Decimal("0.5")),
            empirical_compound_upper=_quantile(
                compounds, _ONE - plan.lower_tail_probability
            ),
            empirical_drawdown_upper=_quantile(
                drawdowns, _ONE - plan.lower_tail_probability
            ),
            drawdown_limit_breach_count=sum(
                row.maximum_drawdown > plan.maximum_drawdown_limit for row in replicas
            ),
        )
    frozen = _freeze(report, contract_type=MovingBlockReportV1)
    if len(frozen.payload) > _MAX_REPORT_BYTES:
        raise ArtifactVerificationError("moving-block report size invalid")
    return frozen


def verify_moving_block_v1(
    report_bytes: bytes,
    bootstrap_plan_bytes: bytes,
    cost_plan_bytes: bytes,
    cost_path_bytes: bytes,
    *,
    expected_report_sha256: str,
    expected_bootstrap_plan_sha256: str,
    expected_cost_plan_sha256: str,
    expected_cost_path_sha256: str,
) -> FrozenGate3Artifact[MovingBlockReportV1]:
    """Independent replay; refuses every changed byte or result."""
    checked = _read(
        report_bytes, expected_report_sha256, MovingBlockReportV1, _MAX_REPORT_BYTES
    )
    replay = evaluate_moving_block_v1(
        bootstrap_plan_bytes,
        cost_plan_bytes,
        cost_path_bytes,
        expected_bootstrap_plan_sha256=expected_bootstrap_plan_sha256,
        expected_cost_plan_sha256=expected_cost_plan_sha256,
        expected_cost_path_sha256=expected_cost_path_sha256,
    )
    if checked.canonical_json_bytes() != replay.payload:
        raise ArtifactVerificationError("moving-block report replay mismatch")
    return replay
