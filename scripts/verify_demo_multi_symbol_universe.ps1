param(
    [string]$RepoPath = "C:\CTCC-V2"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) {
    throw "Repository path does not exist: $RepoPath"
}

$probe = @'
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.config.settings import get_settings
from app.exchange.okx.public_rest import OkxPublicRestClient
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS


CANDIDATES = REVIEWED_DEMO_INSTRUMENT_IDS
MAX_SPREAD_BPS = Decimal("8")
MIN_QUOTE_NOTIONAL_24H_USDT = Decimal("10000000")
MAX_MINIMUM_ORDER_NOTIONAL_USDT = Decimal("25")
MIN_CONFIRMED_4H_CANDLES = 200
MAX_CONFIRMED_4H_AGE_HOURS = Decimal("8")


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


async def inspect_symbol(
    client: OkxPublicRestClient,
    instrument_id: str,
) -> dict[str, object]:
    try:
        instrument_rows, ticker, candles, order_book = await asyncio.gather(
            client.instruments(instrument_id),
            client.ticker(instrument_id),
            client.candles(instrument_id, "4H", 250),
            client.order_book(instrument_id, 5),
        )
        if len(instrument_rows) != 1:
            return {
                "instrument_id": instrument_id,
                "qualified": False,
                "blockers": ["instrument_metadata_not_unique"],
            }

        instrument = instrument_rows[0]
        blockers: list[str] = []
        if instrument.instrument_id != instrument_id:
            blockers.append("instrument_metadata_mismatch")
        if instrument.state != "live":
            blockers.append("instrument_not_live")
        if instrument.instrument_type != "SWAP":
            blockers.append("instrument_not_swap")
        if instrument.settlement_currency != "USDT":
            blockers.append("settlement_currency_not_usdt")
        if (
            ticker.last <= 0
            or ticker.bid <= 0
            or ticker.ask <= 0
            or ticker.ask < ticker.bid
        ):
            blockers.append("invalid_best_bid_ask")
        if not order_book.bids or not order_book.asks:
            blockers.append("order_book_empty")

        spread_bps = ticker.spread_pct * Decimal("100")
        if spread_bps > MAX_SPREAD_BPS:
            blockers.append("spread_above_8_bps")

        # OKX derivatives expose volCcy24h in base-currency units. Multiplying
        # by last produces a conservative estimated USDT notional screen.
        quote_notional_24h = ticker.volume_quote_24h * ticker.last
        if quote_notional_24h < MIN_QUOTE_NOTIONAL_24H_USDT:
            blockers.append("quote_notional_24h_below_10m_usdt")

        minimum_order_notional: Decimal | None = None
        base_currency = instrument_id.split("-", 1)[0]
        if instrument.contract_currency != base_currency:
            blockers.append("contract_value_currency_not_base")
        elif instrument.contract_value is None or instrument.contract_value <= 0:
            blockers.append("contract_value_missing")
        elif instrument.minimum_size <= 0:
            blockers.append("minimum_size_invalid")
        else:
            minimum_order_notional = (
                instrument.minimum_size
                * instrument.contract_value
                * ticker.last
            )
            if minimum_order_notional > MAX_MINIMUM_ORDER_NOTIONAL_USDT:
                blockers.append("minimum_order_notional_above_25_usdt")

        confirmed = [candle for candle in candles if candle.confirmed]
        if len(confirmed) < MIN_CONFIRMED_4H_CANDLES:
            blockers.append("insufficient_confirmed_4h_history")
        confirmed_age_hours: Decimal | None = None
        if confirmed:
            age_seconds = Decimal(
                str(
                    max(
                        0.0,
                        (
                            datetime.now(timezone.utc)
                            - confirmed[-1].timestamp.astimezone(timezone.utc)
                        ).total_seconds(),
                    )
                )
            )
            confirmed_age_hours = age_seconds / Decimal("3600")
            if confirmed_age_hours > MAX_CONFIRMED_4H_AGE_HOURS:
                blockers.append("confirmed_4h_candle_stale")

        return {
            "instrument_id": instrument_id,
            "qualified": not blockers,
            "blockers": blockers,
            "state": instrument.state,
            "settlement_currency": instrument.settlement_currency,
            "contract_value": decimal_text(instrument.contract_value),
            "contract_currency": instrument.contract_currency,
            "minimum_size_contracts": decimal_text(instrument.minimum_size),
            "minimum_order_notional_usdt": decimal_text(minimum_order_notional),
            "last": decimal_text(ticker.last),
            "spread_bps": decimal_text(spread_bps),
            "quote_notional_24h_usdt": decimal_text(quote_notional_24h),
            "confirmed_4h_candles": len(confirmed),
            "confirmed_4h_age_hours": decimal_text(confirmed_age_hours),
        }
    except Exception as exc:
        return {
            "instrument_id": instrument_id,
            "qualified": False,
            "blockers": ["public_market_probe_failed"],
            "error_type": type(exc).__name__,
        }


async def main() -> int:
    settings = get_settings()
    write_flags = {
        "AUTO_TRADE": settings.auto_trade,
        "PAPER_AUTO_EXECUTION": settings.paper_auto_execution,
        "LIVE_TRADING": settings.live_trading,
        "OKX_LIVE_ALLOW_ORDER_WRITES": settings.okx_live_allow_order_writes,
        "OKX_LIVE_AUTO_EXECUTION": settings.okx_live_auto_execution,
        "OKX_DEMO_ALLOW_ORDER_WRITES": settings.okx_demo_allow_order_writes,
        "OKX_DEMO_AUTO_EXECUTION": settings.okx_demo_auto_execution,
        "OKX_DEMO_SOAK_ALLOW_EXECUTE": settings.okx_demo_soak_allow_execute,
    }
    if any(write_flags.values()):
        print(
            json.dumps(
                {
                    "verified": False,
                    "blocker": "write_authority_must_be_disabled",
                    "write_flags": write_flags,
                },
                indent=2,
            )
        )
        return 2

    configured = {
        "okx_ws_symbols": settings.okx_ws_symbol_list,
        "paper_scan_symbols": settings.paper_scan_symbol_list,
        "okx_demo_allowed_symbols": settings.okx_demo_allowed_symbol_list,
        "okx_demo_scan_symbols": settings.okx_demo_scan_symbol_list,
        "okx_live_allowed_symbols": settings.okx_live_allowed_symbol_list,
        "okx_live_scan_symbols": settings.okx_live_scan_symbol_list,
    }
    expected = list(CANDIDATES)
    expanded_keys = (
        "okx_ws_symbols",
        "paper_scan_symbols",
        "okx_demo_allowed_symbols",
        "okx_demo_scan_symbols",
    )
    mismatched = [
        key for key in expanded_keys if configured[key] != expected
    ]
    if mismatched:
        print(
            json.dumps(
                {
                    "verified": False,
                    "blocker": "configured_universe_mismatch",
                    "mismatched": mismatched,
                    "expected": expected,
                    "configured": configured,
                },
                indent=2,
            )
        )
        return 3

    timeout = httpx.Timeout(settings.okx_public_timeout_seconds)
    async with httpx.AsyncClient(
        base_url=settings.okx_rest_base_url,
        timeout=timeout,
        headers={"User-Agent": f"CTCC-V2/{settings.app_version}"},
    ) as http_client:
        client = OkxPublicRestClient(http_client)
        semaphore = asyncio.Semaphore(2)

        async def bounded(instrument_id: str) -> dict[str, object]:
            async with semaphore:
                return await inspect_symbol(client, instrument_id)

        results = await asyncio.gather(*(bounded(item) for item in CANDIDATES))

    qualified = [
        str(item["instrument_id"])
        for item in results
        if item.get("qualified") is True
    ]
    rejected = [
        str(item["instrument_id"])
        for item in results
        if item.get("qualified") is not True
    ]
    payload = {
        "verified": not rejected,
        "read_only": True,
        "write_flags": write_flags,
        "configured": configured,
        "policy": {
            "max_spread_bps": decimal_text(MAX_SPREAD_BPS),
            "min_quote_notional_24h_usdt": decimal_text(
                MIN_QUOTE_NOTIONAL_24H_USDT
            ),
            "max_minimum_order_notional_usdt": decimal_text(
                MAX_MINIMUM_ORDER_NOTIONAL_USDT
            ),
            "min_confirmed_4h_candles": MIN_CONFIRMED_4H_CANDLES,
            "max_confirmed_4h_age_hours": decimal_text(
                MAX_CONFIRMED_4H_AGE_HOURS
            ),
        },
        "candidate_count": len(CANDIDATES),
        "qualified_count": len(qualified),
        "qualified_symbols": qualified,
        "rejected_symbols": rejected,
        "results": results,
    }
    print(json.dumps(payload, indent=2))
    return 0 if not rejected else 1


raise SystemExit(asyncio.run(main()))
'@

Push-Location -LiteralPath $RepoPath
try {
    docker compose ps api
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose API status check failed"
    }

    $health = (
        docker inspect --format '{{.State.Health.Status}}' ctcc-v2-api
    ).Trim()
    if ($LASTEXITCODE -ne 0 -or $health -ne "healthy") {
        throw "CTCC API container is not healthy"
    }

    Write-Host "Running read-only eight-symbol OKX Demo universe qualification..."
    $probe | docker compose exec -T api python -
    $probeExit = $LASTEXITCODE
    Write-Host "UNIVERSE_PROBE_EXIT=$probeExit"
    if ($probeExit -ne 0) {
        throw "One or more candidate symbols did not satisfy the universe policy"
    }

    Write-Host "DEMO_UNIVERSE_READONLY_VERIFIED=1"
    Write-Host "NO_ORDER_ENDPOINT_CALLED=1"
    Write-Host "ALL_WRITE_AUTHORITY_DISABLED=1"
}
finally {
    Pop-Location
}
