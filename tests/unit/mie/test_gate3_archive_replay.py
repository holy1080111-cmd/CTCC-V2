import hashlib
import stat
import zipfile
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from pydantic import ValidationError

from app.mie.contracts import ForecastHorizon
from app.mie.validation.archive_replay import (
    PROVIDER_HEADER,
    ArchiveReplayDataset,
    ArchiveReplayValidationError,
    conservative_archive_rows,
    load_binance_archive_rehearsal,
    validate_archive_replay_source,
)
from app.mie.validation.availability import ArchiveObservationReceipt, AvailabilityBasis
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.replay import ReplayValidationError, replay_features_at

START = datetime(2024, 1, 1, tzinfo=UTC)
OBSERVED = START + timedelta(days=2)
RETRIEVED = OBSERVED + timedelta(minutes=1)
MEMBER = "BTCUSDT-1m-2024-01-01.csv"


def raw_rows() -> list[str]:
    start_ms = 1_704_067_200_000
    return [
        f"{start_ms + minute * 60_000},100,102,99,101,10,"
        f"{start_ms + minute * 60_000 + 59_999},1000,20,5,500,0"
        for minute in range(1440)
    ]


def archive_bytes(
    rows: list[str] | None = None,
    *,
    member: str = MEMBER,
    payload: bytes | None = None,
    second_member: bool = False,
    mode: int | None = None,
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    stream = BytesIO()
    if payload is None:
        payload = ("\n".join(raw_rows() if rows is None else rows) + "\n").encode(
            "utf-8"
        )
    info = zipfile.ZipInfo(member, (2024, 1, 1, 0, 0, 0))
    info.compress_type = compression
    if mode is not None:
        info.create_system = 3
        info.external_attr = mode << 16
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(info, payload)
        if second_member:
            archive.writestr("extra.csv", b"unreviewed")
    return stream.getvalue()


def receipt_for(data: bytes, **updates) -> ArchiveObservationReceipt:
    return ArchiveObservationReceipt(
        **{
            "archive_sha256": hashlib.sha256(data).hexdigest(),
            "archive_byte_size": len(data),
            "symbol": "BTCUSDT",
            "instrument_id": "BTC-USDT-SWAP",
            "partition": DatasetPartition.DEVELOPMENT,
            "day_start_at": START,
            "member_name": MEMBER,
            "observed_at": OBSERVED,
            "retrieved_at": RETRIEVED,
            **updates,
        }
    )


def load(data: bytes, **updates) -> ArchiveReplayDataset:
    receipt = receipt_for(data, **updates)
    return load_binance_archive_rehearsal(
        data, receipt=receipt, receipt_sha256=receipt.canonical_sha256()
    )


@pytest.fixture(scope="module")
def dataset() -> ArchiveReplayDataset:
    return load(archive_bytes())


@pytest.mark.parametrize(
    "symbol,partition",
    [
        ("BTCUSDT", DatasetPartition.DEVELOPMENT),
        ("ETHUSDT", DatasetPartition.VALIDATION),
    ],
)
def test_complete_daily_csv_has_normalized_events_and_bound_rows(
    symbol, partition
) -> None:
    member = f"{symbol}-1m-2024-01-01.csv"
    payload = ("\r\n".join([",".join(PROVIDER_HEADER), *raw_rows()]) + "\r\n").encode()
    data = archive_bytes(member=member, payload=payload)
    result = load(
        data,
        symbol=symbol,
        instrument_id=f"{symbol[:-4]}-USDT-SWAP",
        member_name=member,
        partition=partition,
    )
    assert len(result.rows) == 1440
    assert result.rows[0].bar.closed_at == START + timedelta(minutes=1)
    assert result.rows[-1].bar.closed_at == START + timedelta(days=1)
    assert result.rows[0].bar.volume == Decimal(10)
    assert (
        result.rows[0].provenance.raw_row_sha256
        == hashlib.sha256(raw_rows()[0].encode()).hexdigest()
    )
    assert result.rows[0].availability.available_at == RETRIEVED
    assert result.rows[0].provenance.archive_sha256 == hashlib.sha256(data).hexdigest()
    assert result.rows[0].provenance.receipt_sha256 == result.receipt_sha256
    assert result.point_in_time_provenance is False
    assert result.predictive_oos_eligible is False
    assert result.current_claim.value == "computational"


def test_deterministic_round_trip_and_receipt_changes_bind_all_rows(dataset) -> None:
    assert (
        dataset.canonical_json_bytes() == load(archive_bytes()).canonical_json_bytes()
    )
    assert (
        ArchiveReplayDataset.model_validate_json(dataset.canonical_json_bytes())
        == dataset
    )
    later = load(archive_bytes(), retrieved_at=RETRIEVED + timedelta(seconds=1))
    assert later.canonical_sha256() != dataset.canonical_sha256()
    assert (
        later.rows[0].availability.source_row_sha256
        != dataset.rows[0].availability.source_row_sha256
    )


def test_conservative_bars_reject_historical_cutoff(dataset) -> None:
    rows = conservative_archive_rows(dataset, archive_bytes=archive_bytes())
    assert all(row.available_at == RETRIEVED for row in rows)
    with pytest.raises(ReplayValidationError, match="not available"):
        replay_features_at(
            rows,
            as_of=START + timedelta(hours=1),
            bar_horizon=ForecastHorizon(label="1m", seconds=60),
        )


@pytest.mark.parametrize(
    "column,value",
    [
        (0, "1704067200000000"),
        (0, "1704067200001"),
        (6, "1704067260000"),
        (1, "NaN"),
        (1, "Infinity"),
        (1, "1e999999999"),
        (1, "1_00"),
        (1, "１００"),
        (1, "9" * 81),
        (1, " 100"),
        (1, "0"),
        (2, "100"),
        (3, "102"),
        (5, "-1"),
        (7, "-1"),
        (8, "1.0"),
        (8, "-1"),
        (9, "11"),
        (10, "1001"),
        (11, "NaN"),
    ],
)
def test_rejects_nonfinite_unsafe_numeric_or_invalid_market_rows(column, value) -> None:
    rows = raw_rows()
    first = rows[0].split(",")
    first[column] = value
    rows[0] = ",".join(first)
    with pytest.raises(ArchiveReplayValidationError):
        load(archive_bytes(rows))


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "reordered", "extra", "wrong_day", "wrong_schema"],
)
def test_rejects_incomplete_or_noncontiguous_days(mutation) -> None:
    rows = raw_rows()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "reordered":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "extra":
        rows.append(rows[-1])
    elif mutation == "wrong_day":
        rows = [row.replace("170406", "170416") for row in rows]
    else:
        rows[0] += ",extra"
    with pytest.raises(ArchiveReplayValidationError):
        load(archive_bytes(rows))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"member": "../" + MEMBER},
        {"member": "ETHUSDT-1m-2024-01-01.csv"},
        {"member": "folder\\" + MEMBER},
        {"second_member": True},
        {"mode": stat.S_IFLNK | 0o777},
        {"mode": stat.S_IFIFO | 0o600},
        {"payload": b"\xff\xfeinvalid"},
        {"payload": b"x" * (4 * 1024 * 1024 + 1), "compression": zipfile.ZIP_DEFLATED},
    ],
)
def test_rejects_unsafe_zip_and_invalid_text(kwargs) -> None:
    with pytest.raises(ArchiveReplayValidationError):
        load(archive_bytes(**kwargs))


def test_crc_is_checked_even_when_archive_hash_matches() -> None:
    data = bytearray(archive_bytes())
    offset = data.index(b",100,102,99,101,")
    data[offset + 2] = ord("1")
    with pytest.raises(ArchiveReplayValidationError, match="CRC"):
        load(bytes(data))


def test_zip_member_nul_suffix_cannot_impersonate_the_exact_name() -> None:
    original = MEMBER + "Xunexpected"
    replacement = MEMBER + "\x00unexpected"
    data = archive_bytes(member=original).replace(
        original.encode(), replacement.encode()
    )
    with pytest.raises(ArchiveReplayValidationError, match="unsafe or unexpected"):
        load(data)


def test_wrong_receipt_or_archive_hash_and_nonbytes_are_rejected() -> None:
    data = archive_bytes()
    receipt = receipt_for(data)
    with pytest.raises(ArchiveReplayValidationError, match="receipt SHA"):
        load_binance_archive_rehearsal(data, receipt=receipt, receipt_sha256="0" * 64)
    with pytest.raises(ArchiveReplayValidationError, match="bytes do not match"):
        load_binance_archive_rehearsal(
            data + b"extra", receipt=receipt, receipt_sha256=receipt.canonical_sha256()
        )
    with pytest.raises(ArchiveReplayValidationError, match="bounded bytes"):
        load_binance_archive_rehearsal(
            "https://example.invalid/archive.zip",
            receipt=receipt,
            receipt_sha256=receipt.canonical_sha256(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "receipt",
        "raw",
        "price",
        "coercible_price",
        "row_hash",
        "ordinal",
        "assumed",
        "measured",
        "eligibility",
    ],
)
def test_conversion_revalidates_nested_copy_tampering(dataset, mutation) -> None:
    first = dataset.rows[0]
    if mutation == "receipt":
        changed = dataset.model_copy(
            update={
                "receipt": dataset.receipt.model_copy(
                    update={"partition": DatasetPartition.RETROSPECTIVE_HOLDOUT}
                )
            }
        )
    elif mutation == "eligibility":
        changed = dataset.model_copy(update={"predictive_oos_eligible": True})
    else:
        if mutation == "raw":
            first = first.model_copy(
                update={"raw_row": first.raw_row.replace(",101,", ",100,")}
            )
        elif mutation in {"price", "coercible_price"}:
            value = Decimal(99) if mutation == "price" else 100.0
            first = first.model_copy(
                update={"bar": first.bar.model_copy(update={"open": value})}
            )
        elif mutation in {"row_hash", "ordinal"}:
            update = (
                {"raw_row_sha256": "0" * 64}
                if mutation == "row_hash"
                else {"row_ordinal": 2}
            )
            first = first.model_copy(
                update={"provenance": first.provenance.model_copy(update=update)}
            )
        else:
            basis = (
                AvailabilityBasis.ASSUMED_BAR_CLOSE
                if mutation == "assumed"
                else AvailabilityBasis.MEASURED_ROW_RECEIPT
            )
            update = {
                "basis": basis,
                "available_at": first.bar.closed_at
                if mutation == "assumed"
                else OBSERVED,
                "assumption": "confirmed_at_bar_close"
                if mutation == "assumed"
                else "none",
            }
            first = first.model_copy(
                update={"availability": first.availability.model_copy(update=update)}
            )
        changed = dataset.model_copy(update={"rows": (first, *dataset.rows[1:])})
    warning_context = (
        pytest.warns(UserWarning, match="Pydantic serializer warnings")
        if mutation == "coercible_price"
        else nullcontext()
    )
    with warning_context, pytest.raises(ArchiveReplayValidationError):
        conservative_archive_rows(changed, archive_bytes=archive_bytes())


def test_receipt_and_dataset_subclasses_are_not_accepted(dataset) -> None:
    class AlternateReceipt(ArchiveObservationReceipt):
        pass

    class AlternateDataset(ArchiveReplayDataset):
        pass

    receipt = AlternateReceipt.model_validate(dataset.receipt.model_dump())
    with pytest.raises(ArchiveReplayValidationError, match="exact contract"):
        load_binance_archive_rehearsal(
            archive_bytes(), receipt=receipt, receipt_sha256=receipt.canonical_sha256()
        )
    alternate = AlternateDataset.model_validate(dataset.model_dump())
    with pytest.raises(ArchiveReplayValidationError, match="exact contract"):
        conservative_archive_rows(alternate, archive_bytes=archive_bytes())
    with pytest.raises(ValidationError):
        dataset.rows = ()


def test_full_rehash_cannot_substitute_prices_from_outside_the_archive(dataset) -> None:
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
            "bar": first.bar.model_copy(update={"close": Decimal(100)}),
            "availability": first.availability.model_copy(
                update={
                    "source_row_sha256": provenance.canonical_sha256(),
                }
            ),
        }
    )
    changed = dataset.model_copy(update={"rows": (changed_row, *dataset.rows[1:])})
    # Every row hash and derived value agrees, but the original archive does not.
    structurally_valid = ArchiveReplayDataset.model_validate(changed.model_dump())
    assert structurally_valid.receipt == dataset.receipt
    for operation in (validate_archive_replay_source, conservative_archive_rows):
        with pytest.raises(ArchiveReplayValidationError, match="original source bytes"):
            operation(structurally_valid, archive_bytes=archive_bytes())


def test_source_verification_requires_the_original_archive(dataset) -> None:
    with pytest.raises(TypeError):
        conservative_archive_rows(dataset)
    with pytest.raises(ArchiveReplayValidationError, match="bytes do not match"):
        validate_archive_replay_source(
            dataset, archive_bytes=archive_bytes() + b"changed"
        )
    assert (
        validate_archive_replay_source(dataset, archive_bytes=archive_bytes())
        == dataset
    )
