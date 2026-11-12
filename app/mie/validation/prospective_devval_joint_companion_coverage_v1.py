"""Computational coverage of a frozen D/V event and two Global companions.

The report records missing and refused events. It does not establish independent
publication, source authenticity, a revised study decision, or trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.domain.source_primitives import utc_from_ns
from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.availability import AvailabilityBasis
from app.mie.validation.batch_replay import AggregateBar
from app.mie.validation.contracts import DatasetPartition, Gate3Contract, Sha256
from app.mie.validation.legacy_baseline_market_replay_v1 import (
    LegacyBaselineMarketReplayError,
    replay_legacy_baseline_market_from_companion_v1,
)
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    _MAX_BATCH_BYTES,
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    _MAX_SCHEDULE_BYTES,
    GlobalDevvalCompanionScheduleV1,
)
from app.trade_qualification.global_public_capture_companion_v1 import (
    GlobalPublicCompanionError,
    verify_global_public_companion_publication_v1,
    verify_global_public_measured_companion_v1,
)

_SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
_PARTITIONS = (DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION)
_FRAME_SECONDS = (("15m", 900), ("1H", 3600), ("4H", 14400))
_MAX_REPORT_BYTES = 16 * 1024 * 1024
_REFUSALS = ("capture_denied", "publication_denied", "readback_denied", "expired")


class DevvalJointCompanionCoverageError(ValueError):
    """Fixed refusal without paths, source prices, or private payloads."""


@dataclass(frozen=True, slots=True, repr=False)
class PublishedCompanionV1:
    slot_ordinal: int
    instrument_id: str
    journal_root: Path
    companion_root: Path
    publication_json: bytes
    expected_publication_sha256: str
    readback_json: bytes
    expected_readback_sha256: str


@dataclass(frozen=True, slots=True)
class RefusedCompanionV1:
    slot_ordinal: int
    instrument_id: str
    reason: Literal[
        "capture_denied", "publication_denied", "readback_denied", "expired"
    ]


class AggregateReadyV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    source_row_id: str
    source_row_sha256: Sha256
    constituent_sha256: Sha256
    bar_closed_at: datetime
    available_at: datetime


class CompanionSideCoverageV1(Gate3Contract):
    aggregate: AggregateReadyV1
    status: Literal[
        "missing",
        "refused",
        "measured",
        "late",
        "replay_refused",
        "ohlcv_mismatch",
        "covered_computational",
    ]
    refusal_reason: str | None = None
    publication_sha256: Sha256 | None = None
    readback_sha256: Sha256 | None = None
    postpublication_readback_at: datetime | None = None
    replay_receipt_sha256: Sha256 | None = None


class JointCompanionSlotCoverageV1(Gate3Contract):
    ordinal: int = Field(ge=0)
    partition: Literal[DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
    event_closed_at: datetime
    decision_cutoff_at: datetime
    joint_decision_at: datetime | None = None
    status: Literal["incomplete", "covered_computational"]
    sides: tuple[CompanionSideCoverageV1, CompanionSideCoverageV1]


class DevvalJointCompanionCoverageV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_joint_companion_coverage.v1"] = (
        "ctcc.mie.devval_joint_companion_coverage.v1"
    )
    schedule_sha256: Sha256
    batch_sha256: Sha256
    campaign_plan_sha256: Sha256
    research_blueprint_sha256: Sha256
    slots: tuple[JointCompanionSlotCoverageV1, ...] = Field(min_length=1)
    complete_computational_slots: int = Field(ge=0)
    incomplete_slots: int = Field(ge=0)
    independent_availability_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def _series(batch: DevvalCampaignBatchV1, schedule: GlobalDevvalCompanionScheduleV1):
    expected = tuple(
        (partition, symbol) for partition in _PARTITIONS for symbol in _SYMBOLS
    )
    observed = tuple(
        (
            member.aggregation.plan.window.partition,
            member.aggregation.plan.instrument_id,
        )
        for member in batch.series
    )
    if observed != expected or batch.plan_sha256 != schedule.campaign_plan_sha256:
        raise DevvalJointCompanionCoverageError("joint_companion_batch_scope_changed")
    if batch.research_blueprint_sha256 != schedule.research_blueprint_sha256 or (
        batch.source_commit,
        batch.source_tree,
        batch.source_manifest_sha256,
    ) != (
        schedule.source_checkpoint.commit,
        schedule.source_checkpoint.tree,
        schedule.source_checkpoint.manifest_sha256,
    ):
        raise DevvalJointCompanionCoverageError("joint_companion_source_scope_changed")
    lookup = {}
    for member in batch.series:
        plan = member.aggregation.plan
        if (
            plan.volume_unit != "contracts"
            or plan.source_manifest_sha256 != batch.campaign_sha256
        ):
            raise DevvalJointCompanionCoverageError(
                "joint_companion_volume_unit_changed"
            )
        if member.aggregation.plan_sha256 != plan.canonical_sha256():
            raise DevvalJointCompanionCoverageError(
                "joint_companion_aggregate_pin_changed"
            )
        window = plan.window
        for label, seconds in _FRAME_SECONDS:
            frames = tuple(
                item
                for item in member.aggregation.timeframes
                if item.horizon.label == label and item.horizon.seconds == seconds
            )
            width = timedelta(seconds=seconds)
            expected_count, remainder = divmod(window.end_at - window.start_at, width)
            if len(frames) != 1 or remainder or len(frames[0].bars) != expected_count:
                raise DevvalJointCompanionCoverageError(
                    "joint_companion_aggregate_grid_changed"
                )
            for offset, aggregate in enumerate(frames[0].bars, 1):
                close = window.start_at + offset * width
                if (
                    type(aggregate) is not AggregateBar
                    or aggregate.row.bar.closed_at != close
                    or aggregate.row.instrument_id != plan.instrument_id
                    or aggregate.row.source_row_sha256 != aggregate.constituent_sha256
                    or _ns(aggregate.row.available_at) > member.cutoff_utc_ns
                    or aggregate.constituent_count != seconds // 60
                    or len(aggregate.availability_bases) != seconds // 60
                    or any(
                        basis is not AvailabilityBasis.MEASURED_ROW_RECEIPT
                        for basis in aggregate.availability_bases
                    )
                    or len(aggregate.receipt_sha256s) != seconds // 60
                ):
                    raise DevvalJointCompanionCoverageError(
                        "joint_companion_aggregate_grid_changed"
                    )
                key = (window.partition, plan.instrument_id, label, close)
                if key in lookup:
                    raise DevvalJointCompanionCoverageError(
                        "joint_companion_duplicate_aggregate"
                    )
                lookup[key] = aggregate
    return lookup


def _current(lookup, partition, symbol, horizon, close):
    selected = lookup.get((partition, symbol, horizon, close))
    if selected is None:
        raise DevvalJointCompanionCoverageError("joint_companion_current_bar_missing")
    row = selected.row
    return AggregateReadyV1(
        instrument_id=symbol,
        source_row_id=row.source_row_id,
        source_row_sha256=row.source_row_sha256,
        constituent_sha256=selected.constituent_sha256,
        bar_closed_at=row.bar.closed_at,
        available_at=row.available_at,
    )


def _outcomes(outcomes, slot_count):
    if type(outcomes) is not tuple or len(outcomes) > slot_count * len(_SYMBOLS):
        raise DevvalJointCompanionCoverageError("joint_companion_input_count_invalid")
    indexed = {}
    publication_pins = set()
    readback_pins = set()
    roots = set()
    for item in outcomes:
        if type(item) not in (PublishedCompanionV1, RefusedCompanionV1):
            raise DevvalJointCompanionCoverageError("joint_companion_input_invalid")
        key = (item.slot_ordinal, item.instrument_id)
        if (
            type(item.slot_ordinal) is not int
            or not 0 <= item.slot_ordinal < slot_count
            or item.instrument_id not in _SYMBOLS
            or key in indexed
        ):
            raise DevvalJointCompanionCoverageError(
                "joint_companion_duplicate_or_foreign"
            )
        indexed[key] = item
        if type(item) is RefusedCompanionV1:
            if item.reason not in _REFUSALS:
                raise DevvalJointCompanionCoverageError("joint_companion_input_invalid")
            continue
        if (
            type(item.journal_root) is not type(Path())
            or type(item.companion_root) is not type(Path())
            or not item.journal_root.is_absolute()
            or not item.companion_root.is_absolute()
            or type(item.publication_json) is not bytes
            or type(item.readback_json) is not bytes
            or type(item.expected_publication_sha256) is not str
            or type(item.expected_readback_sha256) is not str
            or SHA256_RE.fullmatch(item.expected_publication_sha256) is None
            or SHA256_RE.fullmatch(item.expected_readback_sha256) is None
            or item.expected_publication_sha256 in publication_pins
            or item.expected_readback_sha256 in readback_pins
            or item.journal_root in roots
            or item.companion_root in roots
            or item.journal_root == item.companion_root
        ):
            raise DevvalJointCompanionCoverageError(
                "joint_companion_duplicate_or_foreign"
            )
        publication_pins.add(item.expected_publication_sha256)
        readback_pins.add(item.expected_readback_sha256)
        roots.update((item.journal_root, item.companion_root))
    return indexed


def _measure(item: PublishedCompanionV1, schedule):
    try:
        publication = verify_global_public_companion_publication_v1(
            item.publication_json,
            expected_sha256=item.expected_publication_sha256,
        )
        if (
            publication.instrument_id != item.instrument_id
            or publication.market_policy_sha256 != schedule.market_policy_sha256
        ):
            raise DevvalJointCompanionCoverageError(
                "joint_companion_policy_or_symbol_changed"
            )
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
        ):
            raise DevvalJointCompanionCoverageError("joint_companion_readback_changed")
        return readback.readback_completed.utc_ns
    except (GlobalPublicCompanionError, OSError, ValueError):
        return None


def _ohlcv_matches(market, lookup, partition, symbol, close):
    """Compare exact closed 15m/1H/4H bars in shared contract units.

    The Global 5m frame has no counterpart in the D/V batch and is not claimed.
    """
    for label, seconds in _FRAME_SECONDS:
        width = timedelta(seconds=seconds)
        epoch = datetime(1970, 1, 1, tzinfo=close.tzinfo)
        target = epoch + (close - epoch) // width * width
        aggregate = lookup.get((partition, symbol, label, target))
        if aggregate is None:
            return False
        candles = market.candles.get(label)
        if type(candles) is not list:
            return False
        matching = tuple(row for row in candles if row.timestamp + width == target)
        if len(matching) != 1:
            return False
        row = matching[0]
        expected = aggregate.row.bar
        if row.confirmed is not True or (
            row.open,
            row.high,
            row.low,
            row.close,
            row.volume_contracts,
        ) != (
            expected.open,
            expected.high,
            expected.low,
            expected.close,
            expected.volume,
        ):
            return False
    return True


def audit_devval_joint_companion_coverage_v1(
    schedule_payload: bytes,
    *,
    expected_schedule_sha256: str,
    batch_payload: bytes,
    expected_batch_sha256: str,
    outcomes: tuple[PublishedCompanionV1 | RefusedCompanionV1, ...],
) -> FrozenGate3Artifact[DevvalJointCompanionCoverageV1]:
    """Replay every event from the separately verified compiled schedule pin."""
    try:
        if (
            type(schedule_payload) is not bytes
            or not 0 < len(schedule_payload) <= _MAX_SCHEDULE_BYTES
            or type(batch_payload) is not bytes
            or not 0 < len(batch_payload) <= _MAX_BATCH_BYTES
        ):
            raise DevvalJointCompanionCoverageError(
                "joint_companion_input_size_invalid"
            )
        schedule = _verify(
            schedule_payload,
            expected_sha256=expected_schedule_sha256,
            contract_type=GlobalDevvalCompanionScheduleV1,
        )
        batch = _verify(
            batch_payload,
            expected_sha256=expected_batch_sha256,
            contract_type=DevvalCampaignBatchV1,
        )
        lookup = _series(batch, schedule)
        indexed = _outcomes(outcomes, len(schedule.slots))
        horizon_label = next(
            label
            for label, seconds in _FRAME_SECONDS
            if seconds == schedule.horizon_seconds
        )
        results = []
        for ordinal, slot in enumerate(schedule.slots):
            aggregate = {
                symbol: _current(
                    lookup,
                    slot.partition,
                    symbol,
                    horizon_label,
                    slot.event_closed_at,
                )
                for symbol in _SYMBOLS
            }
            sides = {}
            measured = {}
            for symbol in _SYMBOLS:
                item = indexed.get((ordinal, symbol))
                if item is None:
                    sides[symbol] = CompanionSideCoverageV1(
                        aggregate=aggregate[symbol], status="missing"
                    )
                elif type(item) is RefusedCompanionV1:
                    sides[symbol] = CompanionSideCoverageV1(
                        aggregate=aggregate[symbol],
                        status="refused",
                        refusal_reason=item.reason,
                    )
                else:
                    measured_ns = _measure(item, schedule)
                    if measured_ns is None:
                        sides[symbol] = CompanionSideCoverageV1(
                            aggregate=aggregate[symbol],
                            status="refused",
                            refusal_reason="measured_readback_refused",
                            publication_sha256=item.expected_publication_sha256,
                            readback_sha256=item.expected_readback_sha256,
                        )
                    elif measured_ns < _ns(slot.event_closed_at):
                        sides[symbol] = CompanionSideCoverageV1(
                            aggregate=aggregate[symbol],
                            status="refused",
                            refusal_reason="readback_before_event_close",
                            publication_sha256=item.expected_publication_sha256,
                            readback_sha256=item.expected_readback_sha256,
                            postpublication_readback_at=utc_from_ns(measured_ns),
                        )
                    else:
                        measured[symbol] = measured_ns
                        sides[symbol] = CompanionSideCoverageV1(
                            aggregate=aggregate[symbol],
                            status=(
                                "late"
                                if max(measured_ns, _ns(aggregate[symbol].available_at))
                                > _ns(slot.decision_cutoff_at)
                                else "measured"
                            ),
                            publication_sha256=item.expected_publication_sha256,
                            readback_sha256=item.expected_readback_sha256,
                            postpublication_readback_at=utc_from_ns(measured_ns),
                        )
            decision = None
            if len(measured) == len(_SYMBOLS):
                joint_ns = max(
                    *measured.values(),
                    *(_ns(item.available_at) for item in aggregate.values()),
                )
                decision = utc_from_ns(joint_ns)
                if (
                    joint_ns <= _ns(slot.decision_cutoff_at)
                    and decision <= slot.decision_cutoff_at
                ):
                    for symbol in _SYMBOLS:
                        item = indexed[(ordinal, symbol)]
                        if type(item) is not PublishedCompanionV1:
                            raise DevvalJointCompanionCoverageError(
                                "joint_companion_pair_changed"
                            )
                        try:
                            replay = replay_legacy_baseline_market_from_companion_v1(
                                item.journal_root,
                                item.companion_root,
                                publication_json=item.publication_json,
                                expected_publication_sha256=item.expected_publication_sha256,
                                decision_at=decision,
                            )
                        except (LegacyBaselineMarketReplayError, OSError):
                            sides[symbol] = sides[symbol].model_copy(
                                update={"status": "replay_refused"}
                            )
                            continue
                        if not _ohlcv_matches(
                            replay.market,
                            lookup,
                            slot.partition,
                            symbol,
                            slot.event_closed_at,
                        ):
                            sides[symbol] = sides[symbol].model_copy(
                                update={"status": "ohlcv_mismatch"}
                            )
                            continue
                        sides[symbol] = sides[symbol].model_copy(
                            update={
                                "status": "covered_computational",
                                "replay_receipt_sha256": replay.receipt_sha256,
                            }
                        )
            complete = all(
                sides[symbol].status == "covered_computational" for symbol in _SYMBOLS
            )
            results.append(
                JointCompanionSlotCoverageV1(
                    ordinal=ordinal,
                    partition=slot.partition,
                    event_closed_at=slot.event_closed_at,
                    decision_cutoff_at=slot.decision_cutoff_at,
                    joint_decision_at=decision if complete else None,
                    status="covered_computational" if complete else "incomplete",
                    sides=tuple(sides[symbol] for symbol in _SYMBOLS),
                )
            )
        covered = sum(item.status == "covered_computational" for item in results)
        report = _freeze(
            DevvalJointCompanionCoverageV1(
                schedule_sha256=expected_schedule_sha256,
                batch_sha256=expected_batch_sha256,
                campaign_plan_sha256=schedule.campaign_plan_sha256,
                research_blueprint_sha256=schedule.research_blueprint_sha256,
                slots=tuple(results),
                complete_computational_slots=covered,
                incomplete_slots=len(results) - covered,
            ),
            contract_type=DevvalJointCompanionCoverageV1,
        )
        if len(report.payload) > _MAX_REPORT_BYTES:
            raise DevvalJointCompanionCoverageError("joint_companion_report_too_large")
        return report
    except DevvalJointCompanionCoverageError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError):
        raise DevvalJointCompanionCoverageError(
            "joint_companion_coverage_refused"
        ) from None
