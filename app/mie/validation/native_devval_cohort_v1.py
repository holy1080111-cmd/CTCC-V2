"""DENY-only two-instrument Development/Validation post-read source audit.

This bounded pilot replays four original V3 minute batches. It does not
authenticate the database clock, protect caller pins, seal a candidate, or
establish first evaluator access. No result from this module is an admission
token for predictive research or execution.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
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
from app.mie.validation.post_read_stitch_v1 import PostReadBatchInputV1
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_COHORT_INPUT_BYTES = 128 * 1024 * 1024
MAX_COHORT_ARTIFACT_BYTES = 32 * 1024
_PERMANENT_REASONS = (
    "caller_pin_custody_unverified",
    "candidate_unbound",
    "first_evaluator_read_unproven",
    "single_window_rehearsal_only",
    "trusted_clock_unverified",
)
_Reason = Literal[
    "caller_pin_custody_unverified",
    "candidate_unbound",
    "first_evaluator_read_unproven",
    "post_read_late_for_cutoff",
    "single_window_rehearsal_only",
    "trusted_clock_unverified",
]


class NativeDevValCohortError(ValueError):
    """The retained plan, original source, or cohort identity failed closed."""


class NativeDevValCohortPlanV1(Gate3Contract):
    """One fixed four-hour pilot window per partition, never a research seal."""

    schema_version: Literal["ctcc.mie.gate3.native_devval_cohort_plan.v1"] = (
        "ctcc.mie.gate3.native_devval_cohort_plan.v1"
    )
    instrument_ids: tuple[str, str]
    development: PartitionWindow
    validation: PartitionWindow
    development_cutoff_at: datetime
    validation_cutoff_at: datetime
    source: Literal["okx.public.minute.post_read.v3"] = "okx.public.minute.post_read.v3"
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("development_cutoff_at", "validation_cutoff_at")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def exact_pilot_grid(self) -> NativeDevValCohortPlanV1:
        if (
            len(set(self.instrument_ids)) != 2
            or self.instrument_ids != tuple(sorted(self.instrument_ids))
            or any(
                re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)+", item) is None
                for item in self.instrument_ids
            )
            or self.development.partition != DatasetPartition.DEVELOPMENT
            or self.validation.partition != DatasetPartition.VALIDATION
            or self.development.end_at > self.validation.start_at
        ):
            raise ValueError("native cohort instruments or partition order differ")
        for window, cutoff in (
            (self.development, self.development_cutoff_at),
            (self.validation, self.validation_cutoff_at),
        ):
            if (
                window.end_at - window.start_at != timedelta(hours=4)
                or any(
                    value.hour % 4 or value.minute or value.second or value.microsecond
                    for value in (window.start_at, window.end_at)
                )
                or not window.end_at <= cutoff <= window.end_at + timedelta(minutes=1)
            ):
                raise ValueError("native cohort needs an exact 4H window and cutoff")
        return self


class NativeDevValGroupV1(Gate3Contract):
    partition: Literal[DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
    instrument_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
    window: PartitionWindow
    cutoff_at: datetime
    batch_sha256: Sha256
    batch_plan_sha256: Sha256
    source_minutes_sha256: Sha256
    first_row_sha256: Sha256
    last_row_sha256: Sha256
    minute_count: Literal[240] = 240
    late_rows: int = Field(ge=0, le=240)
    latest_persisted_observed_at: datetime

    @field_validator("cutoff_at", "latest_persisted_observed_at")
    @classmethod
    def utc(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)


class NativeDevValCohortAdmissionV1(Gate3Contract):
    """Immutable source-replayed diagnostic, with no positive status value."""

    schema_version: Literal["ctcc.mie.gate3.native_devval_cohort_admission.v1"] = (
        "ctcc.mie.gate3.native_devval_cohort_admission.v1"
    )
    plan: NativeDevValCohortPlanV1
    plan_sha256: Sha256
    groups: tuple[
        NativeDevValGroupV1,
        NativeDevValGroupV1,
        NativeDevValGroupV1,
        NativeDevValGroupV1,
    ]
    ordered_source_sha256: Sha256
    total_rows: Literal[960] = 960
    late_rows: int = Field(ge=0, le=960)
    reasons: tuple[_Reason, ...] = Field(min_length=5)
    pin_custody: Literal["caller_supplied_unverified"] = "caller_supplied_unverified"
    availability_basis: Literal[
        "persisted_restricted_post_read_server_clock_unverified"
    ] = "persisted_restricted_post_read_server_clock_unverified"
    status: Literal["DENY"] = "DENY"
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    point_in_time_provenance_qualified: Literal[False] = False
    candidate_seal_eligible: Literal[False] = False
    formal_evaluation_eligible: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def denial_matches_source_groups(self) -> NativeDevValCohortAdmissionV1:
        expected = tuple(
            (partition, instrument, window, cutoff)
            for partition, window, cutoff in (
                (
                    DatasetPartition.DEVELOPMENT,
                    self.plan.development,
                    self.plan.development_cutoff_at,
                ),
                (
                    DatasetPartition.VALIDATION,
                    self.plan.validation,
                    self.plan.validation_cutoff_at,
                ),
            )
            for instrument in self.plan.instrument_ids
        )
        if (
            self.plan_sha256 != self.plan.canonical_sha256()
            or tuple(
                (item.partition, item.instrument_id, item.window, item.cutoff_at)
                for item in self.groups
            )
            != expected
            or self.late_rows != sum(item.late_rows for item in self.groups)
            or self.ordered_source_sha256 != _ordered_source_sha256(self.groups)
            or any(
                (item.late_rows > 0)
                != (item.latest_persisted_observed_at > item.cutoff_at)
                for item in self.groups
            )
            or self.reasons != tuple(sorted(set(self.reasons)))
            or not set(_PERMANENT_REASONS).issubset(self.reasons)
            or ("post_read_late_for_cutoff" in self.reasons) != (self.late_rows > 0)
        ):
            raise ValueError("native cohort denial or source grid differs")
        return self


def _ordered_source_sha256(groups: tuple[NativeDevValGroupV1, ...]) -> str:
    digest = hashlib.sha256()
    for group in groups:
        payload = group.canonical_json_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


async def _rebuild(
    batches: tuple[PostReadBatchInputV1, ...],
    *,
    plan: NativeDevValCohortPlanV1,
    expected_plan_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
) -> NativeDevValCohortAdmissionV1:
    if (
        type(plan) is not NativeDevValCohortPlanV1
        or type(expected_plan_sha256) is not str
        or _SHA256.fullmatch(expected_plan_sha256) is None
        or plan.canonical_sha256() != expected_plan_sha256
        or type(observation_repository) is not PublicReceiptPostReadRepository
        or type(batches) is not tuple
        or len(batches) != 4
        or any(type(item) is not PostReadBatchInputV1 for item in batches)
    ):
        raise NativeDevValCohortError("native_cohort_exact_inputs_required")
    checked_plan = NativeDevValCohortPlanV1.model_validate(
        plan.model_dump(mode="python")
    )
    expected = tuple(
        (window, instrument)
        for window in (checked_plan.development, checked_plan.validation)
        for instrument in checked_plan.instrument_ids
    )
    total_bytes = 0
    batch_ids: set[str] = set()
    batch_plan_ids: set[str] = set()
    journals: dict[ControlledPublicReceiptJournal, None] = {}
    for item, (window, instrument) in zip(batches, expected, strict=True):
        if (
            type(item.payload) is not bytes
            or not 1 <= len(item.payload) <= MAX_BATCH_ARTIFACT_BYTES
            or type(item.expected_sha256) is not str
            or _SHA256.fullmatch(item.expected_sha256) is None
            or type(item.plan) is not MinuteAggregationPlan
            or item.plan.window != window
            or item.plan.instrument_id != instrument
            or item.plan.volume_unit != "contracts"
            or item.plan.expected_minute_rows != 240
            or type(item.expected_plan_sha256) is not str
            or _SHA256.fullmatch(item.expected_plan_sha256) is None
            or item.plan.canonical_sha256() != item.expected_plan_sha256
            or type(item.sources) is not tuple
            or len(item.sources) != 240
            or item.expected_sha256 in batch_ids
            or item.expected_plan_sha256 in batch_plan_ids
        ):
            raise NativeDevValCohortError("native_cohort_source_grid_invalid")
        batch_ids.add(item.expected_sha256)
        batch_plan_ids.add(item.expected_plan_sha256)
        total_bytes += len(item.payload)
        if total_bytes > MAX_COHORT_INPUT_BYTES:
            raise NativeDevValCohortError("native_cohort_input_budget_exceeded")
        for source in item.sources:
            if (
                type(source) is not PostReadCaptureInputV3
                or type(source.payload) is not bytes
                or not 1 <= len(source.payload) <= MAX_CAPTURE_ARTIFACT_BYTES
                or type(source.journal) is not ControlledPublicReceiptJournal
            ):
                raise NativeDevValCohortError("native_cohort_capture_input_invalid")
            total_bytes += len(source.payload)
            if total_bytes > MAX_COHORT_INPUT_BYTES:
                raise NativeDevValCohortError("native_cohort_input_budget_exceeded")
            journals[source.journal] = None

    # No journal or database observation is opened until the entire bounded
    # coordinate and byte inventory has passed preflight.
    journal_pins = {
        journal: journal.checkpoint.canonical_sha256() for journal in journals
    }

    groups = []
    capture_ids: set[str] = set()
    row_ids: set[str] = set()
    witness_keys: set[tuple[str, int]] = set()
    for item, (window, instrument) in zip(batches, expected, strict=True):
        verified = await verify_post_read_minute_batch_v3(
            item.payload,
            expected_sha256=item.expected_sha256,
            sources=item.sources,
            observation_repository=observation_repository,
            plan=item.plan,
            expected_plan_sha256=item.expected_plan_sha256,
        )
        if (
            type(verified) is not PostReadMinuteBatchV3
            or verified.plan.window != window
            or verified.plan.instrument_id != instrument
            or verified.plan_sha256 != item.expected_plan_sha256
            or len(verified.minutes) != 240
        ):
            raise NativeDevValCohortError("native_cohort_replayed_source_differs")
        for minute in verified.minutes:
            witness_key = (minute.journal_genesis_sha256, minute.witness_revision)
            if (
                minute.capture_id in capture_ids
                or minute.source_row_identity in row_ids
                or witness_key in witness_keys
            ):
                raise NativeDevValCohortError("native_cohort_global_identity_reused")
            capture_ids.add(minute.capture_id)
            row_ids.add(minute.source_row_identity)
            witness_keys.add(witness_key)
        cutoff = (
            checked_plan.development_cutoff_at
            if window.partition == DatasetPartition.DEVELOPMENT
            else checked_plan.validation_cutoff_at
        )
        groups.append(
            NativeDevValGroupV1(
                partition=window.partition,
                instrument_id=instrument,
                window=window,
                cutoff_at=cutoff,
                batch_sha256=item.expected_sha256,
                batch_plan_sha256=item.expected_plan_sha256,
                source_minutes_sha256=verified.source_minutes_sha256,
                first_row_sha256=verified.minutes[0].row.source_row_sha256,
                last_row_sha256=verified.minutes[-1].row.source_row_sha256,
                late_rows=sum(
                    minute.persisted_observed_at > cutoff for minute in verified.minutes
                ),
                latest_persisted_observed_at=max(
                    minute.persisted_observed_at for minute in verified.minutes
                ),
            )
        )

    # A changed journal anywhere in the cohort invalidates this local replay.
    # This is still not an atomic snapshot of independent database custody.
    for journal, pinned in journal_pins.items():
        if journal.checkpoint.canonical_sha256() != pinned:
            raise NativeDevValCohortError("native_cohort_journal_changed")
        try:
            journal.read_all()
        except (OSError, ValueError) as exc:
            raise NativeDevValCohortError(
                "native_cohort_journal_readback_failed"
            ) from exc
        if journal.checkpoint.canonical_sha256() != pinned:
            raise NativeDevValCohortError("native_cohort_journal_changed")

    group_tuple = tuple(groups)
    late_rows = sum(group.late_rows for group in group_tuple)
    reasons: set[_Reason] = set(_PERMANENT_REASONS)
    if late_rows:
        reasons.add("post_read_late_for_cutoff")
    return NativeDevValCohortAdmissionV1(
        plan=checked_plan,
        plan_sha256=expected_plan_sha256,
        groups=group_tuple,
        ordered_source_sha256=_ordered_source_sha256(group_tuple),
        late_rows=late_rows,
        reasons=tuple(sorted(reasons)),
    )


async def freeze_native_devval_cohort_v1(
    batches: tuple[PostReadBatchInputV1, ...],
    *,
    plan: NativeDevValCohortPlanV1,
    expected_plan_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
) -> FrozenGate3Artifact[NativeDevValCohortAdmissionV1]:
    rebuilt = await _rebuild(
        batches,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
        observation_repository=observation_repository,
    )
    frozen = _freeze(rebuilt, contract_type=NativeDevValCohortAdmissionV1)
    if len(frozen.payload) > MAX_COHORT_ARTIFACT_BYTES:
        raise NativeDevValCohortError("native_cohort_artifact_oversized")
    return frozen


async def verify_native_devval_cohort_v1(
    payload: bytes,
    *,
    expected_sha256: str,
    batches: tuple[PostReadBatchInputV1, ...],
    plan: NativeDevValCohortPlanV1,
    expected_plan_sha256: str,
    observation_repository: PublicReceiptPostReadRepository,
) -> NativeDevValCohortAdmissionV1:
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_COHORT_ARTIFACT_BYTES:
        raise NativeDevValCohortError("native_cohort_artifact_size_invalid")
    recorded = _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=NativeDevValCohortAdmissionV1,
    )
    rebuilt = await _rebuild(
        batches,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
        observation_repository=observation_repository,
    )
    if recorded.canonical_json_bytes() != rebuilt.canonical_json_bytes():
        raise ArtifactVerificationError("native cohort differs from source replay")
    return rebuilt


__all__ = (
    "NativeDevValCohortAdmissionV1",
    "NativeDevValCohortError",
    "NativeDevValCohortPlanV1",
    "NativeDevValGroupV1",
    "freeze_native_devval_cohort_v1",
    "verify_native_devval_cohort_v1",
)
