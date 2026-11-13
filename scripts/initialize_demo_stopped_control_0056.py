"""Initialize or reconcile the configured Demo account's stopped DB0056 Control."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from app.core.native_stopped_control_genesis_0056_v1 import (
    NativeStoppedControlGenesis0056Error,
    initialize_configured_stopped_control_0056_v1,
)


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--initialize-stopped", action="store_true")
    operation.add_argument("--reconcile-only", action="store_true")
    args = parser.parse_args(arguments)

    try:
        result = asyncio.run(
            initialize_configured_stopped_control_0056_v1(
                args.instrument, reconcile_only=args.reconcile_only
            )
        )
    except NativeStoppedControlGenesis0056Error as exc:
        print(str(exc), file=sys.stderr)
        return 2 if str(exc).endswith("_uncertain_reconcile_only") else 1
    except KeyboardInterrupt:
        print("db0056_stopped_control_interrupted_use_reconcile_only", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
