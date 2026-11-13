"""Project one stored DB0056 terminal report to the configured private local outbox."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

from app.database.repositories import native_closed_reporting_0056_v1 as reporting
from app.domain.source_primitives import canonical
from app.trade_evidence.submission_reporting import pin
from scripts import native_control_status_0056_v1 as startup


async def project_configured_native_terminal_report_0056_v1(
    *, descriptor_file: Path, source_archive_file: Path, spool_sha256: str
):
    deployment = startup.deployment
    requested = pin(spool_sha256)
    release = startup._read_bound_release(descriptor_file, source_archive_file)
    anchor, descriptor = release
    settings = deployment.get_settings()
    deployment._check_runtime_settings(settings, descriptor)
    configured_queue = reporting._configured_projection_queue(settings)

    def require_unchanged():
        deployment._need(deployment.get_settings() is settings)
        deployment._check_runtime_settings(settings, descriptor)
        deployment._need(
            reporting._configured_projection_queue(settings) == configured_queue
        )
        deployment._need(
            startup._read_bound_release(descriptor_file, source_archive_file) == release
        )

    selected = deployment.selectors.select_configured_native_demo_factories_v2()
    try:
        roles = await deployment.identity.observe_restricted_role_identity_0056_v2(
            selected, source=deployment._SOURCE
        )
        deployment._check_restricted_role_observation(roles, descriptor)
        require_unchanged()
        projected = await reporting._project_terminal_report_0056_v1(
            selected,
            spool_id=requested,
            root=configured_queue[0],
            queue_namespace=configured_queue[1],
            expected_roles=roles,
        )
        deployment._need(
            type(projected) is reporting.NativeTerminalLocalProjection0056V1
            and projected.spool_sha256 == requested
            and projected.server_incarnation_sha256 == roles.server_incarnation_sha256
            and projected.role_route_sha256 == roles.role_route_sha256
        )
    finally:
        await deployment.selectors._dispose_failed_selection_factories_v2(selected)
    require_unchanged()
    return {**asdict(projected), "deployment_admin_anchor_sha256": anchor.trust_sha256}


def main(argv: list[str] | None = None) -> int:
    parser = startup.deployment._PrivateParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--source-archive-file", required=True, type=Path)
    parser.add_argument("--descriptor-file", required=True, type=Path)
    parser.add_argument("--spool-sha256", required=True, type=pin)
    envelope = {
        "schema": "ctcc.db0056.native_terminal_local_projection.v1",
        "database_head": "0056",
        "admission": "DENY",
        "delivery_authority": False,
        "execution_authority": False,
        "notion_writes": 0,
        "order_writes": 0,
    }
    try:
        options = vars(parser.parse_args(argv))
        projected = asyncio.run(
            project_configured_native_terminal_report_0056_v1(**options)
        )
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- no private details
        print(
            canonical({**envelope, "status": "PROJECTION_UNCONFIRMED"}).decode("ascii")
        )
        return 1
    print(
        canonical(
            {**projected, **envelope, "status": "LOCAL_PROJECTION_VERIFIED"}
        ).decode("ascii")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
