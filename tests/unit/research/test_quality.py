from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.research.external_benchmarks import (
    DatasetQualityPolicy,
    profile_dataset_records,
)
from tests.unit.research.helpers import END, RETRIEVED, START, trade_manifest


def milliseconds(value) -> int:
    return int(value.timestamp() * 1000)


def test_strict_quality_profile_accepts_complete_ordered_records() -> None:
    manifest = trade_manifest()
    records = (
        {
            "trade_id": "1",
            "timestamp": milliseconds(START),
            "price": "100",
            "quantity": "1",
        },
        {
            "trade_id": "2",
            "timestamp": milliseconds(START + timedelta(seconds=1)),
            "price": "101",
            "quantity": "2",
        },
        {
            "trade_id": "3",
            "timestamp": milliseconds(END),
            "price": "102",
            "quantity": "3",
        },
    )

    report = profile_dataset_records(
        manifest,
        records,
        generated_at=RETRIEVED,
    )

    assert report.passed is True
    assert report.failure_codes == ()
    assert report.observed_row_count == 3
    assert report.manifest_sha256 == manifest.canonical_sha256()
    assert report.execution_authority is False


def test_quality_profile_reports_missing_duplicate_temporal_and_numeric_failures() -> None:
    manifest = trade_manifest(row_count=4)
    records = (
        {
            "trade_id": "1",
            "timestamp": milliseconds(START + timedelta(seconds=1)),
            "price": "100",
            "quantity": "1",
        },
        {
            "trade_id": "1",
            "timestamp": milliseconds(START + timedelta(milliseconds=500)),
            "price": "101",
            "quantity": "1",
        },
        {
            "trade_id": "3",
            "timestamp": "invalid",
            "quantity": "-1",
        },
        {
            "trade_id": "4",
            "timestamp": milliseconds(END + timedelta(seconds=1)),
            "price": "103",
            "quantity": "1",
        },
    )

    report = profile_dataset_records(
        manifest,
        records,
        generated_at=RETRIEVED,
    )

    assert report.passed is False
    assert report.missing_required_rows == 1
    assert report.duplicate_key_rows == 1
    assert report.invalid_timestamp_rows == 1
    assert report.out_of_order_rows == 1
    assert report.out_of_window_rows == 1
    assert report.nonpositive_numeric_rows == 1
    assert set(report.failure_codes) == {
        "missing_required_rate_exceeded",
        "duplicate_key_rate_exceeded",
        "invalid_timestamp_rate_exceeded",
        "out_of_order_rate_exceeded",
        "out_of_window_rate_exceeded",
        "nonpositive_numeric_rate_exceeded",
    }


def test_explicit_quality_policy_can_document_but_not_repair_known_rates() -> None:
    manifest = trade_manifest(row_count=2)
    records = (
        {
            "trade_id": "1",
            "timestamp": milliseconds(START + timedelta(seconds=1)),
            "price": "100",
            "quantity": "1",
        },
        {
            "trade_id": "1",
            "timestamp": milliseconds(START),
            "price": "101",
            "quantity": "1",
        },
    )
    policy = DatasetQualityPolicy(
        max_duplicate_key_rate=Decimal("0.5"),
        max_out_of_order_rate=Decimal("1"),
    )

    report = profile_dataset_records(
        manifest,
        records,
        generated_at=RETRIEVED,
        policy=policy,
    )

    assert report.passed is True
    assert report.duplicate_key_rows == 1
    assert report.out_of_order_rows == 1
    assert report.duplicate_key_rate == Decimal("0.5")
    assert report.out_of_order_rate == Decimal("1")


def test_quality_report_rejects_forged_rates() -> None:
    manifest = trade_manifest()
    records = tuple(
        {
            "trade_id": str(index),
            "timestamp": milliseconds(START + timedelta(seconds=index - 1)),
            "price": "100",
            "quantity": "1",
        }
        for index in range(1, 4)
    )
    report = profile_dataset_records(
        manifest,
        records,
        generated_at=RETRIEVED,
    )
    with pytest.raises(ValidationError, match="rates must match"):
        type(report).model_validate(
            {**report.model_dump(), "duplicate_key_rate": Decimal("0.5")}
        )
