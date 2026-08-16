from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.research.external_benchmarks import (
    BenchmarkMetric,
    BenchmarkRunStatus,
    DatasetArtifact,
    DatasetWindow,
    ExternalBenchmarkRun,
    ExternalDatasetManifest,
    PublishedBenchmarkRecord,
    ReproducibilityLevel,
)
from tests.unit.research.helpers import AVAILABLE, END, RETRIEVED, START, trade_manifest


SHA = "a" * 64
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_manifest_is_frozen_strict_and_reference_only() -> None:
    manifest = trade_manifest()
    assert manifest.reference_only is True
    assert manifest.promotion_eligible is False
    assert manifest.execution_authority is False
    assert len(manifest.canonical_sha256()) == 64

    with pytest.raises(ValidationError):
        ExternalDatasetManifest.model_validate(
            {**manifest.model_dump(), "execution_authority": True}
        )
    with pytest.raises(ValidationError):
        ExternalDatasetManifest.model_validate(
            {**manifest.model_dump(), "unexpected": "field"}
        )


def test_dataset_window_enforces_point_in_time_availability() -> None:
    with pytest.raises(ValidationError, match="before its final event"):
        DatasetWindow(
            start=START,
            end=END,
            available_at=END - timedelta(seconds=1),
        )

    payload = trade_manifest().model_dump()
    payload["retrieved_at"] = AVAILABLE - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="retrieved before"):
        ExternalDatasetManifest.model_validate(payload)


def test_manifest_rejects_unsafe_paths_fields_and_transport() -> None:
    with pytest.raises(ValidationError, match="below its dataset root"):
        DatasetArtifact(
            relative_path="../escape.csv",
            sha256=SHA,
            byte_size=1,
            row_count=1,
            media_type="text/csv",
        )

    payload = trade_manifest().model_dump()
    payload["source_url"] = "http://www.okx.com/historical-data"
    with pytest.raises(ValidationError, match="HTTPS"):
        ExternalDatasetManifest.model_validate(payload)

    payload = trade_manifest().model_dump()
    payload["positive_numeric_fields"] = ("unknown",)
    with pytest.raises(ValidationError, match="contained in fields"):
        ExternalDatasetManifest.model_validate(payload)


def test_manifest_rejects_row_count_and_artifact_identity_mismatch() -> None:
    payload = trade_manifest().model_dump()
    payload["row_count"] = 4
    with pytest.raises(ValidationError, match="artifact row counts"):
        ExternalDatasetManifest.model_validate(payload)

    payload = trade_manifest().model_dump()
    payload["artifacts"] = payload["artifacts"] * 2
    payload["row_count"] = 6
    with pytest.raises(ValidationError, match="artifact paths"):
        ExternalDatasetManifest.model_validate(payload)


def test_public_benchmark_record_can_never_promote_or_execute() -> None:
    record = PublishedBenchmarkRecord(
        reference_id="qlib.alpha158.lightgbm.official",
        source_id="microsoft.qlib.benchmarks",
        project_name="Qlib Alpha158 LightGBM benchmark",
        source_url="https://github.com/microsoft/qlib/tree/main/examples/benchmarks",
        dataset_id="qlib.alpha158.csi300",
        model_or_engine_version="main-2026-08-16",
        metric_spec_version="qlib-benchmark-v1",
        published_at=NOW,
        retrieved_at=NOW,
        seed_count=20,
        reproducibility=ReproducibilityLevel.DATA_AND_CODE_AVAILABLE,
        source_artifact_sha256="b" * 64,
        metrics=(
            BenchmarkMetric(
                name="information_coefficient_mean",
                value=Decimal("0.031"),
                unit="ratio",
            ),
        ),
    )
    assert record.permitted_use == "calculation_reference_only"
    assert record.promotion_eligible is False
    assert record.execution_authority is False

    with pytest.raises(ValidationError):
        PublishedBenchmarkRecord.model_validate(
            {**record.model_dump(), "promotion_eligible": True}
        )


def test_benchmark_run_requires_consistent_status_and_unique_metrics() -> None:
    run = ExternalBenchmarkRun(
        run_id="external-benchmark-2026-01-02",
        dataset_id=trade_manifest().dataset_id,
        manifest_sha256=SHA,
        quality_report_sha256="b" * 64,
        source_tree_sha="c" * 40,
        configuration_sha256="d" * 64,
        metric_spec_version="ctcc-reference-metrics-v1",
        seed=7,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        status=BenchmarkRunStatus.PASSED,
        metrics=(
            BenchmarkMetric(
                name="total_return",
                value=Decimal("0.01"),
                unit="ratio",
            ),
        ),
    )
    assert run.execution_authority is False
    assert run.promotion_eligible is False

    payload = run.model_dump()
    payload["status"] = BenchmarkRunStatus.FAILED
    with pytest.raises(ValidationError, match="requires failure codes"):
        ExternalBenchmarkRun.model_validate(payload)

    payload = run.model_dump()
    payload["metrics"] = payload["metrics"] * 2
    with pytest.raises(ValidationError, match="metric names"):
        ExternalBenchmarkRun.model_validate(payload)
