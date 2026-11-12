"""Materialize an already-read fitted holdout from exact original public bytes."""

from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from app.mie.features import FeatureBar
from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.blind_window_dataset import (
    MAX_DATASET_ROWS,
    BlindWindowDatasetRow,
    BlindWindowJournalSegment,
    CompleteBlindWindowDataset,
    _bind_complete_blind_window_dataset,
)
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
    verify_fitted_holdout_readback_v1,
)
from app.mie.validation.controlled_holdout_reader import (
    MAX_DATASET_BYTES,
    MAX_MANIFEST_BYTES,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import FittedCandidateInputsV1
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
    _ns,
)
from app.mie.validation.replay import PointInTimeBar
from app.public_market_source.public_market_capture import replay_public_capture
from app.public_market_source.public_market_receipts import (
    MAX_RAW,
    MINUTE_NS,
    MeasuredPublicMinuteReceiptV1,
    PublicMinuteCapturePlanV1,
    PublicReceiptError,
    parsed_rows,
    row_content_sha,
    row_identity,
    utc_from_ns,
)

_MAX_COORDINATE_PLAN_BYTES = 64 * 1024
_MAX_BASE64 = ((MAX_RAW + 2) // 3) * 4


class FittedHoldoutMaterializationError(ValueError):
    """Refuse incomplete, changed or unbound fitted holdout source material."""


class FittedHoldoutRawCaptureV1(Gate3Contract):
    receipt: MeasuredPublicMinuteReceiptV1
    time_before_base64: str = Field(min_length=4, max_length=_MAX_BASE64)
    page_base64: str = Field(min_length=4, max_length=_MAX_BASE64)
    time_after_base64: str = Field(min_length=4, max_length=_MAX_BASE64)


class FittedHoldoutRawBundleV1(Gate3Contract):
    """The explicit dataset wire format, one full original capture per minute."""

    schema_version: Literal["ctcc.mie.fitted_holdout_raw_bundle.v1"] = (
        "ctcc.mie.fitted_holdout_raw_bundle.v1"
    )
    manifest_sha256: Sha256
    captures: tuple[FittedHoldoutRawCaptureV1, ...] = Field(
        min_length=1, max_length=MAX_DATASET_ROWS
    )


@dataclass(frozen=True, slots=True)
class FittedHoldoutSourceArtifactsV1:
    manifest: FrozenGate3Artifact[CompleteBlindWindowDataset]
    dataset: FrozenGate3Artifact[FittedHoldoutRawBundleV1]


def _coordinates_for_holdout(
    seal: Gate3ProspectivePreregistration, coordinate_plan_bytes: bytes
) -> ProspectiveCoordinatePlanV1:
    window = seal.prospective_holdout
    if (
        type(coordinate_plan_bytes) is not bytes
        or not 1 <= len(coordinate_plan_bytes) <= _MAX_COORDINATE_PLAN_BYTES
        or window.bar_interval_seconds != 60
        or window.artifact_interval_seconds != 60
        or window.expected_artifact_count != window.expected_rows
    ):
        raise FittedHoldoutMaterializationError(
            "fitted_holdout_coordinate_contract_required"
        )
    coordinates = _verify(
        coordinate_plan_bytes,
        expected_sha256=window.coordinate_plan_sha256,
        contract_type=ProspectiveCoordinatePlanV1,
    )
    if (
        coordinates.holdout_id != window.holdout_id
        or coordinates.source != window.source
        or coordinates.source_version != window.source_version
        or coordinates.instrument_ids != window.instrument_ids
        or coordinates.start_at != window.start_at
        or coordinates.end_at != window.end_at
        or coordinates.expected_rows != window.expected_rows
    ):
        raise FittedHoldoutMaterializationError(
            "fitted_holdout_source_identity_mismatch"
        )
    return coordinates


def prepare_fitted_holdout_source_v1(
    *,
    preregistration: Gate3ProspectivePreregistration,
    expected_preregistration_sha256: str,
    coordinate_plan_bytes: bytes,
    segments: tuple[BlindWindowJournalSegment, ...],
) -> FittedHoldoutSourceArtifactsV1:
    """Prepare the protected source pair from already-retained custodian journals."""
    if type(preregistration) is not Gate3ProspectivePreregistration:
        raise FittedHoldoutMaterializationError("fitted_holdout_seal_required")
    coordinates = _coordinates_for_holdout(preregistration, coordinate_plan_bytes)
    captures: list[FittedHoldoutRawCaptureV1] = []
    capture_bytes = 0

    def retain_original(
        plan: PublicMinuteCapturePlanV1,
        receipt: MeasuredPublicMinuteReceiptV1,
        raw: dict[str, bytes],
    ) -> None:
        nonlocal capture_bytes
        locator = receipt.rows[0]
        if (
            plan.origin != coordinates.origin
            or locator["page_index"] != 0
            or locator["row_ordinal"] != 0
            or raw.keys() != {"time-before.raw", "page-000.raw", "time-after.raw"}
        ):
            raise FittedHoldoutMaterializationError(
                "fitted_holdout_raw_source_mismatch"
            )
        capture = FittedHoldoutRawCaptureV1(
            receipt=receipt,
            time_before_base64=b64encode(raw["time-before.raw"]).decode("ascii"),
            page_base64=b64encode(raw["page-000.raw"]).decode("ascii"),
            time_after_base64=b64encode(raw["time-after.raw"]).decode("ascii"),
        )
        capture_bytes += len(capture.canonical_json_bytes()) + (1 if captures else 0)
        if capture_bytes > MAX_DATASET_BYTES:
            raise FittedHoldoutMaterializationError(
                "fitted_holdout_source_size_exceeded"
            )
        captures.append(capture)

    manifest = _freeze(
        _bind_complete_blind_window_dataset(
            preregistration=preregistration,
            expected_preregistration_sha256=expected_preregistration_sha256,
            segments=segments,
            retain_original=retain_original,
        ),
        contract_type=CompleteBlindWindowDataset,
    )
    if len(manifest.payload) > MAX_MANIFEST_BYTES:
        raise FittedHoldoutMaterializationError("fitted_holdout_source_size_exceeded")
    dataset = _freeze(
        FittedHoldoutRawBundleV1(
            manifest_sha256=manifest.sha256,
            captures=tuple(captures),
        ),
        contract_type=FittedHoldoutRawBundleV1,
    )
    if len(dataset.payload) > MAX_DATASET_BYTES:
        raise FittedHoldoutMaterializationError("fitted_holdout_source_size_exceeded")
    return FittedHoldoutSourceArtifactsV1(manifest, dataset)


class FittedHoldoutMinuteV1(Gate3Contract):
    """A replayed bar and its original manifest timing and source evidence."""

    row: PointInTimeBar
    evidence: BlindWindowDatasetRow

    @model_validator(mode="after")
    def validate_evidence_link(self) -> FittedHoldoutMinuteV1:
        row, evidence = self.row, self.evidence
        if (
            type(row) is not PointInTimeBar
            or type(evidence) is not BlindWindowDatasetRow
            or row.source_row_id != "okx-public-minute:" + evidence.source_row_identity
            or row.source_row_sha256 != evidence.source_row_sha256
            or row.instrument_id != evidence.instrument_id
            or row.bar.closed_at != evidence.closed_at
            or row.available_at != evidence.decision_available_at
        ):
            raise ValueError("fitted_holdout_minute_evidence_mismatch")
        return self


def _minutes_sha256(minutes: tuple[FittedHoldoutMinuteV1, ...]) -> str:
    digest = sha256()
    for minute in minutes:
        payload = minute.canonical_json_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


class FittedHoldoutMinuteTapeV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_holdout_minute_tape.v1"] = (
        "ctcc.mie.fitted_holdout_minute_tape.v1"
    )
    seal_sha256: Sha256
    binding_sha256: Sha256
    receipt_sha256: Sha256
    reservation_sha256: Sha256
    coordinate_plan_sha256: Sha256
    manifest_sha256: Sha256
    dataset_sha256: Sha256
    minutes_sha256: Sha256
    minutes: tuple[FittedHoldoutMinuteV1, ...] = Field(
        min_length=1, max_length=MAX_DATASET_ROWS
    )
    volume_unit: Literal["contracts"] = "contracts"
    row_availability_basis: Literal["pinned_manifest_durable_readback"] = (
        "pinned_manifest_durable_readback"
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    independent_source_custody_verified: Literal[False] = False
    independent_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_minute_inventory(self) -> FittedHoldoutMinuteTapeV1:
        if (
            any(
                minute.evidence.ordinal != index
                for index, minute in enumerate(self.minutes)
            )
            or len({minute.row.source_row_id for minute in self.minutes})
            != len(self.minutes)
            or self.minutes_sha256 != _minutes_sha256(self.minutes)
        ):
            raise ValueError("fitted_holdout_minute_inventory_mismatch")
        return self


def _decode_original(encoded: str) -> bytes:
    try:
        raw = b64decode(encoded, validate=True)
    except ValueError:
        raise FittedHoldoutMaterializationError(
            "fitted_holdout_raw_encoding_invalid"
        ) from None
    if not 0 < len(raw) <= MAX_RAW or b64encode(raw).decode("ascii") != encoded:
        raise FittedHoldoutMaterializationError("fitted_holdout_raw_encoding_invalid")
    return raw


def _replay_minute(
    expected: BlindWindowDatasetRow,
    capture: FittedHoldoutRawCaptureV1,
    *,
    origin: str,
    source_version: str,
    seal_created_ns: int,
) -> FittedHoldoutMinuteV1:
    receipt = capture.receipt
    if (
        receipt.capture_id != expected.capture_id
        or receipt.attempt_sha256 != expected.attempt_sha256
        or receipt.transport_origin != "owned_native_tls"
        or receipt.canonical_sha256() != expected.capture_receipt_sha256
    ):
        raise FittedHoldoutMaterializationError("fitted_holdout_capture_pin_mismatch")
    raw = {
        "time-before.raw": _decode_original(capture.time_before_base64),
        "page-000.raw": _decode_original(capture.page_base64),
        "time-after.raw": _decode_original(capture.time_after_base64),
    }
    try:
        plan, replayed = replay_public_capture(receipt, raw)
        rows, _ = parsed_rows(raw["page-000.raw"])
    except (PublicReceiptError, ValueError):
        raise FittedHoldoutMaterializationError(
            "fitted_holdout_raw_replay_refused"
        ) from None
    if (
        len(rows) != 1
        or len(replayed.rows) != 1
        or plan.expected_rows != 1
        or plan.origin != origin
        or plan.source_parser != source_version
        or plan.instrument_id != expected.instrument_id
        or plan.canonical_sha256() != expected.capture_plan_sha256
        or not seal_created_ns < plan.created_ns < plan.start_ns
    ):
        raise FittedHoldoutMaterializationError("fitted_holdout_capture_plan_mismatch")
    row = rows[0]
    locator = replayed.rows[0]
    opened_ns = int(row[0]) * 1_000_000
    closed_at = utc_from_ns(opened_ns + MINUTE_NS)
    if (
        locator["page_index"] != 0
        or locator["row_ordinal"] != 0
        or locator["raw_sha256"] != expected.raw_page_sha256
        or row_identity(plan, row) != expected.source_row_identity
        or row_content_sha(plan, row) != expected.source_row_sha256
        or utc_from_ns(opened_ns) != expected.opened_at
        or closed_at != expected.closed_at
        or utc_from_ns(replayed.time_before["request_start"]["utc_ns"])
        != expected.request_started_at
        or utc_from_ns(replayed.validation_complete["utc_ns"]) != expected.observed_at
    ):
        raise FittedHoldoutMaterializationError("fitted_holdout_row_locator_mismatch")
    return FittedHoldoutMinuteV1(
        row=PointInTimeBar(
            source_row_id="okx-public-minute:" + expected.source_row_identity,
            source_row_sha256=expected.source_row_sha256,
            instrument_id=expected.instrument_id,
            available_at=expected.decision_available_at,
            bar=FeatureBar(
                closed_at=closed_at,
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[5]),
            ),
        ),
        evidence=expected,
    )


def materialize_fitted_holdout_v1(
    readback: ControlledFittedHoldoutReadV1,
    *,
    inputs: FittedCandidateInputsV1,
    receipt_bytes: bytes,
    coordinate_plan_bytes: bytes,
    expected_binding_sha256: str,
    expected_receipt_sha256: str,
    expected_reservation_sha256: str,
) -> FrozenGate3Artifact[FittedHoldoutMinuteTapeV1]:
    """Verify only already-read bytes and the separately retained seal coordinates."""
    custody = verify_fitted_holdout_readback_v1(
        readback,
        inputs=inputs,
        receipt_bytes=receipt_bytes,
        expected_binding_sha256=expected_binding_sha256,
        expected_receipt_sha256=expected_receipt_sha256,
        expected_reservation_sha256=expected_reservation_sha256,
    )
    seal = inputs.preregistration
    window = seal.prospective_holdout
    coordinates = _coordinates_for_holdout(seal, coordinate_plan_bytes)
    manifest = _verify(
        readback.manifest_bytes,
        expected_sha256=custody.manifest_sha256,
        contract_type=CompleteBlindWindowDataset,
    )
    bundle = _verify(
        readback.dataset_bytes,
        expected_sha256=custody.dataset_sha256,
        contract_type=FittedHoldoutRawBundleV1,
    )
    if (
        manifest.preregistration_sha256 != inputs.expected_preregistration_sha256
        or manifest.source_tree_sha256 != seal.source_tree_sha256
        or manifest.holdout_id != coordinates.holdout_id
        or manifest.coordinate_plan_sha256 != window.coordinate_plan_sha256
        or manifest.start_at != coordinates.start_at
        or manifest.end_at != coordinates.end_at
        or manifest.instrument_ids != coordinates.instrument_ids
        or manifest.first_permitted_evaluator_access_at
        != window.first_permitted_access_at
        or manifest.expected_rows != coordinates.expected_rows
        or bundle.manifest_sha256 != custody.manifest_sha256
        or len(bundle.captures) != manifest.expected_rows
    ):
        raise FittedHoldoutMaterializationError(
            "fitted_holdout_source_identity_mismatch"
        )

    minutes = tuple(
        _replay_minute(
            row,
            capture,
            origin=coordinates.origin,
            source_version=coordinates.source_version,
            seal_created_ns=_ns(seal.created_at),
        )
        for row, capture in zip(manifest.rows, bundle.captures, strict=True)
    )
    return _freeze(
        FittedHoldoutMinuteTapeV1(
            seal_sha256=inputs.expected_preregistration_sha256,
            binding_sha256=custody.binding_sha256,
            receipt_sha256=custody.receipt_sha256,
            reservation_sha256=custody.reservation_sha256,
            coordinate_plan_sha256=window.coordinate_plan_sha256,
            manifest_sha256=custody.manifest_sha256,
            dataset_sha256=custody.dataset_sha256,
            minutes_sha256=_minutes_sha256(minutes),
            minutes=minutes,
        ),
        contract_type=FittedHoldoutMinuteTapeV1,
    )
