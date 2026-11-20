"""Explicit physical58 negative-only setup; never starts or Arms the tool.

Creates only a stopped account Control and, on an otherwise empty supported
database, its missing unknown/locked legacy singleton. This is not adoption of
the preserved DB0017 or evidence of exchange flatness. No owner56 descriptor
is read, rewritten or treated as current58 startup trust.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from scripts import native_owner_upgrade_0058_v1 as owner58
from scripts import windows_tool_0058_v1 as package


def _confirmation(pins_sha, source_sha, scope_sha, roles_sha):
    package.need(
        all(
            type(value) is str and package._HEX.fullmatch(value) is not None
            for value in (pins_sha, source_sha, scope_sha, roles_sha)
        )
    )
    return "CTCC_DB0058_CREATE_STOPPED_STATE " + package.sha(
        package.canonical([pins_sha, source_sha, scope_sha, roles_sha])
    )


def _confirm(phrase):
    package.need(os.isatty(sys.stdin.fileno()) and os.isatty(sys.stderr.fileno()))
    print("只建立停止狀態；不代表帳戶已核對或可交易。", file=sys.stderr, flush=True)
    print(phrase, file=sys.stderr, flush=True)
    package.need(sys.stdin.readline(len(phrase) + 3) == phrase + "\n")


async def _account_stop(instrument_id, *, pins_sha, source_sha, roles, reconcile_only):
    from app.config.settings import get_settings
    from app.core import native_stopped_control_genesis_0056_v1 as account
    from app.core.native_manual_demo_lifespan_0056_v1 import (
        require_manual_demo_settings_0056_v1,
    )
    from app.demo_automation import native_server_selectors_v2 as selectors
    from app.demo_automation.native_source_only_v1 import _dispose
    from app.domain import native_database_profile_v1 as profiles
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient

    settings = get_settings()
    require_manual_demo_settings_0056_v1(settings)
    package.need(
        settings.okx_demo_credentials_configured
        and settings.okx_demo_rest_base_url == "https://openapi.okx.com"
        and settings.okx_demo_read_max_retries == 0
        and instrument_id in settings.okx_demo_allowed_symbol_list
    )
    selection = factories = result = None
    try:
        client = OkxDemoPrivateRestClient(settings=settings)
        with client.pinned_read_session() as lease:
            selection = await selectors._select_configured_demo_runtime_identity_v2(
                private_client=client,
                read_session=lease,
                instrument_ids=(instrument_id,),
                history_only=True,
            )
            factories = selectors._SELECTIONS[selection]["factories"]
            observed = selection.source_roles_0056
            package.need(
                selectors._selection_database_profile_0056(selection) is profiles.DB0058
                and observed.observation.database_head == "0058"
                and (observed.server_incarnation_sha256, observed.role_route_sha256)
                == (roles.server_incarnation_sha256, roles.role_route_sha256)
            )
            scope_sha = package.sha(
                package.canonical(["demo", selection.scope_config.expected_uid])
            )
            if not reconcile_only:
                _confirm(
                    _confirmation(
                        pins_sha, source_sha, scope_sha, roles.role_route_sha256
                    )
                )
            # A late/expired selector refuses before either write. The genuine
            # operation atomically stores a stop and independently reads it.
            selectors._record(selection)
            result = await account._operate_selected_genesis(
                selection, reconcile_only=reconcile_only
            )
            package.need(
                result.database_head == "0058"
                and result.armed is result.execution_authority is False
                and result.emergency_stop is True
            )
        return result
    finally:
        try:
            await _dispose(selection, factories)
        except BaseException:
            if result is not None and result.status == "CREATED_STOPPED":
                raise account.NativeStoppedControlGenesis0056Error(
                    "db0058_stopped_control_cleanup_uncertain_reconcile_only"
                ) from None
            raise


async def _run(
    *,
    profile_file,
    reviewed_pins_file,
    reviewed_pins_sha256,
    owner_dsn_file,
    ca_file,
    source_archive_file,
    instrument,
    reconcile_only,
):
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories import (
        native_legacy_control_genesis_0056_v1 as legacy,
    )
    from app.domain import native_database_profile_v1 as profiles
    from scripts.launch_windows_tool_0058_v1 import configure_process, runtime_lock

    document = configure_process(profile_file)
    package.need(document["mode"] == "demo" and type(reconcile_only) is bool)
    package.verify_dependencies(Path(document["package_root"]))
    package.need(
        Path(sys.executable).resolve()
        == Path(document["package_root"]) / "runtime/Scripts/python.exe"
    )
    original = owner58._load_pins(reviewed_pins_file, reviewed_pins_sha256)
    package.need(original.route.database_head == "0058")
    public = package.verify(
        Path(document["package_root"]), document["package_identity_sha256"]
    )
    package.need(
        (
            original.source_commit,
            original.source_tree,
            original.source_archive_sha256,
            original.route.source_manifest_sha256,
        )
        == (
            public["source_commit"],
            public["source_tree"],
            public["source_archive_sha256"],
            public["source_manifest_sha256"],
        )
    )
    profile_raw = package.regular(profile_file, 65536)

    def check():
        package.need(
            package.regular(profile_file, 65536) == profile_raw
            and owner58._load_pins(reviewed_pins_file, reviewed_pins_sha256) == original
        )
        current, _ = owner58._prepare(
            original, owner_dsn_file, ca_file, source_archive_file
        )
        package.need(current.source_head == "0058")
        return current

    with runtime_lock(Path(document["package_root"])):
        prepared = check()
        async with AsyncExitStack() as stack:
            owner, selected = await owner58.parent._configured_resources(
                stack, prepared, original
            )
            owner58._checked_preflight(
                await owner58.route.preflight_owner_route_0056_v1(
                    original.route,
                    owner_dsn_file=owner_dsn_file,
                    ca_file=ca_file,
                    profile=profiles.DB0058,
                ),
                original,
                prepared,
            )
            identity, _ = await owner58._role_observation(
                selected, owner, original, "0058"
            )
            roles = await observe_restricted_native_roles_0056_witness_v1(
                selected, source=package._SOURCE, profile=profiles.DB0058
            )
            package.need(
                (roles.server_incarnation_sha256, roles.role_route_sha256)
                == (identity.server_incarnation_sha256, identity.role_route_sha256)
            )
            check()
            stopped = await _account_stop(
                instrument,
                pins_sha=reviewed_pins_sha256,
                source_sha=document["package_identity_sha256"],
                roles=roles,
                reconcile_only=reconcile_only,
            )
            check()
            after, _ = await owner58._role_observation(
                selected, owner, original, "0058"
            )
            package.need(after == identity)
            factory = owner58.async_sessionmaker(
                owner, autoflush=False, expire_on_commit=False
            )
            result = await legacy.seed_owner_legacy_control_genesis_0056_v1(
                factory,
                event_sha256=stopped.control_event_sha256,
                pins=original.route,
                server_incarnation_sha256=roles.server_incarnation_sha256,
                reconcile_only=reconcile_only,
                profile=profiles.DB0058,
            )
            await legacy.read_restricted_legacy_control_genesis_0056_v1(
                selected,
                roles,
                event_sha256=stopped.control_event_sha256,
                expected_at=result.updated_at,
            )
            check()
            final, _ = await owner58._role_observation(
                selected, owner, original, "0058"
            )
            package.need(final == identity)
    return {
        "schema": "ctcc.windows_tool.physical0058.negative_setup_data.v1",
        "status": result.status,
        "database_head": "0058",
        "armed": False,
        "emergency_stop": True,
        "account_complete": False,
        "exchange_flat_verified": False,
        "startup_admission": False,
        "execution_authority": False,
        "order_writes": 0,
    }


def main(arguments=None):
    parser = package.PrivateParser(description=__doc__, allow_abbrev=False)
    for name in ("profile", "reviewed-pins", "owner-dsn", "ca", "source-archive"):
        parser.add_argument("--" + name + "-file", required=True, type=Path)
    parser.add_argument("--reviewed-pins-sha256", required=True)
    parser.add_argument(
        "--instrument", choices=("BTC-USDT-SWAP", "ETH-USDT-SWAP"), required=True
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--initialize-stopped", action="store_false", dest="reconcile_only"
    )
    mode.add_argument("--reconcile-only", action="store_true")
    args = parser.parse_args(arguments)
    try:
        print(package.canonical(asyncio.run(_run(**vars(args)))).decode())
        return 0
    except Exception:  # noqa: BLE001 -- stop-only setup never prints DSNs/UID/source/provider text.
        print(
            '{"schema":"ctcc.windows_tool.physical0058.negative_setup_data.v1","status":"STOPPED_SETUP_UNCONFIRMED_RECONCILE_ONLY","execution_authority":false,"order_writes":0}'
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
