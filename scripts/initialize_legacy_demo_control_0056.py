"""Create an explicitly reviewed, locked legacy Demo stop on an empty DB0056."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.domain.source_primitives import canonical, sha
from app.trade_evidence.submission_reporting import pin
from scripts import native_deployment_descriptor_0056_v1 as deployment
from scripts import native_owner_postgrant_readback_0056_v1 as postgrant
from scripts import native_owner_route_preflight_0056_v1 as owner_route


def _confirmation(plan_sha, descriptor_sha, event_sha, source_commit):
    return "CTCC_DB0056_CREATE_STOPPED_LEGACY " + sha(
        canonical(
            [
                "ctcc.native_legacy_control_genesis.confirmation.v1",
                pin(plan_sha),
                pin(descriptor_sha),
                pin(event_sha),
                source_commit,
            ]
        )
    )


def _confirm(expected):
    deployment._need(os.isatty(sys.stdin.fileno()) and os.isatty(sys.stderr.fileno()))
    print(expected, file=sys.stderr, flush=True)
    deployment._need(sys.stdin.readline(len(expected) + 3) == expected + "\n")


def _check_plan(pins, descriptor):
    deployment._need(
        (
            pins.source_commit,
            pins.source_tree,
            pins.source_archive_sha256,
            pins.route.source_manifest_sha256,
            pins.route.ca_sha256,
            pins.route.cluster_system_identifier_sha256,
            pins.portable_inherited_definitions_sha256,
            sha(canonical(asdict(pins))),
        )
        == (
            descriptor.source_commit,
            descriptor.source_tree,
            descriptor.source_archive_sha256,
            descriptor.source_manifest_sha256,
            descriptor.tls_ca_sha256,
            descriptor.owner_observed_cluster_sha256,
            descriptor.inherited_native_definitions_sha256,
            descriptor.owner_grant_plan_sha256,
        )
        and pins.route.database_head == "0056"
    )


async def _run(
    *,
    owner_plan_file: Path,
    owner_plan_sha256: str,
    owner_dsn_file: Path,
    descriptor_file: Path,
    descriptor_sha256: str,
    source_archive_file: Path,
    control_event_sha256: str,
    reconcile_only: bool,
):
    pins = postgrant._load_plan(owner_plan_file, owner_plan_sha256)
    descriptor = deployment._read_private_descriptor(descriptor_file, descriptor_sha256)
    settings = deployment.get_settings()

    def check_inputs():
        deployment._need(
            deployment.get_settings() is settings
            and postgrant._load_plan(owner_plan_file, owner_plan_sha256) == pins
            and deployment._read_private_descriptor(descriptor_file, descriptor_sha256)
            == descriptor
        )
        deployment._check_runtime_settings(settings, descriptor)
        deployment._check_installed_release(descriptor, source_archive_file)
        postgrant.target_plan._require_private_file(owner_dsn_file)
        _check_plan(pins, descriptor)

    check_inputs()
    event = pin(control_event_sha256)
    if not reconcile_only:
        _confirm(
            _confirmation(
                owner_plan_sha256, descriptor_sha256, event, descriptor.source_commit
            )
        )
    check_inputs()
    ca_file = Path(settings.ctcc_native_pg_tls_ca_file)
    readback = await postgrant.observe_owner_postgrant_readback_0056_v1(
        pins,
        owner_dsn_file=owner_dsn_file,
        ca_file=ca_file,
        source_archive_file=source_archive_file,
    )
    deployment._need(
        readback["status"] == "OBSERVED_DENY"
        and readback["database_head"] == "0056"
        and readback["target_grant_pins_sha256"] == descriptor.owner_grant_plan_sha256
        and readback["cluster_system_identifier_sha256"]
        == descriptor.owner_observed_cluster_sha256
        and readback["restricted_role_identity_sha256"]
        == descriptor.restricted_role_identity_sha256
        and readback["inherited_native_definitions_sha256"]
        == descriptor.inherited_native_definitions_sha256
        and readback["granted_inherited_acl_shape_sha256"]
        == descriptor.granted_inherited_acl_shape_sha256
        and readback["startup_admission"] is readback["execution_authority"] is False
    )
    server = pin(readback["server_incarnation_sha256"])
    check_inputs()
    prepared = owner_route._prepare(
        pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
    )
    engine = create_async_engine(
        prepared.url,
        poolclass=NullPool,
        connect_args={"ssl": prepared.tls, "timeout": 10},
        hide_parameters=True,
    )
    result = None
    try:
        selected = deployment.selectors.select_configured_native_demo_factories_v2()
        try:
            roles = await deployment.identity.observe_restricted_role_identity_0056_v2(
                selected, source=deployment._SOURCE
            )
            deployment._check_restricted_role_observation(roles, descriptor)
            deployment._need(roles.server_incarnation_sha256 == server)
            check_inputs()
            result = await genesis.seed_owner_legacy_control_genesis_0056_v1(
                postgrant._factory(engine),
                event_sha256=event,
                pins=pins.route,
                server_incarnation_sha256=server,
                reconcile_only=reconcile_only,
            )
            await genesis.read_restricted_legacy_control_genesis_0056_v1(
                selected, roles, event_sha256=event, expected_at=result.updated_at
            )
        finally:
            await deployment.selectors._dispose_failed_selection_factories_v2(selected)
        check_inputs()
    except (Exception, asyncio.CancelledError):
        if result is not None and result.status == "STOPPED_CREATED":
            raise genesis.LegacyControlGenesis0056Uncertain(
                "native_legacy_control_genesis_0056_uncertain"
            ) from None
        raise
    finally:
        try:
            await engine.dispose()
        except (Exception, asyncio.CancelledError):
            if result is not None and result.status == "STOPPED_CREATED":
                raise genesis.LegacyControlGenesis0056Uncertain(
                    "native_legacy_control_genesis_0056_uncertain"
                ) from None
            raise
    return result.status


def main(argv: list[str] | None = None) -> int:
    parser = deployment._PrivateParser(allow_abbrev=False, description=__doc__)
    for name in ("owner-plan", "owner-dsn", "descriptor", "source-archive"):
        parser.add_argument(f"--{name}-file", required=True, type=Path)
    for name in ("owner-plan", "descriptor", "control-event"):
        parser.add_argument(f"--{name}-sha256", required=True, type=pin)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--initialize-stopped", action="store_false", dest="reconcile_only"
    )
    mode.add_argument("--reconcile-only", action="store_true")
    exit_code = 1
    try:
        status = asyncio.run(_run(**vars(parser.parse_args(argv))))
        exit_code = 0
    except genesis.LegacyControlGenesis0056Uncertain:
        status = "STOPPED_COMMIT_UNCERTAIN_RECONCILE_ONLY"
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- redact setup inputs and database failures
        status = "REFUSED"
    print(
        canonical(
            {
                "schema": "ctcc.native_only.db0056_legacy_control_genesis.v1",
                "status": status,
                "database_head": "0056",
                "startup_admission": False,
                "execution_authority": False,
                "venue_writes": 0,
                "admission": "DENY",
            }
        ).decode("ascii")
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
