from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.mie.validation.archive_batch import (
    ArchiveBatchPlan,
    ArchiveBatchSource,
    ArchiveBatchValidationError,
    load_archive_batch_rehearsal,
)
from app.mie.validation.availability import ArchiveObservationReceipt
from app.mie.validation.contracts import (
    DatasetPartition,
    PartitionWindow,
    PurgedWalkForwardSplit,
)

START = datetime(2024, 1, 1, tzinfo=UTC)


def valid_plan(*, symbols: tuple[str, ...] = ("BTCUSDT",)) -> ArchiveBatchPlan:
    return ArchiveBatchPlan(
        plan_id="gate3:archive:batch:fixture:v1",
        created_at=START + timedelta(days=7),
        split=PurgedWalkForwardSplit(
            development=PartitionWindow(
                partition=DatasetPartition.DEVELOPMENT,
                start_at=START,
                end_at=START + timedelta(days=2),
            ),
            validation=PartitionWindow(
                partition=DatasetPartition.VALIDATION,
                start_at=START + timedelta(days=3),
                end_at=START + timedelta(days=4),
            ),
            holdout=PartitionWindow(
                partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
                start_at=START + timedelta(days=5),
                end_at=START + timedelta(days=6),
            ),
            purge_seconds=60,
            embargo_seconds=60,
            max_feature_dependency_seconds=60,
            label_dependency_seconds=60,
        ),
        symbols=symbols,
        expected_artifact_count=3 * len(symbols),
        expected_rows=4320 * len(symbols),
    )


def metadata_source(
    *,
    day_offset: int,
    partition: DatasetPartition,
    symbol: str = "BTCUSDT",
) -> ArchiveBatchSource:
    # Deliberately not a ZIP: metadata failures must be rejected before parsing.
    day = START + timedelta(days=day_offset)
    payload = f"not-parsed:{partition.value}:{symbol}:{day.date()}".encode()
    receipt = ArchiveObservationReceipt(
        archive_sha256=hashlib.sha256(payload).hexdigest(),
        archive_byte_size=len(payload),
        symbol=symbol,
        instrument_id=f"{symbol.removesuffix('USDT')}-USDT-SWAP",
        partition=partition,
        day_start_at=day,
        member_name=f"{symbol}-1m-{day.date().isoformat()}.csv",
        observed_at=START + timedelta(days=8),
        retrieved_at=START + timedelta(days=8, seconds=1),
    )
    return ArchiveBatchSource(
        archive_bytes=payload,
        receipt=receipt,
        receipt_sha256=receipt.canonical_sha256(),
    )


def valid_sources(
    *, symbols: tuple[str, ...] = ("BTCUSDT",)
) -> tuple[ArchiveBatchSource, ...]:
    return tuple(
        metadata_source(day_offset=day, partition=partition, symbol=symbol)
        for partition, days in (
            (DatasetPartition.DEVELOPMENT, (0, 1)),
            (DatasetPartition.VALIDATION, (3,)),
        )
        for day in days
        for symbol in symbols
    )


def test_plan_derives_complete_development_validation_counts_only() -> None:
    plan = valid_plan()
    assert plan.expected_artifact_count == 3
    assert plan.expected_rows == 4320
    assert plan.runtime_consumers == 0
    assert plan.execution_authority is False
    assert plan.predictive_oos_eligible is False
    assert ArchiveBatchPlan.model_validate_json(plan.canonical_json_bytes()) == plan
    with pytest.raises(ValidationError, match="frozen"):
        plan.execution_authority = True  # type: ignore[misc]
    two_symbols = valid_plan(symbols=("BTCUSDT", "ETHUSDT"))
    assert two_symbols.expected_artifact_count == 6
    assert two_symbols.expected_rows == 8640


@pytest.mark.parametrize("partition", ["development", "validation", "holdout"])
@pytest.mark.parametrize("boundary", ["start_at", "end_at"])
@pytest.mark.parametrize("delta", [timedelta(hours=1), timedelta(microseconds=1)])
def test_every_calendar_boundary_requires_exact_utc_midnight(
    partition: str, boundary: str, delta: timedelta
) -> None:
    payload = valid_plan().model_dump()
    payload["split"][partition][boundary] += delta
    with pytest.raises(ValidationError):
        ArchiveBatchPlan.model_validate(payload)


@pytest.mark.parametrize("offset", [None, timezone(timedelta(hours=8))])
def test_plan_rejects_naive_and_non_utc_timestamps(offset) -> None:
    payload = valid_plan().model_dump()
    payload["created_at"] = payload["created_at"].replace(tzinfo=offset)
    with pytest.raises(ValidationError):
        ArchiveBatchPlan.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "purge_seconds",
        "embargo_seconds",
        "max_feature_dependency_seconds",
        "label_dependency_seconds",
    ],
)
def test_plan_requires_minute_aligned_dependencies_and_separation(field: str) -> None:
    payload = valid_plan().model_dump()
    # Keep the generic split's dependency inequalities valid, isolating cadence.
    payload["split"]["purge_seconds"] = 120
    payload["split"]["embargo_seconds"] = 120
    payload["split"][field] = 61
    with pytest.raises(ValidationError):
        ArchiveBatchPlan.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_artifact_count", 2),
        ("expected_artifact_count", 4),
        ("expected_artifact_count", True),
        ("expected_rows", 4319),
        ("expected_rows", 4321),
        ("expected_rows", True),
        ("execution_authority", True),
        ("runtime_consumers", 1),
        ("predictive_oos_eligible", True),
    ],
)
def test_plan_rejects_count_disagreement_and_authority_escalation(
    field: str, value: object
) -> None:
    payload = valid_plan().model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        ArchiveBatchPlan.model_validate(payload)


@pytest.mark.parametrize(
    "symbols", [("ETHUSDT", "BTCUSDT"), ("BTCUSDT", "BTCUSDT"), ("SOLUSDT",), ()]
)
def test_plan_rejects_unsorted_duplicate_or_unsupported_symbols(symbols) -> None:
    payload = valid_plan().model_dump()
    payload.update(
        symbols=symbols,
        expected_artifact_count=3 * len(symbols),
        expected_rows=4320 * len(symbols),
    )
    with pytest.raises(ValidationError):
        ArchiveBatchPlan.model_validate(payload)


def test_plan_rejects_more_than_256_daily_artifacts() -> None:
    payload = valid_plan().model_dump()
    payload["split"]["development"]["end_at"] = START + timedelta(days=256)
    payload["split"]["validation"].update(
        start_at=START + timedelta(days=257),
        end_at=START + timedelta(days=258),
    )
    payload["split"]["holdout"].update(
        start_at=START + timedelta(days=259),
        end_at=START + timedelta(days=260),
    )
    payload["created_at"] = START + timedelta(days=261)
    payload.update(expected_artifact_count=257, expected_rows=257 * 1440)
    with pytest.raises(ValidationError):
        ArchiveBatchPlan.model_validate(payload)


@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "extra",
        "duplicate_same_count",
        "holdout_mislabeled_development",
        "gap_mislabeled_development",
        "wrong_partition",
        "wrong_symbol",
        "wrong_day_order",
        "wrong_partition_order",
        "wrong_symbol_order",
    ],
)
def test_all_metadata_is_checked_before_any_archive_is_parsed(
    monkeypatch, attack
) -> None:
    symbols = ("BTCUSDT", "ETHUSDT") if attack == "wrong_symbol_order" else ("BTCUSDT",)
    plan = valid_plan(symbols=symbols)
    sources = valid_sources(symbols=symbols)
    if attack == "missing":
        sources = sources[:-1]
    elif attack == "extra":
        sources += (sources[0],)
    elif attack == "duplicate_same_count":
        sources = (sources[0], sources[0], sources[2])
    elif attack in {"holdout_mislabeled_development", "gap_mislabeled_development"}:
        day = 5 if attack == "holdout_mislabeled_development" else 2
        sources = (
            sources[0],
            metadata_source(day_offset=day, partition=DatasetPartition.DEVELOPMENT),
            sources[2],
        )
    elif attack == "wrong_partition":
        sources = (
            sources[0],
            metadata_source(day_offset=1, partition=DatasetPartition.VALIDATION),
            sources[2],
        )
    elif attack == "wrong_symbol":
        sources = (
            sources[0],
            metadata_source(
                day_offset=1, partition=DatasetPartition.DEVELOPMENT, symbol="ETHUSDT"
            ),
            sources[2],
        )
    elif attack == "wrong_day_order":
        sources = (sources[1], sources[0], sources[2])
    elif attack == "wrong_partition_order":
        sources = (sources[2], sources[0], sources[1])
    else:
        # Preserve each day, but reverse the canonical instrument order within it.
        sources = (sources[1], sources[0], *sources[2:])
    calls = []

    def forbidden_parser(*args, **kwargs):
        calls.append(True)
        raise AssertionError("invalid batch metadata reached the ZIP parser")

    monkeypatch.setattr(
        "app.mie.validation.archive_batch.load_binance_archive_rehearsal",
        forbidden_parser,
    )
    with pytest.raises(ArchiveBatchValidationError):
        load_archive_batch_rehearsal(
            sources, plan=plan, expected_plan_sha256=plan.canonical_sha256()
        )
    assert calls == []


@pytest.mark.parametrize("symbols", [("BTCUSDT",), ("BTCUSDT", "ETHUSDT")])
def test_valid_complete_metadata_reaches_the_parser(monkeypatch, symbols) -> None:
    plan = valid_plan(symbols=symbols)
    calls = []

    class ReachedParser(Exception):
        pass

    def sentinel_parser(*args, **kwargs):
        calls.append(True)
        raise ReachedParser

    monkeypatch.setattr(
        "app.mie.validation.archive_batch.load_binance_archive_rehearsal",
        sentinel_parser,
    )
    with pytest.raises(ReachedParser):
        load_archive_batch_rehearsal(
            valid_sources(symbols=symbols),
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
        )
    assert calls == [True]


@pytest.mark.parametrize("pin", ["", "not-a-hash", "A" * 64, "0" * 64, None])
def test_external_plan_pin_is_checked_before_zip_parsing(monkeypatch, pin) -> None:
    plan = valid_plan()
    calls = []

    def forbidden_parser(*args, **kwargs):
        calls.append(True)
        raise AssertionError("untrusted plan reached the ZIP parser")

    monkeypatch.setattr(
        "app.mie.validation.archive_batch.load_binance_archive_rehearsal",
        forbidden_parser,
    )
    with pytest.raises(ArchiveBatchValidationError):
        load_archive_batch_rehearsal(
            valid_sources(), plan=plan, expected_plan_sha256=pin
        )
    assert calls == []


def test_fully_rehashed_changed_plan_cannot_replace_the_external_pin(
    monkeypatch,
) -> None:
    original = valid_plan()
    payload = original.model_dump()
    payload["split"]["holdout"]["end_at"] += timedelta(days=1)
    replacement = ArchiveBatchPlan.model_validate(payload)
    assert replacement.canonical_sha256() != original.canonical_sha256()
    calls = []

    def forbidden_parser(*args, **kwargs):
        calls.append(True)
        raise AssertionError("replacement plan reached the ZIP parser")

    monkeypatch.setattr(
        "app.mie.validation.archive_batch.load_binance_archive_rehearsal",
        forbidden_parser,
    )
    with pytest.raises(ArchiveBatchValidationError):
        load_archive_batch_rehearsal(
            valid_sources(),
            plan=replacement,
            expected_plan_sha256=original.canonical_sha256(),
        )
    assert calls == []
