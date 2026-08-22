from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import hashlib
import zipfile

import pytest

from app.research.external_benchmarks import (
    ArchiveKind,
    BinanceKlineCoordinates,
    BinanceKlineValidationError,
    BinancePublicArtifactIdentity,
    ExternalArtifactAcquisitionReceipt,
    ExternalArtifactAcquisitionRequest,
    profile_binance_kline_archive,
)


NOW = datetime(2026, 8, 17, 1, 2, 3, tzinfo=timezone.utc)
LAST_MODIFIED = datetime(2024, 1, 2, 6, 7, 8, tzinfo=timezone.utc)
HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,"
    "count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
)


def coordinates() -> BinanceKlineCoordinates:
    return BinanceKlineCoordinates(
        symbol="BTCUSDT",
        interval="1m",
        day=date(2024, 1, 1),
    )


def kline_rows(*, invalid_high_at: int | None = None) -> list[str]:
    start = 1_704_067_200_000
    rows: list[str] = []
    for index in range(1440):
        open_time = start + index * 60_000
        high = "99" if index == invalid_high_at else "102"
        rows.append(
            f"{open_time},100,{high},99,101,2,"
            f"{open_time + 59_999},201,10,1,100.5,0"
        )
    return rows


def archive_payload(
    value: BinanceKlineCoordinates,
    *,
    rows: list[str] | None = None,
    member_name: str | None = None,
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            member_name or value.member_filename,
            HEADER + "\n".join(rows or kline_rows()) + "\n",
        )
    return buffer.getvalue()


def contracts(
    value: BinanceKlineCoordinates,
    payload: bytes,
) -> tuple[
    BinancePublicArtifactIdentity,
    ExternalArtifactAcquisitionRequest,
    ExternalArtifactAcquisitionReceipt,
]:
    digest = hashlib.sha256(payload).hexdigest()
    request = ExternalArtifactAcquisitionRequest(
        request_id=value.request_id,
        source_id="binance.public_data",
        download_url=value.download_url,
        terms_url="https://github.com/binance/binance-public-data",
        terms_review_sha256="a" * 64,
        terms_reviewed_at=NOW,
        relative_path=value.relative_path,
        expected_sha256=digest,
        expected_byte_size=len(payload),
        expected_media_types=(
            "application/zip",
            "application/octet-stream",
            "binary/octet-stream",
        ),
        archive_kind=ArchiveKind.ZIP,
    )
    identity = BinancePublicArtifactIdentity(
        coordinates_sha256=value.canonical_sha256(),
        artifact_url=value.download_url,
        checksum_url=value.checksum_url,
        checksum_payload_sha256="b" * 64,
        artifact_sha256=digest,
        artifact_byte_size=len(payload),
        artifact_media_type="application/zip",
        provider_last_modified_at=LAST_MODIFIED,
        observed_at=NOW,
        terms_review_sha256="a" * 64,
    )
    receipt = ExternalArtifactAcquisitionReceipt(
        request_id=value.request_id,
        request_sha256=request.canonical_sha256(),
        source_id="binance.public_data",
        final_url=value.download_url,
        relative_path=value.relative_path,
        sha256=digest,
        byte_size=len(payload),
        media_type="application/zip",
        retrieved_at=NOW,
        redirect_count=0,
        status="downloaded",
        archive_report_sha256="c" * 64,
    )
    return identity, request, receipt


def stage(tmp_path: Path, relative_path: str, payload: bytes) -> None:
    path = tmp_path.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)


def test_complete_binance_day_passes_generic_and_provider_quality(
    tmp_path: Path,
) -> None:
    value = coordinates()
    payload = archive_payload(value)
    identity, request, receipt = contracts(value, payload)
    stage(tmp_path, value.relative_path, payload)

    manifest, generic, provider, evidence = profile_binance_kline_archive(
        value,
        identity,
        request,
        receipt,
        tmp_path,
        generated_at=NOW,
    )

    assert manifest.row_count == 1440
    assert manifest.instrument_ids == ("BTC-USDT-SWAP",)
    assert generic.passed is True
    assert provider.passed is True
    assert provider.observed_row_count == 1440
    assert provider.header_present is True
    assert provider.failure_codes == ()
    assert evidence.passed is True
    assert evidence.promotion_eligible is False
    assert evidence.execution_authority is False


@pytest.mark.parametrize(
    ("rows", "failure"),
    [
        (kline_rows()[:-1], "row_count_mismatch"),
        (kline_rows(invalid_high_at=4), "invalid_ohlc_rows"),
    ],
)
def test_provider_quality_reports_missing_or_invalid_bars(
    tmp_path: Path,
    rows: list[str],
    failure: str,
) -> None:
    value = coordinates()
    payload = archive_payload(value, rows=rows)
    identity, request, receipt = contracts(value, payload)
    stage(tmp_path, value.relative_path, payload)

    _, _, provider, evidence = profile_binance_kline_archive(
        value,
        identity,
        request,
        receipt,
        tmp_path,
        generated_at=NOW,
    )

    assert provider.passed is False
    assert failure in provider.failure_codes
    assert evidence.passed is False
    assert evidence.execution_authority is False


def test_parser_rejects_an_unexpected_zip_member(tmp_path: Path) -> None:
    value = coordinates()
    payload = archive_payload(value, member_name="unexpected.csv")
    identity, request, receipt = contracts(value, payload)
    stage(tmp_path, value.relative_path, payload)

    with pytest.raises(BinanceKlineValidationError, match="expected CSV"):
        profile_binance_kline_archive(
            value,
            identity,
            request,
            receipt,
            tmp_path,
            generated_at=NOW,
        )


@pytest.mark.parametrize(
    ("tamper", "match"),
    [
        ("identity_url", "identity URLs"),
        ("terms_digest", "artifact identities"),
        ("receipt_url", "artifact identities"),
    ],
)
def test_profile_rejects_cross_contract_identity_tampering(
    tmp_path: Path,
    tamper: str,
    match: str,
) -> None:
    value = coordinates()
    payload = archive_payload(value)
    identity, request, receipt = contracts(value, payload)
    stage(tmp_path, value.relative_path, payload)

    if tamper == "identity_url":
        identity = identity.model_copy(
            update={
                "artifact_url": (
                    "https://data.binance.vision/data/futures/um/daily/"
                    "klines/ETHUSDT/1m/ETHUSDT-1m-2024-01-01.zip"
                )
            }
        )
    elif tamper == "terms_digest":
        identity = identity.model_copy(update={"terms_review_sha256": "d" * 64})
    else:
        receipt = receipt.model_copy(
            update={
                "final_url": (
                    "https://data.binance.vision/data/futures/um/daily/"
                    "klines/ETHUSDT/1m/ETHUSDT-1m-2024-01-01.zip"
                )
            }
        )

    with pytest.raises(BinanceKlineValidationError, match=match):
        profile_binance_kline_archive(
            value,
            identity,
            request,
            receipt,
            tmp_path,
            generated_at=NOW,
        )


def test_decimal_values_remain_exact_in_the_reference_parser() -> None:
    assert Decimal("100.00000001") + Decimal("0.00000001") == Decimal(
        "100.00000002"
    )
