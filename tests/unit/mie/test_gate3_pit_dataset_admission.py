from __future__ import annotations

import hashlib
import zipfile
from datetime import timedelta
from io import BytesIO

import pytest
from pydantic import ValidationError
from test_gate3_archive_batch_artifacts import synthetic_source
from test_gate3_archive_batch_plan import valid_plan

from app.mie.validation.archive_batch import (
    ArchiveBatchSource,
    ArchiveBatchValidationError,
    load_archive_batch_rehearsal,
)
from app.mie.validation.availability import AvailabilityBasis, AvailabilityProvenance
from app.mie.validation.batch_replay import (
    BoundMinute,
    archive_batch_minutes,
    minute_source_sha256,
)
from app.mie.validation.contracts import DatasetIdentity, DatasetPartition
from app.mie.validation.pit_dataset_admission import (
    ArchivePITDatasetAdmissionV1,
    PITDatasetAdmissionError,
    audit_archive_pit_dataset_admission,
    verify_archive_pit_dataset_admission,
)


@pytest.fixture(scope="module")
def source():
    plan = valid_plan()
    sources = tuple(
        synthetic_source(day_offset=day, partition=partition, symbol="BTCUSDT")
        for partition, days in (
            (DatasetPartition.DEVELOPMENT, (0, 1)),
            (DatasetPartition.VALIDATION, (3,)),
        )
        for day in days
    )
    manifest = load_archive_batch_rehearsal(
        sources, plan=plan, expected_plan_sha256=plan.canonical_sha256()
    )
    _, minutes = archive_batch_minutes(
        manifest,
        archive_bytes=tuple(item.archive_bytes for item in sources),
        expected_plan_sha256=plan.canonical_sha256(),
        partition=DatasetPartition.DEVELOPMENT,
        instrument_id="BTC-USDT-SWAP",
    )
    identity = DatasetIdentity(
        dataset_id="dataset:synthetic:development:v1",
        source="binance.public_data",
        source_version="futures_um.daily.klines.v1",
        instrument_ids=("BTC-USDT-SWAP",),
        manifest_sha256=manifest.canonical_sha256(),
        content_sha256=hashlib.sha256(b"placeholder").hexdigest(),
        expected_rows=len(minutes),
        first_event_at=minutes[0].row.bar.closed_at,
        last_event_at=minutes[-1].row.bar.closed_at,
        frozen_at=max(item.row.available_at for item in minutes) + timedelta(seconds=1),
        event_time_field="closed_at",
        available_time_field="available_at",
    )
    identity = identity.model_copy(
        update={"content_sha256": minute_source_sha256(minutes)}
    )
    return plan, sources, manifest, minutes, identity


def audit(source, **updates):
    plan, sources, manifest, minutes, identity = source
    arguments = {
        "manifest": manifest,
        "archive_bytes": tuple(item.archive_bytes for item in sources),
        "expected_plan_sha256": plan.canonical_sha256(),
        "expected_manifest_sha256": manifest.canonical_sha256(),
        "partition": DatasetPartition.DEVELOPMENT,
        "instrument_id": "BTC-USDT-SWAP",
        "claimed_dataset": identity,
        "submitted_minutes": minutes,
    }
    arguments.update(updates)
    return audit_archive_pit_dataset_admission(**arguments)


def test_source_exact_and_caller_true_still_deny_late_archive(source) -> None:
    result = audit(source)
    assert result.status == "DENY"
    assert result.expected_rows == result.submitted_rows == 2880
    assert result.late_source_rows == 2880
    assert result.reasons == (
        "archive_observation_is_not_historical_row_receipt",
        "caller_pit_assertion_is_not_source_proof",
        "historical_partition_cutoff_precedes_receipt",
        "independent_pin_custody_unverified",
    )
    assert result.candidate_seal_eligible is False
    assert result.formal_evaluation_eligible is False
    assert result.predictive_oos_eligible is False
    assert result.execution_authority is False
    assert (
        ArchivePITDatasetAdmissionV1.model_validate_json(result.canonical_json_bytes())
        == result
    )
    assert audit(source).canonical_json_bytes() == result.canonical_json_bytes()
    plan, sources, manifest, minutes, identity = source
    assert (
        verify_archive_pit_dataset_admission(
            result,
            expected_receipt_sha256=result.canonical_sha256(),
            manifest=manifest,
            archive_bytes=tuple(item.archive_bytes for item in sources),
            expected_plan_sha256=plan.canonical_sha256(),
            expected_manifest_sha256=manifest.canonical_sha256(),
            partition=DatasetPartition.DEVELOPMENT,
            instrument_id="BTC-USDT-SWAP",
            claimed_dataset=identity,
            submitted_minutes=minutes,
        )
        == result
    )


def test_assumed_close_cannot_replace_source_observation(source) -> None:
    minutes = source[3]
    original = minutes[0]
    closed_at = original.row.bar.closed_at
    forged = BoundMinute(
        row=original.row.model_copy(update={"available_at": closed_at}),
        availability=AvailabilityProvenance(
            basis=AvailabilityBasis.ASSUMED_BAR_CLOSE,
            source_row_sha256=original.row.source_row_sha256,
            receipt_sha256=original.availability.receipt_sha256,
            bar_closed_at=closed_at,
            observed_at=original.availability.observed_at,
            retrieved_at=original.availability.retrieved_at,
            available_at=closed_at,
            assumption="confirmed_at_bar_close",
        ),
    )
    result = audit(source, submitted_minutes=(forged,) + minutes[1:])
    assert "submitted_assumed_bar_close" in result.reasons
    assert "submitted_rows_differ_from_source" in result.reasons
    assert result.formal_evaluation_eligible is False


@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_missing_or_duplicate_minute_cannot_pass(source, change: str) -> None:
    minutes = source[3]
    submitted = (
        minutes[1:] if change == "missing" else (minutes[0], minutes[0]) + minutes[2:]
    )
    result = audit(source, submitted_minutes=submitted)
    assert "submitted_rows_differ_from_source" in result.reasons
    if change == "duplicate":
        assert "duplicate_submitted_row_identity" in result.reasons
    assert result.candidate_seal_eligible is False


def _changed_archive(source: ArchiveBatchSource) -> ArchiveBatchSource:
    original = BytesIO(source.archive_bytes)
    with zipfile.ZipFile(original) as archive:
        name = archive.namelist()[0]
        content = archive.read(name)
    assert b",100,102,99,101," in content
    content = content.replace(b",100,102,99,101,", b",101,103,100,102,", 1)
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(zipfile.ZipInfo(name, (2024, 1, 1, 0, 0, 0)), content)
    changed = stream.getvalue()
    receipt = source.receipt.model_copy(
        update={
            "archive_sha256": hashlib.sha256(changed).hexdigest(),
            "archive_byte_size": len(changed),
        }
    )
    return ArchiveBatchSource(changed, receipt, receipt.canonical_sha256())


def test_rehashed_source_packet_cannot_escape_retained_manifest_pin(source) -> None:
    plan, sources, _, _, identity = source
    revised = (_changed_archive(sources[0]),) + sources[1:]
    rebuilt = load_archive_batch_rehearsal(
        revised, plan=plan, expected_plan_sha256=plan.canonical_sha256()
    )
    with pytest.raises(PITDatasetAdmissionError, match="retained source manifest"):
        audit(source, manifest=rebuilt)

    # Even if a caller controls the replacement manifest and its new pin, the
    # resulting record remains DENY with perfectly matching replacement rows.
    _, revised_minutes = archive_batch_minutes(
        rebuilt,
        archive_bytes=tuple(item.archive_bytes for item in revised),
        expected_plan_sha256=plan.canonical_sha256(),
        partition=DatasetPartition.DEVELOPMENT,
        instrument_id="BTC-USDT-SWAP",
    )
    result = audit(
        source,
        manifest=rebuilt,
        archive_bytes=tuple(item.archive_bytes for item in revised),
        expected_manifest_sha256=rebuilt.canonical_sha256(),
        claimed_dataset=identity.model_copy(
            update={
                "manifest_sha256": rebuilt.canonical_sha256(),
                "content_sha256": minute_source_sha256(revised_minutes),
            }
        ),
        submitted_minutes=revised_minutes,
    )
    assert result.pin_custody == "caller_supplied_unverified"
    assert "submitted_rows_differ_from_source" not in result.reasons
    assert "dataset_identity_differs_from_source" not in result.reasons
    assert result.candidate_seal_eligible is False
    assert result.formal_evaluation_eligible is False


def test_changed_original_zip_or_forged_eligibility_fails(source) -> None:
    altered = (source[1][0].archive_bytes + b"x",) + tuple(
        item.archive_bytes for item in source[1][1:]
    )
    with pytest.raises(ArchiveBatchValidationError):
        audit(source, archive_bytes=altered)
    forged = audit(source).model_copy(update={"candidate_seal_eligible": True})
    with pytest.raises(ValidationError):
        ArchivePITDatasetAdmissionV1.model_validate(forged.model_dump(mode="python"))


def test_holdout_and_wrong_retained_pin_never_enter_audit(source) -> None:
    with pytest.raises(ArchiveBatchValidationError):
        audit(source, partition=DatasetPartition.RETROSPECTIVE_HOLDOUT)
    with pytest.raises(PITDatasetAdmissionError, match="retained source manifest"):
        audit(source, expected_manifest_sha256="0" * 64)
    result = audit(source)
    with pytest.raises(PITDatasetAdmissionError, match="receipt pin changed"):
        verify_archive_pit_dataset_admission(
            result,
            expected_receipt_sha256="0" * 64,
            manifest=source[2],
            archive_bytes=tuple(item.archive_bytes for item in source[1]),
            expected_plan_sha256=source[0].canonical_sha256(),
            expected_manifest_sha256=source[2].canonical_sha256(),
            partition=DatasetPartition.DEVELOPMENT,
            instrument_id="BTC-USDT-SWAP",
            claimed_dataset=source[4],
            submitted_minutes=source[3],
        )
