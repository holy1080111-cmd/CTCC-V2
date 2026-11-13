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
from app.demo_automation.native_history_source_0056_v1 import (
    NativeHistorySource0056Error,
    observe_configured_current_history_0056_v1,
)
from app.demo_automation.native_restart_inventory_0056_v1 import (
    observe_configured_restart_inventory_0056_v1,
)


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--include-history",
        action="store_true",
        help="Collect the original CURRENT-index history bundle under a fresh lease.",
    )
    mode.add_argument(
        "--restart-inventory",
        action="store_true",
        help="Observe fresh exchange inventory and local exposure around the stopped account.",
    )
    args = parser.parse_args(arguments)
    if args.include_history:
        try:
            digest = asyncio.run(
                observe_configured_current_history_0056_v1(args.instrument)
            )
        except NativeHistorySource0056Error as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("db0056_current_history_interrupted", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "status": "CURRENT_HISTORY_OBSERVED",
                    "database_head": "0056",
                    "receipt_sha256": digest,
                    "history_tail_closed": False,
                    "account_complete": False,
                    "execution_authority": False,
                    "admission": "DENY",
                },
                sort_keys=True,
            )
        )
        return 0
    try:
        operation = (
            observe_configured_restart_inventory_0056_v1
            if args.restart_inventory
            else observe_configured_current_account_0056_v1
        )
        result = asyncio.run(operation(args.instrument))
    except (NativeCurrentSource0056Error, KeyboardInterrupt):
        print(
            "db0056_restart_inventory_refused"
            if args.restart_inventory
            else "db0056_current_source_refused",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": (
                    "RESTART_INVENTORY_OBSERVED"
                    if args.restart_inventory
                    else "CURRENT_SOURCE_OBSERVED"
                ),
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
