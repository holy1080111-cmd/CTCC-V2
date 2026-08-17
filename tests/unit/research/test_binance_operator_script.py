from pathlib import Path


SCRIPT = Path("scripts/run_binance_btcusdt_reference_probe.ps1")


def test_operator_probe_requires_review_before_artifact_get() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    prepare = source.index("Prepare official Binance identity")
    confirmation = source.index('Read-Host (')
    acquire = source.index("Acquire pinned Binance ZIP")
    profile = source.index("Profile Binance kline quality")

    assert prepare < confirmation < acquire < profile
    assert source.count("ACQUIRE_REFERENCE_ONLY") == 2
    assert "--day 2024-01-01" in source
    assert "--symbol BTCUSDT" in source
    assert "--max-bytes 1048576" in source
    assert "--max-redirects 0" in source


def test_operator_probe_cannot_enable_execution_authority() -> None:
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

    assert "EXECUTION_AUTHORITY=0" in source
    assert "PROMOTION_ELIGIBLE=0" in source
    assert "REAL_ORDER_TESTED=0" in source
    assert "Invoke-RestMethod" not in source
    assert "place_order" not in source
