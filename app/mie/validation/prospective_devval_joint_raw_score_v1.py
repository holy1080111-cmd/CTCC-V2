"""Bind two frozen legacy raw scores to one measured D/V companion event.

This computational join cannot turn an incomplete source event into a score.
The historical study decision is not changed by this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import DatasetPartition, Gate3Contract, Sha256
from app.mie.validation.frozen_legacy_raw_scorer_v1 import (
    FrozenLegacyRawScoreV1,
    _checked_config,
    replay_frozen_legacy_raw_score_v1,
)
from app.mie.validation.legacy_baseline_market_replay_v1 import (
    replay_legacy_baseline_market_from_companion_v1,
)
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    _MAX_SCHEDULE_BYTES,
    GlobalDevvalCompanionScheduleV1,
)
from app.mie.validation.prospective_devval_joint_companion_coverage_v1 import (
    _MAX_REPORT_BYTES,
    AggregateReadyV1,
    DevvalJointCompanionCoverageV1,
    PublishedCompanionV1,
)
from app.trade_qualification.global_public_capture_companion_v1 import (
    verify_global_public_companion_publication_v1,
    verify_global_public_measured_companion_v1,
)

_SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
_MAX_EVENT_BYTES = 64 * 1024


class JointDevvalRawScoreError(ValueError):
    """Fixed refusal without source payload, path, or price details."""


class JointDevvalRawScoreSideV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    aggregate: AggregateReadyV1
    coverage_status: str = Field(min_length=1, max_length=32)
    status: Literal["missing", "neutral", "directional"]
    publication_sha256: Sha256 | None = None
    readback_sha256: Sha256 | None = None
    market_replay_receipt_sha256: Sha256 | None = None
    raw_result_sha256: Sha256 | None = None
    raw_result: FrozenLegacyRawScoreV1 | None = None


class JointDevvalRawScoreEventV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_joint_raw_score_event.v1"] = (
        "ctcc.mie.devval_joint_raw_score_event.v1"
    )
    schedule_sha256: Sha256
    coverage_sha256: Sha256
    batch_sha256: Sha256
    config_sha256: Sha256
    slot_ordinal: int = Field(ge=0)
    partition: Literal[DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
    event_closed_at: datetime
    decision_cutoff_at: datetime
    joint_decision_at: datetime | None = None
    status: Literal["incomplete", "complete_computational"]
    sides: tuple[JointDevvalRawScoreSideV1, JointDevvalRawScoreSideV1]
    source_authenticity_verified: Literal[False] = False
    independent_availability_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def _verified_inputs(
    schedule_payload,
    expected_schedule_sha256,
    coverage_payload,
    expected_coverage_sha256,
    expected_batch_sha256,
    config_json,
    expected_config_sha256,
    slot_ordinal,
):
    if (
        type(schedule_payload) is not bytes
        or not 0 < len(schedule_payload) <= _MAX_SCHEDULE_BYTES
        or type(coverage_payload) is not bytes
        or not 0 < len(coverage_payload) <= _MAX_REPORT_BYTES
        or type(config_json) is not bytes
        or type(slot_ordinal) is not int
        or any(
            type(pin) is not str or SHA256_RE.fullmatch(pin) is None
            for pin in (
                expected_schedule_sha256,
                expected_coverage_sha256,
                expected_batch_sha256,
                expected_config_sha256,
            )
        )
    ):
        raise JointDevvalRawScoreError("joint_raw_input_invalid")
    schedule = _verify(
        schedule_payload,
        expected_sha256=expected_schedule_sha256,
        contract_type=GlobalDevvalCompanionScheduleV1,
    )
    coverage = _verify(
        coverage_payload,
        expected_sha256=expected_coverage_sha256,
        contract_type=DevvalJointCompanionCoverageV1,
    )
    config = _checked_config(
        config_json,
        expected_config_sha256=expected_config_sha256,
        expected_source_checkpoint=schedule.source_checkpoint,
        expected_source_archive_sha256=schedule.source_archive_sha256,
    )
    if (
        not 0 <= slot_ordinal < len(schedule.slots)
        or len(coverage.slots) != len(schedule.slots)
        or coverage.schedule_sha256 != expected_schedule_sha256
        or coverage.batch_sha256 != expected_batch_sha256
        or coverage.campaign_plan_sha256 != schedule.campaign_plan_sha256
        or coverage.research_blueprint_sha256 != schedule.research_blueprint_sha256
        or schedule.legacy_scorer_config_sha256 != expected_config_sha256
        or schedule.source_checkpoint != config.source_checkpoint
        or schedule.source_archive_sha256 != config.source_archive_sha256
    ):
        raise JointDevvalRawScoreError("joint_raw_lineage_changed")
    if coverage.complete_computational_slots != sum(
        slot.status == "covered_computational" for slot in coverage.slots
    ) or coverage.incomplete_slots != sum(
        slot.status == "incomplete" for slot in coverage.slots
    ):
        raise JointDevvalRawScoreError("joint_raw_coverage_count_changed")
    for ordinal, (planned, observed) in enumerate(
        zip(schedule.slots, coverage.slots, strict=True)
    ):
        if (
            observed.ordinal != ordinal
            or observed.partition != planned.partition
            or observed.event_closed_at != planned.event_closed_at
            or observed.decision_cutoff_at != planned.decision_cutoff_at
            or tuple(side.aggregate.instrument_id for side in observed.sides)
            != _SYMBOLS
            or any(
                side.aggregate.bar_closed_at != planned.event_closed_at
                or side.aggregate.available_at < planned.event_closed_at
                for side in observed.sides
            )
            or (observed.status == "covered_computational")
            != all(side.status == "covered_computational" for side in observed.sides)
            or (
                observed.status == "incomplete"
                and observed.joint_decision_at is not None
            )
            or (
                observed.status == "covered_computational"
                and (
                    observed.joint_decision_at is None
                    or observed.joint_decision_at > planned.decision_cutoff_at
                    or any(
                        side.status != "covered_computational"
                        for side in observed.sides
                    )
                )
            )
        ):
            raise JointDevvalRawScoreError("joint_raw_coverage_geometry_changed")
    return schedule, coverage, config


def _missing_side(symbol, coverage_side):
    return JointDevvalRawScoreSideV1(
        instrument_id=symbol,
        aggregate=coverage_side.aggregate,
        coverage_status=coverage_side.status,
        status="missing",
        publication_sha256=coverage_side.publication_sha256,
        readback_sha256=coverage_side.readback_sha256,
        market_replay_receipt_sha256=coverage_side.replay_receipt_sha256,
    )


def _replay_pair(companions, slot, coverage_slot, schedule):
    if (
        type(companions) is not tuple
        or len(companions) != len(_SYMBOLS)
        or tuple(type(item) for item in companions)
        != (PublishedCompanionV1, PublishedCompanionV1)
    ):
        raise JointDevvalRawScoreError("joint_raw_companion_pair_missing")
    markets = []
    for symbol, item, covered_side in zip(
        _SYMBOLS, companions, coverage_slot.sides, strict=True
    ):
        if (
            type(item.slot_ordinal) is not int
            or item.slot_ordinal != coverage_slot.ordinal
            or item.instrument_id != symbol
            or item.expected_publication_sha256 != covered_side.publication_sha256
            or item.expected_readback_sha256 != covered_side.readback_sha256
            or covered_side.replay_receipt_sha256 is None
            or covered_side.postpublication_readback_at is None
        ):
            raise JointDevvalRawScoreError("joint_raw_companion_pin_changed")
        publication = verify_global_public_companion_publication_v1(
            item.publication_json,
            expected_sha256=item.expected_publication_sha256,
        )
        if (
            publication.instrument_id != symbol
            or publication.market_policy_sha256 != schedule.market_policy_sha256
        ):
            raise JointDevvalRawScoreError("joint_raw_companion_scope_changed")
        readback = verify_global_public_measured_companion_v1(
            item.journal_root,
            item.companion_root,
            publication=publication,
            readback_json=item.readback_json,
            expected_readback_sha256=item.expected_readback_sha256,
        )
        if (
            readback.publication_sha256 != item.expected_publication_sha256
            or readback.companion_sha256 != publication.companion_sha256
            or utc_from_ns(readback.readback_completed.utc_ns)
            != covered_side.postpublication_readback_at
            or readback.readback_completed.utc_ns < _ns(slot.event_closed_at)
        ):
            raise JointDevvalRawScoreError("joint_raw_companion_readback_changed")
        replay = replay_legacy_baseline_market_from_companion_v1(
            item.journal_root,
            item.companion_root,
            publication_json=item.publication_json,
            expected_publication_sha256=item.expected_publication_sha256,
            decision_at=coverage_slot.joint_decision_at,
        )
        if replay.receipt_sha256 != covered_side.replay_receipt_sha256:
            raise JointDevvalRawScoreError("joint_raw_market_replay_changed")
        markets.append(replay)
    return tuple(markets)


def score_joint_devval_companion_event_v1(
    schedule_payload: bytes,
    *,
    expected_schedule_sha256: str,
    coverage_payload: bytes,
    expected_coverage_sha256: str,
    expected_batch_sha256: str,
    config_json: bytes,
    expected_config_sha256: str,
    slot_ordinal: int,
    companions: tuple[PublishedCompanionV1, PublishedCompanionV1] | None,
) -> FrozenGate3Artifact[JointDevvalRawScoreEventV1]:
    """Score both exact markets or return two explicit missing raw scores."""
    try:
        schedule, coverage, config = _verified_inputs(
            schedule_payload,
            expected_schedule_sha256,
            coverage_payload,
            expected_coverage_sha256,
            expected_batch_sha256,
            config_json,
            expected_config_sha256,
            slot_ordinal,
        )
        slot = schedule.slots[slot_ordinal]
        covered = coverage.slots[slot_ordinal]
        complete = covered.status == "covered_computational"
        if not complete:
            if companions is not None:
                raise JointDevvalRawScoreError(
                    "joint_raw_incomplete_companion_supplied"
                )
            sides = tuple(
                _missing_side(symbol, side)
                for symbol, side in zip(_SYMBOLS, covered.sides, strict=True)
            )
            joint_decision = None
        else:
            if covered.joint_decision_at is None or any(
                side.postpublication_readback_at is None
                or side.replay_receipt_sha256 is None
                or side.publication_sha256 is None
                or side.readback_sha256 is None
                for side in covered.sides
            ):
                raise JointDevvalRawScoreError("joint_raw_coverage_incomplete")
            measured_ns = max(
                *(_ns(side.aggregate.available_at) for side in covered.sides),
                *(_ns(side.postpublication_readback_at) for side in covered.sides),
            )
            joint_decision = utc_from_ns(measured_ns)
            if (
                joint_decision != covered.joint_decision_at
                or joint_decision > slot.decision_cutoff_at
            ):
                raise JointDevvalRawScoreError("joint_raw_decision_changed")
            # Reopen and verify both original N184 sources before scoring either.
            markets = _replay_pair(companions, slot, covered, schedule)
            scored = []
            for symbol, replay in zip(_SYMBOLS, markets, strict=True):
                snapshot_json = canonical(replay.market.model_dump(mode="json"))
                raw = replay_frozen_legacy_raw_score_v1(
                    config_json=config_json,
                    expected_config_sha256=expected_config_sha256,
                    expected_source_checkpoint=config.source_checkpoint,
                    expected_source_archive_sha256=config.source_archive_sha256,
                    receipt_json=replay.receipt_json,
                    expected_receipt_sha256=replay.receipt_sha256,
                    snapshot_json=snapshot_json,
                    expected_snapshot_sha256=sha(snapshot_json),
                    instrument_id=symbol,
                    decision_at=joint_decision,
                )
                scored.append(raw)
            sides = tuple(
                JointDevvalRawScoreSideV1(
                    instrument_id=symbol,
                    aggregate=covered_side.aggregate,
                    coverage_status=covered_side.status,
                    status=raw.contract.status,
                    publication_sha256=covered_side.publication_sha256,
                    readback_sha256=covered_side.readback_sha256,
                    market_replay_receipt_sha256=replay.receipt_sha256,
                    raw_result_sha256=raw.sha256,
                    raw_result=raw.contract,
                )
                for symbol, covered_side, replay, raw in zip(
                    _SYMBOLS, covered.sides, markets, scored, strict=True
                )
            )
        event = _freeze(
            JointDevvalRawScoreEventV1(
                schedule_sha256=expected_schedule_sha256,
                coverage_sha256=expected_coverage_sha256,
                batch_sha256=expected_batch_sha256,
                config_sha256=expected_config_sha256,
                slot_ordinal=slot_ordinal,
                partition=slot.partition,
                event_closed_at=slot.event_closed_at,
                decision_cutoff_at=slot.decision_cutoff_at,
                joint_decision_at=joint_decision,
                status="complete_computational" if complete else "incomplete",
                sides=sides,
            ),
            contract_type=JointDevvalRawScoreEventV1,
        )
        if len(event.payload) > _MAX_EVENT_BYTES:
            raise JointDevvalRawScoreError("joint_raw_event_too_large")
        return event
    except JointDevvalRawScoreError:
        raise
    except (ArithmeticError, AttributeError, KeyError, OSError, TypeError, ValueError):
        raise JointDevvalRawScoreError("joint_raw_event_refused") from None
