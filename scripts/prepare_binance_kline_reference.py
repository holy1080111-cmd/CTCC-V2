from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path
import sys

from app.research.external_benchmarks.binance import (
    BinanceKlineCoordinates,
    prepare_binance_kline_request,
)
from app.research.external_benchmarks.evidence_io import write_contract_json


async def _run(args: argparse.Namespace) -> int:
    coordinates = BinanceKlineCoordinates(
        symbol=args.symbol,
        interval=args.interval,
        day=args.day,
    )
    identity, request = await prepare_binance_kline_request(
        coordinates,
        args.terms_review,
    )
    prefix = (
        f"evidence/{coordinates.symbol.lower()}-"
        f"{coordinates.interval}-{coordinates.day.isoformat()}"
    )
    identity_path = f"{prefix}-identity.json"
    request_path = f"{prefix}-request.json"
    identity_status = write_contract_json(
        args.dataset_root,
        identity_path,
        identity,
    )
    request_status = write_contract_json(
        args.dataset_root,
        request_path,
        request,
    )
    print(identity.model_dump_json(indent=2))
    print(request.model_dump_json(indent=2))
    print(f"BINANCE_IDENTITY_OUTPUT={identity_path}")
    print(f"BINANCE_IDENTITY_OUTPUT_STATUS={identity_status}")
    print(f"BINANCE_REQUEST_OUTPUT={request_path}")
    print(f"BINANCE_REQUEST_OUTPUT_STATUS={request_status}")
    print("BINANCE_ARTIFACT_GET_PERFORMED=0")
    print("EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare one pinned Binance kline acquisition request without "
            "downloading the ZIP artifact"
        )
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--terms-review", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument(
        "--day",
        type=date.fromisoformat,
        default=date(2024, 1, 1),
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
