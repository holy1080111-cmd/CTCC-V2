from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts/run_binance_reference_batch_probe.ps1"


def test_batch_probe_requires_review_before_artifact_gets() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    prepare = source.index("Prepare frozen Binance batch identities")
    review = source.index("Review frozen batch before artifact GETs")
    confirmation = source.index("Read-Host (")
    acquire = source.index("Acquire and profile frozen Binance batch")

    assert prepare < review < confirmation < acquire
    assert source.count("ACQUIRE_BINANCE_BATCH_REFERENCE_ONLY") == 2
    assert "expected_artifact_count -ne 180" in source
    assert "completed_artifact_count -ne 180" in source
    assert "total_minute_rows -ne 259200" in source
    assert "--max-concurrency 4" in source


def test_batch_probe_keeps_every_execution_authority_disabled() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for name in (
        "AUTO_TRADE",
        "PAPER_AUTO_EXECUTION",
        "LIVE_TRADING",
        "OKX_LIVE_ALLOW_ORDER_WRITES",
        "OKX_LIVE_AUTO_EXECUTION",
        "OKX_DEMO_ALLOW_ORDER_WRITES",
        "OKX_DEMO_AUTO_EXECUTION",
        "OKX_DEMO_SOAK_ALLOW_EXECUTE",
    ):
        assert f'"{name}"' in source

    assert "STRATEGY_EVALUATED=0" in source
    assert "COSTS_EVALUATED=0" in source
    assert "RUNTIME_CONSUMERS=0" in source
    assert "EXECUTION_AUTHORITY=0" in source
    assert "PROMOTION_ELIGIBLE=0" in source
    assert "REAL_ORDER_TESTED=0" in source
    assert "Invoke-RestMethod" not in source
    assert "place_order" not in source


def test_batch_probe_uses_only_the_frozen_plan() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "prepare_binance_kline_batch.py" in source
    assert "acquire_profile_binance_kline_batch.py" in source
    assert "--symbol" not in source
    assert "--day" not in source
    assert "RETROSPECTIVE_HOLDOUT=1" in source
