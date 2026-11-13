"""Read back a granted DB0056 target through fresh owner and role logins."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict
from datetime import UTC
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.core import native_role_identity_0056_v2 as identity56
from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import canonical, sha
from scripts import native_owner_grant_0056_draft_v1 as draft56
from scripts import native_owner_route_preflight_0056_v1 as owner_route
from scripts import native_owner_target_plan_0056_v1 as target_plan
from scripts import native_owner_upgrade_0056_v1 as owner_upgrade
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_reviewed_source_0056_v1 import (
    require_exact_installed_owner_source_0056_v1,
)

_SOURCE = Path(__file__).resolve().parents[1]
_SCHEMA = "ctcc.native_owner_0056.postgrant_target_readback.inert.v1"


class OwnerPostgrantReadbackRefusal(ValueError):
    """Fixed refusal without source, role, SQL or credential values."""


def _need(condition: bool) -> None:
    if condition is not True:
        raise OwnerPostgrantReadbackRefusal(
            "native_owner_0056_postgrant_readback_denied"
        )


def _factory(engine):
    return async_sessionmaker(engine, autoflush=False, expire_on_commit=False)


def _load_plan(path: Path, expected_sha256: str) -> draft56.OwnerGrantDraftPinsV1:
    _need(draft56._digest(expected_sha256))
    target_plan._require_private_file(path)
    raw = _read_bounded_regular_file_v1(path, 32768)
    _need(sha(raw) == expected_sha256)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and canonical(value) == raw
        and set(value) == {"schema", "pins"}
        and value["schema"] == target_plan._PRIVATE_PLAN_SCHEMA
        and type(value["pins"]) is dict
        and set(value["pins"])
        == set(draft56.OwnerGrantDraftPinsV1.__dataclass_fields__)
        and type(value["pins"]["route"]) is dict
        and set(value["pins"]["route"])
        == set(owner_route.OwnerRoutePinsV1.__dataclass_fields__)
    )
    fields = {
        **value["pins"],
        "route": owner_route.OwnerRoutePinsV1(**value["pins"]["route"]),
    }
    pins = draft56.OwnerGrantDraftPinsV1(**fields)
    draft56._names(pins)
    return pins


def _configured_role_urls(selected, route, names):
    _need(type(selected) is selectors.ConfiguredNativeDemoFactoriesV2)
    writer = selected.writer_factory.kw["bind"]
    custody = selected.custody_factory.kw["bind"]
    frame = selected.frame_reader_factory.kw["bind"]
    _need(custody is selected.custody_engine and frame is selected.frame_reader_engine)
    urls = (writer.url, custody.url, frame.url)
    _need(
        tuple(url.username for url in urls) == names
        and all(selectors._database_route(url) == route for url in urls)
    )
    return urls


def _owner_server_sha256(identity) -> str:
    # Match the role-neutral server component of runtime_witness_0056.
    _, _, _, _, database_oid, start, version, address, port = identity
    return sha(
        canonical(
            {
                "schema": "ctcc.native_only.pg0056_incarnation.v1",
                "database_oid": database_oid,
                "postmaster_started_at": start.astimezone(UTC).isoformat(
                    timespec="microseconds"
                ),
                "server_version_num": version,
                "server_address": address,
                "server_port": port,
            }
        )
    )


async def _owner_incarnation(factory, pins, names, role_oids):
    async with factory() as session, session.begin():
        for statement in roles56._OWNER_SETTINGS:
            await session.execute(text(statement))
        profile = tuple((await session.execute(owner_route._PROFILE_SQL)).one())
        owner_upgrade._checked_profile(profile, pins.route, "0056", read_only=True)
        actor = await roles56._identity_at_0056(session, pins.route.owner)
        _need((actor[3], actor[4], actor[8]) == (profile[4], profile[5], profile[3]))
        connection = await session.connection()
        observed_roles = await draft56.core_acl._roles(connection, names, actor[3])
        _need(observed_roles == dict(zip(names, role_oids, strict=True)))
        fitted = await roles56._catalog(session, roles56.fitted.CATALOG_SQL)
        _need(
            roles56.fitted.inspect_catalog_document(
                fitted, owner=pins.route.owner, demo_roles=names, fitted_roles=None
            )
            == pins.raw_fitted_owner_readonly_sha256
        )
        _need(await roles56._identity_at_0056(session, pins.route.owner) == actor)
    return _owner_server_sha256(actor)


async def observe_owner_postgrant_readback_0056_v1(
    pins: draft56.OwnerGrantDraftPinsV1,
    *,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
) -> dict[str, object]:
    """Check the externally pinned target and actual granted role principals.

    The owner proves the cluster identifier. Restricted roles need no right to
    query it: each connects to the same verified TLS route and must observe the
    same running server tuple as both owner sessions. This is an endpoint and
    incarnation comparison, not a cryptographic assertion by the three roles
    about the cluster identifier or proof of their authentication mechanism.
    """
    try:
        names = draft56._names(pins)
        role_oids = (pins.writer_oid, pins.custody_oid, pins.frame_reader_oid)
        source = owner_upgrade.OwnerUpgradePinsV1(
            pins.source_commit, pins.source_tree, pins.source_archive_sha256, pins.route
        )
        require_exact_installed_owner_source_0056_v1(
            _SOURCE, source, source_archive_file
        )
        target_plan._require_private_file(owner_dsn_file)
        expected_route = (
            "postgresql+asyncpg",
            pins.route.host,
            pins.route.port,
            pins.route.database,
        )
        prepared = owner_route._prepare(
            pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
        )
        settings = get_settings()
        _need(settings.ctcc_native_pg_tls_ca_sha256 == pins.route.ca_sha256)
        route = selectors._database_route(prepared.url)
        _need(route == expected_route and prepared.url.username == pins.route.owner)
        configured = selectors.select_configured_native_demo_factories_v2()
        async with AsyncExitStack() as stack:
            stack.push_async_callback(configured.custody_engine.dispose)
            stack.push_async_callback(configured.frame_reader_engine.dispose)
            role_urls = _configured_role_urls(configured, route, names)
            engines = []
            for url in (prepared.url, *role_urls):
                engine = create_async_engine(
                    url,
                    poolclass=NullPool,
                    connect_args={"ssl": prepared.tls, "timeout": 10},
                    hide_parameters=True,
                )
                stack.push_async_callback(engine.dispose)
                engines.append(engine)
            owner_engine, writer, custody, frame = engines
            owner = _factory(owner_engine)
            selected = selectors.ConfiguredNativeDemoFactoriesV2(
                _factory(writer), _factory(custody), _factory(frame), custody, frame
            )
            async with asyncio.timeout(220):
                before = await _owner_incarnation(owner, pins, names, role_oids)
                actual = await identity56.observe_restricted_role_identity_0056_v2(
                    selected, source=_SOURCE
                )
                after = await _owner_incarnation(owner, pins, names, role_oids)
            identity56.compare_static_role_identity_0056_v2(
                actual,
                expected_database_oid=pins.route.database_oid,
                expected_owner_oid=pins.route.owner_oid,
                expected_role_names=names,
                expected_role_oids=role_oids,
                expected_route=route,
                expected_sha256=identity56._static_identity_sha256(
                    pins.route.database_oid,
                    pins.route.owner_oid,
                    names,
                    role_oids,
                    route,
                ),
            )
            inherited = pins.portable_inherited_definitions_sha256
            _need(
                before == actual.server_incarnation_sha256 == after
                and actual.inherited_native_definitions_sha256 == inherited
                and actual.granted_inherited_acl_shape_sha256
                == roles56._granted_inherited_acl_shape_sha256(names, inherited)
                and get_settings() is settings
                and settings.ctcc_native_pg_tls_ca_sha256 == pins.route.ca_sha256
                and _configured_role_urls(configured, route, names) == role_urls
            )
            read_again = owner_route._prepare(
                pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
            )
            _need(read_again.url == prepared.url)
        require_exact_installed_owner_source_0056_v1(
            _SOURCE, source, source_archive_file
        )
        return {
            "schema": _SCHEMA,
            "status": "OBSERVED_DENY",
            "database_head": "0056",
            "target_grant_pins_sha256": sha(canonical(asdict(pins))),
            "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
            "server_incarnation_sha256": actual.server_incarnation_sha256,
            "restricted_role_identity_sha256": actual.stable_identity_sha256,
            "inherited_native_definitions_sha256": inherited,
            "granted_inherited_acl_shape_sha256": actual.granted_inherited_acl_shape_sha256,
            "restricted_logins_observed": 3,
            "separate_read_only_owner_observations": 2,
            "password_authentication_verified": False,
            "external_approval_authenticated": False,
            "startup_admission": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    except (asyncio.CancelledError, OwnerPostgrantReadbackRefusal):
        raise
    except Exception:  # noqa: BLE001 -- do not expose private profiles or SQL.
        _need(False)


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _need(False)


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--inspect-granted-owner-target", action="store_true")
    parser.add_argument("--plan-file", type=Path, required=True)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--owner-dsn-file", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument("--source-archive-file", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        _need(args.inspect_granted_owner_target is True)
        pins = _load_plan(args.plan_file, args.plan_file_sha256)
        receipt = asyncio.run(
            observe_owner_postgrant_readback_0056_v1(
                pins,
                owner_dsn_file=args.owner_dsn_file,
                ca_file=args.ca_file,
                source_archive_file=args.source_archive_file,
            )
        )
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- redact paths and private catalog inputs
        print('{"admission":"DENY","status":"REFUSED"}')
        return 1
    print(canonical(receipt).decode("ascii"))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
