"""Versioned durable readback of a post-publication database observation.

V1 and V2 bytes are unchanged. V3 makes the later server-clock sample durable
and independently readable, but retains computational-only authority. The
database clock and its custody, first evaluator access, and predictive use
remain unverified.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadReadback,
    PublicReceiptPostReadRepository,
)
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import (
    Gate3Claim,
    Gate3Contract,
    Sha256,
    require_utc,
)
from app.mie.validation.post_publication_availability_v2 import (
    PostPublicationCaptureV2,
    freeze_post_publication_capture_v2,
    verify_post_publication_capture_v2,
)
from app.mie.validation.replay import PointInTimeBar


class PostReadObservationV3(Gate3Contract):
    journal_key: Sha256
    witness_revision: int = Field(ge=3, le=8192)
    witness_record_sha256: Sha256
    checkpoint_sha256: Sha256
    capture_sequence: int = Field(ge=1, le=1024)
    capture_head_sha256: Sha256
    capture_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_sha256: Sha256
    receipt_sha256: Sha256
    source_rows_sha256: Sha256
    v2_capture_sha256: Sha256
    witness_recorded_at: datetime
    observed_at: datetime

    @field_validator("witness_recorded_at", "observed_at")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def causal_order(self) -> PostReadObservationV3:
        if self.witness_recorded_at > self.observed_at:
            raise ValueError("post-read witness time order invalid")
        return self


class PostPublicationCaptureV3(Gate3Contract):
    schema_version: Literal["ctcc.mie.gate3.post_publication_capture.v3"] = (
        "ctcc.mie.gate3.post_publication_capture.v3"
    )
    v2_capture: PostPublicationCaptureV2
    v2_capture_sha256: Sha256
    post_read_observation: PostReadObservationV3
    available_at: datetime
    availability_basis: Literal[
        "persisted_restricted_post_read_server_clock_unverified"
    ] = "persisted_restricted_post_read_server_clock_unverified"
    persisted_observation_independently_readable: Literal[True] = True
    trusted_clock_verified: Literal[False] = False
    independently_protected: Literal[False] = False
    evaluator_first_read_proven: Literal[False] = False
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @field_validator("available_at")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def source_and_time(self) -> PostPublicationCaptureV3:
        source = self.v2_capture
        observed = self.post_read_observation
        if (
            source.canonical_sha256() != self.v2_capture_sha256
            or observed.v2_capture_sha256 != self.v2_capture_sha256
            or source.journal_genesis_sha256 != observed.journal_key
            or source.witness_revision != observed.witness_revision
            or source.witness_record_sha256 != observed.witness_record_sha256
            or source.capture_checkpoint_sha256 != observed.checkpoint_sha256
            or source.capture_sequence != observed.capture_sequence
            or source.capture_id != observed.capture_id
            or source.plan_sha256 != observed.plan_sha256
            or source.receipt_sha256 != observed.receipt_sha256
            or source.source_rows_sha256 != observed.source_rows_sha256
            or source.available_at > observed.observed_at
            or self.available_at != observed.observed_at
        ):
            raise ValueError("post-read source or time pins differ")
        return self


def _pins(source: PostPublicationCaptureV2, source_sha256: str) -> dict:
    return {
        "journal_key": source.journal_genesis_sha256,
        "witness_revision": source.witness_revision,
        "witness_record_sha256": source.witness_record_sha256,
        "checkpoint_sha256": source.capture_checkpoint_sha256,
        "capture_id": source.capture_id,
        "plan_sha256": source.plan_sha256,
        "receipt_sha256": source.receipt_sha256,
        "source_rows_sha256": source.source_rows_sha256,
        "v2_capture_sha256": source_sha256,
        "not_before": source.available_at,
    }


def _observation(
    readback: PublicReceiptPostReadReadback,
) -> PostReadObservationV3:
    if (
        type(readback) is not PublicReceiptPostReadReadback
        or readback.persisted_observation_replayable is not True
        or readback.trusted_clock_verified is not False
        or readback.independently_protected is not False
        or readback.evaluator_first_read_proven is not False
        or readback.predictive_oos_eligible is not False
        or readback.execution_authority is not False
    ):
        raise ArtifactVerificationError("post-read observation type or flags invalid")
    return PostReadObservationV3(
        journal_key=readback.journal_key,
        witness_revision=readback.witness_revision,
        witness_record_sha256=readback.witness_record_sha256,
        checkpoint_sha256=readback.checkpoint_sha256,
        capture_sequence=readback.capture_sequence,
        capture_head_sha256=readback.capture_head_sha256,
        capture_id=readback.capture_id,
        plan_sha256=readback.plan_sha256,
        receipt_sha256=readback.receipt_sha256,
        source_rows_sha256=readback.source_rows_sha256,
        v2_capture_sha256=readback.v2_capture_sha256,
        witness_recorded_at=readback.witness_recorded_at,
        observed_at=readback.observed_at,
    )


async def freeze_post_publication_capture_v3(
    *,
    observation_repository: PublicReceiptPostReadRepository,
    **source_kwargs,
) -> FrozenGate3Artifact[PostPublicationCaptureV3]:
    """Persist one new observation and read it back; never retry uncertain writes."""
    if type(observation_repository) is not PublicReceiptPostReadRepository:
        raise ValueError("restricted_observation_repository_required")
    source = await freeze_post_publication_capture_v2(**source_kwargs)
    observed = await observation_repository.append_new(
        **_pins(source.contract, source.sha256)
    )
    return _freeze(
        PostPublicationCaptureV3(
            v2_capture=source.contract,
            v2_capture_sha256=source.sha256,
            post_read_observation=_observation(observed),
            available_at=observed.observed_at,
        ),
        contract_type=PostPublicationCaptureV3,
    )


async def verify_post_publication_capture_v3(
    payload: bytes,
    *,
    expected_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
    **source_kwargs,
) -> PostPublicationCaptureV3:
    """Replay raw source, full witness chain, and the exact persisted sample."""
    if type(observation_repository) is not PublicReceiptPostReadRepository:
        raise ArtifactVerificationError("restricted observation repository required")
    recorded = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=PostPublicationCaptureV3,
    )
    await verify_post_publication_capture_v2(
        recorded.v2_capture.canonical_json_bytes(),
        expected_sha256=recorded.v2_capture_sha256,
        **source_kwargs,
    )
    observed = await observation_repository.read(
        **_pins(recorded.v2_capture, recorded.v2_capture_sha256)
    )
    if _observation(observed) != recorded.post_read_observation:
        raise ArtifactVerificationError("persisted post-read observation differs")
    return recorded


async def computational_point_in_time_rows_v3(
    payload: bytes,
    *,
    expected_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
    **source_kwargs,
) -> tuple[PointInTimeBar, ...]:
    """Offline replay rows only after exact V3 source and DB readback.

    The later persisted post-read time applies to every row. These values do
    not qualify historical availability or grant predictive/execution use.
    """
    verified = await verify_post_publication_capture_v3(
        payload,
        expected_sha256=expected_sha256,
        observation_repository=observation_repository,
        **source_kwargs,
    )
    return tuple(
        PointInTimeBar(
            source_row_id="okx-public-minute:" + row.source_row_identity,
            source_row_sha256=row.source_row_sha256,
            instrument_id=verified.v2_capture.instrument_id,
            available_at=verified.available_at,
            bar=row.bar,
        )
        for row in verified.v2_capture.rows
    )


__all__ = (
    "PostPublicationCaptureV3",
    "PostReadObservationV3",
    "computational_point_in_time_rows_v3",
    "freeze_post_publication_capture_v3",
    "verify_post_publication_capture_v3",
)
