import hashlib
import json
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest

from app.mie.validation import (
    ArchiveObservationReceipt,
    ArchiveReplayDataset,
    ArtifactVerificationError,
    DatasetPartition,
    freeze_archive_observation_receipt,
    freeze_archive_replay_dataset,
    load_binance_archive_rehearsal,
    verify_archive_observation_receipt,
    verify_archive_replay_dataset,
)


@pytest.fixture(scope="module")
def original_archive() -> tuple[bytes, ArchiveReplayDataset]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    member = "BTCUSDT-1m-2024-01-01.csv"
    lines = [
        f"{1704067200000 + minute * 60000},100,102,99,101,10,"
        f"{1704067259999 + minute * 60000},1000,20,5,500,0"
        for minute in range(1440)
    ]
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            zipfile.ZipInfo(member, (2024, 1, 1, 0, 0, 0)), "\n".join(lines)
        )
    data = stream.getvalue()
    receipt = ArchiveObservationReceipt(
        archive_sha256=hashlib.sha256(data).hexdigest(),
        archive_byte_size=len(data),
        symbol="BTCUSDT",
        instrument_id="BTC-USDT-SWAP",
        partition=DatasetPartition.DEVELOPMENT,
        day_start_at=start,
        member_name=member,
        observed_at=start + timedelta(days=2),
        retrieved_at=start + timedelta(days=2, seconds=1),
    )
    return data, load_binance_archive_rehearsal(
        data, receipt=receipt, receipt_sha256=receipt.canonical_sha256()
    )


def test_archive_receipt_and_dataset_canonical_round_trip(original_archive) -> None:
    data, dataset = original_archive
    receipt_artifact = freeze_archive_observation_receipt(dataset.receipt)
    assert (
        verify_archive_observation_receipt(
            receipt_artifact.payload, expected_sha256=receipt_artifact.sha256
        )
        == dataset.receipt
    )
    artifact = freeze_archive_replay_dataset(dataset, archive_bytes=data)
    assert artifact.sha256 == hashlib.sha256(artifact.payload).hexdigest()
    assert (
        verify_archive_replay_dataset(
            artifact.payload, expected_sha256=artifact.sha256, archive_bytes=data
        )
        == dataset
    )
    assert artifact.contract.predictive_oos_eligible is False
    assert artifact.contract.execution_authority is False


def test_archive_artifacts_reject_noncanonical_json_and_bad_digests(
    original_archive,
) -> None:
    data, dataset = original_archive
    for artifact, verifier, options in (
        (
            freeze_archive_observation_receipt(dataset.receipt),
            verify_archive_observation_receipt,
            {},
        ),
        (
            freeze_archive_replay_dataset(dataset, archive_bytes=data),
            verify_archive_replay_dataset,
            {"archive_bytes": data},
        ),
    ):
        with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
            verifier(artifact.payload, expected_sha256="0" * 64, **options)
        pretty = json.dumps(json.loads(artifact.payload), indent=2).encode()
        with pytest.raises(ArtifactVerificationError, match="not canonical"):
            verifier(
                pretty, expected_sha256=hashlib.sha256(pretty).hexdigest(), **options
            )


def test_dataset_freeze_and_verify_require_original_zip_membership(
    original_archive,
) -> None:
    data, dataset = original_archive
    artifact = freeze_archive_replay_dataset(dataset, archive_bytes=data)
    with pytest.raises(ArtifactVerificationError, match="source revalidation"):
        freeze_archive_replay_dataset(dataset, archive_bytes=data + b"changed")
    with pytest.raises(ArtifactVerificationError, match="source revalidation"):
        verify_archive_replay_dataset(
            artifact.payload,
            expected_sha256=artifact.sha256,
            archive_bytes=data + b"changed",
        )
    # Even a completely self-consistent alternative dataset is not in this ZIP.
    alternate_stream = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(data)) as source,
        zipfile.ZipFile(alternate_stream, "w") as archive,
    ):
        archive.writestr(
            source.infolist()[0],
            source.read(source.infolist()[0]).replace(b",101,", b",100,"),
        )
    alternate_data = alternate_stream.getvalue()
    alternate_receipt = dataset.receipt.model_copy(
        update={
            "archive_sha256": hashlib.sha256(alternate_data).hexdigest(),
            "archive_byte_size": len(alternate_data),
        }
    )
    alternate = load_binance_archive_rehearsal(
        alternate_data,
        receipt=alternate_receipt,
        receipt_sha256=alternate_receipt.canonical_sha256(),
    )
    with pytest.raises(ArtifactVerificationError, match="source revalidation"):
        freeze_archive_replay_dataset(alternate, archive_bytes=data)


def test_archive_artifacts_revalidate_nested_tampering(original_archive) -> None:
    data, dataset = original_archive
    receipt = dataset.receipt.model_copy(update={"execution_authority": True})
    with pytest.raises(ArtifactVerificationError, match="revalidation"):
        freeze_archive_observation_receipt(receipt)
    changed = dataset.model_copy(update={"rows": dataset.rows[:-1]})
    with pytest.raises(ArtifactVerificationError, match="revalidation"):
        freeze_archive_replay_dataset(changed, archive_bytes=data)


def test_fully_rehashed_forgery_cannot_claim_membership_in_original_zip(
    original_archive,
) -> None:
    data, dataset = original_archive
    first = dataset.rows[0]
    raw = first.raw_row.replace(",101,", ",100,")
    provenance = first.provenance.model_copy(
        update={
            "raw_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        }
    )
    changed_row = first.model_copy(
        update={
            "raw_row": raw,
            "provenance": provenance,
            "availability": first.availability.model_copy(
                update={
                    "source_row_sha256": provenance.canonical_sha256(),
                }
            ),
            "bar": first.bar.model_copy(update={"close": Decimal(100)}),
        }
    )
    candidate = dataset.model_copy(update={"rows": (changed_row, *dataset.rows[1:])})
    forged = ArchiveReplayDataset.model_validate(candidate.model_dump(mode="python"))
    assert forged.receipt == dataset.receipt
    assert forged.receipt_sha256 == dataset.receipt_sha256
    assert forged.rows[0].bar.close != dataset.rows[0].bar.close
    with pytest.raises(ArtifactVerificationError, match="source revalidation"):
        freeze_archive_replay_dataset(forged, archive_bytes=data)
    payload = forged.canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError, match="source revalidation"):
        verify_archive_replay_dataset(
            payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            archive_bytes=data,
        )
