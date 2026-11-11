"""Deterministic aggregation of the original measured campaign minutes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze
from app.mie.validation.availability import AvailabilityBasis
from app.mie.validation.batch_replay import (
    BoundMinute,
    DeterministicMinuteBatch,
    MinuteAggregationPlan,
    aggregate_point_in_time_minutes,
    minute_source_sha256,
)
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignManifestV1,
)
from app.mie.validation.replay import PointInTimeReplaySnapshot, replay_features_at

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_BATCH_BYTES = 64 * 1024 * 1024
_HISTORY_BARS = 21


class DevvalCampaignBatchError(ValueError):
    """Fixed refusal without source rows or private evidence locations."""


class DevvalCampaignBatchSeriesV1(Gate3Contract):
    cutoff_utc_ns: int = Field(gt=0)
    aggregation: DeterministicMinuteBatch
    features: tuple[PointInTimeReplaySnapshot, ...] = Field(min_length=3, max_length=3)


class DevvalCampaignBatchV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_campaign_batch.v1"] = (
        "ctcc.mie.devval_campaign_batch.v1"
    )
    campaign_sha256: Sha256
    plan_sha256: Sha256
    completion_sha256: Sha256
    root_binding_sha256: Sha256
    row_inventory_sha256: Sha256
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_manifest_sha256: Sha256
    series: tuple[DevvalCampaignBatchSeriesV1, ...] = Field(min_length=4, max_length=4)
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def _build_batch(
    campaign: ProspectiveDevvalCampaignManifestV1,
    rows: tuple[BoundMinute, ...],
    *,
    completion_sha256: str,
    root_binding_sha256: str,
    row_inventory_sha256: str,
    development_cutoff_utc_ns: int,
    validation_cutoff_utc_ns: int,
) -> FrozenGate3Artifact[DevvalCampaignBatchV1]:
    """Called by the verified replay before its source context is released."""
    plan = campaign.plan
    windows = (plan.development, plan.validation)
    cutoffs = (development_cutoff_utc_ns, validation_cutoff_utc_ns)
    if (
        len(rows) != plan.expected_rows
        or not _ns(plan.development.end_at)
        <= development_cutoff_utc_ns
        < _ns(plan.validation.start_at)
        or validation_cutoff_utc_ns < _ns(plan.validation.end_at)
    ):
        raise DevvalCampaignBatchError("devval_campaign_batch_cutoff_or_count_invalid")

    campaign_pin = campaign.canonical_sha256()
    width = len(plan.instrument_ids)
    groups = []
    offset = 0
    for window, cutoff_ns in zip(windows, cutoffs, strict=True):
        minute_count = (window.end_at - window.start_at) // timedelta(minutes=1)
        block_size = minute_count * width
        partition_rows = rows[offset : offset + block_size]
        offset += block_size
        cutoff = _EPOCH + timedelta(microseconds=cutoff_ns // 1000)
        for symbol_index, symbol in enumerate(plan.instrument_ids):
            minutes = partition_rows[symbol_index::width]
            if len(minutes) != minute_count or any(
                item.row.instrument_id != symbol
                or item.row.bar.closed_at != window.start_at + timedelta(minutes=index)
                or item.availability.basis is not AvailabilityBasis.MEASURED_ROW_RECEIPT
                or item.row.available_at > cutoff
                for index, item in enumerate(minutes, start=1)
            ):
                raise DevvalCampaignBatchError("devval_campaign_batch_minute_changed")
            groups.append((window, symbol, cutoff_ns, cutoff, minutes))
    if offset != len(rows):
        raise DevvalCampaignBatchError("devval_campaign_batch_cutoff_or_count_invalid")

    series = []
    for window, symbol, cutoff_ns, cutoff, minutes in groups:
        aggregation_plan = MinuteAggregationPlan(
            source_manifest_sha256=campaign_pin,
            window=window,
            instrument_id=symbol,
            expected_minute_rows=len(minutes),
            volume_unit="contracts",
        )
        aggregation = aggregate_point_in_time_minutes(
            minutes,
            plan=aggregation_plan,
            expected_plan_sha256=aggregation_plan.canonical_sha256(),
            expected_source_sha256=minute_source_sha256(minutes),
        )
        features = tuple(
            replay_features_at(
                tuple(item.row for item in timeframe.bars),
                as_of=cutoff,
                bar_horizon=timeframe.horizon,
                history_bars=_HISTORY_BARS,
            )
            for timeframe in aggregation.timeframes
        )
        series.append(
            DevvalCampaignBatchSeriesV1(
                cutoff_utc_ns=cutoff_ns, aggregation=aggregation, features=features
            )
        )
    result = _freeze(
        DevvalCampaignBatchV1(
            campaign_sha256=campaign_pin,
            plan_sha256=campaign.plan_sha256,
            completion_sha256=completion_sha256,
            root_binding_sha256=root_binding_sha256,
            row_inventory_sha256=row_inventory_sha256,
            source_commit=plan.declared_source_commit,
            source_tree=plan.declared_source_tree,
            source_manifest_sha256=plan.manifest_sha256,
            series=tuple(series),
        ),
        contract_type=DevvalCampaignBatchV1,
    )
    if len(result.payload) > _MAX_BATCH_BYTES:
        raise DevvalCampaignBatchError("devval_campaign_batch_size")
    return result
