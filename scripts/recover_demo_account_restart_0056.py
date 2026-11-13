"""Recover the exact configured Demo account after an interrupted process."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from app.core.native_account_restart_recovery_0056_v1 import (
    NativeAccountRestartRecovery0056Error,
    NativeAccountRestartRecovery0056Uncertain,
    recover_configured_account_restart_0056_v1,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--latch-stop", action="store_true")
    action.add_argument("--reconcile-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            recover_configured_account_restart_0056_v1(
                args.instrument, reconcile_only=args.reconcile_only
            )
        )
    except (NativeAccountRestartRecovery0056Uncertain, KeyboardInterrupt):
        print(
            "db0056_account_restart_recovery_uncertain_reconcile_only", file=sys.stderr
        )
        return 2
    except NativeAccountRestartRecovery0056Error:
        print("db0056_account_restart_recovery_refused", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
