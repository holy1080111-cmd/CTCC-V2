"""Source-bound minute aggregation for development/validation rehearsal only.

Canonical hashes prove repeatability, not independent acquisition authenticity.
No historical receipt is inferred from an event or archive publication timestamp.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Context, Decimal, Inexact, localcontext
from typing import Literal

from pydantic import Field, model_validator

from app.mie.contracts import ForecastHorizon
from app.mie.features import FeatureBar
from app.mie.validation.archive_batch import (
    ArchiveBatchManifest,
    conservative_batch_rows,
)
from app.mie.validation.availability import AvailabilityBasis, AvailabilityProvenance
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Claim,
    Gate3Contract,
    PartitionWindow,
    Sha256,
)
from app.mie.validation.replay import (
    PointInTimeBar,
    PointInTimeReplaySnapshot,
    replay_features_at,
)

TARGETS = (("15m", 900), ("1H", 3600), ("4H", 14400))
MAX_MINUTES = 256 * 1440


class MinuteAggregationError(ValueError):
    """Source, partition, pin, or chronology cannot be established."""


class BoundMinute(Gate3Contract):
    row: PointInTimeBar
    availability: AvailabilityProvenance

    @model_validator(mode="after")
    def validate_binding(self) -> BoundMinute:
        if (
            self.row.source_row_sha256 != self.availability.source_row_sha256
            or self.row.bar.closed_at != self.availability.bar_closed_at
            or self.row.available_at != self.availability.available_at
        ):
            raise ValueError("minute availability is not bound to its source row")
        return self


class MinuteAggregationPlan(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.minute_aggregation_plan.v1"] = (
        "ctcc.mie.gate3.minute_aggregation_plan.v1"
    )
    source_manifest_sha256: Sha256
    window: PartitionWindow
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    expected_minute_rows: int = Field(ge=240, le=MAX_MINUTES)
    volume_unit: Literal[
        "source_unspecified", "base_currency", "quote_currency", "contracts"
    ]
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False
    runtime_consumers: Literal[0] = 0

    @model_validator(mode="after")
    def validate_partition(self) -> MinuteAggregationPlan:
        if self.window.partition not in (
            DatasetPartition.DEVELOPMENT,
            DatasetPartition.VALIDATION,
        ):
            raise ValueError("minute aggregation excludes holdout partitions")
        for boundary in (self.window.start_at, self.window.end_at):
            if boundary.hour % 4 or any(
                (boundary.minute, boundary.second, boundary.microsecond)
            ):
                raise ValueError(
                    "aggregation window requires complete UTC 4H boundaries"
                )
        if self.window.end_at - self.window.start_at != timedelta(
            minutes=self.expected_minute_rows
        ):
            raise ValueError("minute count does not match the exact partition window")
        return self


class AggregateBar(Gate3Contract):
    row: PointInTimeBar
    constituent_sha256: Sha256
    constituent_count: int = Field(ge=15, le=240)
    availability_bases: tuple[AvailabilityBasis, ...]
    receipt_sha256s: tuple[Sha256, ...]


class AggregatedTimeframe(Gate3Contract):
    horizon: ForecastHorizon
    bars: tuple[AggregateBar, ...] = Field(min_length=1)


class DeterministicMinuteBatch(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.minute_aggregation.v1"] = (
        "ctcc.mie.gate3.minute_aggregation.v1"
    )
    plan: MinuteAggregationPlan
    plan_sha256: Sha256
    source_minutes_sha256: Sha256
    timeframes: tuple[AggregatedTimeframe, ...]
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False
    runtime_consumers: Literal[0] = 0


def _validated_minutes(minutes: tuple[BoundMinute, ...]) -> tuple[BoundMinute, ...]:
    if type(minutes) is not tuple or not 1 <= len(minutes) <= MAX_MINUTES:
        raise MinuteAggregationError("minute source must be a bounded tuple")
    checked = []
    for minute in minutes:
        if type(minute) is not BoundMinute:
            raise MinuteAggregationError("minute source requires the exact contract")
        checked.append(BoundMinute.model_validate(minute.model_dump(mode="python")))
    return tuple(checked)


def _checked_minute_sha256(minutes: tuple[BoundMinute, ...]) -> str:
    digest = hashlib.sha256()
    for minute in minutes:
        data = minute.canonical_json_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def minute_source_sha256(minutes: tuple[BoundMinute, ...]) -> str:
    """Hash complete ordered records including receipt/basis and raw-row links."""
    return _checked_minute_sha256(_validated_minutes(minutes))


def aggregate_point_in_time_minutes(
    minutes: tuple[BoundMinute, ...],
    *,
    plan: MinuteAggregationPlan,
    expected_plan_sha256: str,
    expected_source_sha256: str,
) -> DeterministicMinuteBatch:
    if type(plan) is not MinuteAggregationPlan:
        raise MinuteAggregationError("aggregation plan requires the exact contract")
    plan = MinuteAggregationPlan.model_validate(plan.model_dump(mode="python"))
    if plan.canonical_sha256() != expected_plan_sha256:
        raise MinuteAggregationError("aggregation plan pin changed")
    # Both hashing and arithmetic consume the same reconstructed contracts.
    # A model_copy/model_construct input must never bypass nested validation.
    minutes = _validated_minutes(minutes)
    source_sha256 = _checked_minute_sha256(minutes)
    if source_sha256 != expected_source_sha256:
        raise MinuteAggregationError("minute source pin changed")
    if len(minutes) != plan.expected_minute_rows:
        raise MinuteAggregationError("minute source count is incomplete")
    identities = set()
    for ordinal, minute in enumerate(minutes, start=1):
        row = minute.row
        if row.source_row_id in identities:
            raise MinuteAggregationError("duplicate minute identity")
        identities.add(row.source_row_id)
        if row.instrument_id != plan.instrument_id:
            raise MinuteAggregationError("minute instrument differs from the plan")
        if row.bar.closed_at != plan.window.start_at + timedelta(minutes=ordinal):
            raise MinuteAggregationError(
                "missing, reordered, or out-of-partition minute"
            )

    timeframes = []
    arithmetic = Context(prec=256)
    arithmetic.traps[Inexact] = True
    for label, seconds in TARGETS:
        width = seconds // 60
        bars = []
        for start in range(0, len(minutes), width):
            group = minutes[start : start + width]
            group_sha256 = _checked_minute_sha256(group)
            with localcontext(arithmetic):
                volume = sum((item.row.bar.volume for item in group), Decimal(0))
            bar = FeatureBar(
                closed_at=group[-1].row.bar.closed_at,
                open=group[0].row.bar.open,
                high=max(item.row.bar.high for item in group),
                low=min(item.row.bar.low for item in group),
                close=group[-1].row.bar.close,
                volume=volume,
            )
            bars.append(
                AggregateBar(
                    row=PointInTimeBar(
                        source_row_id=f"aggregate:{seconds}:{group_sha256}",
                        source_row_sha256=group_sha256,
                        instrument_id=plan.instrument_id,
                        available_at=max(item.row.available_at for item in group),
                        bar=bar,
                    ),
                    constituent_sha256=group_sha256,
                    constituent_count=width,
                    availability_bases=tuple(item.availability.basis for item in group),
                    receipt_sha256s=tuple(
                        item.availability.receipt_sha256 for item in group
                    ),
                )
            )
        timeframes.append(
            AggregatedTimeframe(
                horizon=ForecastHorizon(label=label, seconds=seconds), bars=tuple(bars)
            )
        )
    return DeterministicMinuteBatch(
        plan=plan,
        plan_sha256=expected_plan_sha256,
        source_minutes_sha256=source_sha256,
        timeframes=tuple(timeframes),
    )


def replay_aggregated_features_at(
    batch: DeterministicMinuteBatch,
    *,
    minutes: tuple[BoundMinute, ...],
    expected_plan_sha256: str,
    expected_source_sha256: str,
    horizon_seconds: Literal[900, 3600, 14400],
    as_of: datetime,
    history_bars: int = 256,
) -> PointInTimeReplaySnapshot:
    """Rebuild original inputs before handing aggregates to the existing replay."""
    if type(batch) is not DeterministicMinuteBatch:
        raise MinuteAggregationError("batch requires the exact contract")
    rebuilt = aggregate_point_in_time_minutes(
        minutes,
        plan=batch.plan,
        expected_plan_sha256=expected_plan_sha256,
        expected_source_sha256=expected_source_sha256,
    )
    if rebuilt.canonical_json_bytes() != batch.canonical_json_bytes():
        raise MinuteAggregationError("aggregate output failed original-source replay")
    series = next(
        (
            item
            for item in rebuilt.timeframes
            if item.horizon.seconds == horizon_seconds
        ),
        None,
    )
    if series is None:
        raise MinuteAggregationError("unsupported aggregation horizon")
    return replay_features_at(
        tuple(item.row for item in series.bars),
        as_of=as_of,
        bar_horizon=series.horizon,
        history_bars=history_bars,
    )


def archive_batch_minutes(
    manifest: ArchiveBatchManifest,
    *,
    archive_bytes: tuple[bytes, ...],
    expected_plan_sha256: str,
    partition: DatasetPartition,
    instrument_id: str,
) -> tuple[MinuteAggregationPlan, tuple[BoundMinute, ...]]:
    """Bridge the existing fully verified archive batch; never accepts a holdout."""
    rows = conservative_batch_rows(
        manifest,
        archive_bytes=archive_bytes,
        expected_plan_sha256=expected_plan_sha256,
        partition=partition,
        instrument_id=instrument_id,
    )
    receipts = {item.receipt_sha256: item.receipt for item in manifest.members}
    minutes = []
    for row in rows:
        receipt_hash = row.source_row_id.split(":")[1]
        receipt = receipts[receipt_hash]
        minutes.append(
            BoundMinute(
                row=row,
                availability=AvailabilityProvenance(
                    basis=AvailabilityBasis.ARCHIVE_OBSERVATION,
                    source_row_sha256=row.source_row_sha256,
                    receipt_sha256=receipt_hash,
                    bar_closed_at=row.bar.closed_at,
                    observed_at=receipt.observed_at,
                    retrieved_at=receipt.retrieved_at,
                    available_at=receipt.retrieved_at,
                ),
            )
        )
    window = (
        manifest.plan.split.development
        if partition == DatasetPartition.DEVELOPMENT
        else manifest.plan.split.validation
    )
    return (
        MinuteAggregationPlan(
            source_manifest_sha256=manifest.canonical_sha256(),
            window=window,
            instrument_id=instrument_id,
            expected_minute_rows=len(minutes),
            volume_unit="source_unspecified",
        ),
        tuple(minutes),
    )
