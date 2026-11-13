"""Verify a stored terminal report in an administrator-pinned DB0056 deployment."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

from app.database.repositories import native_closed_reporting_0056_v1 as reporting
from app.domain.source_primitives import canonical
from app.trade_evidence.submission_reporting import pin
from scripts import native_control_status_0056_v1 as startup


async def observe_configured_report_lineage_0056_v1(
    *, descriptor_file: Path, source_archive_file: Path, spool_sha256: str
):
    deployment = startup.deployment
    release = startup._read_bound_release(descriptor_file, source_archive_file)
    anchor, descriptor = release
    settings = deployment.get_settings()
    deployment._check_runtime_settings(settings, descriptor)
    selected = deployment.selectors.select_configured_native_demo_factories_v2()
    try:
        roles = await deployment.identity.observe_restricted_role_identity_0056_v2(
            selected, source=deployment._SOURCE
        )
        deployment._check_restricted_role_observation(roles, descriptor)
        observed = await reporting.observe_terminal_lineage_0056_v1(
            selected, spool_id=spool_sha256
        )
        deployment._need(
            type(observed) is reporting.NativeTerminalLineage0056V1
            and observed.spool_sha256 == spool_sha256
            and observed.server_incarnation_sha256 == roles.server_incarnation_sha256
            and observed.role_route_sha256 == roles.role_route_sha256
            and observed.delivery_authority is observed.execution_authority is False
            and observed.admission == "DENY"
        )
    finally:
        await deployment.selectors._dispose_failed_selection_factories_v2(selected)
    deployment._need(deployment.get_settings() is settings)
    deployment._check_runtime_settings(settings, descriptor)
    deployment._need(
        startup._read_bound_release(descriptor_file, source_archive_file) == release
    )
    return {**asdict(observed), "deployment_admin_anchor_sha256": anchor.trust_sha256}


def main(argv: list[str] | None = None) -> int:
    parser = startup.deployment._PrivateParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--source-archive-file", required=True, type=Path)
    parser.add_argument("--descriptor-file", required=True, type=Path)
    parser.add_argument("--spool-sha256", required=True, type=pin)
    envelope = {
        "schema": "ctcc.db0056.native_terminal_lineage_observation.v1",
        "admission": "DENY",
        "delivery_authority": False,
        "execution_authority": False,
        "database_mutations": 0,
        "notion_writes": 0,
        "order_writes": 0,
    }
    try:
        options = vars(parser.parse_args(argv))
        observed = asyncio.run(observe_configured_report_lineage_0056_v1(**options))
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- no private details
        print(canonical({**envelope, "status": "REFUSED"}).decode("ascii"))
        return 1
    print(
        canonical(
            {**observed, **envelope, "status": "TERMINAL_LINEAGE_OBSERVED"}
        ).decode("ascii")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
