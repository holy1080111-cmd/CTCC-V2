"""Run one configured Demo CURRENT source observation using restricted DB0056 roles."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.demo_automation.native_current_source_0056_v1 import (
    NativeCurrentSource0056Error,
    observe_configured_current_account_0056_v1,
)


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True)
    args = parser.parse_args(arguments)
    try:
        result = asyncio.run(
            observe_configured_current_account_0056_v1(args.instrument)
        )
    except (NativeCurrentSource0056Error, KeyboardInterrupt):
        print("db0056_current_source_refused", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "CURRENT_SOURCE_OBSERVED",
                "database_head": result.database_head,
                "receipt_sha256": result.receipt_sha256,
                "account_complete": result.account_complete,
                "execution_authority": result.execution_authority,
                "admission": result.admission,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
