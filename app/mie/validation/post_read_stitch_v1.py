"""Computational-only feature replay across source-reverified DB0035 batches.

The caller pins the ordered identities outside the batches. Every invocation
replays each original public journal, committed witness, and DB0035 observation
through the V3 verifier. Neither those caller pins nor this replay prove trusted
clock custody, evaluator first access, predictive value, or trading authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.mie.contracts import ForecastHorizon
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.batch_replay import MinuteAggregationPlan
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Claim,
    Gate3Contract,
    PartitionWindow,
    Sha256,
    require_utc,
)
from app.mie.validation.post_read_batch_v3 import (
    MAX_BATCH_ARTIFACT_BYTES,
    MAX_CAPTURE_ARTIFACT_BYTES,
    PostReadCaptureInputV3,
    PostReadMinuteBatchV3,
    verify_post_read_minute_batch_v3,
)
from app.mie.validation.replay import (
    PointInTimeBar,
    PointInTimeReplaySnapshot,
    replay_features_at,
)
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

MAX_STITCH_BATCHES = 32
MAX_STITCH_ARTIFACT_BYTES = 256 * 1024
MAX_STITCH_INPUT_BYTES = 128 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HORIZONS = {900: "15m", 3600: "1H", 14400: "4H"}


class PostReadStitchError(ValueError):
    """A source identity, sequence, availability, or replay failed closed."""


def _feature_context() -> Context:
    """Freeze all Decimal settings instead of inheriting mutable defaults."""
    return Context(
        prec=28,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


@dataclass(frozen=True, slots=True)
class PostReadBatchInputV1:
    payload: bytes
    expected_sha256: str
    sources: tuple[PostReadCaptureInputV3, ...]
    plan: MinuteAggregationPlan
    expected_plan_sha256: str


class PostReadBatchIdentityV1(Gate3Contract):
    batch_sha256: Sha256
    plan_sha256: Sha256
    source_manifest_sha256: Sha256
    source_minutes_sha256: Sha256
    window: PartitionWindow
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    minute_count: int = Field(ge=240, le=1024)
    volume_unit: Literal["contracts"] = "contracts"


def ordered_batch_chain_sha256_v1(
    identities: tuple[PostReadBatchIdentityV1, ...],
) -> str:
    """Hash exact ordered, externally retained batch coordinates and digests."""
    if (
        type(identities) is not tuple
        or not 2 <= len(identities) <= MAX_STITCH_BATCHES
        or any(type(item) is not PostReadBatchIdentityV1 for item in identities)
    ):
        raise PostReadStitchError("post_read_stitch_identity_chain_invalid")
    digest = hashlib.sha256()
    for item in identities:
        payload = PostReadBatchIdentityV1.model_validate(
            item.model_dump(mode="python")
        ).canonical_json_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _rows_sha256(rows: tuple[PointInTimeBar, ...]) -> str:
    digest = hashlib.sha256()
    for item in rows:
        payload = json.dumps(
            item.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _identity(
    batch: PostReadMinuteBatchV3, *, batch_sha256: str
) -> PostReadBatchIdentityV1:
    return PostReadBatchIdentityV1(
        batch_sha256=batch_sha256,
        plan_sha256=batch.plan_sha256,
        source_manifest_sha256=batch.plan.source_manifest_sha256,
        source_minutes_sha256=batch.source_minutes_sha256,
        window=batch.plan.window,
        instrument_id=batch.plan.instrument_id,
        minute_count=len(batch.minutes),
    )


def _check_sequence(identities: tuple[PostReadBatchIdentityV1, ...]) -> None:
    first = identities[0]
    if first.window.partition not in (
        DatasetPartition.DEVELOPMENT,
        DatasetPartition.VALIDATION,
    ):
        raise PostReadStitchError("post_read_stitch_partition_invalid")
    seen_batches = set()
    seen_plans = set()
    for index, item in enumerate(identities):
        if (
            item.instrument_id != first.instrument_id
            or item.window.partition != first.window.partition
            or item.window.end_at - item.window.start_at
            != timedelta(minutes=item.minute_count)
            or any(
                value.minute or value.second or value.microsecond or value.hour % 4
                for value in (item.window.start_at, item.window.end_at)
            )
            or item.batch_sha256 in seen_batches
            or item.plan_sha256 in seen_plans
            or (index and item.window.start_at != identities[index - 1].window.end_at)
        ):
            raise PostReadStitchError("post_read_stitch_sequence_invalid")
        seen_batches.add(item.batch_sha256)
        seen_plans.add(item.plan_sha256)


class PostReadStitchedReplayV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.post_read_stitched_replay.v1"] = (
        "ctcc.mie.gate3.post_read_stitched_replay.v1"
    )
    batch_identities: tuple[PostReadBatchIdentityV1, ...] = Field(
        min_length=2, max_length=MAX_STITCH_BATCHES
    )
    batch_chain_sha256: Sha256
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    partition: Literal[DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
    volume_unit: Literal["contracts"] = "contracts"
    horizon: ForecastHorizon
    history_bars: int = Field(ge=21, le=10_000)
    as_of: datetime
    due_bar_count: int = Field(ge=21, le=10_000)
    due_rows_sha256: Sha256
    replay: PointInTimeReplaySnapshot
    availability_basis: Literal[
        "persisted_restricted_post_read_server_clock_unverified"
    ] = "persisted_restricted_post_read_server_clock_unverified"
    trusted_clock_verified: Literal[False] = False
    independently_protected: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: datetime) -> datetime:
        return require_utc(value, "as_of")

    @model_validator(mode="after")
    def validate_links(self) -> PostReadStitchedReplayV1:
        identities = self.batch_identities
        _check_sequence(identities)
        if (
            self.batch_chain_sha256 != ordered_batch_chain_sha256_v1(identities)
            or self.instrument_id != identities[0].instrument_id
            or self.partition != identities[0].window.partition
            or self.horizon.seconds not in _HORIZONS
            or self.horizon.label != _HORIZONS[self.horizon.seconds]
            or self.replay.instrument_id != self.instrument_id
            or self.replay.as_of != self.as_of
            or self.replay.feature_snapshot.horizon != self.horizon
            or self.replay.data_cutoff > self.as_of
            or self.replay.source_row_count > self.due_bar_count
            or self.replay.source_row_count > self.history_bars
        ):
            raise ValueError("post-read stitched replay links differ")
        return self


async def _rebuild(
    batches: tuple[PostReadBatchInputV1, ...],
    *,
    expected_identities: tuple[PostReadBatchIdentityV1, ...],
    expected_chain_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
    horizon_seconds: Literal[900, 3600, 14400],
    as_of: datetime,
    history_bars: int,
) -> PostReadStitchedReplayV1:
    cutoff = require_utc(as_of, "as_of")
    if (
        type(batches) is not tuple
        or not 2 <= len(batches) <= MAX_STITCH_BATCHES
        or type(expected_identities) is not tuple
        or len(expected_identities) != len(batches)
        or type(expected_chain_sha256) is not str
        or _SHA256.fullmatch(expected_chain_sha256) is None
        or type(observation_repository) is not PublicReceiptPostReadRepository
        or type(horizon_seconds) is not int
        or horizon_seconds not in _HORIZONS
        or type(history_bars) is not int
        or not 21 <= history_bars <= 10_000
        or any(type(item) is not PostReadBatchInputV1 for item in batches)
    ):
        raise PostReadStitchError("post_read_stitch_inputs_invalid")
    checked_identities = []
    for item in expected_identities:
        if type(item) is not PostReadBatchIdentityV1:
            raise PostReadStitchError("post_read_stitch_identity_invalid")
        checked_identities.append(
            PostReadBatchIdentityV1.model_validate(item.model_dump(mode="python"))
        )
    identities = tuple(checked_identities)
    if ordered_batch_chain_sha256_v1(identities) != expected_chain_sha256:
        raise PostReadStitchError("post_read_stitch_chain_pin_mismatch")
    _check_sequence(identities)
    if any(
        type(item.payload) is not bytes
        or type(item.expected_sha256) is not str
        or _SHA256.fullmatch(item.expected_sha256) is None
        or type(item.sources) is not tuple
        or type(item.plan) is not MinuteAggregationPlan
        or type(item.expected_plan_sha256) is not str
        or _SHA256.fullmatch(item.expected_plan_sha256) is None
        or item.expected_sha256 != identity.batch_sha256
        or item.expected_plan_sha256 != identity.plan_sha256
        for item, identity in zip(batches, identities, strict=True)
    ):
        raise PostReadStitchError("post_read_stitch_source_pin_mismatch")

    # Reject oversized or malformed input before opening any journal or DB.
    total_bytes = 0
    for item, identity in zip(batches, identities, strict=True):
        if (
            not 1 <= len(item.payload) <= MAX_BATCH_ARTIFACT_BYTES
            or len(item.sources) != identity.minute_count
            or len(item.sources) != item.plan.expected_minute_rows
        ):
            raise PostReadStitchError("post_read_stitch_input_budget_invalid")
        total_bytes += len(item.payload)
        if total_bytes > MAX_STITCH_INPUT_BYTES:
            raise PostReadStitchError("post_read_stitch_input_budget_exceeded")
        for source in item.sources:
            if (
                type(source) is not PostReadCaptureInputV3
                or type(source.payload) is not bytes
                or not 1 <= len(source.payload) <= MAX_CAPTURE_ARTIFACT_BYTES
                or type(source.journal) is not ControlledPublicReceiptJournal
            ):
                raise PostReadStitchError("post_read_stitch_capture_input_invalid")
            total_bytes += len(source.payload)
            if total_bytes > MAX_STITCH_INPUT_BYTES:
                raise PostReadStitchError("post_read_stitch_input_budget_exceeded")

    # Pin every native inventory before the first await; V3 verifies each
    # source locally, then this final pass checks all journals as a set.
    journal_pins: dict[ControlledPublicReceiptJournal, str] = {}
    for item in batches:
        for source in item.sources:
            if source.journal not in journal_pins:
                journal_pins[source.journal] = (
                    source.journal.checkpoint.canonical_sha256()
                )

    verified_batches = []
    for item, identity in zip(batches, identities, strict=True):
        verified = await verify_post_read_minute_batch_v3(
            item.payload,
            expected_sha256=item.expected_sha256,
            sources=item.sources,
            observation_repository=observation_repository,
            plan=item.plan,
            expected_plan_sha256=item.expected_plan_sha256,
        )
        if _identity(verified, batch_sha256=item.expected_sha256) != identity:
            raise PostReadStitchError("post_read_stitch_identity_mismatch")
        verified_batches.append(verified)

    for journal, expected in journal_pins.items():
        if journal.checkpoint.canonical_sha256() != expected:
            raise PostReadStitchError("post_read_stitch_journal_changed")
        try:
            journal.read_all()
        except (OSError, ValueError) as exc:
            raise PostReadStitchError("post_read_stitch_journal_replay_failed") from exc
        if journal.checkpoint.canonical_sha256() != expected:
            raise PostReadStitchError("post_read_stitch_journal_changed")

    capture_ids = set()
    source_ids = set()
    witness_keys = set()
    rows = []
    for batch in verified_batches:
        for minute in batch.minutes:
            witness_key = (
                minute.journal_genesis_sha256,
                minute.witness_revision,
            )
            if (
                minute.capture_id in capture_ids
                or minute.source_row_identity in source_ids
                or witness_key in witness_keys
            ):
                raise PostReadStitchError("post_read_stitch_global_identity_reused")
            capture_ids.add(minute.capture_id)
            source_ids.add(minute.source_row_identity)
            witness_keys.add(witness_key)
        frame = next(
            frame
            for frame in batch.timeframes
            if frame.horizon.seconds == horizon_seconds
        )
        rows.extend(bar.row for bar in frame.bars)
    horizon = ForecastHorizon(label=_HORIZONS[horizon_seconds], seconds=horizon_seconds)
    due = tuple(row for row in rows if row.bar.closed_at <= cutoff)
    if not due or any(row.available_at > cutoff for row in due):
        raise PostReadStitchError("post_read_stitch_due_constituent_unavailable")
    # The existing feature families do decimal arithmetic outside their own
    # contexts. Pin the standard Gate 2 precision here so a caller's ambient
    # Decimal settings cannot change this artifact's bytes.
    with localcontext(_feature_context()):
        replay = replay_features_at(
            tuple(rows),
            as_of=cutoff,
            bar_horizon=horizon,
            history_bars=history_bars,
        )
    return PostReadStitchedReplayV1(
        batch_identities=identities,
        batch_chain_sha256=expected_chain_sha256,
        instrument_id=identities[0].instrument_id,
        partition=identities[0].window.partition,
        horizon=horizon,
        history_bars=history_bars,
        as_of=cutoff,
        due_bar_count=len(due),
        due_rows_sha256=_rows_sha256(due),
        replay=replay,
    )


async def freeze_post_read_stitched_replay_v1(
    batches: tuple[PostReadBatchInputV1, ...],
    *,
    expected_identities: tuple[PostReadBatchIdentityV1, ...],
    expected_chain_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
    horizon_seconds: Literal[900, 3600, 14400],
    as_of: datetime,
    history_bars: int = 256,
) -> FrozenGate3Artifact[PostReadStitchedReplayV1]:
    rebuilt = await _rebuild(
        batches,
        expected_identities=expected_identities,
        expected_chain_sha256=expected_chain_sha256,
        observation_repository=observation_repository,
        horizon_seconds=horizon_seconds,
        as_of=as_of,
        history_bars=history_bars,
    )
    frozen = _freeze(rebuilt, contract_type=PostReadStitchedReplayV1)
    if len(frozen.payload) > MAX_STITCH_ARTIFACT_BYTES:
        raise PostReadStitchError("post_read_stitch_artifact_oversized")
    return frozen


async def verify_post_read_stitched_replay_v1(
    payload: bytes,
    *,
    expected_sha256: str,
    batches: tuple[PostReadBatchInputV1, ...],
    expected_identities: tuple[PostReadBatchIdentityV1, ...],
    expected_chain_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
    horizon_seconds: Literal[900, 3600, 14400],
    as_of: datetime,
    history_bars: int = 256,
) -> PostReadStitchedReplayV1:
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_STITCH_ARTIFACT_BYTES:
        raise PostReadStitchError("post_read_stitch_artifact_size_invalid")
    recorded = _verify(
        payload, expected_sha256=expected_sha256, contract_type=PostReadStitchedReplayV1
    )
    rebuilt = await _rebuild(
        batches,
        expected_identities=expected_identities,
        expected_chain_sha256=expected_chain_sha256,
        observation_repository=observation_repository,
        horizon_seconds=horizon_seconds,
        as_of=as_of,
        history_bars=history_bars,
    )
    if recorded.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise ArtifactVerificationError("post-read stitch differs from source replay")
    return rebuilt


__all__ = (
    "PostReadBatchIdentityV1",
    "PostReadBatchInputV1",
    "PostReadStitchError",
    "PostReadStitchedReplayV1",
    "freeze_post_read_stitched_replay_v1",
    "ordered_batch_chain_sha256_v1",
    "verify_post_read_stitched_replay_v1",
)
