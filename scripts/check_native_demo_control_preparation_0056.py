"""Check one configured DB0056 Demo account before Control preparation."""

from __future__ import annotations

import argparse
import asyncio

from app.domain.source_primitives import canonical
from app.trade_qualification.native_demo_control_prepare_0056_v1 import (
    inspect_configured_demo_control_0056,
    verify_configured_demo_control_lifecycle_0056,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True)
    parser.add_argument(
        "--verify-disarmed-lifecycle",
        action="store_true",
        help="Explicitly acquire and release a disarmed owner for the selected account.",
    )
    args = parser.parse_args(argv)
    report = {
        "schema": "ctcc.demo.db0056.control_preparation.v1",
        "database_head": "0056",
        "admission": "DENY",
        "execution_authority": False,
        "control_writes": 0,
        "order_writes": 0,
    }
    if args.verify_disarmed_lifecycle:
        del report["control_writes"]
    try:
        if args.verify_disarmed_lifecycle:
            observation = asyncio.run(
                verify_configured_demo_control_lifecycle_0056(args.instrument)
            )
            report.update(
                status="CONTROL_RELEASED_DISARMED",
                control_mutation="DISARMED_LIFECYCLE_VERIFIED",
                control_revision=observation.state.revision,
                emergency_stop=observation.state.emergency_stop,
                arm_request_recorded=False,
            )
        else:
            result = asyncio.run(inspect_configured_demo_control_0056(args.instrument))
            state = result.observation.state
            report.update(
                status="CONTROL_PREPARED_AS_DATA",
                control_revision=state.revision,
                emergency_stop=state.emergency_stop,
                arm_request_recorded=state.arm_request_id is not None,
                genesis_sha256=result.source_genesis_sha256,
            )
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- no private provider, DB or account data.
        report["status"] = "CONTROL_PREPARE_REFUSED"
        if args.verify_disarmed_lifecycle:
            report["control_mutation"] = "UNCONFIRMED"
        print(canonical(report).decode("ascii"))
        return 1
    print(canonical(report).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
