"""Check one configured DB0056 Demo account before Control preparation."""

from __future__ import annotations

import argparse
import asyncio

from app.domain.source_primitives import canonical
from app.trade_qualification.native_demo_control_prepare_0056_v1 import (
    inspect_configured_demo_control_0056,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True)
    args = parser.parse_args(argv)
    report = {
        "schema": "ctcc.demo.db0056.control_preparation.v1",
        "database_head": "0056",
        "admission": "DENY",
        "execution_authority": False,
        "control_writes": 0,
        "order_writes": 0,
    }
    try:
        result = asyncio.run(inspect_configured_demo_control_0056(args.instrument))
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- no private provider, DB or account data.
        print(
            canonical({**report, "status": "CONTROL_PREPARE_REFUSED"}).decode("ascii")
        )
        return 1
    state = result.observation.state
    print(
        canonical(
            {
                **report,
                "status": "CONTROL_PREPARED_AS_DATA",
                "control_revision": state.revision,
                "emergency_stop": state.emergency_stop,
                "arm_request_recorded": state.arm_request_id is not None,
                "genesis_sha256": result.source_genesis_sha256,
            }
        ).decode("ascii")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
