"""Run one configured Demo CURRENT source observation using restricted DB0056 roles."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.demo_automation.native_current_source_0056_v1 import (
    NativeCurrentSource0056Error,
    observe_configured_current_account_0056_v1,
    observe_configured_qualification_preflight_0056_v1,
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
        "--bootstrap-history",
        action="store_true",
        help="Create and reread the first source observation while stopped.",
    )
    mode.add_argument(
        "--restart-inventory",
        action="store_true",
        help="Observe fresh exchange inventory and local exposure around the stopped account.",
    )
    mode.add_argument(
        "--qualification-preflight",
        action="store_true",
        help="Compare stopped Control and ledger checkpoints around real CURRENT data.",
    )
    args = parser.parse_args(arguments)
    if args.include_history or args.bootstrap_history:
        try:
            digest = asyncio.run(
                observe_configured_current_history_0056_v1(
                    args.instrument, bootstrap_only=args.bootstrap_history
                )
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
                    "status": (
                        "MEASURED_HISTORY_GENESIS_OBSERVED"
                        if args.bootstrap_history
                        else "CURRENT_HISTORY_OBSERVED"
                    ),
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
    if args.qualification_preflight:
        operation = observe_configured_qualification_preflight_0056_v1
        status = "QUALIFICATION_PREFLIGHT_OBSERVED"
        failure = "db0056_qualification_preflight_refused"
    elif args.restart_inventory:
        operation = observe_configured_restart_inventory_0056_v1
        status = "RESTART_INVENTORY_OBSERVED"
        failure = "db0056_restart_inventory_refused"
    else:
        operation = observe_configured_current_account_0056_v1
        status = "CURRENT_SOURCE_OBSERVED"
        failure = "db0056_current_source_refused"
    try:
        result = asyncio.run(operation(args.instrument))
    except (NativeCurrentSource0056Error, KeyboardInterrupt):
        print(failure, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": status,
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
