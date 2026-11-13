"""Review the installed DB0056 release and stored control through restricted roles."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core import native_startup_control_observation_0056_v1 as control
from app.core import native_startup_trust_0056_v1 as trust
from app.domain.source_primitives import canonical
from scripts import native_deployment_descriptor_0056_v1 as deployment

_PIN_FIELDS = (
    "source_commit",
    "source_tree",
    "source_archive_sha256",
    "source_manifest_sha256",
    "tls_ca_sha256",
    "restricted_role_identity_sha256",
    "inherited_native_definitions_sha256",
    "granted_inherited_acl_shape_sha256",
    "reviewed_portable_reference_sha256",
)


def _read_bound_release(descriptor_file, source_archive_file):
    anchor = trust.read_native_startup_trust_0056_v1()
    deployment._need(
        type(anchor) is trust.AdminStartupTrust0056V1
        and anchor.startup_mode == "read_only_control_monitoring"
        and anchor.database_head == "0056"
    )
    descriptor = deployment._read_private_descriptor(
        descriptor_file, anchor.descriptor_sha256
    )
    deployment._need(
        all(getattr(anchor, name) == getattr(descriptor, name) for name in _PIN_FIELDS)
    )
    deployment._check_installed_release(descriptor, source_archive_file)
    return anchor, descriptor


def _require_same_route(witness, roles):
    deployment._need(
        type(witness) is control.NativeStartupControl0056WitnessV1
        and witness.server_incarnation_sha256 == roles.server_incarnation_sha256
        and witness.role_route_sha256 == roles.role_route_sha256
        and witness.observation.database_head == "0056"
        and witness.observation.startup_admission is False
        and witness.observation.execution_authority is False
    )


async def read_restricted_control_status_0056_v1(
    *, descriptor_file: Path, source_archive_file: Path
) -> dict[str, object]:
    """Cross-check one release, three configured roles, and two control reads."""
    release = _read_bound_release(descriptor_file, source_archive_file)
    anchor, descriptor = release
    settings = deployment.get_settings()
    deployment._check_runtime_settings(settings, descriptor)
    selected = deployment.selectors.select_configured_native_demo_factories_v2()
    try:
        before = await control.observe_startup_control_0056_witness_v1(selected)
        roles = await deployment.identity.observe_restricted_role_identity_0056_v2(
            selected, source=deployment._SOURCE
        )
        deployment._check_restricted_role_observation(roles, descriptor)
        after = await control.observe_startup_control_0056_witness_v1(selected)
        _require_same_route(before, roles)
        _require_same_route(after, roles)
        deployment._need(before == after)
    finally:
        await deployment.selectors._dispose_failed_selection_factories_v2(selected)
    deployment._need(deployment.get_settings() is settings)
    deployment._check_runtime_settings(settings, descriptor)
    deployment._need(
        _read_bound_release(descriptor_file, source_archive_file) == release
    )
    observed = after.observation.control
    return {
        "status": "CONTROL_OBSERVED",
        "database_head": "0056",
        "source_commit": anchor.source_commit,
        "source_tree": anchor.source_tree,
        "source_manifest_sha256": anchor.source_manifest_sha256,
        "deployment_admin_anchor_sha256": anchor.trust_sha256,
        "control_snapshot_sha256": after.control_snapshot_sha256,
        "control_revision": observed.revision,
        "stored_armed": observed.armed,
        "emergency_stop": observed.emergency_stop,
        "locked": observed.locked,
        "restart_latch_required": observed.restart_latch_required,
    }


def main(argv: list[str] | None = None) -> int:
    parser = deployment._PrivateParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--source-archive-file", required=True, type=Path)
    parser.add_argument("--descriptor-file", required=True, type=Path)
    envelope = {
        "schema": "ctcc.native_only.db0056_control_status.v1",
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
        "control_writes": 0,
        "order_writes": 0,
    }
    try:
        options = vars(parser.parse_args(argv))
        observed = asyncio.run(read_restricted_control_status_0056_v1(**options))
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- do not expose private input
        print(canonical({**envelope, "status": "REFUSED"}).decode("ascii"))
        return 1
    print(canonical({**envelope, **observed}).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
