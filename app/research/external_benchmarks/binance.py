from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import Field, HttpUrl, field_validator, model_validator

from app.research.external_benchmarks.artifacts import sha256_file
from app.research.external_benchmarks.contracts import (
    ArchiveKind,
    ExternalArtifactAcquisitionRequest,
    ReferenceContract,
    require_utc,
)


Clock = Callable[[], datetime]
BINANCE_DATA_HOST = "data.binance.vision"
BINANCE_TERMS_URL = "https://github.com/binance/binance-public-data"
CHECKSUM_MAX_BYTES = 512
ARTIFACT_MAX_BYTES = 1024 * 1024
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
EXPECTED_MEDIA_TYPES = ("application/zip", "application/octet-stream")
FIRST_REFERENCE_DAY = date(2024, 1, 1)
CHECKSUM_PATTERN = re.compile(
    rb"\A([0-9a-fA-F]{64})[ \t]+\*?([^\r\n]+)\r?\n?\Z"
)


class BinanceReferencePreparationError(RuntimeError):
    pass


class BinanceKlineCoordinates(ReferenceContract):
    """Reviewed coordinates for the first canonical Binance reference."""

    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    interval: Literal["1m"] = "1m"
    day: date
    market: Literal["futures_um"] = "futures_um"
    dataset_kind: Literal["klines"] = "klines"
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("day")
    @classmethod
    def validate_day(cls, value: date) -> date:
        if value != FIRST_REFERENCE_DAY:
            raise ValueError("first Binance reference day must be 2024-01-01")
        return value

    @property
    def filename(self) -> str:
        return f"{self.symbol}-{self.interval}-{self.day.isoformat()}.zip"

    @property
    def member_filename(self) -> str:
        return self.filename.removesuffix(".zip") + ".csv"

    @property
    def download_url(self) -> str:
        return (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{self.symbol}/{self.interval}/{self.filename}"
        )

    @property
    def checksum_url(self) -> str:
        return f"{self.download_url}.CHECKSUM"

    @property
    def relative_path(self) -> str:
        return (
            "binance/futures/um/daily/klines/"
            f"{self.symbol}/{self.interval}/{self.filename}"
        )

    @property
    def request_id(self) -> str:
        return (
            f"binance.{self.symbol.lower()}.klines."
            f"{self.interval}.{self.day.isoformat()}"
        )

    @property
    def dataset_id(self) -> str:
        return self.request_id

    @property
    def instrument_id(self) -> str:
        base = self.symbol.removesuffix("USDT")
        return f"{base}-USDT-SWAP"


class BinancePublicArtifactIdentity(ReferenceContract):
    source_id: Literal["binance.public_data"] = "binance.public_data"
    coordinates_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_url: HttpUrl
    checksum_url: HttpUrl
    checksum_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_byte_size: int = Field(ge=1)
    artifact_media_type: str = Field(min_length=3, max_length=100)
    provider_last_modified_at: datetime
    observed_at: datetime
    terms_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_policy: Literal["provider_correctable"] = "provider_correctable"
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("artifact_url", "checksum_url")
    @classmethod
    def validate_urls(cls, value: HttpUrl) -> HttpUrl:
        parsed = urlparse(str(value))
        if (
            parsed.scheme != "https"
            or parsed.hostname != BINANCE_DATA_HOST
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Binance identity URLs must be exact reviewed HTTPS URLs")
        return value

    @field_validator("provider_last_modified_at", "observed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime, info) -> datetime:
        return require_utc(value, info.field_name)

    @field_validator("artifact_media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if value not in EXPECTED_MEDIA_TYPES:
            raise ValueError("unexpected Binance ZIP media type")
        return value

    @model_validator(mode="after")
    def validate_timing(self) -> "BinancePublicArtifactIdentity":
        if self.provider_last_modified_at > self.observed_at:
            raise ValueError("provider Last-Modified cannot be in the future")
        return self


def _exact_binance_url(value: str) -> str:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise BinanceReferencePreparationError(
            "Binance URL contains an invalid port"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != BINANCE_DATA_HOST
        or port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise BinanceReferencePreparationError(
            "Binance URL left the exact reviewed data host"
        )
    return value


def _media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _http_datetime(value: str | None) -> datetime:
    if not value:
        raise BinanceReferencePreparationError(
            "Binance artifact omitted Last-Modified"
        )
    try:
        parsed = datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT")
    except ValueError as exc:
        raise BinanceReferencePreparationError(
            "Binance artifact Last-Modified is invalid"
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def parse_binance_checksum(payload: bytes, expected_filename: str) -> str:
    if not payload or len(payload) > CHECKSUM_MAX_BYTES or b"\x00" in payload:
        raise BinanceReferencePreparationError(
            "Binance checksum sidecar size is invalid"
        )
    match = CHECKSUM_PATTERN.fullmatch(payload)
    if match is None:
        raise BinanceReferencePreparationError(
            "Binance checksum sidecar format is invalid"
        )
    try:
        filename = match.group(2).decode("ascii")
    except UnicodeDecodeError as exc:
        raise BinanceReferencePreparationError(
            "Binance checksum filename is not ASCII"
        ) from exc
    if filename != expected_filename:
        raise BinanceReferencePreparationError(
            "Binance checksum filename disagrees with reviewed coordinates"
        )
    return match.group(1).decode("ascii").lower()


async def _bounded_response_with_redirects(
    client: httpx.AsyncClient,
    method: Literal["GET", "HEAD"],
    initial_url: str,
    *,
    max_body_bytes: int,
    max_redirects: int = 2,
) -> tuple[dict[str, str], bytes, str, int]:
    current_url = _exact_binance_url(initial_url)
    redirect_count = 0
    while True:
        try:
            async with client.stream(
                method,
                current_url,
                headers={
                    "Accept-Encoding": "identity",
                    "User-Agent": "CTCC-V2-binance-reference-preparation/1",
                },
                follow_redirects=False,
            ) as response:
                if response.status_code in REDIRECT_STATUS_CODES:
                    if redirect_count >= max_redirects:
                        raise BinanceReferencePreparationError(
                            "Binance metadata redirect limit exceeded"
                        )
                    location = response.headers.get("location")
                    if not location:
                        raise BinanceReferencePreparationError(
                            "Binance metadata redirect omitted Location"
                        )
                    current_url = _exact_binance_url(
                        urljoin(current_url, location)
                    )
                    redirect_count += 1
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise BinanceReferencePreparationError(
                        "Binance metadata request failed with HTTP "
                        f"{response.status_code}"
                    )
                declared_length = response.headers.get("content-length")
                if method == "GET" and declared_length is not None:
                    try:
                        declared_bytes = int(declared_length)
                    except ValueError as exc:
                        raise BinanceReferencePreparationError(
                            "Binance metadata Content-Length is invalid"
                        ) from exc
                    if declared_bytes < 0 or declared_bytes > max_body_bytes:
                        raise BinanceReferencePreparationError(
                            "Binance metadata exceeded its declared byte limit"
                        )
                payload = bytearray()
                if method == "GET":
                    async for chunk in response.aiter_raw():
                        payload.extend(chunk)
                        if len(payload) > max_body_bytes:
                            raise BinanceReferencePreparationError(
                                "Binance metadata exceeded its streamed byte limit"
                            )
                return (
                    {key.lower(): value for key, value in response.headers.items()},
                    bytes(payload),
                    current_url,
                    redirect_count,
                )
        except httpx.HTTPError as exc:
            raise BinanceReferencePreparationError(
                f"Binance metadata transport failed: {type(exc).__name__}"
            ) from exc


def _real_terms_review(path: Path) -> Path:
    if path.is_symlink():
        raise BinanceReferencePreparationError(
            "terms review cannot be a symlink"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BinanceReferencePreparationError(
            "terms review does not exist"
        ) from exc
    if not resolved.is_file():
        raise BinanceReferencePreparationError(
            "terms review must be a real file"
        )
    return resolved


async def prepare_binance_kline_request(
    coordinates: BinanceKlineCoordinates,
    terms_review_path: Path,
    *,
    client: httpx.AsyncClient | None = None,
    clock: Clock | None = None,
) -> tuple[BinancePublicArtifactIdentity, ExternalArtifactAcquisitionRequest]:
    """Resolve the official checksum and size before any artifact GET."""

    observed_at = require_utc(
        (clock or (lambda: datetime.now(timezone.utc)))(),
        "observed_at",
    )
    terms_path = _real_terms_review(terms_review_path)
    terms_sha256 = sha256_file(terms_path)
    own_client = client is None
    transport_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        (
            checksum_headers,
            checksum_payload,
            checksum_url,
            _,
        ) = await _bounded_response_with_redirects(
            transport_client,
            "GET",
            coordinates.checksum_url,
            max_body_bytes=CHECKSUM_MAX_BYTES,
        )
        encoding = checksum_headers.get(
            "content-encoding", "identity"
        ).strip().lower()
        if encoding not in {"", "identity"}:
            raise BinanceReferencePreparationError(
                "encoded checksum responses are not accepted"
            )
        artifact_sha256 = parse_binance_checksum(
            checksum_payload,
            coordinates.filename,
        )
        (
            artifact_headers,
            artifact_payload,
            artifact_url,
            _,
        ) = await _bounded_response_with_redirects(
            transport_client,
            "HEAD",
            coordinates.download_url,
            max_body_bytes=0,
        )
        if artifact_payload:
            raise BinanceReferencePreparationError(
                "Binance HEAD response unexpectedly contained a body"
            )
        content_length = artifact_headers.get("content-length")
        try:
            artifact_byte_size = int(content_length or "")
        except ValueError as exc:
            raise BinanceReferencePreparationError(
                "Binance artifact Content-Length is missing or invalid"
            ) from exc
        if artifact_byte_size <= 0 or artifact_byte_size > ARTIFACT_MAX_BYTES:
            raise BinanceReferencePreparationError(
                "Binance artifact Content-Length is outside the reviewed limit"
            )
        artifact_media_type = _media_type(
            artifact_headers.get("content-type")
        )
        if artifact_media_type not in EXPECTED_MEDIA_TYPES:
            raise BinanceReferencePreparationError(
                "Binance artifact media type is outside the reviewed set"
            )
        last_modified = _http_datetime(
            artifact_headers.get("last-modified")
        )
        if checksum_url != coordinates.checksum_url:
            raise BinanceReferencePreparationError(
                "Binance checksum redirected away from reviewed coordinates"
            )
        if artifact_url != coordinates.download_url:
            raise BinanceReferencePreparationError(
                "Binance artifact redirected away from reviewed coordinates"
            )
    finally:
        if own_client:
            await transport_client.aclose()

    identity = BinancePublicArtifactIdentity(
        coordinates_sha256=coordinates.canonical_sha256(),
        artifact_url=artifact_url,
        checksum_url=checksum_url,
        checksum_payload_sha256=hashlib.sha256(
            checksum_payload
        ).hexdigest(),
        artifact_sha256=artifact_sha256,
        artifact_byte_size=artifact_byte_size,
        artifact_media_type=artifact_media_type,
        provider_last_modified_at=last_modified,
        observed_at=observed_at,
        terms_review_sha256=terms_sha256,
    )
    request = ExternalArtifactAcquisitionRequest(
        request_id=coordinates.request_id,
        source_id="binance.public_data",
        download_url=artifact_url,
        terms_url=BINANCE_TERMS_URL,
        terms_review_sha256=terms_sha256,
        terms_reviewed_at=observed_at,
        relative_path=coordinates.relative_path,
        expected_sha256=artifact_sha256,
        expected_byte_size=artifact_byte_size,
        expected_media_types=EXPECTED_MEDIA_TYPES,
        archive_kind=ArchiveKind.ZIP,
    )
    return identity, request
