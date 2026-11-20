"""Render an explicit synthetic dashboard fixture without loading runtime settings."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.dashboard.native_manual_demo_0056_v1 import (
    render_manual_demo_dashboard_0056_v1,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--max-leverage", type=int, required=True)
    parser.add_argument("--max-contracts", required=True)
    parser.add_argument("--execution-enabled", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = SimpleNamespace(
        okx_demo_allowed_symbol_list=args.symbol,
        okx_demo_max_leverage=args.max_leverage,
        okx_demo_max_order_size_contracts=Decimal(args.max_contracts),
        okx_demo_allow_order_writes=args.execution_enabled,
        okx_demo_auto_execution=args.execution_enabled,
        okx_ws_enabled=args.execution_enabled,
    )
    response = render_manual_demo_dashboard_0056_v1(selected)
    output = args.output.resolve()
    headers = output.with_name(output.name + ".headers.json")
    if output.exists() or headers.exists():
        parser.error("output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema": "ctcc.synthetic_manual_dashboard.render.v1",
        "synthetic_configuration": True,
        "runtime_settings_loaded": False,
        "headers": dict(response.headers),
        "html_sha256": hashlib.sha256(response.body).hexdigest(),
        "exchange_requests": 0,
        "order_writes": 0,
    }
    with output.open("xb") as stream:
        stream.write(response.body)
    with headers.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(metadata, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
