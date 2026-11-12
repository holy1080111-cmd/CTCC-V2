"""Replay a preregistered legacy predicate score from pinned market bytes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.analysis.service import analyze_snapshot_at
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode, sha
from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.replay import _fixed_decimal_context
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.range_reversal import _direction as range_direction
from app.trade_qualification.global_public_runtime_v2 import (
    matches_global_public_market_identity_v2,
)

_CONFIG_BYTES = 4096
_RECEIPT_BYTES = 4096
_MARKET_BYTES = 4 * 1024 * 1024
_INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
_INPUT_SHA_FIELDS = (
    "plan_sha256",
    "journal_sha256",
    "packet_sha256",
    "market_policy_sha256",
    "post_readback_receipt_sha256",
    "snapshot_sha256",
)
_NO_CLAIM_FIELDS = (
    "source_authenticity_verified",
    "independent_availability_verified",
    "predictive_oos_eligible",
    "execution_authority",
)
_TIME_FIELDS = (
    "decision_at",
    "journal_completed_at",
    "post_readback_sample_at",
    "capture_deadline_at",
    "snapshot_received_at",
    "funding_forecast_settlement_at",
    "funding_following_forecast_at",
)
_OVERALL_BIAS = frozenset(
    (
        "trend_pullback",
        "breakout_continuation",
        "fvg_return",
        "order_block_return",
        "volatility_expansion",
    )
)
_CHOCH_FRAME = {"liquidity_sweep_reversal": "15m", "structure_reversal": "1H"}

Strategy = Literal[
    "trend_pullback",
    "breakout_continuation",
    "liquidity_sweep_reversal",
    "fvg_return",
    "order_block_return",
    "range_reversal",
    "structure_reversal",
    "volatility_expansion",
]
Instrument = Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
Direction = Literal["long", "short", "neutral"]


class FrozenLegacyRawScoreError(ValueError):
    """Fixed offline refusal with no market or path details."""


class FrozenLegacyRawScorerConfigV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.frozen_legacy_raw_scorer_config.v1"] = (
        "ctcc.mie.frozen_legacy_raw_scorer_config.v1"
    )
    strategy: Strategy
    analysis_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    predicate_decimal_precision: Literal[28] = 28


class FrozenLegacyRawScoreV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.frozen_legacy_raw_score.v1"] = (
        "ctcc.mie.frozen_legacy_raw_score.v1"
    )
    instrument_id: Instrument
    decision_at: datetime
    strategy: Strategy
    raw_direction: Direction
    status: Literal["directional", "neutral"]
    raw_score: int | None = Field(ge=0, le=100)
    config_sha256: Sha256
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    market_replay_receipt_sha256: Sha256
    market_snapshot_sha256: Sha256
    analysis_version: str
    source_authenticity_verified: Literal[False] = False
    independent_availability_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("decision_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "decision_at")

    @model_validator(mode="after")
    def status_matches_direction(self) -> FrozenLegacyRawScoreV1:
        if (self.raw_direction == "neutral") != (self.status == "neutral") or (
            self.raw_score is None
        ) != (self.status == "neutral"):
            raise ValueError("legacy raw score direction disagrees with score status")
        return self


def _checked_config(
    config_json: bytes,
    *,
    expected_config_sha256: str,
    expected_source_checkpoint: DevvalCandidateSourceV1,
    expected_source_archive_sha256: str,
) -> FrozenLegacyRawScorerConfigV1:
    if (
        type(config_json) is not bytes
        or not 0 < len(config_json) <= _CONFIG_BYTES
        or type(expected_config_sha256) is not str
        or type(expected_source_checkpoint) is not DevvalCandidateSourceV1
        or type(expected_source_archive_sha256) is not str
    ):
        raise FrozenLegacyRawScoreError("legacy_raw_score_scope_invalid")
    config = _verify(
        config_json,
        expected_sha256=expected_config_sha256,
        contract_type=FrozenLegacyRawScorerConfigV1,
    )
    source = DevvalCandidateSourceV1.model_validate(expected_source_checkpoint)
    if (
        config.source_checkpoint != source
        or config.source_archive_sha256 != expected_source_archive_sha256
    ):
        raise FrozenLegacyRawScoreError("legacy_raw_score_source_changed")
    return config


def _wire(raw: bytes, expected_sha256: str, maximum: int) -> dict:
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= maximum
        or type(expected_sha256) is not str
        or sha(raw) != expected_sha256
    ):
        raise FrozenLegacyRawScoreError("legacy_raw_market_pin_changed")
    value = decode(raw, maximum)
    if type(value) is not dict or canonical(value) != raw:
        raise FrozenLegacyRawScoreError("legacy_raw_market_wire_changed")
    return value


def _wire_time(value: str) -> datetime:
    parsed = require_utc(datetime.fromisoformat(value), "market replay time")
    if parsed.isoformat() != value:
        raise FrozenLegacyRawScoreError("legacy_raw_market_time_changed")
    return parsed


def _market_at_decision(
    *,
    receipt_json: bytes,
    expected_receipt_sha256: str,
    snapshot_json: bytes,
    expected_snapshot_sha256: str,
    instrument_id: str,
    decision: datetime,
) -> MarketSnapshot:
    receipt = _wire(receipt_json, expected_receipt_sha256, _RECEIPT_BYTES)
    if (
        set(receipt)
        != {
            "schema_version",
            "environment",
            "instrument_id",
            *_INPUT_SHA_FIELDS,
            *_NO_CLAIM_FIELDS,
            *_TIME_FIELDS,
        }
        or receipt["schema_version"] != "ctcc.mie.legacy_baseline_market_replay.v1"
        or receipt["environment"] != "anonymous_global_public"
        or receipt["instrument_id"] != instrument_id
        or receipt["snapshot_sha256"] != expected_snapshot_sha256
        or any(
            type(receipt[name]) is not str or SHA256_RE.fullmatch(receipt[name]) is None
            for name in _INPUT_SHA_FIELDS
        )
        or any(receipt[name] is not False for name in _NO_CLAIM_FIELDS)
    ):
        raise FrozenLegacyRawScoreError("legacy_raw_market_receipt_scope_changed")

    times = {name: _wire_time(receipt[name]) for name in _TIME_FIELDS}
    received = times["snapshot_received_at"]
    readback = times["post_readback_sample_at"]
    settlement = times["funding_forecast_settlement_at"]
    if (
        times["decision_at"] != decision
        or not received <= times["journal_completed_at"] <= readback <= decision
        or readback >= times["capture_deadline_at"]
        or not decision < settlement < times["funding_following_forecast_at"]
    ):
        raise FrozenLegacyRawScoreError("legacy_raw_market_time_changed")

    market = MarketSnapshot.model_validate(
        _wire(snapshot_json, expected_snapshot_sha256, _MARKET_BYTES)
    )
    if (
        canonical(market.model_dump(mode="json")) != snapshot_json
        or not matches_global_public_market_identity_v2(market, instrument_id)
        or market.received_at != received
        or market.next_funding_time != settlement
    ):
        raise FrozenLegacyRawScoreError("legacy_raw_market_snapshot_scope_changed")
    return market


def _condition_direction(ctx: StrategyContext, strategy: Strategy) -> Direction:
    # Match the exact source-pinned legacy selectors before short-biased helper
    # branches can interpret a neutral direction.
    if strategy in _OVERALL_BIAS:
        return ctx.analysis.overall_bias
    if strategy == "range_reversal":
        return range_direction(ctx)
    side = ctx.tf(_CHOCH_FRAME[strategy]).structure.choch
    return {"up": "long", "down": "short"}.get(side, "neutral")


def replay_frozen_legacy_raw_score_v1(
    *,
    config_json: bytes,
    expected_config_sha256: str,
    expected_source_checkpoint: DevvalCandidateSourceV1,
    expected_source_archive_sha256: str,
    receipt_json: bytes | None,
    expected_receipt_sha256: str,
    snapshot_json: bytes | None,
    expected_snapshot_sha256: str,
    instrument_id: str,
    decision_at: datetime,
) -> FrozenGate3Artifact[FrozenLegacyRawScoreV1]:
    """Reproduce a raw score on already verified N183/N184 market replay bytes."""
    try:
        if (
            type(instrument_id) is not str
            or instrument_id not in _INSTRUMENTS
            or type(decision_at) is not datetime
        ):
            raise FrozenLegacyRawScoreError("legacy_raw_score_scope_invalid")
        config = _checked_config(
            config_json,
            expected_config_sha256=expected_config_sha256,
            expected_source_checkpoint=expected_source_checkpoint,
            expected_source_archive_sha256=expected_source_archive_sha256,
        )
        decision = require_utc(decision_at, "legacy raw decision")
        if receipt_json is None or snapshot_json is None:
            raise FrozenLegacyRawScoreError("legacy_raw_score_market_missing")
        market = _market_at_decision(
            receipt_json=receipt_json,
            expected_receipt_sha256=expected_receipt_sha256,
            snapshot_json=snapshot_json,
            expected_snapshot_sha256=expected_snapshot_sha256,
            instrument_id=instrument_id,
            decision=decision,
        )
        analysis = analyze_snapshot_at(
            market, evaluated_at=decision, version=config.analysis_version
        )
        context = StrategyContext(
            analysis=analysis,
            market=market,
            minimum_score=100,  # Eligibility-only field unused by predicates.
            minimum_risk_reward=Decimal(1),  # Used only for candidate construction.
        )
        score = None
        with localcontext(_fixed_decimal_context(config.predicate_decimal_precision)):
            direction = _condition_direction(context, config.strategy)
            if direction != "neutral":
                assessment = assess_conditions(context, config.strategy)
                if (
                    assessment.strategy != config.strategy
                    or assessment.direction != direction
                ):
                    raise FrozenLegacyRawScoreError(
                        "legacy_raw_score_direction_changed"
                    )
                score = assessment.score
        return _freeze(
            FrozenLegacyRawScoreV1(
                instrument_id=instrument_id,
                decision_at=decision,
                strategy=config.strategy,
                raw_direction=direction,
                status="neutral" if direction == "neutral" else "directional",
                raw_score=score,
                config_sha256=expected_config_sha256,
                source_checkpoint=config.source_checkpoint,
                source_archive_sha256=config.source_archive_sha256,
                market_replay_receipt_sha256=expected_receipt_sha256,
                market_snapshot_sha256=expected_snapshot_sha256,
                analysis_version=analysis.version,
            ),
            contract_type=FrozenLegacyRawScoreV1,
        )
    except FrozenLegacyRawScoreError:
        raise
    except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
        raise FrozenLegacyRawScoreError("legacy_raw_score_replay_refused") from None
