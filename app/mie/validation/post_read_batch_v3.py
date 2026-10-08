"""Bounded computational 1m-to-15m/1H/4H replay from DB0035 observations.

Every source capture is independently verified against its raw journal, full
committed witness chain, and persisted DB0035 row before this module accepts
its one minute. Source payload retrieval and later witness observation remain
distinct times. No V1 availability field is synthesized or rewritten.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Context, Decimal, Inexact, localcontext
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.mie.contracts import ForecastHorizon
from app.mie.features import FeatureBar
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.batch_replay import TARGETS, MinuteAggregationPlan
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Sha256,
    require_utc,
)
from app.mie.validation.post_publication_availability_v3 import (
    PostPublicationCaptureV3,
    verify_post_publication_capture_v3,
)
from app.mie.validation.replay import PointInTimeBar
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

MAX_POST_READ_MINUTES = 1024
MAX_CAPTURE_ARTIFACT_BYTES = 64 * 1024
MAX_BATCH_ARTIFACT_BYTES = 8 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BASIS = "persisted_restricted_post_read_server_clock_unverified"


class PostReadBatchError(ValueError):
    """A source pin, coverage, chronology, or cutoff failed closed."""


@dataclass(frozen=True, slots=True)
class PostReadCaptureInputV3:
    payload: bytes
    expected_sha256: str
    journal: ControlledPublicReceiptJournal
    witness_repository: PublicReceiptWitnessRepository
    capture_id: str
    expected_plan_sha256: str
    expected_receipt_sha256: str
    expected_journal_genesis_sha256: str
    expected_capture_checkpoint_sha256: str
    expected_witness_revision: int
    expected_witness_record_sha256: str


class PostReadMinuteV3(Gate3Contract):
    capture_v3_sha256: Sha256
    capture_v2_sha256: Sha256
    journal_genesis_sha256: Sha256
    witness_revision: int = Field(ge=3, le=8192)
    witness_record_sha256: Sha256
    checkpoint_sha256: Sha256
    capture_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_sha256: Sha256
    receipt_sha256: Sha256
    source_row_identity: Sha256
    raw_page_sha256: Sha256
    validation_complete_at: datetime
    payload_readback_at: datetime
    witness_recorded_at: datetime
    v2_witness_observed_at: datetime
    persisted_observed_at: datetime
    row: PointInTimeBar
    availability_basis: Literal[
        "persisted_restricted_post_read_server_clock_unverified"
    ] = _BASIS
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator(
        "validation_complete_at",
        "payload_readback_at",
        "witness_recorded_at",
        "v2_witness_observed_at",
        "persisted_observed_at",
    )
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def source_order(self) -> PostReadMinuteV3:
        if not (
            self.row.bar.closed_at
            <= self.validation_complete_at
            <= self.payload_readback_at
            <= self.witness_recorded_at
            <= self.v2_witness_observed_at
            <= self.persisted_observed_at
            == self.row.available_at
        ):
            raise ValueError("post-read minute has noncausal availability")
        if self.row.source_row_id != "okx-public-minute:" + self.source_row_identity:
            raise ValueError("post-read minute source identity differs")
        return self


class PostReadAggregateBarV3(Gate3Contract):
    row: PointInTimeBar
    constituent_sha256: Sha256
    constituent_count: int = Field(ge=15, le=240)
    capture_v3_sha256s: tuple[Sha256, ...] = Field(min_length=15, max_length=240)
    receipt_sha256s: tuple[Sha256, ...] = Field(min_length=15, max_length=240)
    availability_basis: Literal[
        "persisted_restricted_post_read_server_clock_unverified"
    ] = _BASIS

    @model_validator(mode="after")
    def links(self) -> PostReadAggregateBarV3:
        if (
            self.row.source_row_sha256 != self.constituent_sha256
            or len(self.capture_v3_sha256s) != self.constituent_count
            or len(self.receipt_sha256s) != self.constituent_count
        ):
            raise ValueError("post-read aggregate links differ")
        return self


class PostReadTimeframeV3(Gate3Contract):
    horizon: ForecastHorizon
    bars: tuple[PostReadAggregateBarV3, ...] = Field(min_length=1)


def _sequence_sha256(records: tuple[Gate3Contract, ...]) -> str:
    digest = hashlib.sha256()
    for record in records:
        payload = record.canonical_json_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def capture_chain_sha256_v3(capture_sha256s: tuple[str, ...]) -> str:
    """Ordered external V3 artifact digest chain; no sorting or deduplication."""
    if (
        type(capture_sha256s) is not tuple
        or not 1 <= len(capture_sha256s) <= MAX_POST_READ_MINUTES
        or any(
            type(digest) is not str or _SHA256.fullmatch(digest) is None
            for digest in capture_sha256s
        )
    ):
        raise PostReadBatchError("post_read_capture_chain_invalid")
    digest = hashlib.sha256()
    for item in capture_sha256s:
        digest.update(bytes.fromhex(item))
    return digest.hexdigest()


class PostReadMinuteBatchV3(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.post_read_minute_batch.v3"] = (
        "ctcc.mie.gate3.post_read_minute_batch.v3"
    )
    plan: MinuteAggregationPlan
    plan_sha256: Sha256
    source_minutes_sha256: Sha256
    minutes: tuple[PostReadMinuteV3, ...] = Field(min_length=240, max_length=1024)
    timeframes: tuple[PostReadTimeframeV3, ...] = Field(min_length=3, max_length=3)
    availability_basis: Literal[
        "persisted_restricted_post_read_server_clock_unverified"
    ] = _BASIS
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def source_and_coverage(self) -> PostReadMinuteBatchV3:
        if (
            self.plan.canonical_sha256() != self.plan_sha256
            or self.plan.volume_unit != "contracts"
            or len(self.minutes) != self.plan.expected_minute_rows
            or self.source_minutes_sha256 != _sequence_sha256(self.minutes)
            or self.plan.source_manifest_sha256
            != capture_chain_sha256_v3(
                tuple(item.capture_v3_sha256 for item in self.minutes)
            )
        ):
            raise ValueError("post-read plan, unit, or source chain differs")
        capture_ids = set()
        row_ids = set()
        witness_keys = set()
        for ordinal, minute in enumerate(self.minutes, start=1):
            witness_key = (minute.journal_genesis_sha256, minute.witness_revision)
            if (
                minute.row.instrument_id != self.plan.instrument_id
                or minute.row.bar.closed_at
                != self.plan.window.start_at + timedelta(minutes=ordinal)
                or minute.capture_id in capture_ids
                or minute.row.source_row_id in row_ids
                or witness_key in witness_keys
            ):
                raise ValueError("post-read minute is missing, repeated, or reordered")
            capture_ids.add(minute.capture_id)
            row_ids.add(minute.row.source_row_id)
            witness_keys.add(witness_key)
        for frame, (label, seconds) in zip(self.timeframes, TARGETS, strict=True):
            width = seconds // 60
            if (
                frame.horizon.label != label
                or frame.horizon.seconds != seconds
                or len(frame.bars) != len(self.minutes) // width
            ):
                raise ValueError("post-read aggregate horizon or coverage differs")
            for index, bar in enumerate(frame.bars):
                group = self.minutes[index * width : (index + 1) * width]
                group_sha256 = _sequence_sha256(group)
                arithmetic = Context(prec=256)
                arithmetic.traps[Inexact] = True
                with localcontext(arithmetic):
                    expected_volume = sum(
                        (item.row.bar.volume for item in group), Decimal(0)
                    )
                if (
                    bar.constituent_count != width
                    or bar.constituent_sha256 != group_sha256
                    or bar.row.source_row_id
                    != f"aggregate-post-read:{seconds}:{group_sha256}"
                    or bar.row.source_row_sha256 != group_sha256
                    or bar.row.instrument_id != self.plan.instrument_id
                    or bar.row.bar.closed_at != group[-1].row.bar.closed_at
                    or bar.row.available_at
                    != max(item.persisted_observed_at for item in group)
                    or bar.row.bar.open != group[0].row.bar.open
                    or bar.row.bar.high != max(item.row.bar.high for item in group)
                    or bar.row.bar.low != min(item.row.bar.low for item in group)
                    or bar.row.bar.close != group[-1].row.bar.close
                    or bar.row.bar.volume != expected_volume
                    or bar.capture_v3_sha256s
                    != tuple(item.capture_v3_sha256 for item in group)
                    or bar.receipt_sha256s
                    != tuple(item.receipt_sha256 for item in group)
                ):
                    raise ValueError("post-read aggregate source or time differs")
        return self


async def _verified_minute(
    source: PostReadCaptureInputV3,
    observation_repository: PublicReceiptPostReadRepository,
) -> PostReadMinuteV3:
    if (
        type(source) is not PostReadCaptureInputV3
        or type(source.payload) is not bytes
        or not 1 <= len(source.payload) <= MAX_CAPTURE_ARTIFACT_BYTES
        or type(source.expected_sha256) is not str
        or _SHA256.fullmatch(source.expected_sha256) is None
    ):
        raise PostReadBatchError("post_read_capture_input_invalid")
    source_kwargs = {
        "journal": source.journal,
        "witness_repository": source.witness_repository,
        "capture_id": source.capture_id,
        "expected_plan_sha256": source.expected_plan_sha256,
        "expected_receipt_sha256": source.expected_receipt_sha256,
        "expected_journal_genesis_sha256": source.expected_journal_genesis_sha256,
        "expected_capture_checkpoint_sha256": source.expected_capture_checkpoint_sha256,
        "expected_witness_revision": source.expected_witness_revision,
        "expected_witness_record_sha256": source.expected_witness_record_sha256,
    }
    verified = await verify_post_publication_capture_v3(
        source.payload,
        expected_sha256=source.expected_sha256,
        observation_repository=observation_repository,
        **source_kwargs,
    )
    if (
        type(verified) is not PostPublicationCaptureV3
        or len(verified.v2_capture.rows) != 1
    ):
        raise PostReadBatchError("post_read_capture_requires_one_minute")
    v2 = verified.v2_capture
    row = v2.rows[0]
    observed = verified.post_read_observation
    return PostReadMinuteV3(
        capture_v3_sha256=source.expected_sha256,
        capture_v2_sha256=verified.v2_capture_sha256,
        journal_genesis_sha256=v2.journal_genesis_sha256,
        witness_revision=v2.witness_revision,
        witness_record_sha256=v2.witness_record_sha256,
        checkpoint_sha256=v2.capture_checkpoint_sha256,
        capture_id=v2.capture_id,
        plan_sha256=v2.plan_sha256,
        receipt_sha256=v2.receipt_sha256,
        source_row_identity=row.source_row_identity,
        raw_page_sha256=row.raw_page_sha256,
        validation_complete_at=v2.validation_complete_at,
        payload_readback_at=v2.payload_readback_at,
        witness_recorded_at=observed.witness_recorded_at,
        v2_witness_observed_at=v2.committed_witness_observed_at,
        persisted_observed_at=observed.observed_at,
        row=PointInTimeBar(
            source_row_id="okx-public-minute:" + row.source_row_identity,
            source_row_sha256=row.source_row_sha256,
            instrument_id=v2.instrument_id,
            available_at=observed.observed_at,
            bar=row.bar,
        ),
    )


def _build(
    minutes: tuple[PostReadMinuteV3, ...],
    *,
    plan: MinuteAggregationPlan,
    expected_plan_sha256: str,
) -> PostReadMinuteBatchV3:
    if (
        type(plan) is not MinuteAggregationPlan
        or type(minutes) is not tuple
        or not 240 <= len(minutes) <= MAX_POST_READ_MINUTES
    ):
        raise PostReadBatchError("post_read_plan_or_count_invalid")
    plan = MinuteAggregationPlan.model_validate(plan.model_dump(mode="python"))
    if (
        plan.canonical_sha256() != expected_plan_sha256
        or plan.volume_unit != "contracts"
    ):
        raise PostReadBatchError("post_read_plan_pin_or_volume_unit_invalid")
    checked_minutes = []
    for item in minutes:
        if type(item) is not PostReadMinuteV3:
            raise PostReadBatchError("post_read_minute_contract_invalid")
        checked_minutes.append(
            PostReadMinuteV3.model_validate(item.model_dump(mode="python"))
        )
    checked = tuple(checked_minutes)
    source_digest = _sequence_sha256(checked)
    arithmetic = Context(prec=256)
    arithmetic.traps[Inexact] = True
    frames = []
    for label, seconds in TARGETS:
        width = seconds // 60
        bars = []
        for start in range(0, len(checked), width):
            group = checked[start : start + width]
            if len(group) != width:
                raise PostReadBatchError("post_read_partial_aggregate")
            group_sha256 = _sequence_sha256(group)
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
                PostReadAggregateBarV3(
                    row=PointInTimeBar(
                        source_row_id=f"aggregate-post-read:{seconds}:{group_sha256}",
                        source_row_sha256=group_sha256,
                        instrument_id=plan.instrument_id,
                        available_at=max(item.persisted_observed_at for item in group),
                        bar=bar,
                    ),
                    constituent_sha256=group_sha256,
                    constituent_count=width,
                    capture_v3_sha256s=tuple(item.capture_v3_sha256 for item in group),
                    receipt_sha256s=tuple(item.receipt_sha256 for item in group),
                )
            )
        frames.append(
            PostReadTimeframeV3(
                horizon=ForecastHorizon(label=label, seconds=seconds),
                bars=tuple(bars),
            )
        )
    return PostReadMinuteBatchV3(
        plan=plan,
        plan_sha256=expected_plan_sha256,
        source_minutes_sha256=source_digest,
        minutes=checked,
        timeframes=tuple(frames),
    )


async def _rebuild(
    sources: tuple[PostReadCaptureInputV3, ...],
    *,
    observation_repository: PublicReceiptPostReadRepository,
    plan: MinuteAggregationPlan,
    expected_plan_sha256: str,
) -> PostReadMinuteBatchV3:
    if (
        type(sources) is not tuple
        or not 240 <= len(sources) <= MAX_POST_READ_MINUTES
        or type(observation_repository) is not PublicReceiptPostReadRepository
    ):
        raise PostReadBatchError("post_read_batch_inputs_invalid")
    if any(type(item) is not PostReadCaptureInputV3 for item in sources):
        raise PostReadBatchError("post_read_capture_input_invalid")
    if any(
        type(item.payload) is not bytes
        or not 1 <= len(item.payload) <= MAX_CAPTURE_ARTIFACT_BYTES
        or type(item.journal) is not ControlledPublicReceiptJournal
        for item in sources
    ):
        raise PostReadBatchError("post_read_capture_payload_invalid")
    if (
        type(plan) is not MinuteAggregationPlan
        or plan.source_manifest_sha256
        != capture_chain_sha256_v3(tuple(item.expected_sha256 for item in sources))
    ):
        raise PostReadBatchError("post_read_external_capture_chain_mismatch")
    journal_pins = {}
    for source in sources:
        if source.journal not in journal_pins:
            journal_pins[source.journal] = source.journal.checkpoint.canonical_sha256()
    minutes = []
    for source in sources:
        minutes.append(await _verified_minute(source, observation_repository))
    for journal, expected in journal_pins.items():
        if journal.checkpoint.canonical_sha256() != expected:
            raise PostReadBatchError("post_read_journal_changed_during_batch")
        try:
            # Native inventory replay catches an append through another object
            # or process while this object still holds its old _checkpoint.
            journal.read_all()
        except (OSError, ValueError) as exc:
            raise PostReadBatchError("post_read_journal_final_replay_failed") from exc
        if journal.checkpoint.canonical_sha256() != expected:
            raise PostReadBatchError("post_read_journal_changed_during_batch")
    return _build(tuple(minutes), plan=plan, expected_plan_sha256=expected_plan_sha256)


async def freeze_post_read_minute_batch_v3(
    sources: tuple[PostReadCaptureInputV3, ...],
    *,
    observation_repository: PublicReceiptPostReadRepository,
    plan: MinuteAggregationPlan,
    expected_plan_sha256: str,
) -> FrozenGate3Artifact[PostReadMinuteBatchV3]:
    """Verify every exact prior artifact and freeze computational output."""
    rebuilt = await _rebuild(
        sources,
        observation_repository=observation_repository,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    frozen = _freeze(rebuilt, contract_type=PostReadMinuteBatchV3)
    if len(frozen.payload) > MAX_BATCH_ARTIFACT_BYTES:
        raise PostReadBatchError("post_read_batch_artifact_oversized")
    return frozen


async def verify_post_read_minute_batch_v3(
    payload: bytes,
    *,
    expected_sha256: str,
    sources: tuple[PostReadCaptureInputV3, ...],
    observation_repository: PublicReceiptPostReadRepository,
    plan: MinuteAggregationPlan,
    expected_plan_sha256: str,
) -> PostReadMinuteBatchV3:
    """Read original bytes and DB0035 again before accepting batch bytes."""
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_BATCH_ARTIFACT_BYTES:
        raise PostReadBatchError("post_read_batch_artifact_size_invalid")
    recorded = _verify(
        payload, expected_sha256=expected_sha256, contract_type=PostReadMinuteBatchV3
    )
    rebuilt = await _rebuild(
        sources,
        observation_repository=observation_repository,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    if recorded.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise ArtifactVerificationError("post-read batch differs from source replay")
    return rebuilt


async def computational_rows_at_cutoff_v3(
    payload: bytes,
    *,
    expected_sha256: str,
    sources: tuple[PostReadCaptureInputV3, ...],
    observation_repository: PublicReceiptPostReadRepository,
    plan: MinuteAggregationPlan,
    expected_plan_sha256: str,
    horizon_seconds: Literal[900, 3600, 14400],
    as_of: datetime,
) -> tuple[PointInTimeBar, ...]:
    """Fail if any due aggregate is unavailable; never silently omit it."""
    cutoff = require_utc(as_of, "as_of")
    batch = await verify_post_read_minute_batch_v3(
        payload,
        expected_sha256=expected_sha256,
        sources=sources,
        observation_repository=observation_repository,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    frame = next(
        (item for item in batch.timeframes if item.horizon.seconds == horizon_seconds),
        None,
    )
    if frame is None:
        raise PostReadBatchError("post_read_horizon_invalid")
    due = tuple(item.row for item in frame.bars if item.row.bar.closed_at <= cutoff)
    if not due or any(item.available_at > cutoff for item in due):
        raise PostReadBatchError("post_read_due_bar_unavailable")
    return due


__all__ = (
    "PostReadAggregateBarV3",
    "PostReadBatchError",
    "PostReadCaptureInputV3",
    "PostReadMinuteBatchV3",
    "PostReadMinuteV3",
    "PostReadTimeframeV3",
    "capture_chain_sha256_v3",
    "computational_rows_at_cutoff_v3",
    "freeze_post_read_minute_batch_v3",
    "verify_post_read_minute_batch_v3",
)
