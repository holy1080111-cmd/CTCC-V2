"""One-shot reviewed owner provisioner; never imported or run by the API.

Requires three pre-created restricted LOGIN roles. It calls the existing exact
current55 28 + 11 + 3 grant transactions and then independently reads back
the writer/custody/frame roles. A partial commit remains a FAIL requiring
owner review; this program never broadens or silently retries an ACL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.native_role_preflight_v1 import verify_selected_native_roles_v1
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
    _database_route,
)
from scripts import current0055_native_account_source_acl_v1 as account
from scripts import current0055_native_lifecycle_acl_v1 as lifecycle
from scripts import current0055_runtime_grants as core

_SECRET_FILES = {
    "owner": Path("/run/secrets/native_owner_dsn"),
    "writer": Path("/run/secrets/native_writer_dsn"),
    "custody": Path("/run/secrets/native_custody_dsn"),
    "frame_reader": Path("/run/secrets/native_frame_dsn"),
}


def _secret_url(path: Path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError("native_role_secret_missing")
    raw = path.read_bytes()
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\n" in raw or b"\x00" in raw:
        raise ValueError("native_role_secret_invalid")
    return make_url(raw.decode("utf-8"))


async def _provision() -> dict:
    urls = {name: _secret_url(path) for name, path in _SECRET_FILES.items()}
    routes = {_database_route(url) for url in urls.values()}
    if len(routes) != 1 or len({url.username for url in urls.values()}) != 4:
        raise ValueError("native_role_route_or_identity_invalid")
    async with AsyncExitStack() as cleanup:
        engines = {}
        for name, url in urls.items():
            engine = create_async_engine(url, poolclass=NullPool, pool_pre_ping=True)
            cleanup.push_async_callback(engine.dispose)
            engines[name] = engine
        names = {
            name: urls[name].username for name in ("writer", "custody", "frame_reader")
        }
        async with engines["owner"].connect() as connection:
            core_receipt = await core.provision_current55_core_grants(
                connection, **names
            )
        async with (
            engines["owner"].connect() as owner,
            engines["writer"].connect() as writer,
        ):
            account_receipt = (
                await account.provision_current55_native_account_source_acl_v1(
                    owner, writer, **names
                )
            )
        async with (
            engines["owner"].connect() as owner,
            engines["writer"].connect() as writer,
        ):
            lifecycle_receipt = (
                await lifecycle.provision_current55_native_lifecycle_acl_v1(
                    owner, writer, **names
                )
            )
        if (
            core_receipt["grants"] != 28
            or account_receipt["delta_grants"] != 11
            or lifecycle_receipt["delta_grants"] != 3
            or any(
                receipt["database_head"] != "0055"
                or receipt["execution_authority"] is not False
                for receipt in (core_receipt, account_receipt, lifecycle_receipt)
            )
        ):
            raise ValueError("native_role_grant_receipt_invalid")
        writer_factory = async_sessionmaker(
            engines["writer"], autoflush=False, expire_on_commit=False
        )
        selected = ConfiguredNativeDemoFactoriesV2(
            writer_factory,
            async_sessionmaker(
                engines["custody"], autoflush=False, expire_on_commit=False
            ),
            async_sessionmaker(
                engines["frame_reader"], autoflush=False, expire_on_commit=False
            ),
            engines["custody"],
            engines["frame_reader"],
        )
        verified = await verify_selected_native_roles_v1(selected)
        return {
            "schema": "ctcc.native_only.role_provision.v1",
            "status": "PASS",
            "database_head": "0055",
            "grants": [28, 11, 3],
            "writer_seal_verified": verified["writer_seal_verified"],
            "custody_deployment_verified": verified["custody_deployment_verified"],
            "frame_pair_verified": verified["frame_pair_verified"],
            "account_complete": False,
            "execution_authority": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision-reviewed-native-roles", action="store_true")
    args = parser.parse_args()
    if not args.provision_reviewed_native_roles:
        print('{"status":"REFUSED","code":"explicit_owner_provision_required"}')
        return 2
    try:
        result = asyncio.run(_provision())
    except Exception:  # noqa: BLE001 - never print secret, SQL or catalog values
        print('{"status":"FAIL","code":"native_role_provision_refused"}')
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
