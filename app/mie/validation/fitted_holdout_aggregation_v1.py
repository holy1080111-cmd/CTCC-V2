"""Inert aggregation of an already materialized, pinned fitted holdout tape.

No file, exchange, account, custody writer, seal reader, model, or Gate 4
entrypoint is present. This contract never reclassifies a HOLDOUT as D/V.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from app.mie.contracts import ForecastHorizon
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.batch_replay import TARGETS
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
)
from app.mie.validation.fitted_holdout_materializer_v1 import (
    FittedHoldoutMinuteTapeV1,
    FittedHoldoutMinuteV1,
    materialize_fitted_holdout_v1,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import FittedCandidateInputsV1
from app.mie.validation.minute_ohlcv_core_v1 import aggregate_ohlcv_group
from app.mie.validation.prospective_capture_schedule import ProspectiveCoordinatePlanV1
from app.mie.validation.replay import PointInTimeBar

_MAX_COORDINATE_BYTES = 64 * 1024
_MAX_TAPE_BYTES = 32 * 1024 * 1024
_MAX_REPORT_BYTES = 8 * 1024 * 1024
_MINUTE = timedelta(minutes=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FittedHoldoutAggregationError(ValueError):
    """Fixed denial for a changed source, grid, or aggregate."""


class HoldoutAggregateBarV1(Gate3Contract):
    row: PointInTimeBar
    constituent_sha256: Sha256
    constituent_count: Literal[15, 60, 240]
    first_ordinal: int = Field(ge=0)
    last_ordinal: int = Field(ge=0)
    ordinal_stride: int = Field(ge=1)
    capture_receipt_sha256s: tuple[Sha256, ...] = Field(min_length=15, max_length=240)
    availability_basis: Literal["pinned_manifest_durable_readback"] = (
        "pinned_manifest_durable_readback"
    )

    @model_validator(mode="after")
    def validate_inventory(self) -> HoldoutAggregateBarV1:
        if (
            self.last_ordinal - self.first_ordinal
            != (self.constituent_count - 1) * self.ordinal_stride
            or len(self.capture_receipt_sha256s) != self.constituent_count
            or self.row.source_row_sha256 != self.constituent_sha256
        ):
            raise ValueError("holdout_aggregate_inventory_invalid")
        return self


class HoldoutTimeframeV1(Gate3Contract):
    horizon: ForecastHorizon
    input_minute_count: int = Field(ge=1)
    excluded_leading_minutes: int = Field(ge=0)
    excluded_trailing_minutes: int = Field(ge=0)
    bucket_status: Literal["complete_buckets", "no_complete_bucket"]
    bars: tuple[HoldoutAggregateBarV1, ...]

    @model_validator(mode="after")
    def validate_coverage(self) -> HoldoutTimeframeV1:
        width = self.horizon.seconds // 60
        if (
            self.horizon.seconds % 60
            or self.excluded_leading_minutes
            + len(self.bars) * width
            + self.excluded_trailing_minutes
            != self.input_minute_count
            or self.bucket_status
            != ("complete_buckets" if self.bars else "no_complete_bucket")
            or any(bar.constituent_count != width for bar in self.bars)
        ):
            raise ValueError("holdout_aggregate_bucket_coverage_invalid")
        return self


class HoldoutSymbolSeriesV1(Gate3Contract):
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    timeframes: tuple[HoldoutTimeframeV1, ...] = Field(min_length=3, max_length=3)


class FittedHoldoutAggregationV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_holdout_aggregation.v1"] = (
        "ctcc.mie.fitted_holdout_aggregation.v1"
    )
    tape_sha256: Sha256
    coordinate_plan_sha256: Sha256
    seal_sha256: Sha256
    binding_sha256: Sha256
    receipt_sha256: Sha256
    reservation_sha256: Sha256
    manifest_sha256: Sha256
    dataset_sha256: Sha256
    original_minutes_sha256: Sha256
    partition: Literal["prospective_holdout"] = "prospective_holdout"
    start_at: datetime
    end_at: datetime
    instrument_ids: tuple[str, ...] = Field(min_length=1)
    series: tuple[HoldoutSymbolSeriesV1, ...] = Field(min_length=1)
    volume_unit: Literal["contracts"] = "contracts"
    row_availability_basis: Literal["pinned_manifest_durable_readback"] = (
        "pinned_manifest_durable_readback"
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    independent_source_custody_verified: Literal[False] = False
    independent_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    gate4_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_series(self) -> FittedHoldoutAggregationV1:
        if tuple(
            item.instrument_id for item in self.series
        ) != self.instrument_ids or any(
            tuple(
                (frame.horizon.label, frame.horizon.seconds)
                for frame in item.timeframes
            )
            != TARGETS
            for item in self.series
        ):
            raise ValueError("holdout_aggregate_series_invalid")
        return self


def _group_sha256(minutes: tuple[FittedHoldoutMinuteV1, ...]) -> str:
    digest = hashlib.sha256()
    for minute in minutes:
        payload = minute.canonical_json_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _read[T: Gate3Contract](
    payload: bytes, digest: str, contract: type[T], limit: int
) -> T:
    if type(payload) is not bytes or not 0 < len(payload) <= limit:
        raise ArtifactVerificationError("holdout_aggregate_source_size_invalid")
    return _verify(payload, expected_sha256=digest, contract_type=contract)


def _bucket_start_index(start_at: datetime, seconds: int, minute_count: int) -> int:
    delta = start_at - _EPOCH
    start_seconds = delta.days * 86_400 + delta.seconds
    remainder = start_seconds % seconds
    offset_minutes = (seconds - remainder) // 60 if remainder else 0
    return min(offset_minutes, minute_count)


def aggregate_fitted_holdout_v1(
    tape_bytes: bytes,
    coordinate_plan_bytes: bytes,
    *,
    expected_tape_sha256: str,
    expected_coordinate_plan_sha256: str,
) -> FrozenGate3Artifact[FittedHoldoutAggregationV1]:
    """Aggregate the exact previously read tape; never open a holdout path."""
    tape = _read(
        tape_bytes, expected_tape_sha256, FittedHoldoutMinuteTapeV1, _MAX_TAPE_BYTES
    )
    coordinates = _read(
        coordinate_plan_bytes,
        expected_coordinate_plan_sha256,
        ProspectiveCoordinatePlanV1,
        _MAX_COORDINATE_BYTES,
    )
    if (
        tape.coordinate_plan_sha256 != expected_coordinate_plan_sha256
        or coordinates.expected_rows != len(tape.minutes)
        or coordinates.interval_seconds != 60
        or coordinates.source != "okx.public_market_source"
        or coordinates.source_version != "okx.history_candles.nine_strings.v1"
        or tape.volume_unit != "contracts"
    ):
        raise FittedHoldoutAggregationError("holdout_aggregate_plan_or_tape_invalid")
    symbols = coordinates.instrument_ids
    width = len(symbols)
    minute_count = (coordinates.end_at - coordinates.start_at) // _MINUTE
    if minute_count < 1:
        raise FittedHoldoutAggregationError("holdout_aggregate_window_invalid")
    for ordinal, minute in enumerate(tape.minutes):
        minute_index, symbol_index = divmod(ordinal, width)
        expected_open = coordinates.start_at + minute_index * _MINUTE
        if (
            minute.evidence.ordinal != ordinal
            or minute.evidence.opened_at != expected_open
            or minute.evidence.closed_at != expected_open + _MINUTE
            or minute.row.bar.closed_at != expected_open + _MINUTE
            or minute.row.instrument_id != symbols[symbol_index]
            or minute.evidence.instrument_id != symbols[symbol_index]
            or minute.row.available_at != minute.evidence.retained_at
            or minute.evidence.decision_available_at != minute.row.available_at
        ):
            raise FittedHoldoutAggregationError("holdout_aggregate_grid_changed")
    series = []
    for symbol_index, symbol in enumerate(symbols):
        symbol_minutes = tape.minutes[symbol_index::width]
        if len(symbol_minutes) != minute_count:
            raise FittedHoldoutAggregationError("holdout_aggregate_grid_changed")
        frames = []
        for label, seconds in TARGETS:
            block_width = seconds // 60
            leading = _bucket_start_index(coordinates.start_at, seconds, minute_count)
            complete_count = (minute_count - leading) // block_width
            trailing = minute_count - leading - complete_count * block_width
            bars = []
            for block in range(complete_count):
                start = leading + block * block_width
                group = symbol_minutes[start : start + block_width]
                if len(group) != block_width:
                    raise FittedHoldoutAggregationError(
                        "holdout_aggregate_group_missing"
                    )
                group_sha = _group_sha256(group)
                bar, available_at = aggregate_ohlcv_group(
                    tuple(item.row for item in group)
                )
                bars.append(
                    HoldoutAggregateBarV1(
                        row=PointInTimeBar(
                            source_row_id=f"holdout-aggregate:{seconds}:{group_sha}",
                            source_row_sha256=group_sha,
                            instrument_id=symbol,
                            available_at=available_at,
                            bar=bar,
                        ),
                        constituent_sha256=group_sha,
                        constituent_count=block_width,
                        first_ordinal=group[0].evidence.ordinal,
                        last_ordinal=group[-1].evidence.ordinal,
                        ordinal_stride=width,
                        capture_receipt_sha256s=tuple(
                            item.evidence.capture_receipt_sha256 for item in group
                        ),
                    )
                )
            frames.append(
                HoldoutTimeframeV1(
                    horizon=ForecastHorizon(label=label, seconds=seconds),
                    input_minute_count=minute_count,
                    excluded_leading_minutes=leading,
                    excluded_trailing_minutes=trailing,
                    bucket_status=(
                        "complete_buckets" if bars else "no_complete_bucket"
                    ),
                    bars=tuple(bars),
                )
            )
        series.append(
            HoldoutSymbolSeriesV1(instrument_id=symbol, timeframes=tuple(frames))
        )
    frozen = _freeze(
        FittedHoldoutAggregationV1(
            tape_sha256=expected_tape_sha256,
            coordinate_plan_sha256=expected_coordinate_plan_sha256,
            seal_sha256=tape.seal_sha256,
            binding_sha256=tape.binding_sha256,
            receipt_sha256=tape.receipt_sha256,
            reservation_sha256=tape.reservation_sha256,
            manifest_sha256=tape.manifest_sha256,
            dataset_sha256=tape.dataset_sha256,
            original_minutes_sha256=tape.minutes_sha256,
            start_at=coordinates.start_at,
            end_at=coordinates.end_at,
            instrument_ids=symbols,
            series=tuple(series),
        ),
        contract_type=FittedHoldoutAggregationV1,
    )
    if len(frozen.payload) > _MAX_REPORT_BYTES:
        raise ArtifactVerificationError("holdout_aggregate_output_size_invalid")
    return frozen


def aggregate_fitted_holdout_readback_v1(
    readback: ControlledFittedHoldoutReadV1,
    *,
    inputs: FittedCandidateInputsV1,
    receipt_bytes: bytes,
    coordinate_plan_bytes: bytes,
    expected_binding_sha256: str,
    expected_receipt_sha256: str,
    expected_reservation_sha256: str,
) -> FrozenGate3Artifact[FittedHoldoutAggregationV1]:
    """Rebuild the tape from custody bytes before computing any aggregate."""
    tape = materialize_fitted_holdout_v1(
        readback,
        inputs=inputs,
        receipt_bytes=receipt_bytes,
        coordinate_plan_bytes=coordinate_plan_bytes,
        expected_binding_sha256=expected_binding_sha256,
        expected_receipt_sha256=expected_receipt_sha256,
        expected_reservation_sha256=expected_reservation_sha256,
    )
    return aggregate_fitted_holdout_v1(
        tape.payload,
        coordinate_plan_bytes,
        expected_tape_sha256=tape.sha256,
        expected_coordinate_plan_sha256=tape.contract.coordinate_plan_sha256,
    )


def verify_fitted_holdout_aggregation_v1(
    report_bytes: bytes,
    tape_bytes: bytes,
    coordinate_plan_bytes: bytes,
    *,
    expected_report_sha256: str,
    expected_tape_sha256: str,
    expected_coordinate_plan_sha256: str,
) -> FrozenGate3Artifact[FittedHoldoutAggregationV1]:
    """Read back and recompute every constituent from the pinned minute tape."""
    checked = _read(
        report_bytes,
        expected_report_sha256,
        FittedHoldoutAggregationV1,
        _MAX_REPORT_BYTES,
    )
    rebuilt = aggregate_fitted_holdout_v1(
        tape_bytes,
        coordinate_plan_bytes,
        expected_tape_sha256=expected_tape_sha256,
        expected_coordinate_plan_sha256=expected_coordinate_plan_sha256,
    )
    if checked.canonical_json_bytes() != rebuilt.payload:
        raise ArtifactVerificationError("holdout_aggregate_readback_changed")
    return rebuilt
