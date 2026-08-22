from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.research.external_benchmarks.contracts import (
    ReferenceContract,
    ReferenceMetricBundle,
    require_utc,
)


REVIEWED_BATCH_SYMBOLS = ("BTCUSDT", "ETHUSDT")
REVIEWED_BATCH_FIRST_DAY = date(2024, 1, 1)
REVIEWED_BATCH_LAST_DAY = date(2026, 8, 21)
MAX_BATCH_ARTIFACTS = 192
EXPECTED_MINUTE_ROWS_PER_DAY = 1440


class BinanceBatchPartition(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    RETROSPECTIVE_HOLDOUT = "retrospective_holdout"


class BinanceBatchKlineCoordinates(ReferenceContract):
    """Reviewed coordinates for the bounded multi-day reference batch."""

    symbol: Literal["BTCUSDT", "ETHUSDT"]
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
        if not REVIEWED_BATCH_FIRST_DAY <= value <= REVIEWED_BATCH_LAST_DAY:
            raise ValueError(
                "batch day is outside the reviewed 2024-01-01 through 2026-08-21 range"
            )
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


class BinanceBatchWindow(ReferenceContract):
    partition: BinanceBatchPartition
    start_day: date
    end_day: date

    @model_validator(mode="after")
    def validate_window(self) -> "BinanceBatchWindow":
        if self.end_day < self.start_day:
            raise ValueError("batch window end cannot precede start")
        if (
            self.start_day < REVIEWED_BATCH_FIRST_DAY
            or self.end_day > REVIEWED_BATCH_LAST_DAY
        ):
            raise ValueError("batch window is outside the reviewed date range")
        return self

    @property
    def day_count(self) -> int:
        return (self.end_day - self.start_day).days + 1

    def days(self) -> tuple[date, ...]:
        return tuple(
            self.start_day + timedelta(days=offset) for offset in range(self.day_count)
        )


class BinanceBatchPlan(ReferenceContract):
    plan_id: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$",
    )
    symbols: tuple[Literal["BTCUSDT", "ETHUSDT"], ...] = Field(min_length=1)
    interval: Literal["1m"] = "1m"
    windows: tuple[BinanceBatchWindow, ...] = Field(min_length=1)
    selection_policy: Literal["fixed_non_overlapping_calendar_windows"] = (
        "fixed_non_overlapping_calendar_windows"
    )
    holdout_semantics: Literal["retrospective_not_prospective"] = (
        "retrospective_not_prospective"
    )
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("symbols")
    @classmethod
    def validate_symbols(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("batch symbols must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("batch symbols must use canonical sorted order")
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> "BinanceBatchPlan":
        partitions = [window.partition for window in self.windows]
        if len(partitions) != len(set(partitions)):
            raise ValueError("batch partitions must be unique")
        if tuple(self.windows) != tuple(
            sorted(self.windows, key=lambda item: item.start_day)
        ):
            raise ValueError("batch windows must use chronological order")
        for previous, current in zip(self.windows, self.windows[1:]):
            if current.start_day <= previous.end_day:
                raise ValueError("batch windows cannot overlap")
        if not 1 <= self.expected_artifact_count <= MAX_BATCH_ARTIFACTS:
            raise ValueError("batch artifact count exceeds the reviewed limit")
        return self

    @property
    def expected_artifact_count(self) -> int:
        return len(self.symbols) * sum(window.day_count for window in self.windows)

    def coordinate_items(
        self,
    ) -> tuple[tuple[BinanceBatchPartition, BinanceBatchKlineCoordinates], ...]:
        return tuple(
            (
                window.partition,
                BinanceBatchKlineCoordinates(
                    symbol=symbol,
                    interval=self.interval,
                    day=day,
                ),
            )
            for window in self.windows
            for symbol in self.symbols
            for day in window.days()
        )


def canonical_binance_batch_plan() -> BinanceBatchPlan:
    """Return the frozen v3 research split; it is not a trading universe."""

    return BinanceBatchPlan(
        plan_id="binance.btc_eth.1m.calendar_split.v1",
        symbols=("BTCUSDT", "ETHUSDT"),
        windows=(
            BinanceBatchWindow(
                partition=BinanceBatchPartition.DEVELOPMENT,
                start_day=date(2024, 1, 1),
                end_day=date(2024, 1, 30),
            ),
            BinanceBatchWindow(
                partition=BinanceBatchPartition.VALIDATION,
                start_day=date(2025, 1, 1),
                end_day=date(2025, 1, 30),
            ),
            BinanceBatchWindow(
                partition=BinanceBatchPartition.RETROSPECTIVE_HOLDOUT,
                start_day=date(2026, 7, 23),
                end_day=date(2026, 8, 21),
            ),
        ),
    )


def batch_evidence_prefix(
    coordinates: BinanceBatchKlineCoordinates,
) -> str:
    return (
        f"evidence/{coordinates.symbol.lower()}-"
        f"{coordinates.interval}-{coordinates.day.isoformat()}"
    )


class BinanceBatchPreparationEntry(ReferenceContract):
    partition: BinanceBatchPartition
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    interval: Literal["1m"] = "1m"
    day: date
    request_id: str = Field(min_length=3, max_length=160)
    identity_relative_path: str = Field(min_length=1, max_length=240)
    request_relative_path: str = Field(min_length=1, max_length=240)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_byte_size: int = Field(ge=1, le=1024 * 1024)
    provider_last_modified_at: datetime
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("provider_last_modified_at")
    @classmethod
    def validate_provider_time(cls, value: datetime) -> datetime:
        return require_utc(value, "provider_last_modified_at")

    @model_validator(mode="after")
    def validate_coordinates(self) -> "BinanceBatchPreparationEntry":
        coordinates = BinanceBatchKlineCoordinates(
            symbol=self.symbol,
            interval=self.interval,
            day=self.day,
        )
        if self.request_id != coordinates.request_id:
            raise ValueError("batch preparation request ID does not match")
        prefix = batch_evidence_prefix(coordinates)
        if self.identity_relative_path != f"{prefix}-identity.json":
            raise ValueError("batch identity path does not match")
        if self.request_relative_path != f"{prefix}-request.json":
            raise ValueError("batch request path does not match")
        return self


class BinanceBatchPreparation(ReferenceContract):
    plan_id: str = Field(min_length=3, max_length=160)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_at: datetime
    expected_artifact_count: int = Field(ge=1, le=MAX_BATCH_ARTIFACTS)
    total_expected_bytes: int = Field(ge=1)
    entries: tuple[BinanceBatchPreparationEntry, ...] = Field(min_length=1)
    all_urls_exact_reviewed_host: Literal[True] = True
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("prepared_at")
    @classmethod
    def validate_prepared_at(cls, value: datetime) -> datetime:
        return require_utc(value, "prepared_at")

    @model_validator(mode="after")
    def validate_preparation(self) -> "BinanceBatchPreparation":
        if len(self.entries) != self.expected_artifact_count:
            raise ValueError("batch preparation entry count does not match")
        request_ids = [entry.request_id for entry in self.entries]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("batch preparation request IDs must be unique")
        expected_bytes = sum(entry.artifact_byte_size for entry in self.entries)
        if self.total_expected_bytes != expected_bytes:
            raise ValueError("batch preparation byte total does not match")
        if (
            max(entry.provider_last_modified_at for entry in self.entries)
            > self.prepared_at
        ):
            raise ValueError("batch was prepared before provider availability")
        return self


class BinanceDailyMarketSummary(ReferenceContract):
    partition: BinanceBatchPartition
    dataset_id: str = Field(min_length=3, max_length=160)
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    day: date
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: Literal[1440] = 1440
    first_open: Decimal = Field(gt=0)
    last_close: Decimal = Field(gt=0)
    period_high: Decimal = Field(gt=0)
    period_low: Decimal = Field(gt=0)
    base_volume: Decimal = Field(ge=0)
    quote_volume: Decimal = Field(ge=0)
    trade_count: int = Field(ge=0)
    simple_return: Decimal
    observed_direction: Literal["rising", "flat", "falling"]
    descriptive_only: Literal[True] = True
    predictive_validity_claimed: Literal[False] = False
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_summary(self) -> "BinanceDailyMarketSummary":
        coordinates = BinanceBatchKlineCoordinates(
            symbol=self.symbol,
            day=self.day,
        )
        if (
            self.dataset_id != coordinates.dataset_id
            or self.instrument_id != coordinates.instrument_id
        ):
            raise ValueError("daily summary coordinates do not match")
        if self.period_high < max(self.first_open, self.last_close):
            raise ValueError("daily high cannot be below open or close")
        if self.period_low > min(self.first_open, self.last_close):
            raise ValueError("daily low cannot be above open or close")
        expected_return = (self.last_close / self.first_open) - Decimal("1")
        if self.simple_return != expected_return:
            raise ValueError("daily simple return does not match prices")
        expected_direction = (
            "rising"
            if self.simple_return > 0
            else "falling"
            if self.simple_return < 0
            else "flat"
        )
        if self.observed_direction != expected_direction:
            raise ValueError("daily observed direction does not match return")
        if any(
            not value.is_finite()
            for value in (
                self.first_open,
                self.last_close,
                self.period_high,
                self.period_low,
                self.base_volume,
                self.quote_volume,
                self.simple_return,
            )
        ):
            raise ValueError("daily summary values must be finite")
        return self


class BinancePartitionMarketSummary(ReferenceContract):
    partition: BinanceBatchPartition
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    start_day: date
    end_day: date
    day_count: int = Field(ge=2)
    minute_row_count: int = Field(ge=2880)
    first_open: Decimal = Field(gt=0)
    last_close: Decimal = Field(gt=0)
    period_high: Decimal = Field(gt=0)
    period_low: Decimal = Field(gt=0)
    base_volume: Decimal = Field(ge=0)
    quote_volume: Decimal = Field(ge=0)
    trade_count: int = Field(ge=0)
    close_path_metrics: ReferenceMetricBundle
    theil_sen_log_slope_per_day: Decimal
    path_efficiency: Decimal = Field(ge=0, le=1)
    observed_direction: Literal["rising", "flat", "falling"]
    descriptive_only: Literal[True] = True
    predictive_validity_claimed: Literal[False] = False
    strategy_evaluated: Literal[False] = False
    costs_evaluated: Literal[False] = False
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_partition_summary(self) -> "BinancePartitionMarketSummary":
        expected_instrument = (
            "BTC-USDT-SWAP" if self.symbol == "BTCUSDT" else "ETH-USDT-SWAP"
        )
        if self.instrument_id != expected_instrument:
            raise ValueError("partition instrument does not match symbol")
        if self.end_day < self.start_day:
            raise ValueError("partition summary end cannot precede start")
        if self.day_count != (self.end_day - self.start_day).days + 1:
            raise ValueError("partition summary day count does not match")
        if self.minute_row_count != self.day_count * 1440:
            raise ValueError("partition minute row count does not match")
        if self.close_path_metrics.sample_size != self.day_count:
            raise ValueError("partition metric sample size does not match")
        if self.period_high < max(self.first_open, self.last_close):
            raise ValueError("partition high cannot be below open or close")
        if self.period_low > min(self.first_open, self.last_close):
            raise ValueError("partition low cannot be above open or close")
        if not self.theil_sen_log_slope_per_day.is_finite():
            raise ValueError("partition slope must be finite")
        expected_direction = (
            "rising"
            if self.theil_sen_log_slope_per_day > 0
            else "falling"
            if self.theil_sen_log_slope_per_day < 0
            else "flat"
        )
        if self.observed_direction != expected_direction:
            raise ValueError("partition observed direction does not match slope")
        return self


class BinanceBatchResultEntry(ReferenceContract):
    partition: BinanceBatchPartition
    request_id: str = Field(min_length=3, max_length=160)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generic_quality_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_quality_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    daily_summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: Literal[1440] = 1440
    passed: Literal[True] = True
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


class BinanceBatchEvidence(ReferenceContract):
    plan_id: str = Field(min_length=3, max_length=160)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preparation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    expected_artifact_count: int = Field(ge=1, le=MAX_BATCH_ARTIFACTS)
    completed_artifact_count: int = Field(ge=1, le=MAX_BATCH_ARTIFACTS)
    total_minute_rows: int = Field(ge=1440)
    entries: tuple[BinanceBatchResultEntry, ...] = Field(min_length=1)
    partition_summaries: tuple[BinancePartitionMarketSummary, ...] = Field(min_length=1)
    partition_overlap_count: Literal[0] = 0
    holdout_semantics: Literal["retrospective_not_prospective"] = (
        "retrospective_not_prospective"
    )
    strategy_evaluated: Literal[False] = False
    costs_evaluated: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    passed: Literal[True] = True
    reference_only: Literal[True] = True
    promotion_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        return require_utc(value, "generated_at")

    @model_validator(mode="after")
    def validate_batch_evidence(self) -> "BinanceBatchEvidence":
        if self.completed_artifact_count != self.expected_artifact_count:
            raise ValueError("batch evidence is incomplete")
        if len(self.entries) != self.completed_artifact_count:
            raise ValueError("batch result entry count does not match")
        request_ids = [entry.request_id for entry in self.entries]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("batch result request IDs must be unique")
        if self.total_minute_rows != sum(entry.row_count for entry in self.entries):
            raise ValueError("batch total minute rows do not match")
        if self.total_minute_rows != sum(
            summary.minute_row_count for summary in self.partition_summaries
        ):
            raise ValueError("partition minute rows do not match batch total")
        summary_keys = [
            (summary.partition, summary.symbol) for summary in self.partition_summaries
        ]
        if len(summary_keys) != len(set(summary_keys)):
            raise ValueError("partition summaries must have unique keys")
        by_symbol: dict[str, list[BinancePartitionMarketSummary]] = {}
        for summary in self.partition_summaries:
            by_symbol.setdefault(summary.symbol, []).append(summary)
        for summaries in by_symbol.values():
            ordered = sorted(summaries, key=lambda item: item.start_day)
            for previous, current in zip(ordered, ordered[1:]):
                if current.start_day <= previous.end_day:
                    raise ValueError("partition summaries cannot overlap")
        return self
