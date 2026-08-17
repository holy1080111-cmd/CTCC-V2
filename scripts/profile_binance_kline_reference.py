from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

from app.research.external_benchmarks import (
    ExternalArtifactAcquisitionReceipt,
    ExternalArtifactAcquisitionRequest,
)
from app.research.external_benchmarks.binance import (
    BinanceKlineCoordinates,
    BinancePublicArtifactIdentity,
)
from app.research.external_benchmarks.binance_klines import (
    profile_binance_kline_archive,
)
from app.research.external_benchmarks.evidence_io import (
    read_contract_json,
    write_contract_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile one pinned Binance kline ZIP without extracting it"
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument(
        "--day",
        type=date.fromisoformat,
        default=date(2024, 1, 1),
    )
    args = parser.parse_args()
    coordinates = BinanceKlineCoordinates(
        symbol=args.symbol,
        interval=args.interval,
        day=args.day,
    )
    prefix = (
        f"evidence/{coordinates.symbol.lower()}-"
        f"{coordinates.interval}-{coordinates.day.isoformat()}"
    )
    identity = read_contract_json(
        args.dataset_root,
        f"{prefix}-identity.json",
        BinancePublicArtifactIdentity,
    )
    request = read_contract_json(
        args.dataset_root,
        f"{prefix}-request.json",
        ExternalArtifactAcquisitionRequest,
    )
    receipt = read_contract_json(
        args.dataset_root,
        f"{prefix}-receipt.json",
        ExternalArtifactAcquisitionReceipt,
    )
    manifest, generic_quality, binance_quality, evidence = (
        profile_binance_kline_archive(
            coordinates,
            identity,
            request,
            receipt,
            args.dataset_root,
            generated_at=receipt.retrieved_at,
        )
    )
    outputs = (
        (f"{prefix}-manifest.json", manifest),
        (f"{prefix}-generic-quality.json", generic_quality),
        (f"{prefix}-binance-quality.json", binance_quality),
        (f"{prefix}-evidence.json", evidence),
    )
    for relative_path, model in outputs:
        status = write_contract_json(args.dataset_root, relative_path, model)
        print(f"EVIDENCE_OUTPUT={relative_path}")
        print(f"EVIDENCE_OUTPUT_STATUS={status}")
    print(binance_quality.model_dump_json(indent=2))
    print(evidence.model_dump_json(indent=2))
    print(f"BINANCE_KLINE_QUALITY_PASSED={int(evidence.passed)}")
    print("EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0")
    return 0 if evidence.passed else 2


if __name__ == "__main__":
    sys.exit(main())
