"""Inspect the DB0056 Demo source profile without reaching OKX or starting CTCC."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict

from app.demo_automation.native_source_profile_0056_v1 import (
    NativeSourceProfileInspection0056V1,
    inspect_configured_demo_source_profile_0056_v1,
)

_USAGE = (
    "python -B -m scripts.inspect_demo_source_profile_0056 "
    "--preflight-only --instrument BTC-USDT-SWAP"
)


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    if args in (["--help"], ["-h"]):
        print(_USAGE)
        return 0
    report = NativeSourceProfileInspection0056V1()
    status = "INPUT_REFUSED"
    if len(args) == 3 and args[:2] == ["--preflight-only", "--instrument"]:
        try:
            report = asyncio.run(
                inspect_configured_demo_source_profile_0056_v1(args[2])
            )
            status = "OBSERVED_DENY" if report.checks_passed else "REFUSED"
        except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- no private traceback
            status = "REFUSED"
    print(json.dumps({"status": status, **asdict(report)}, sort_keys=True))
    return 2 if status == "OBSERVED_DENY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
