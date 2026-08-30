from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, HttpUrl, field_validator, model_validator

from app.research.external_benchmarks.contracts import (
    DatasetKind,
    ExternalArtifactAcquisitionRequest,
    ExternalDatasetManifest,
    IntendedUse,
    ReferenceContract,
    PublishedBenchmarkRecord,
    SourceKind,
)


class ReferenceSourceDescriptor(ReferenceContract):
    source_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    )
    provider: str = Field(min_length=1, max_length=120)
    source_kind: SourceKind
    official_url: HttpUrl
    approved_hosts: tuple[str, ...] = Field(min_length=1)
    supported_dataset_kinds: tuple[DatasetKind, ...] = Field(min_length=1)
    intended_uses: tuple[IntendedUse, ...] = Field(min_length=1)
    terms_review_required: Literal[True] = True
    reference_only: Literal[True] = True
    execution_authority: Literal[False] = False

    @field_validator("official_url")
    @classmethod
    def validate_official_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https" or value.username or value.password:
            raise ValueError("catalog URLs must be credential-free HTTPS")
        return value

    @model_validator(mode="after")
    def validate_unique_values(self) -> "ReferenceSourceDescriptor":
        if len(self.supported_dataset_kinds) != len(
            set(self.supported_dataset_kinds)
        ):
            raise ValueError("supported dataset kinds must be unique")
        if len(self.intended_uses) != len(set(self.intended_uses)):
            raise ValueError("catalog intended uses must be unique")
        if len(self.approved_hosts) != len(set(self.approved_hosts)) or any(
            not host or host != host.lower() or "/" in host
            for host in self.approved_hosts
        ):
            raise ValueError("approved hosts must be unique lowercase hostnames")
        return self


_SOURCES = (
    ReferenceSourceDescriptor(
        source_id="okx.historical_data",
        provider="OKX",
        source_kind=SourceKind.EXCHANGE,
        official_url="https://www.okx.com/historical-data",
        approved_hosts=("okx.com",),
        supported_dataset_kinds=(
            DatasetKind.TRADE,
            DatasetKind.CANDLE,
            DatasetKind.FUNDING,
            DatasetKind.ORDER_BOOK,
        ),
        intended_uses=(
            IntendedUse.DATA_QUALITY_REFERENCE,
            IntendedUse.EXECUTION_CALIBRATION,
            IntendedUse.DETERMINISTIC_REPLAY,
            IntendedUse.RESEARCH_BASELINE,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="binance.public_data",
        provider="Binance",
        source_kind=SourceKind.EXCHANGE,
        official_url="https://github.com/binance/binance-public-data",
        approved_hosts=("github.com", "data.binance.vision"),
        supported_dataset_kinds=(
            DatasetKind.TRADE,
            DatasetKind.CANDLE,
            DatasetKind.FUNDING,
        ),
        intended_uses=(
            IntendedUse.DATA_QUALITY_REFERENCE,
            IntendedUse.RESEARCH_BASELINE,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="nasdaq.totalview_itch.sample",
        provider="Nasdaq",
        source_kind=SourceKind.MARKET_INFRASTRUCTURE,
        official_url="https://www.nasdaqtrader.com/Trader.aspx?id=ITCH",
        approved_hosts=("nasdaqtrader.com", "nasdaq.com"),
        supported_dataset_kinds=(DatasetKind.ORDER_BOOK,),
        intended_uses=(
            IntendedUse.DETERMINISTIC_REPLAY,
            IntendedUse.EXECUTION_CALIBRATION,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="lobster.sample",
        provider="LOBSTER",
        source_kind=SourceKind.ACADEMIC,
        official_url="https://lobsterdata.com/",
        approved_hosts=("lobsterdata.com",),
        supported_dataset_kinds=(DatasetKind.ORDER_BOOK,),
        intended_uses=(
            IntendedUse.DETERMINISTIC_REPLAY,
            IntendedUse.EXECUTION_CALIBRATION,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="quantconnect.lean.regression",
        provider="QuantConnect",
        source_kind=SourceKind.OPEN_SOURCE_ENGINE,
        official_url="https://github.com/QuantConnect/Lean",
        approved_hosts=("github.com", "quantconnect.com"),
        supported_dataset_kinds=(DatasetKind.ENGINE_REGRESSION,),
        intended_uses=(
            IntendedUse.METRIC_PARITY,
            IntendedUse.DETERMINISTIC_REPLAY,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="nautilustrader.backtesting",
        provider="NautilusTrader",
        source_kind=SourceKind.OPEN_SOURCE_ENGINE,
        official_url="https://nautilustrader.io/docs/latest/concepts/backtesting/",
        approved_hosts=("nautilustrader.io", "github.com"),
        supported_dataset_kinds=(
            DatasetKind.ENGINE_REGRESSION,
            DatasetKind.ORDER_BOOK,
        ),
        intended_uses=(
            IntendedUse.METRIC_PARITY,
            IntendedUse.DETERMINISTIC_REPLAY,
            IntendedUse.EXECUTION_CALIBRATION,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="microsoft.qlib.benchmarks",
        provider="Microsoft",
        source_kind=SourceKind.OPEN_SOURCE_ENGINE,
        official_url="https://github.com/microsoft/qlib/tree/main/examples/benchmarks",
        approved_hosts=("github.com",),
        supported_dataset_kinds=(DatasetKind.MODEL_BENCHMARK,),
        intended_uses=(
            IntendedUse.METRIC_PARITY,
            IntendedUse.RESEARCH_BASELINE,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="cftc.cot",
        provider="U.S. Commodity Futures Trading Commission",
        source_kind=SourceKind.REGULATORY,
        official_url="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
        approved_hosts=("cftc.gov",),
        supported_dataset_kinds=(DatasetKind.POSITIONING,),
        intended_uses=(
            IntendedUse.DATA_QUALITY_REFERENCE,
            IntendedUse.RESEARCH_BASELINE,
        ),
    ),
    ReferenceSourceDescriptor(
        source_id="fred.vintage",
        provider="Federal Reserve Bank of St. Louis",
        source_kind=SourceKind.MACROECONOMIC,
        official_url="https://fred.stlouisfed.org/docs/api/fred/",
        approved_hosts=("stlouisfed.org",),
        supported_dataset_kinds=(DatasetKind.MACRO_VINTAGE,),
        intended_uses=(
            IntendedUse.DATA_QUALITY_REFERENCE,
            IntendedUse.RESEARCH_BASELINE,
        ),
    ),
)

REFERENCE_SOURCE_CATALOG: dict[str, ReferenceSourceDescriptor] = {
    source.source_id: source for source in _SOURCES
}

if len(REFERENCE_SOURCE_CATALOG) != len(_SOURCES):
    raise RuntimeError("duplicate external benchmark source_id")


def reference_source(source_id: str) -> ReferenceSourceDescriptor:
    try:
        return REFERENCE_SOURCE_CATALOG[source_id]
    except KeyError as exc:
        raise ValueError("external benchmark source is not reviewed") from exc


def _validate_provider_url(
    name: str,
    url: HttpUrl,
    descriptor: ReferenceSourceDescriptor,
) -> None:
    host = (urlparse(str(url)).hostname or "").lower()
    if not any(
        host == approved or host.endswith(f".{approved}")
        for approved in descriptor.approved_hosts
    ):
        raise ValueError(f"{name} host is outside reviewed provider scope")


def validate_manifest_source(
    manifest: ExternalDatasetManifest,
) -> ReferenceSourceDescriptor:
    descriptor = reference_source(manifest.source_id)
    if descriptor.source_kind != manifest.source_kind:
        raise ValueError("manifest source kind disagrees with reviewed catalog")
    if manifest.dataset_kind not in descriptor.supported_dataset_kinds:
        raise ValueError("manifest dataset kind is not reviewed for its source")
    if not set(manifest.intended_uses).issubset(descriptor.intended_uses):
        raise ValueError("manifest intended use exceeds the reviewed source scope")
    for name, url in (
        ("source_url", manifest.source_url),
        ("terms_url", manifest.terms_url),
    ):
        _validate_provider_url(f"manifest {name}", url, descriptor)
    return descriptor


def validate_published_benchmark_source(
    record: PublishedBenchmarkRecord,
) -> ReferenceSourceDescriptor:
    descriptor = reference_source(record.source_id)
    _validate_provider_url("benchmark source_url", record.source_url, descriptor)
    return descriptor


def validate_acquisition_source(
    request: ExternalArtifactAcquisitionRequest,
) -> ReferenceSourceDescriptor:
    descriptor = reference_source(request.source_id)
    _validate_provider_url(
        "acquisition download_url",
        request.download_url,
        descriptor,
    )
    _validate_provider_url(
        "acquisition terms_url",
        request.terms_url,
        descriptor,
    )
    return descriptor
