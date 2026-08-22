from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts/run_binance_btcusdt_reference_probe.ps1"
TERMS_REVIEW = (
    "docs/external_sources/"
    "binance_public_data_review_2026-08-17.md"
)
TERMS_REVIEW_PATH = PROJECT_ROOT / TERMS_REVIEW


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


def test_reviewed_terms_are_packaged_at_the_probe_runtime_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert TERMS_REVIEW_PATH.is_file()
    assert TERMS_REVIEW_PATH.stat().st_size > 0
    assert f"/app/{TERMS_REVIEW}" in source.replace("`\n", "")
