from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from itertools import pairwise

import pytest
from pydantic import ValidationError
from test_gate3_archive_batch_plan import START, valid_plan

from app.mie.validation.archive_batch import (
    ArchiveBatchManifest,
    ArchiveBatchPlan,
    ArchiveBatchSource,
    ArchiveBatchValidationError,
    conservative_batch_rows,
    load_archive_batch_rehearsal,
)
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    freeze_archive_batch_manifest,
    freeze_archive_batch_plan,
    verify_archive_batch_manifest,
    verify_archive_batch_plan,
)
from app.mie.validation.availability import ArchiveObservationReceipt
from app.mie.validation.contracts import DatasetPartition, Gate3Claim


def synthetic_source(
    *, day_offset: int, partition: DatasetPartition, symbol: str
) -> ArchiveBatchSource:
    day = START + timedelta(days=day_offset)
    delta = day - datetime(1970, 1, 1, tzinfo=UTC)
    first_ms = (delta.days * 86400 + delta.seconds) * 1000
    price = (100 if symbol == "BTCUSDT" else 200) + day_offset * 10
    member_name = f"{symbol}-1m-{day.date().isoformat()}.csv"
    rows = [
        f"{first_ms + minute * 60000},{price},{price + 2},{price - 1},"
        f"{price + 1},10,{first_ms + minute * 60000 + 59999},1000,20,5,500,0"
        for minute in range(1440)
    ]
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            zipfile.ZipInfo(member_name, (2024, 1, 1, 0, 0, 0)), "\n".join(rows)
        )
    payload = stream.getvalue()
    # Later event days were retrieved earlier: preserve each original timestamp.
    observed_at = START + timedelta(days=12 - day_offset)
    receipt = ArchiveObservationReceipt(
        archive_sha256=hashlib.sha256(payload).hexdigest(),
        archive_byte_size=len(payload),
        symbol=symbol,
        instrument_id=f"{symbol.removesuffix('USDT')}-USDT-SWAP",
        partition=partition,
        day_start_at=day,
        member_name=member_name,
        observed_at=observed_at,
        retrieved_at=observed_at + timedelta(seconds=1),
    )
    return ArchiveBatchSource(payload, receipt, receipt.canonical_sha256())


@dataclass(frozen=True)
class SyntheticBatch:
    plan: ArchiveBatchPlan
    sources: tuple[ArchiveBatchSource, ...]
    manifest: ArchiveBatchManifest
    frozen: FrozenGate3Artifact[ArchiveBatchManifest]

    @property
    def originals(self) -> tuple[bytes, ...]:
        return tuple(source.archive_bytes for source in self.sources)

    @property
    def plan_pin(self) -> str:
        return self.plan.canonical_sha256()


@pytest.fixture(scope="module")
def synthetic_batch() -> SyntheticBatch:
    plan = valid_plan(symbols=("BTCUSDT", "ETHUSDT"))
    sources = tuple(
        synthetic_source(day_offset=day, partition=partition, symbol=symbol)
        for partition, days in (
            (DatasetPartition.DEVELOPMENT, (0, 1)),
            (DatasetPartition.VALIDATION, (3,)),
        )
        for day in days
        for symbol in plan.symbols
    )
    manifest = load_archive_batch_rehearsal(
        sources, plan=plan, expected_plan_sha256=plan.canonical_sha256()
    )
    frozen = freeze_archive_batch_manifest(
        manifest,
        archive_bytes=tuple(source.archive_bytes for source in sources),
        expected_plan_sha256=plan.canonical_sha256(),
    )
    return SyntheticBatch(plan, sources, manifest, frozen)


def test_complete_multiday_multisymbol_batch_round_trip(synthetic_batch) -> None:
    batch = synthetic_batch
    frozen_plan = freeze_archive_batch_plan(batch.plan)
    assert (
        verify_archive_batch_plan(
            frozen_plan.payload, expected_sha256=frozen_plan.sha256
        )
        == batch.plan
    )
    assert batch.manifest.plan_sha256 == frozen_plan.sha256
    assert len(batch.manifest.members) == 6
    assert batch.manifest.total_rows == 8640
    assert batch.manifest.total_archive_bytes == sum(map(len, batch.originals))
    assert batch.frozen.payload == batch.manifest.canonical_json_bytes()
    assert (
        verify_archive_batch_manifest(
            batch.frozen.payload,
            expected_sha256=batch.frozen.sha256,
            archive_bytes=batch.originals,
            expected_plan_sha256=batch.plan_pin,
        )
        == batch.manifest
    )
    assert batch.manifest.current_claim == Gate3Claim.COMPUTATIONAL
    assert batch.manifest.point_in_time_provenance is False
    assert batch.manifest.predictive_oos_eligible is False
    assert batch.manifest.execution_authority is False
    assert batch.manifest.runtime_consumers == 0


@pytest.mark.parametrize("required", ["archive_bytes", "expected_plan_sha256"])
def test_original_archives_and_plan_pin_are_mandatory(
    synthetic_batch, required
) -> None:
    batch = synthetic_batch
    options = {
        "archive_bytes": batch.originals,
        "expected_plan_sha256": batch.plan_pin,
    }
    del options[required]
    with pytest.raises(TypeError):
        freeze_archive_batch_manifest(batch.manifest, **options)
    with pytest.raises(TypeError):
        verify_archive_batch_manifest(
            batch.frozen.payload, expected_sha256=batch.frozen.sha256, **options
        )


@pytest.mark.parametrize(
    "change", ["missing", "extra", "reordered", "changed", "mutable"]
)
def test_all_original_zips_are_required_exactly(synthetic_batch, change) -> None:
    batch = synthetic_batch
    originals = batch.originals
    if change == "missing":
        originals = originals[:-1]
    elif change == "extra":
        originals += (originals[0],)
    elif change == "reordered":
        originals = (originals[1], originals[0], *originals[2:])
    elif change == "changed":
        originals = (originals[0] + b"changed", *originals[1:])
    else:
        originals = (bytearray(originals[0]), *originals[1:])
    with pytest.raises(ArtifactVerificationError):
        freeze_archive_batch_manifest(
            batch.manifest, archive_bytes=originals, expected_plan_sha256=batch.plan_pin
        )
    with pytest.raises(ArtifactVerificationError):
        verify_archive_batch_manifest(
            batch.frozen.payload,
            expected_sha256=batch.frozen.sha256,
            archive_bytes=originals,
            expected_plan_sha256=batch.plan_pin,
        )


@pytest.mark.parametrize("pin", ["", "not-a-hash", "A" * 64, "0" * 64, None])
def test_manifest_helpers_require_the_independent_plan_pin(
    synthetic_batch, pin
) -> None:
    batch = synthetic_batch
    with pytest.raises(ArtifactVerificationError):
        freeze_archive_batch_manifest(
            batch.manifest, archive_bytes=batch.originals, expected_plan_sha256=pin
        )
    with pytest.raises(ArtifactVerificationError):
        verify_archive_batch_manifest(
            batch.frozen.payload,
            expected_sha256=batch.frozen.sha256,
            archive_bytes=batch.originals,
            expected_plan_sha256=pin,
        )


def test_rehashed_dataset_digest_forgery_fails_original_byte_reconstruction(
    synthetic_batch,
) -> None:
    batch = synthetic_batch
    payload = batch.manifest.model_dump()
    payload["members"][0]["dataset_sha256"] = hashlib.sha256(b"forged-rows").hexdigest()
    # Structurally consistent metadata does not prove membership in original ZIPs.
    forged = ArchiveBatchManifest.model_validate(payload)
    assert forged.members[0].dataset_sha256 != batch.manifest.members[0].dataset_sha256
    with pytest.raises(ArtifactVerificationError):
        freeze_archive_batch_manifest(
            forged, archive_bytes=batch.originals, expected_plan_sha256=batch.plan_pin
        )
    data = forged.canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError):
        verify_archive_batch_manifest(
            data,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            archive_bytes=batch.originals,
            expected_plan_sha256=batch.plan_pin,
        )


@pytest.mark.parametrize("source", ["plan", "receipt", "noncanonical", "duplicate_key"])
def test_manifest_verification_rejects_wrong_schema_and_noncanonical_json(
    synthetic_batch, source
) -> None:
    batch = synthetic_batch
    data = {
        "plan": batch.plan.canonical_json_bytes(),
        "receipt": batch.sources[0].receipt.canonical_json_bytes(),
        "noncanonical": json.dumps(json.loads(batch.frozen.payload), indent=2).encode(),
        "duplicate_key": b'{"execution_authority":false,' + batch.frozen.payload[1:],
    }[source]
    with pytest.raises(ArtifactVerificationError):
        verify_archive_batch_manifest(
            data,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            archive_bytes=batch.originals,
            expected_plan_sha256=batch.plan_pin,
        )


@pytest.mark.parametrize("location", ["manifest", "plan", "receipt"])
def test_nested_model_copy_authority_tampering_cannot_freeze(
    synthetic_batch, location
) -> None:
    batch = synthetic_batch
    if location == "manifest":
        forged = batch.manifest.model_copy(update={"execution_authority": True})
    elif location == "plan":
        plan = batch.plan.model_copy(update={"execution_authority": True})
        forged = batch.manifest.model_copy(update={"plan": plan})
    else:
        member = batch.manifest.members[0]
        receipt = member.receipt.model_copy(update={"execution_authority": True})
        forged_member = member.model_copy(update={"receipt": receipt})
        forged = batch.manifest.model_copy(
            update={"members": (forged_member, *batch.manifest.members[1:])}
        )
    with pytest.raises(ArtifactVerificationError):
        freeze_archive_batch_manifest(
            forged, archive_bytes=batch.originals, expected_plan_sha256=batch.plan_pin
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_claim", Gate3Claim.PREDICTIVE_OOS),
        ("predictive_oos_eligible", True),
        ("point_in_time_provenance", True),
        ("execution_authority", True),
        ("runtime_consumers", 1),
    ],
)
def test_manifest_never_upgrades_claims_or_authority(
    synthetic_batch, field, value
) -> None:
    payload = synthetic_batch.manifest.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        ArchiveBatchManifest.model_validate(payload)


@pytest.mark.parametrize(
    "partition", [DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION]
)
@pytest.mark.parametrize("instrument", ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
def test_conversion_preserves_complete_days_and_each_receipt_availability(
    synthetic_batch, partition, instrument
) -> None:
    batch = synthetic_batch
    rows = conservative_batch_rows(
        batch.manifest,
        archive_bytes=batch.originals,
        expected_plan_sha256=batch.plan_pin,
        partition=partition,
        instrument_id=instrument,
    )
    receipts = tuple(
        member.receipt
        for member in batch.manifest.members
        if member.receipt.partition == partition
        and member.receipt.instrument_id == instrument
    )
    assert len(rows) == 1440 * len(receipts)
    assert {row.instrument_id for row in rows} == {instrument}
    assert len({row.source_row_id for row in rows}) == len(rows)
    for day_index, receipt in enumerate(receipts):
        chunk = rows[day_index * 1440 : (day_index + 1) * 1440]
        assert chunk[0].bar.closed_at == receipt.day_start_at + timedelta(minutes=1)
        assert chunk[-1].bar.closed_at == receipt.day_start_at + timedelta(days=1)
        assert {row.available_at for row in chunk} == {receipt.retrieved_at}
        assert all(row.available_at > row.bar.closed_at for row in chunk)
    window = getattr(batch.plan.split, partition.value)
    assert rows[-1].bar.closed_at == window.end_at
    assert all(a.bar.closed_at < b.bar.closed_at for a, b in pairwise(rows))
    expected_first_close = Decimal(101 if instrument.startswith("BTC") else 201)
    if partition == DatasetPartition.VALIDATION:
        expected_first_close += 30
    assert rows[0].bar.close == expected_first_close
    if partition == DatasetPartition.DEVELOPMENT:
        assert receipts[0].retrieved_at > receipts[1].retrieved_at
        assert rows[0].available_at > rows[1440].available_at


@pytest.mark.parametrize("required", ["partition", "instrument_id"])
def test_conversion_requires_explicit_partition_and_instrument(
    synthetic_batch, required
) -> None:
    batch = synthetic_batch
    selection = {
        "partition": DatasetPartition.DEVELOPMENT,
        "instrument_id": "BTC-USDT-SWAP",
    }
    del selection[required]
    with pytest.raises(TypeError):
        conservative_batch_rows(
            batch.manifest,
            archive_bytes=batch.originals,
            expected_plan_sha256=batch.plan_pin,
            **selection,
        )


@pytest.mark.parametrize(
    "partition,instrument",
    [
        (DatasetPartition.RETROSPECTIVE_HOLDOUT, "BTC-USDT-SWAP"),
        ("development", "BTC-USDT-SWAP"),
        ((DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION), "BTC-USDT-SWAP"),
        (DatasetPartition.DEVELOPMENT, "SOL-USDT-SWAP"),
        (DatasetPartition.DEVELOPMENT, ("BTC-USDT-SWAP", "ETH-USDT-SWAP")),
        (DatasetPartition.DEVELOPMENT, None),
    ],
)
def test_conversion_rejects_holdout_mixed_or_undeclared_selection(
    synthetic_batch, partition, instrument
) -> None:
    batch = synthetic_batch
    with pytest.raises(ArchiveBatchValidationError):
        conservative_batch_rows(
            batch.manifest,
            archive_bytes=batch.originals,
            expected_plan_sha256=batch.plan_pin,
            partition=partition,
            instrument_id=instrument,
        )


def test_corrupted_unselected_partition_invalidates_the_whole_batch(
    synthetic_batch,
) -> None:
    batch = synthetic_batch
    assert batch.manifest.members[-1].receipt.partition == DatasetPartition.VALIDATION
    originals = (*batch.originals[:-1], batch.originals[-1] + b"changed-validation")
    with pytest.raises(ArchiveBatchValidationError):
        conservative_batch_rows(
            batch.manifest,
            archive_bytes=originals,
            expected_plan_sha256=batch.plan_pin,
            partition=DatasetPartition.DEVELOPMENT,
            instrument_id="BTC-USDT-SWAP",
        )
