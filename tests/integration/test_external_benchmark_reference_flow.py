from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.research.external_benchmarks import (
    BenchmarkMetric,
    BenchmarkRunStatus,
    ExternalBenchmarkRun,
    PublishedBenchmarkRecord,
    ReproducibilityLevel,
    calculate_reference_return_metrics,
    profile_dataset_records,
    validate_published_benchmark_source,
    verify_dataset_artifacts,
)
from tests.unit.research.helpers import END, RETRIEVED, START, trade_manifest


@pytest.mark.integration
def test_public_reference_flows_to_replayable_non_promoting_benchmark(
    tmp_path: Path,
) -> None:
    payload = b"1,1767225600000,100,1\n2,1767225601000,101,1\n3,1767225602000,102,1\n"
    manifest = trade_manifest(payload=payload)
    artifact = tmp_path / "raw" / "trades.csv"
    artifact.parent.mkdir()
    artifact.write_bytes(payload)
    receipts = verify_dataset_artifacts(manifest, tmp_path)
    records = (
        {
            "trade_id": "1",
            "timestamp": int(START.timestamp() * 1000),
            "price": "100",
            "quantity": "1",
        },
        {
            "trade_id": "2",
            "timestamp": int((START + timedelta(seconds=1)).timestamp() * 1000),
            "price": "101",
            "quantity": "1",
        },
        {
            "trade_id": "3",
            "timestamp": int(END.timestamp() * 1000),
            "price": "102",
            "quantity": "1",
        },
    )
    quality = profile_dataset_records(
        manifest,
        records,
        generated_at=RETRIEVED,
    )
    metrics = calculate_reference_return_metrics(
        (Decimal("0.01"), Decimal("-0.005"), Decimal("0.002")),
        periods_per_year=365,
    )
    published = PublishedBenchmarkRecord(
        reference_id="lean.regression.metric-definition.v1",
        source_id="quantconnect.lean.regression",
        project_name="LEAN regression metric reference",
        source_url="https://github.com/QuantConnect/Lean",
        dataset_id="lean.sample.regression",
        model_or_engine_version="v2",
        metric_spec_version="lean-statistics-v1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retrieved_at=RETRIEVED,
        reproducibility=ReproducibilityLevel.CODE_AVAILABLE,
        source_artifact_sha256="e" * 64,
        metrics=(
            BenchmarkMetric(
                name="total_return",
                value=metrics.total_return,
                unit="ratio",
            ),
        ),
    )
    validate_published_benchmark_source(published)
    run = ExternalBenchmarkRun(
        run_id="ctcc.external-reference.integration.2026-01-02",
        dataset_id=manifest.dataset_id,
        manifest_sha256=manifest.canonical_sha256(),
        quality_report_sha256=quality.canonical_sha256(),
        source_tree_sha="f" * 40,
        configuration_sha256=hashlib.sha256(b"reference-config").hexdigest(),
        metric_spec_version="ctcc-reference-metrics-v1",
        seed=0,
        started_at=RETRIEVED,
        completed_at=RETRIEVED + timedelta(seconds=1),
        status=BenchmarkRunStatus.PASSED,
        metrics=(
            BenchmarkMetric(
                name="total_return",
                value=metrics.total_return,
                unit="ratio",
            ),
        ),
        reference_ids=(published.reference_id,),
    )

    replayed = ExternalBenchmarkRun.model_validate_json(run.model_dump_json())
    assert receipts[0].verified is True
    assert quality.passed is True
    assert replayed == run
    assert replayed.canonical_sha256() == run.canonical_sha256()
    assert replayed.permitted_use == "research_reference_only"
    assert replayed.promotion_eligible is False
    assert replayed.execution_authority is False
    assert published.promotion_eligible is False
