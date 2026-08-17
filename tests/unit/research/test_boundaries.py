from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.research.external_benchmarks import (
    BinanceKlineEvidence,
    BinanceKlineQualityReport,
    BinancePublicArtifactIdentity,
    REFERENCE_SOURCE_CATALOG,
    DatasetQualityReport,
    ExternalArtifactAcquisitionReceipt,
    ExternalArtifactAcquisitionRequest,
    ExternalBenchmarkRun,
    ExternalDatasetManifest,
    PublishedBenchmarkRecord,
    ReferenceMetricBundle,
    reference_source,
    validate_manifest_source,
    validate_published_benchmark_source,
)
from tests.unit.research.helpers import trade_manifest


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


RESEARCH_ROOT = Path(__file__).resolve().parents[3] / "app" / "research"
APP_ROOT = RESEARCH_ROOT.parent
FORBIDDEN_IMPORT_PREFIXES = (
    "app.demo_automation",
    "app.exchange",
    "app.execution",
    "app.mie",
    "app.okx_demo",
    "app.okx_live",
    "app.orchestrator",
    "app.paper",
    "app.risk",
    "app.strategies",
    "httpx",
    "requests",
    "socket",
    "subprocess",
    "urllib.request",
)
NETWORK_ACQUISITION_MODULES = {
    Path("external_benchmarks/acquisition.py"),
    Path("external_benchmarks/binance.py"),
}


def imported_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if not isinstance(node, ast.ImportFrom):
        return ()
    module = node.module or ""
    return tuple(
        [module]
        + [
            f"{module}.{alias.name}" if module else alias.name
            for alias in node.names
        ]
    )


def test_external_benchmark_network_import_is_isolated_from_execution() -> None:
    violations: list[str] = []
    for path in sorted(RESEARCH_ROOT.rglob("*.py")):
        relative = path.relative_to(RESEARCH_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            for name in imported_names(node):
                if relative in NETWORK_ACQUISITION_MODULES and name == "httpx":
                    continue
                if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    violations.append(
                        f"{relative}:{node.lineno}:{name}"
                    )
    assert violations == []


def test_external_benchmark_pack_has_no_runtime_consumers() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.is_relative_to(RESEARCH_ROOT):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            for name in imported_names(node):
                if name == "app.research" or name.startswith("app.research."):
                    violations.append(
                        f"{path.relative_to(APP_ROOT)}:{node.lineno}:{name}"
                    )
    assert violations == []


def test_reference_contracts_contain_no_order_sizing_or_promotion_fields() -> None:
    forbidden = {
        "order_id",
        "client_order_id",
        "contracts",
        "leverage",
        "margin",
        "position_size",
        "write_authority",
    }
    for model in (
        ExternalDatasetManifest,
        DatasetQualityReport,
        ExternalArtifactAcquisitionRequest,
        ExternalArtifactAcquisitionReceipt,
        BinancePublicArtifactIdentity,
        BinanceKlineQualityReport,
        BinanceKlineEvidence,
        PublishedBenchmarkRecord,
        ReferenceMetricBundle,
        ExternalBenchmarkRun,
    ):
        assert forbidden.isdisjoint(model.model_fields)
        assert model.model_fields["execution_authority"].default is False
        if "promotion_eligible" in model.model_fields:
            assert model.model_fields["promotion_eligible"].default is False


def test_catalog_is_reviewed_reference_metadata_not_a_license_grant() -> None:
    assert len(REFERENCE_SOURCE_CATALOG) == 9
    assert set(REFERENCE_SOURCE_CATALOG) == {
        item.source_id for item in REFERENCE_SOURCE_CATALOG.values()
    }
    assert all(
        item.terms_review_required
        and item.reference_only
        and not item.execution_authority
        for item in REFERENCE_SOURCE_CATALOG.values()
    )
    with pytest.raises(ValueError, match="not reviewed"):
        reference_source("unknown.provider")

    descriptor = validate_manifest_source(trade_manifest())
    assert descriptor.source_id == "okx.historical_data"

    payload = trade_manifest().model_copy(
        update={"source_id": "quantconnect.lean.regression"}
    )
    with pytest.raises(ValueError, match="source kind"):
        validate_manifest_source(payload)

    payload = trade_manifest().model_copy(
        update={"source_url": "https://example.com/fake.csv"}
    )
    with pytest.raises(ValueError, match="outside reviewed provider"):
        validate_manifest_source(payload)

    benchmark = PublishedBenchmarkRecord(
        reference_id="lean.regression.reviewed",
        source_id="quantconnect.lean.regression",
        project_name="LEAN regression",
        source_url="https://github.com/QuantConnect/Lean",
        dataset_id="lean.sample.regression",
        model_or_engine_version="v2",
        metric_spec_version="lean-v1",
        published_at=NOW,
        retrieved_at=NOW,
        source_artifact_sha256="a" * 64,
        metrics=(
            {
                "name": "total_return",
                "value": "0.01",
                "unit": "ratio",
            },
        ),
        reproducibility="code_available",
    )
    assert (
        validate_published_benchmark_source(benchmark).source_id
        == "quantconnect.lean.regression"
    )


def test_external_benchmarks_do_not_change_fail_safe_runtime_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.auto_trade is False
    assert settings.paper_auto_execution is False
    assert settings.live_trading is False
    assert settings.okx_live_allow_order_writes is False
    assert settings.okx_live_auto_execution is False
    assert settings.okx_demo_allow_order_writes is False
    assert settings.okx_demo_auto_execution is False
    assert settings.okx_demo_soak_allow_execute is False


def test_network_acquisition_is_get_only_and_never_follows_redirects_implicitly() -> None:
    combined_literals: set[str] = set()
    for relative in NETWORK_ACQUISITION_MODULES:
        source = (RESEARCH_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        combined_literals.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        assert "follow_redirects=True" not in source
    assert "GET" in combined_literals
    assert "HEAD" in combined_literals
    assert "POST" not in combined_literals
    assert "PUT" not in combined_literals
    assert "PATCH" not in combined_literals
    assert "DELETE" not in combined_literals
